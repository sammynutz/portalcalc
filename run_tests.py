"""Run the regression suite: every tests/*_regression.py plus tests/smoke_checks.py.

Each script runs in its own process, a few at a time, with the offscreen Qt
platform and licensing off. Logs go to build/test-logs/<script>.log.

    python run_tests.py                 # everything (10 to 15 minutes)
    python run_tests.py bracing purlin  # only scripts whose name contains a word
    python run_tests.py --jobs 2
"""

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
LOGS = ROOT / "build" / "test-logs"


def run_one(script: Path, timeout: int) -> dict:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PORTALCALC_LICENSE_ENFORCED"] = "0"
    env["PYTHONPATH"] = str(ROOT)
    started = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), env=env, capture_output=True,
                              text=True, timeout=timeout, errors="replace")
        text, code = proc.stdout + "\n--- STDERR ---\n" + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        text, code = f"TIMEOUT after {timeout}s\n{exc.stdout or ''}\n{exc.stderr or ''}", -1
    (LOGS / f"{script.stem}.log").write_text(text, encoding="utf-8")
    ran = re.search(r"Ran (\d+) tests? in", text)
    failed = re.search(r"FAILED \(([^)]*)\)", text)
    return {"script": script.name, "exit": code, "seconds": time.perf_counter() - started,
            "tests": int(ran.group(1)) if ran else None, "failed": failed.group(1) if failed else ""}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("words", nargs="*", help="run only scripts whose file name contains one of these")
    # The engine already uses several threads/processes per solve, so a few scripts at a time is fastest.
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=7200, help="seconds per script")
    args = parser.parse_args()

    scripts = sorted(TESTS.glob("*_regression.py")) + [TESTS / "smoke_checks.py"]
    if args.words:
        scripts = [s for s in scripts if any(word in s.name for word in args.words)]
    if not scripts:
        print("No matching test scripts.")
        return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    print(f"{len(scripts)} scripts, {args.jobs} at a time; logs in {LOGS}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(lambda s: run_one(s, args.timeout), scripts):
            results.append(result)
            status = "ok" if result["exit"] == 0 else "FAILED"
            count = "" if result["tests"] is None else f"{result['tests']} tests"
            print(f"{status:7s} {result['script']:44s} {count:10s} {result['seconds']:6.0f}s {result['failed']}", flush=True)
    failing = [r for r in results if r["exit"] != 0]
    total = sum(r["tests"] or 0 for r in results)
    print(f"\n{len(results) - len(failing)} of {len(results)} scripts passed ({total} unittest tests).")
    for r in failing:
        print(f"  FAILED {r['script']}  {r['failed']}  (see {LOGS / (Path(r['script']).stem + '.log')})")
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
