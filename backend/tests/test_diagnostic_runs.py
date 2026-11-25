"""Diagnostics and test-suite runs that report while they are going:
the pytest progress plugin, the run a page polls, and the routes that start,
read and stop one."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.api import diagnostics_api
from backend.services import diagnostic_runs


def _wait(run, seconds=20):
    deadline = time.time() + seconds
    while run.status == "running" or run.finished_at is None:
        assert time.time() < deadline, f"run still {run.status}: {run.snapshot()}"
        time.sleep(0.02)
    return run.snapshot()


@pytest.fixture(autouse=True)
def _no_runs_left_over():
    diagnostic_runs._runs.clear()
    yield
    for run in list(diagnostic_runs._runs.values()):
        run.cancel.set()
    diagnostic_runs._runs.clear()


# ---- the pytest plugin ----

def test_the_plugin_reports_the_plan_and_one_result_per_test(tmp_path):
    (tmp_path / "test_sample.py").write_text(textwrap.dedent("""
        import pytest

        @pytest.fixture
        def broken():
            raise RuntimeError("fixture went wrong")

        def test_ok():
            pass

        def test_bad():
            assert 1 == 2

        @pytest.mark.skip(reason="not today")
        def test_skipped():
            pass

        def test_fixture_error(broken):
            pass
    """))
    progress = tmp_path / "progress.jsonl"
    env = dict(os.environ, GUAARDVARK_TEST_PROGRESS=str(progress),
               PYTHONPATH=str(REPO_ROOT / "backend" / "tests" / "plugins"))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p",
         "pytest_progress", "-c", os.devnull, "--rootdir", str(tmp_path),
         "test_sample.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    events = [json.loads(line) for line in progress.read_text().splitlines()]

    collected = next(e for e in events if e["event"] == "collected")
    assert collected["total"] == 4
    assert collected["files"] == {"test_sample.py": 4}
    results = {e["id"].split("::")[1]: e for e in events if e["event"] == "result"}
    assert {name: e["outcome"] for name, e in results.items()} == {
        "test_ok": "passed", "test_bad": "failed",
        "test_skipped": "skipped", "test_fixture_error": "error"}
    assert "assert 1 == 2" in results["test_bad"]["message"]
    assert "fixture went wrong" in results["test_fixture_error"]["message"]
    assert results["test_skipped"]["message"].endswith("not today")
    assert [e["id"] for e in events if e["event"] == "start"] == [e["id"] for e in events if e["event"] == "result"]
    assert events[-1] == {"event": "finished", "exit": proc.returncode} and proc.returncode == 1


# ---- a run's state ----

def test_events_build_the_counts_the_files_and_the_failures():
    run = diagnostic_runs.Run("tests", "full")
    run.plan([("prepare", "p", ""), ("schema", "s", ""), ("collect", "c", ""), ("run", "r", "")])
    for event in (
        {"event": "phase", "key": "schema", "skipped": True},
        {"event": "phase", "key": "collect"},
        {"event": "collected", "total": 3, "files": {"a.py": 2, "b.py": 1}},
        {"event": "start", "id": "a.py::one"},
        {"event": "result", "id": "a.py::one", "outcome": "passed", "duration": 0.5},
        {"event": "result", "id": "a.py::two", "outcome": "failed", "duration": 1, "message": "boom"},
        {"event": "start", "id": "b.py::three"},
    ):
        diagnostic_runs._apply_event(run, event)
    snap = run.snapshot()

    assert (snap["done"], snap["total"], snap["percent"]) == (2, 3, 66)
    assert snap["counts"] == {"passed": 1, "failed": 1, "errors": 0, "skipped": 0}
    assert snap["current"] == "b.py::three"
    assert (snap["files_done"], snap["files_total"]) == (1, 2)
    assert snap["recent_files"] == [
        {"path": "a.py", "total": 2, "done": 2, "failed": 1, "skipped": 0, "duration": 1.5}]
    assert snap["failures"] == [{"id": "a.py::two", "outcome": "failed", "message": "boom"}]
    assert [s["status"] for s in snap["steps"]] == ["passed", "skipped", "passed", "running"]
    assert snap["steps"][2]["detail"] == "3 tests in 2 files"


def test_a_test_run_shows_no_percent_until_the_total_is_known():
    run = diagnostic_runs.Run("tests", "quick")
    run.plan([("prepare", "p", ""), ("collect", "c", "")])
    run.finish_step("prepare", "passed")
    assert run.percent() == 0


def test_a_run_ends_with_its_workers_status_and_one_at_a_time():
    release = []

    def worker(run):
        run.plan([("one", "One", ""), ("two", "Two", "")])
        run.start_step("one")
        while not release:
            time.sleep(0.01)
        run.finish_step("one", "passed", "fine")
        run.status = "passed"

    run = diagnostic_runs.start("diagnostics", "basic", worker, title="Diagnostics")
    with pytest.raises(diagnostic_runs.AlreadyRunning) as busy:
        diagnostic_runs.start("diagnostics", "basic", worker, title="Diagnostics")
    assert busy.value.run is run
    # A run of the other kind is not held up by it.
    other = diagnostic_runs.start("tests", "quick", lambda r: setattr(r, "status", "passed"), title="Test suite")
    release.append(True)
    snap = _wait(run)
    _wait(other)

    assert snap["status"] == "passed" and snap["running"] is False
    assert [(s["status"], s["detail"]) for s in snap["steps"]] == [("passed", "fine"), ("skipped", "")]
    assert diagnostic_runs.latest("diagnostics") is run


def test_a_worker_that_raises_ends_the_run_as_an_error():
    def worker(run):
        run.plan([("one", "One", "")])
        run.start_step("one")
        raise RuntimeError("could not start")

    snap = _wait(diagnostic_runs.start("tests", "quick", worker, title="Test suite"))

    assert snap["status"] == "error" and snap["error"] == "could not start"
    assert snap["steps"][0]["status"] == "skipped"


# ---- the test-suite worker ----

FAKE_RUN_TESTS = """
import json, os, sys, time
path = os.environ["GUAARDVARK_TEST_PROGRESS"]
def emit(e):
    with open(path, "a") as f:
        f.write(json.dumps(e) + "\\n")
