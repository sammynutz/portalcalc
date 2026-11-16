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


def worker_count(tasks, max_workers=MAX_WORKERS):
    override = os.environ.get('PORTALCALC_BRACING_WORKERS')
    if override:
        try:
            return max(1, min(tasks, int(override)))
        except ValueError:
            pass
    return max(1, min(tasks, (os.cpu_count() or 2)-1, max_workers))


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
    from portalcalc.sizing import combined as shared
    pinned, selections, criteria = task
    return shared._check(context, pinned, selections, criteria)[1]


def size_task(context, task):
    """(pinned, selections, candidates, criteria) -> passing fast-sized selections, or None."""
    from portalcalc.sizing import combined as shared
    from portalcalc.bracing.analysis import AnalysisError
    from portalcalc.bracing.design import auto_size
    pinned, selections, candidates, criteria = task
    local = shared._with_layout(context, pinned)
    try:
        result, sized = auto_size(local, selections, candidates, criteria, local['cost_rates'], fast=True)
    except AnalysisError:
        return None
    return sized if result['design']['passes'] else None


def size_cost_task(context, task):
    """As ``size_task``, but returns (estimated cost, sized selections), or None."""
    from portalcalc.sizing import combined as shared
    from portalcalc.bracing.analysis import AnalysisError
    from portalcalc.bracing.design import auto_size
    pinned, selections, candidates, criteria = task
    local = shared._with_layout(context, pinned)
    try:
        result, sized = auto_size(local, selections, candidates, criteria, local['cost_rates'], fast=True)
    except AnalysisError:
        return None
    return (result['design']['estimated_cost'], sized) if result['design']['passes'] else None


def frame_task(context, restraint_kn_mm):
    """Size the 2D frame for one sway restraint (kN/mm); returns the sized frame and its state.

    ``context['baseline']`` is the captured state of the unrestrained sizing
    (sizing.combined.capture_state). Each task runs on its own windowless
    engine, so the result does not depend on which process runs it or on what
    was sized before: it is the 2D auto-size of the baseline with that restraint.
    """
    from portalcalc.frame.engine.headless import HeadlessFrameEngine
    from portalcalc.sizing import combined as shared
    engine = HeadlessFrameEngine()
    # As frontier.optimise holds them on the window while it sizes frames.
    engine._history_suspended = True
    engine._suppress_panel_topology_search = True
    shared.restore_state(engine, context['baseline'])
    engine.trial_sway_restraint_kn_mm = restraint_kn_mm
    sized = shared._size_frame(engine)
    return {**sized, 'state': shared.capture_state(engine)}


_QT_APPLICATION = None


def _worker_qt_application():
    """A hidden Qt application for a worker process, created once.

    Saving a bracing option goes through the 3D workspace dialog (its ratings,
    prices and per-location plans), which needs a Qt application to exist. A
    worker never shows anything, so it uses the offscreen platform. In the main
    process (the serial fallback) the running application is used as it is.
    """
    global _QT_APPLICATION
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        _QT_APPLICATION = QApplication([])
    return QApplication.instance()


def pair_task(context, task):
    """Exact check of one sized frame with one bracing option (sizing.frontier.verify_pair).

    ``task`` is (frame, option); ``context`` carries the design criteria and the
    section candidates. Runs on its own windowless engine: the check restores
    the frame's captured state first, so it depends on nothing else.
    """
    from portalcalc.frame.engine.headless import HeadlessFrameEngine
    from portalcalc.sizing.frontier import verify_pair
    _worker_qt_application()
    frame, option = task
    engine = HeadlessFrameEngine()
    engine._history_suspended = True
    engine._suppress_panel_topology_search = True
    return verify_pair(engine, frame, option, context['criteria'], context['candidates'])


def probe_task(context, task):
    """(process id, context token, task): used to confirm worker processes start and receive the context."""
    return os.getpid(), context.get('token'), task


def engine_probe_task(context, task):
    """(process id, solved, Qt available): what frame_task and pair_task need from a worker.

    Builds the windowless engine (so the field schema and data files must be
    reachable), solves the factory defaults and opens the hidden Qt application.
    """
    from portalcalc.frame.engine.headless import HeadlessFrameEngine
    engine = HeadlessFrameEngine()
    engine.generate_and_solve()
    return os.getpid(), engine.structure is not None, _worker_qt_application() is not None


def worker_probe(tasks=4):
    """Start the pool and report whether real worker processes answered.

    Used by the packaged-build check: in a frozen app a worker re-launches the EXE,
    so this proves the launcher hands control to the worker instead of opening the app,
    and that a worker can run the windowless engine and a hidden Qt application
    (the frame + bracing optimiser sizes frames and checks pairs that way).
    """
    with Runner({'token': 'portalcalc'}, tasks) as runner:
        results = runner.map(probe_task, range(tasks))
        engines = runner.map(engine_probe_task, range(2))
        failed = runner.failed
    pids = sorted({pid for pid, _, _ in results})
    return {
        'workers_requested': runner.workers,
        'worker_pids': pids,
        'parent_pid': os.getpid(),
        'ran_in_workers': failed is None and os.getpid() not in pids and runner.workers > 1,
        'context_received': all(token == 'portalcalc' for _, token, _ in results),
        'results_in_order': [task for _, _, task in results] == list(range(tasks)),
        'engine_solved_in_worker': failed is None and all(solved and pid != os.getpid() for pid, solved, _ in engines),
        'qt_in_worker': failed is None and all(qt for _, _, qt in engines),
        'fallback_error': None if failed is None else repr(failed),
    }


def _run(function, task):
    return function(_CONTEXT, task)


class Runner:
    """Map tasks over a lazily started pool sharing one 3D context.

    Use as a context manager; ``map(function, tasks, progress)`` returns the
    results in task order. ``progress(done, total)`` is called as tasks finish
    and may raise to cancel (the remaining tasks are dropped).
    """

    def __init__(self, context, max_tasks, max_workers=MAX_WORKERS):
        import multiprocessing
        check_not_bootstrapping()
        self.context = context
        # No nested pools inside a worker process.
        self.workers = (1 if multiprocessing.current_process().name != 'MainProcess'
                        else worker_count(max_tasks, max_workers))
        self.pool = None
        self.context_path = None
        self.failed = None
        self.abandoned = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.pool is not None:
            if self.abandoned:
                # Cancelled part-way: stop the workers now. A frame sizing can run
                # for half a minute, and waiting for it would hold up the cancel.
                for process in list((getattr(self.pool, '_processes', None) or {}).values()):
                    try:
                        process.terminate()
                    except Exception:
                        pass
                self.pool.shutdown(wait=False, cancel_futures=True)
            else:
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
                # Report at least every quarter second, so a caller's progress
                # display (and its Cancel button) stays live during long tasks.
                finished, pending = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
                done += len(finished)
                progress(done, len(tasks))
        except BaseException:
            self.abandoned = True
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
