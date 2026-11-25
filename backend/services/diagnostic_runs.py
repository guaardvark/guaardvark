"""Diagnostics and test-suite runs that report while they are going.

A run is started from the System Dashboard, works on its own thread and keeps
a snapshot the page polls: the steps with their state, the check or test in
hand, counts, and what failed. One run of each kind at a time; the last few
finished runs stay readable so a reloaded page can still show the result.

``run_test_suite`` is the test-suite worker. The diagnostics worker lives in
``backend/api/diagnostics_api.py`` beside the checks it calls.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

KEEP_FINISHED = 6
FAILURE_LIMIT = 200
RECENT_FILES = 8
OUTPUT_TAIL = 20000

# A test that spins without sleeping can grow until the kernel starts killing
# other programs, so the run is stopped at this much memory. Measured when
# this was set (18,222 tests, pytest 9.0, 2026-11): the full suite stayed
# under 3 GB; a runaway test had grown 0.17 GB a second.
MEMORY_LIMIT_GB = float(os.environ.get("GUAARDVARK_TEST_MEMORY_GB", "10"))
# One hung test must not hold the run. The slowest test took 40 s in the same
# measurement, and the full suite 9 minutes.
TEST_TIMEOUT_S = int(os.environ.get("GUAARDVARK_TEST_TIMEOUT_S", "300"))

# Tests that reach live services or need the GPU are left out: the run has no
# database, queue or Redis and must not compete with a render for VRAM.
SUITE_MARKERS = "not integration and not gpu"

class Run:
    def __init__(self, kind: str, mode: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.mode = mode
        self.status = "running"
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.steps: List[Dict[str, Any]] = []
        self.current: Optional[str] = None
        self.done = 0
        self.total = 0
        # Progress by tests done (the test suite) instead of by steps finished.
        self.by_count = kind == "tests"
        self.peak_memory_gb = 0.0
        self.counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        self.failures: List[Dict[str, str]] = []
        self.files: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.recent_files: deque = deque(maxlen=RECENT_FILES)
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    # ---- steps ----

    def plan(self, steps: List[tuple]) -> None:
        with self.lock:
            self.steps = [{"key": k, "label": label, "status": "pending", "detail": "",
                           "duration": None, "group": group, "_started": None}
                          for k, label, group in steps]

    def _step(self, key: str) -> Optional[Dict[str, Any]]:
        return next((s for s in self.steps if s["key"] == key), None)

    def add_step(self, key: str, label: str, group: str = "") -> None:
        with self.lock:
            if self._step(key) is None:
                self.steps.append({"key": key, "label": label, "status": "pending", "detail": "",
                                   "duration": None, "group": group, "_started": None})

    def start_step(self, key: str, detail: str = "") -> None:
        with self.lock:
            step = self._step(key)
            if step is None:
                return
            step.update(status="running", detail=detail, _started=time.time())
            self.current = step["label"]

    def finish_step(self, key: str, status: str, detail: str = "",
                    duration: Optional[float] = None) -> None:
        with self.lock:
            step = self._step(key)
            if step is None:
                return
            if duration is None and step["_started"]:
                duration = time.time() - step["_started"]
            step.update(status=status, detail=detail or step["detail"], duration=duration)

    def step_detail(self, key: str, detail: str) -> None:
        with self.lock:
            step = self._step(key)
            if step is not None:
                step["detail"] = detail

    # ---- reading ----

    def percent(self) -> int:
        if self.by_count:
            return int(100 * self.done / self.total) if self.total else 0
        if self.steps:
            finished = sum(1 for s in self.steps if s["status"] not in ("pending", "running"))
            return int(100 * finished / len(self.steps))
        return 0

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            end = self.finished_at or time.time()
            failed_files = [dict(path=p, **f) for p, f in self.files.items() if f["failed"]]
            return {
                "id": self.id,
                "kind": self.kind,
                "mode": self.mode,
                "status": self.status,
                "running": self.status == "running",
                "elapsed": round(end - self.started_at, 1),
                "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in self.steps],
                "current": self.current,
                "done": self.done,
                "total": self.total,
                "percent": self.percent(),
                "counts": dict(self.counts),
                "failures": list(self.failures),
                "failures_truncated": self.counts["failed"] + self.counts["errors"] > len(self.failures),
                "files_total": len(self.files),
                "files_done": sum(1 for f in self.files.values() if f["done"] >= f["total"]),
                "recent_files": list(self.recent_files),
                "failed_files": failed_files[:FAILURE_LIMIT],
                "peak_memory_gb": round(self.peak_memory_gb, 1),
                "result": self.result,
                "error": self.error,
            }


_runs: "OrderedDict[str, Run]" = OrderedDict()
_registry_lock = threading.Lock()


class AlreadyRunning(RuntimeError):
    def __init__(self, run: Run):
        super().__init__(f"a {run.kind} run is already going")
        self.run = run


def get(run_id: str) -> Optional[Run]:
    return _runs.get(run_id)


def latest(kind: str) -> Optional[Run]:
    with _registry_lock:
        for run in reversed(_runs.values()):
            if run.kind == kind:
                return run
    return None


def start(kind: str, mode: str, worker: Callable[[Run], None], *, title: str) -> Run:
    """Start ``worker(run)`` on its own thread, shown in the progress footer."""
    with _registry_lock:
        active = next((r for r in _runs.values() if r.kind == kind and r.status == "running"), None)
        if active is not None:
            raise AlreadyRunning(active)
        run = Run(kind, mode)
        _runs[run.id] = run
        finished = [k for k, r in _runs.items() if r.status != "running"]
        for old in finished[:-KEEP_FINISHED]:
            del _runs[old]

    def _body():
        from backend.services.footer_jobs import footer_job
        try:
            with footer_job("analysis", title) as footer:
                stop = threading.Event()

                def _feed():
                    while not stop.wait(2.0):
                        footer.update(f"{title}: {run.current or '...'}", run.percent())

                feeder = threading.Thread(target=_feed, daemon=True, name=f"{kind}-footer")
                feeder.start()
                try:
                    worker(run)
                finally:
                    stop.set()
                if run.status in ("failed", "error"):
                    footer.fail(f"{title}: {run.error or run.status}")
        except Exception as e:  # noqa: BLE001 - the run records its own failure
            logger.error("%s run %s failed: %s", kind, run.id, e, exc_info=True)
            with run.lock:
                run.status, run.error = "error", str(e)
        finally:
            with run.lock:
                if run.status == "running":
                    run.status = "error"
                    run.error = run.error or "The run ended without a result."
                for step in run.steps:
                    if step["status"] in ("pending", "running"):
                        step["status"] = "skipped"
                run.finished_at = time.time()

    threading.Thread(target=_body, daemon=True, name=f"{kind}-run-{run.id}").start()
    return run


def cancel(run_id: str) -> bool:
    run = get(run_id)
    if run is None or run.status != "running":
        return False
    run.cancel.set()
    return True


# ---- the test suite ----

def _tree_memory_gb(pid: int) -> float:
    try:
        import psutil
        root = psutil.Process(pid)
        total = root.memory_info().rss
        for child in root.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                continue
        return total / (1024 ** 3)
    except Exception:  # noqa: BLE001 - no psutil, or the process just ended
        return 0.0


def _stop_tree(proc: subprocess.Popen) -> None:
    """End the test run and everything it started (it leads its own session)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            continue


