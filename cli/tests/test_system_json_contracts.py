"""JSON contract tests for system/model CLI commands."""

import json

from typer.testing import CliRunner

from llx.client import LlxConnectionError
from llx.main import app


runner = CliRunner()


class _FakeClient:
    server_url = "http://localhost:5002"

    def get(self, endpoint: str, **params):
        if endpoint == "/api/health":
            return {"status": "ok", "version": "x", "uptime_seconds": 60}
        if endpoint == "/api/model/status":
            return {"data": {"text_model": "gemma4:e4b"}}
        if endpoint == "/api/health/celery":
            return {"status": "up", "workers": ["w1"]}
        if endpoint == "/api/system/metrics":
            return {"data": {"gpu_mem": 12.0, "cpu_percent": 18.0}}
        if endpoint == "/api/model/list":
            return {"data": {"models": [{"name": "gemma4:e4b", "id": "gemma4:e4b"}]}}
        return {}

    def post(self, endpoint: str, json=None, **kwargs):
        if endpoint == "/api/model/set":
            return {"success": True}
        return {}


def test_health_json_success_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["--json", "health"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "ok"


def test_status_json_success_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["--json", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "health" in payload["data"]
    assert "model" in payload["data"]
    assert "celery" in payload["data"]


def test_models_set_json_success_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["models", "set", "gemma4:e4b", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["model"] == "gemma4:e4b"


def test_health_json_connection_error_envelope(monkeypatch):
    monkeypatch.setattr(
        "llx.commands.system.get_client",
        lambda server=None: (_ for _ in ()).throw(LlxConnectionError("offline")),
    )
    result = runner.invoke(app, ["--json", "health"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "CONNECTION_ERROR"
