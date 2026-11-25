"""pytest plugin: report a run's progress as JSON lines.

Loaded with ``-p backend.utils.pytest_progress`` by run_tests.py when
GUAARDVARK_TEST_PROGRESS names a file. The Diagnostics panel's test run reads
that file while pytest is still going, which is how it shows the total, the
file being tested and each result as it arrives.

One object per line:
    {"event": "collected", "total": n, "files": {path: tests in it}}
    {"event": "collect_error", "id": path, "message": why the file could not be loaded}
    {"event": "start", "id": nodeid}
    {"event": "result", "id": nodeid, "outcome": "passed" | "failed" | "error" | "skipped",
     "duration": seconds, "message": the end of the failure text}
    {"event": "finished", "exit": pytest's exit status}
"""

import json
import os

_PATH = os.environ.get("GUAARDVARK_TEST_PROGRESS")
_MESSAGE_LIMIT = 1500


def _write(event: dict) -> None:
    if not _PATH:
        return
    try:
        with open(_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass  # progress is a courtesy; the run itself goes on


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def pytest_collection_finish(session):
    files: dict = {}
    for item in session.items:
        path = _file_of(item.nodeid)
        files[path] = files.get(path, 0) + 1
    _write({"event": "collected", "total": len(session.items), "files": files})


def pytest_collectreport(report):
    if report.failed:
        _write({"event": "collect_error", "id": report.nodeid,
                "message": str(report.longrepr)[-_MESSAGE_LIMIT:]})


def pytest_runtest_logstart(nodeid, location):
    _write({"event": "start", "id": nodeid})


class _Outcomes:
    """One result per test, from its setup, call and teardown reports."""

    def __init__(self):
        self.pending: dict = {}

    def add(self, report):
        entry = self.pending.setdefault(
            report.nodeid, {"outcome": None, "duration": 0.0, "message": ""})
        entry["duration"] += getattr(report, "duration", 0.0) or 0.0
        if report.failed:
            # A failure outside the call phase is an error in a fixture.
            outcome = "failed" if report.when == "call" else "error"
            if entry["outcome"] != "failed":
                entry["outcome"] = outcome
                entry["message"] = str(getattr(report, "longreprtext", ""))[-_MESSAGE_LIMIT:]
        elif report.skipped and entry["outcome"] is None:
            entry["outcome"] = "skipped"
            longrepr = report.longrepr
            if isinstance(longrepr, tuple) and len(longrepr) == 3:
                entry["message"] = str(longrepr[2])[:_MESSAGE_LIMIT]
        elif report.passed and report.when == "call" and entry["outcome"] is None:
            entry["outcome"] = "passed"
        if report.when == "teardown":
            done = self.pending.pop(report.nodeid)
            _write({"event": "result", "id": report.nodeid,
                    "outcome": done["outcome"] or "passed",
                    "duration": round(done["duration"], 3), "message": done["message"]})


_outcomes = _Outcomes()


def pytest_runtest_logreport(report):
    _outcomes.add(report)


def pytest_sessionfinish(session, exitstatus):
    _write({"event": "finished", "exit": int(exitstatus)})