def _tail(path: str, limit: int = OUTPUT_TAIL) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _first_json_line(path: str) -> Dict[str, Any]:
    """run_tests.py prints its result as one JSON line, then a text summary."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line)
    except (OSError, ValueError):
        pass
    return {}


def _file_entry(run: Run, path: str) -> Dict[str, Any]:
    entry = run.files.get(path)
    if entry is None:
        entry = run.files[path] = {"total": 0, "done": 0, "failed": 0, "skipped": 0, "duration": 0.0}
    return entry


def _apply_event(run: Run, event: Dict[str, Any]) -> None:
    kind = event.get("event")
    if kind == "phase":
        key = event.get("key")
        if key == "schema":
            run.finish_step("prepare", "passed")
            run.start_step("schema")
            if event.get("skipped"):
                run.finish_step("schema", "skipped", "This run has no database")
        elif key == "collect":
            if (run._step("schema") or {}).get("status") == "running":
                run.finish_step("schema", "passed")
            run.start_step("collect")
    elif kind == "collected":
        with run.lock:
            run.total = int(event.get("total") or 0)
            for path, count in (event.get("files") or {}).items():
                _file_entry(run, path)["total"] = count
            files = len(run.files)
        run.finish_step("collect", "passed", f"{run.total} tests in {files} files")
        run.start_step("run")
    elif kind == "collect_error":
        with run.lock:
            run.counts["errors"] += 1
            if len(run.failures) < FAILURE_LIMIT:
                run.failures.append({"id": event.get("id", ""), "outcome": "error",
                                     "message": event.get("message", "")})
    elif kind == "start":
        with run.lock:
            run.current = event.get("id")
    elif kind == "result":
        nodeid = event.get("id", "")
        outcome = event.get("outcome")
        path = nodeid.split("::", 1)[0]
        with run.lock:
            run.done += 1
            key = {"passed": "passed", "failed": "failed", "error": "errors",
                   "skipped": "skipped"}.get(outcome, "passed")
            run.counts[key] += 1
            entry = _file_entry(run, path)
            entry["done"] += 1
            entry["duration"] = round(entry["duration"] + float(event.get("duration") or 0), 2)
            if outcome in ("failed", "error"):
                entry["failed"] += 1
                if len(run.failures) < FAILURE_LIMIT:
                    run.failures.append({"id": nodeid, "outcome": outcome,
                                         "message": event.get("message", "")})
            elif outcome == "skipped":
                entry["skipped"] += 1
            if entry["total"] and entry["done"] >= entry["total"]:
                run.recent_files.append(dict(path=path, **entry))
            step = run._step("run")
            if step is not None:
                step["detail"] = f"{run.done} of {run.total}"


def run_test_suite(run: Run, repo_root: str) -> None:
    """Run the suite through run_tests.py in an environment with no live state,
    following pytest's progress file."""
    from backend.utils.isolated_env import isolated_env

    quick = run.mode == "quick"
    run.plan([
        ("prepare", "Preparing a run with no live data", ""),
        ("schema", "Checking the database schema", ""),
        ("collect", "Finding the tests", ""),
        ("run", "Running the tests", ""),
        ("summary", "Writing the summary", ""),
    ])
    script = os.path.join(repo_root, "run_tests.py")
    if not os.path.exists(script):
        raise RuntimeError(f"Test script not found at {script}")

    workdir = tempfile.mkdtemp(prefix="guaardvark-tests-")
    progress_path = os.path.join(workdir, "progress.jsonl")
    out_path, err_path = os.path.join(workdir, "stdout.txt"), os.path.join(workdir, "stderr.txt")
    open(progress_path, "w").close()

    pytest_args = ["-q", "-rfE", "-m", SUITE_MARKERS]
    if importlib.util.find_spec("pytest_timeout") is not None:
        pytest_args.append(f"--timeout={TEST_TIMEOUT_S}")
    cmd = [sys.executable, script] + (["--quick"] if quick else []) + pytest_args

    run.start_step("prepare", "No database, task queue, Redis, ComfyUI or GPU")
    stopped: Optional[str] = None
    with open(out_path, "w") as out, open(err_path, "w") as err:
        proc = subprocess.Popen(
            cmd, cwd=repo_root, stdout=out, stderr=err, start_new_session=True,
            env=isolated_env(
                SKIP_PREFIGHT="1",
                GUAARDVARK_TEST_QUIET="1",
                GUAARDVARK_TEST_PROGRESS=progress_path,
                # Tests never take VRAM from a render.
                CUDA_VISIBLE_DEVICES="",
            ),
        )
        try:
            with open(progress_path, "r", encoding="utf-8") as feed:
                buffered = ""
                last_memory_check = 0.0
                while True:
                    chunk = feed.read()
                    if chunk:
                        buffered += chunk
                        *lines, buffered = buffered.split("\n")
                        for line in lines:
                            try:
                                _apply_event(run, json.loads(line))
                            except ValueError:
                                continue
                        continue
                    if proc.poll() is not None:
                        break
                    if run.cancel.is_set():
                        stopped = "cancelled"
                        break
                    now = time.time()
                    if now - last_memory_check > 2.0:
                        last_memory_check = now
                        used = _tree_memory_gb(proc.pid)
                        run.peak_memory_gb = max(run.peak_memory_gb, used)
                        if used > MEMORY_LIMIT_GB:
                            stopped = (f"Stopped: the run was using {used:.1f} GB of memory "
                                       f"(limit {MEMORY_LIMIT_GB:g} GB) while on {run.current}")
                            break
                    time.sleep(0.25)
        finally:
            _stop_tree(proc)

    result = _first_json_line(out_path)
    returncode = result.get("returncode", proc.returncode)
    run.start_step("summary")
    payload = {
        "returncode": returncode,
        "log_path": result.get("log_path"),
        "stdout": (result.get("stdout") or _tail(out_path))[-OUTPUT_TAIL:],
        "stderr": (result.get("stderr") or _tail(err_path))[-OUTPUT_TAIL:],
        "scope": run.mode,
    }
    with run.lock:
        run.result = payload
        run.current = None
        if stopped == "cancelled":
            run.status = "cancelled"
        elif stopped:
            run.status, run.error = "error", stopped
        elif returncode == 0:
            run.status = "passed"
        elif returncode == 1:
            run.status = "failed"
        elif returncode == 5:
            run.status, run.error = "error", "No tests were found to run."
        else:
            run.status = "error"
            run.error = (f"pytest stopped with exit code {returncode} before finishing. "
                         "The output below says why.")
    if run.status in ("passed", "failed"):
        run.finish_step("run", run.status, f"{run.done} of {run.total}")
        run.finish_step("summary", "passed", payload["log_path"] or "")
        if run.status == "failed":
            with run.lock:
                run.error = f"{run.counts['failed']} failed, {run.counts['errors']} errors"
    shutil.rmtree(workdir, ignore_errors=True)
