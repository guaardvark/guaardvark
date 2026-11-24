"""JSON envelopes for `doctor` and `jobs watch`, the checks a script runs and waits on."""

import json

from typer.testing import CliRunner

from llx.main import app

runner = CliRunner()


def _root(tmp_path, manager_exit=0):
    (tmp_path / "start.sh").write_text("#!/bin/sh\n")
    manager = tmp_path / "scripts" / "system-manager" / "system-manager"
    manager.parent.mkdir(parents=True)
    manager.write_text(f"#!/bin/sh\nprintf '\\033[32mOK\\033[0m %s\\n' \"$1\"\nexit {manager_exit}\n")
    manager.chmod(0o755)
    return tmp_path


def test_doctor_cli_json_lists_every_check(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(_root(tmp_path)))
    monkeypatch.setattr("llx.backend_bootstrap.is_backend_healthy", lambda server: True)
    result = runner.invoke(app, ["doctor", "--cli", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    names = [c["name"] for c in payload["data"]["checks"]]
    assert "backend health" in names and "install root (start.sh)" in names
    assert payload["data"]["failed"] == []


def test_doctor_cli_json_reports_a_failed_check(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(_root(tmp_path)))
    monkeypatch.setattr("llx.backend_bootstrap.is_backend_healthy", lambda server: False)
    result = runner.invoke(app, ["--json", "doctor", "--cli"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "CHECKS_FAILED"
    assert payload["data"]["failed"] == ["backend health"]


def test_doctor_json_wraps_the_system_manager_report(tmp_path, monkeypatch):
    root = _root(tmp_path, manager_exit=3)
    result = runner.invoke(app, ["doctor", "--path", str(root), "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["data"]["command"] == "check"
    assert payload["data"]["output"].strip() == f"OK check"
    assert "\x1b" not in payload["data"]["output"]


def test_jobs_watch_json_prints_only_the_final_state(monkeypatch):
    states = iter(["running", "running", "done"])

    class Client:
        server_url = "http://localhost:5000"

        def get(self, endpoint, **params):
            return {"status": next(states), "progress": {"current": 1, "total": 2},
                    "result": {"path": "/x/song.wav"}}

    monkeypatch.setattr("llx.commands.jobs.get_client", lambda server=None: Client())
    monkeypatch.setattr("llx.commands.jobs.time.sleep", lambda s: None)
    result = runner.invoke(app, ["jobs", "watch", "0123456789abcdef0123456789abcdef", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "completed"


def test_jobs_watch_json_failed_job_is_an_error(monkeypatch):
    class Client:
        server_url = "http://localhost:5000"

        def get(self, endpoint, **params):
            return {"status": "error", "error": "out of memory"}

    monkeypatch.setattr("llx.commands.jobs.get_client", lambda server=None: Client())
    result = runner.invoke(app, ["--json", "jobs", "watch", "0123456789abcdef0123456789abcdef"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "JOB_FAILED"
    assert payload["error"]["message"] == "out of memory"
