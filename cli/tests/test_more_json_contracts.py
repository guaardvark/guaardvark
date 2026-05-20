"""JSON contract tests for additional CLI command groups."""

import json

from typer.testing import CliRunner

from llx.main import app


runner = CliRunner()


class _FakeClient:
    server_url = "http://localhost:5002"

    def get(self, endpoint: str, **params):
        if endpoint == "/api/files/browse":
            return {"data": {"folders": [{"name": "a"}], "documents": [{"id": 1, "filename": "x.txt"}]}}
        if endpoint == "/api/projects":
            return {"data": [{"id": 1, "name": "P1", "client": {"name": "C1"}, "document_count": 1, "task_count": 2}]}
        if endpoint == "/api/meta/active_jobs":
            return {"active_jobs": [{"id": "j1", "name": "Job 1", "type": "index", "status": "running"}]}
        if endpoint == "/api/entity-indexing/status":
            return {"entity_counts": {"clients": 3, "projects": 4}}
        if endpoint.startswith("/api/settings/"):
            key = endpoint.rsplit("/", 1)[-1]
            return {"data": {key: "ok"}}
        return {}

    def post(self, endpoint: str, json=None, **kwargs):
        if endpoint == "/api/entity-indexing/index-all":
            return {"success": True, "message": "done"}
        if endpoint.startswith("/api/settings/"):
            return {"success": True}
        return {}


def test_files_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.files.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["files", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "folders" in payload["data"]
    assert "documents" in payload["data"]


def test_projects_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.projects.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["projects", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "projects" in payload["data"]


def test_jobs_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.jobs.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["jobs", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "jobs" in payload["data"]


def test_settings_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.settings.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["settings", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "settings" in payload["data"]


def test_index_status_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.index.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["index", "status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "entity_counts" in payload["data"]