assert os.environ["CUDA_VISIBLE_DEVICES"] == ""
assert os.environ["GUAARDVARK_NO_LIVE_STATE"] == "1"
assert os.environ["GUAARDVARK_COMFYUI_URL"] == "http://127.0.0.1:9"
emit({"event": "phase", "key": "schema", "skipped": True})
emit({"event": "phase", "key": "collect"})
emit({"event": "collected", "total": 2, "files": {"t.py": 2}})
emit({"event": "start", "id": "t.py::a"})
emit({"event": "result", "id": "t.py::a", "outcome": "passed", "duration": 0.1})
%(body)s
print(json.dumps({"returncode": %(rc)d, "stdout": "summary line", "stderr": "", "log_path": "/tmp/x.log"}))
print("Test run text that is not JSON")
sys.exit(%(rc)d)
"""


def _suite(tmp_path, body="", rc=0, mode="quick"):
    (tmp_path / "run_tests.py").write_text(FAKE_RUN_TESTS % {"body": body, "rc": rc})
    return diagnostic_runs.start(
        "tests", mode, lambda run: diagnostic_runs.run_test_suite(run, str(tmp_path)), title="Test suite")


def test_a_passing_suite_is_followed_to_its_summary(tmp_path):
    snap = _wait(_suite(
        tmp_path, 'emit({"event": "result", "id": "t.py::b", "outcome": "passed", "duration": 0.1})'))

    assert snap["status"] == "passed", snap
    assert (snap["done"], snap["total"], snap["percent"]) == (2, 2, 100)
    assert snap["result"]["returncode"] == 0 and snap["result"]["stdout"] == "summary line"
    assert snap["result"]["log_path"] == "/tmp/x.log"
    assert [s["status"] for s in snap["steps"]] == ["passed", "skipped", "passed", "passed", "passed"]


def test_a_failing_suite_names_what_failed(tmp_path):
    snap = _wait(_suite(
        tmp_path, 'emit({"event": "result", "id": "t.py::b", "outcome": "failed", "duration": 0.1, "message": "nope"})',
        rc=1))

    assert snap["status"] == "failed" and snap["error"] == "1 failed, 0 errors"
    assert snap["failures"] == [{"id": "t.py::b", "outcome": "failed", "message": "nope"}]
    assert snap["failed_files"][0]["path"] == "t.py"


@pytest.mark.parametrize("rc, said", [(5, "No tests were found"), (4, "exit code 4")])
def test_a_run_that_tested_nothing_is_not_a_pass(tmp_path, rc, said):
    snap = _wait(_suite(tmp_path, rc=rc))

    assert snap["status"] == "error" and said in snap["error"]


def test_stop_ends_the_run_and_its_process(tmp_path):
    pid_file = tmp_path / "pid"
    run = _suite(tmp_path, f'open({str(pid_file)!r}, "w").write(str(os.getpid())); time.sleep(120)')
    deadline = time.time() + 20
    while not pid_file.exists() or not pid_file.read_text():
        assert time.time() < deadline
        time.sleep(0.05)

    assert diagnostic_runs.cancel(run.id) is True
    snap = _wait(run)

    assert snap["status"] == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert diagnostic_runs.cancel(run.id) is False


def test_a_run_over_the_memory_limit_is_stopped_and_says_why(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(diagnostic_runs, "_tree_memory_gb",
                        lambda _pid: 64.0 if started and started[0].current == "t.py::a" else 0.1)
    monkeypatch.setattr(diagnostic_runs, "MEMORY_LIMIT_GB", 10.0)

    started.append(_suite(tmp_path, "time.sleep(120)"))
    snap = _wait(started[0])

    assert snap["status"] == "error"
    assert "64.0 GB of memory" in snap["error"] and "t.py::a" in snap["error"]
    assert snap["peak_memory_gb"] == 64.0


# ---- run_tests.py ----

def test_the_summary_line_is_read_even_with_no_passes():
    import run_tests

    out = "F.\n=== short test summary info ===\nFAILED x.py::t - boom\n2 failed, 3 errors, 1 skipped in 0.12s\n"
    assert run_tests._parse_pytest_output(out)["counts"] == {
        "passed": 0, "failed": 2, "errors": 3, "skipped": 1}
    assert run_tests._parse_pytest_output("1 error in 2.00s\n")["counts"]["errors"] == 1


# ---- the self-test's overall status ----

@pytest.mark.parametrize("results, expected", [
    ({"legacy_diagnostics": {"llm_basic_response": True, "db_connection": True,
                             "active_model_status": "Responsive"}}, "PASS"),
    ({"legacy_diagnostics": {"llm_basic_response": False, "db_connection": True}}, "CRITICAL"),
    ({"legacy_diagnostics": {"llm_basic_response": True, "db_connection": False}}, "FAIL"),
    ({"legacy_diagnostics": {"llm_basic_response": True, "db_connection": True},
      "categories": {"llm": {"status": "fail"}, "database": {"status": "pass"}}}, "FAIL"),
    ({"legacy_diagnostics": {"llm_basic_response": True, "db_connection": True},
      "categories": {"backend": {"status": "warning"}, "database": {"status": "pass"}}}, "WARNING"),
    ({"legacy_diagnostics": {"llm_basic_response": True, "db_connection": True},
      "categories": {"backend": {"status": "pass"}, "database": {"status": "pass"}}}, "PASS"),
    ({"categories": {"database": {"status": "pass"}}}, "PASS"),
])
def test_overall_status_follows_the_checks(results, expected):
    assert diagnostics_api._determine_overall_status(results) == expected


def test_pip_conflicts_are_shown_not_an_empty_error(monkeypatch):
    monkeypatch.setattr(diagnostics_api.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=1, stdout="a 1.0 requires b, which is not installed.\n", stderr=""))
    legacy = {}

    diagnostics_api._pip_check(legacy)

    assert legacy["pip_check"] == "Error (code 1): a 1.0 requires b, which is not installed."


# ---- the routes ----

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(diagnostics_api, "local_imports_ok", True)
    monkeypatch.setattr(diagnostics_api, "llama_index_available", True)
    monkeypatch.setattr(diagnostics_api, "db", None)
    monkeypatch.setattr(diagnostics_api, "_pip_check", lambda legacy: legacy.update(pip_check="No broken requirements found."))
    monkeypatch.setattr(diagnostics_api, "_pip_outdated", lambda legacy: legacy.update(pip_outdated="Skipped: web access is off"))
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(diagnostics_api.diagnostics_bp)
    return app.test_client()


def _poll(client, run_id):
    deadline = time.time() + 20
    while True:
        snap = client.get(f"/api/meta/runs/{run_id}").get_json()["run"]
        if not snap["running"]:
            return snap
        assert time.time() < deadline
        time.sleep(0.02)


def test_a_started_self_test_reports_each_stage(client, monkeypatch):
    def gather(on_step=None):
        results = {"ollama_reachable": True, "model_count": 3, "llm_basic_response": True,
                   "active_model_name": "m", "active_model_status": "Responsive",
                   "db_connection": True, "document_count_db": 7, "indexing_queue_status": "Idle",
                   "storage_dir_accessible": True, "upload_dir_accessible": True,
                   "output_dir_accessible": False, "qa_prompt_loadable": True,
                   "qa_hello_response": "Hello!", "backend_log_errors": "No ERROR/CRITICAL messages."}
        for key, _label in diagnostics_api.LEGACY_STEPS:
            on_step(key, results)
        return results

    def selftest(mode, category, on_event=None):
        on_event({"event": "category_start", "key": "database", "name": "Database"})
        on_event({"event": "test_start", "name": "db_connection"})
        on_event({"event": "category", "key": "database", "status": "pass", "duration": 0.2,
                  "summary": "3/3 tests passed"})
        return {"categories": {k: {"status": "pass"} for k in ("database", "services", "backend")}}

    monkeypatch.setattr(diagnostics_api, "_gather_diagnostics", gather)
    monkeypatch.setattr(diagnostics_api, "_run_comprehensive_selftest", selftest)

    started = client.post("/api/meta/selftest/start", json={"mode": "quick"})
    assert started.status_code == 202
    snap = _poll(client, started.get_json()["run"]["id"])

    steps = {s["key"]: s for s in snap["steps"]}
    assert list(steps) == [k for k, _ in diagnostics_api.LEGACY_STEPS] + [
        "pip_check", "pip_outdated", "cat_database", "cat_services", "cat_backend"]
    assert (steps["ollama"]["status"], steps["ollama"]["detail"]) == ("passed", "3 models")
    assert (steps["folders"]["status"], steps["folders"]["detail"]) == ("failed", "Not writable: output")
    assert steps["pip_outdated"]["status"] == "skipped"
    assert (steps["cat_database"]["status"], steps["cat_database"]["detail"]) == ("passed", "3/3 tests passed")
    assert steps["cat_database"]["group"] == "Self-test" and steps["ollama"]["group"] == "System"
    assert snap["status"] == "passed" and snap["result"]["overall_status"] == "PASS"
    assert snap["result"]["legacy_diagnostics"]["document_count_db"] == 7
    assert client.get("/api/meta/runs/latest?kind=diagnostics").get_json()["run"]["id"] == snap["id"]


def test_basic_depth_plans_no_self_test_categories(client, monkeypatch):
    monkeypatch.setattr(diagnostics_api, "_gather_diagnostics", lambda on_step=None: {
        "llm_basic_response": True, "db_connection": True, "active_model_status": "Responsive"})

    snap = _poll(client, client.post("/api/meta/selftest/start", json={"mode": "basic"}).get_json()["run"]["id"])

    assert not [s for s in snap["steps"] if s["key"].startswith("cat_")]


def test_the_routes_refuse_what_they_do_not_know(client):
    assert client.post("/api/meta/selftest/start", json={"mode": "everything"}).status_code == 400
    assert client.post("/api/meta/selftest/start", json={"category": "rm -rf"}).status_code == 400
    assert client.post("/api/meta/run-tests", json={"scope": "all"}).status_code == 400
    assert client.get("/api/meta/runs/nope").status_code == 404
    assert client.post("/api/meta/runs/nope/cancel").status_code == 404
    assert client.get("/api/meta/runs/latest?kind=tests").get_json() == {"run": None}


def test_loading_the_progress_plugin_imports_nothing_from_the_backend(tmp_path):
    """backend.config reads the service addresses when it is first imported,
    and the root conftest must get to set them first."""
    probe = ("import sys, pytest_progress; "
             "print(sorted(m for m in sys.modules if m == 'backend' or m.startswith('backend.')))")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "backend" / "tests" / "plugins"))
    done = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "[]", done.stdout + done.stderr[-400:]
