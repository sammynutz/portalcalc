"""Process pool for independent 3D bracing checks and sizings.

The 3D checks are pure Python/NumPy and independent of each other, so they
run in worker processes (threads cannot: the GIL serialises Python code).
Each worker receives the 3D context once, in its initializer, and then only
the per-task panels and sections. Results are returned in task order and are
the same as running the tasks in this process; any pool failure falls back to
running them here.

``PORTALCALC_BRACING_WORKERS`` overrides the worker count (1 runs in-process).
"""
import os
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

MAX_WORKERS = 8
_CONTEXT = None


def worker_count(tasks):
    override = os.environ.get('PORTALCALC_BRACING_WORKERS')
    if override:
        try:
            return max(1, min(tasks, int(override)))
        except ValueError:
            pass
    return max(1, min(tasks, (os.cpu_count() or 2)-1, MAX_WORKERS))


def check_not_bootstrapping():
    """Raise if a worker is still importing the calling script.

    A spawned worker imports the caller's main script; a script that runs the
    optimiser at import time (no ``if __name__ == '__main__':``) would rerun it
    in every worker. Failing fast breaks only that worker, and the caller's
    Runner then runs the checks itself.
    """
    import multiprocessing
    if getattr(multiprocessing.current_process(), '_inheriting', False):
        raise RuntimeError('The frame + bracing optimiser was started while a worker process was importing the '
                           "calling script; put the script's code under if __name__ == '__main__':")


def _init(path):
    import pickle
    global _CONTEXT
    with open(path, 'rb') as handle:
        _CONTEXT = pickle.load(handle)


def check_task(context, task):
    """(pinned, selections, criteria) -> combined_sizing._check summary."""
    import combined_sizing as shared
    pinned, selections, criteria = task
    return shared._check(context, pinned, selections, criteria)[1]


def size_task(context, task):
    """(pinned, selections, candidates, criteria) -> passing fast-sized selections, or None."""
    import combined_sizing as shared
    from bracing_analysis import AnalysisError
    from bracing_design import auto_size
    pinned, selections, candidates, criteria = task
    local = shared._with_layout(context, pinned)
    try:
        result, sized = auto_size(local, selections, candidates, criteria, local['cost_rates'], fast=True)
    except AnalysisError:
        return None
    return sized if result['design']['passes'] else None


def size_cost_task(context, task):
    """As ``size_task``, but returns (estimated cost, sized selections), or None."""
    import combined_sizing as shared
    from bracing_analysis import AnalysisError
    from bracing_design import auto_size
    pinned, selections, candidates, criteria = task
    local = shared._with_layout(context, pinned)
    try:
        result, sized = auto_size(local, selections, candidates, criteria, local['cost_rates'], fast=True)
    except AnalysisError:
        return None
    return (result['design']['estimated_cost'], sized) if result['design']['passes'] else None


def _run(function, task):
    return function(_CONTEXT, task)


class Runner:
    """Map tasks over a lazily started pool sharing one 3D context.

    Use as a context manager; ``map(function, tasks, progress)`` returns the
    results in task order. ``progress(done, total)`` is called as tasks finish
    and may raise to cancel (the remaining tasks are dropped).
    """

    def __init__(self, context, max_tasks):
        import multiprocessing
        check_not_bootstrapping()
        self.context = context
        # No nested pools inside a worker process.
        self.workers = 1 if multiprocessing.current_process().name != 'MainProcess' else worker_count(max_tasks)
        self.pool = None
        self.context_path = None
        self.failed = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
        if self.context_path is not None:
            try:
                os.remove(self.context_path)
            except OSError:
                pass
        return False

    def _serial(self, function, tasks, progress):
        out = []
        for task in tasks:
            out.append(function(self.context, task))
            progress(len(out), len(tasks))
        return out

    def map(self, function, tasks, progress=lambda done, total: None):
        tasks = list(tasks)
        if self.workers <= 1 or len(tasks) <= 1 or self.failed is not None:
            return self._serial(function, tasks, progress)
        try:
            if self.pool is None:
                import pickle
                import tempfile
                # The context (several MB) goes to workers through a file, not
                # the initializer arguments: on Windows those are written down
                # each worker's start-up pipe, which blocks until that worker
                # has imported the main script. Workers then started one at a
                # time, and a worker that died first left the write blocked.
                handle, self.context_path = tempfile.mkstemp(prefix='portalcalc_bracing_', suffix='.pickle')
                with os.fdopen(handle, 'wb') as file:
                    pickle.dump(self.context, file, protocol=pickle.HIGHEST_PROTOCOL)
                self.pool = ProcessPoolExecutor(self.workers, initializer=_init, initargs=(self.context_path,))
            futures = [self.pool.submit(_run, function, task) for task in tasks]
        except Exception as error:   # e.g. no process support: run here
            self.failed = error
            return self._serial(function, tasks, progress)
        pending, done = set(futures), 0
        try:
            while pending:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                done += len(finished)
                progress(done, len(tasks))
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        from concurrent.futures.process import BrokenProcessPool
        try:
            return [future.result() for future in futures]
        except BrokenProcessPool as error:
            self.failed = error
            self.pool = None
            return self._serial(function, tasks, progress)
