"""JSON contract tests for chat/search/tasks/clients/rules commands."""

import json

from typer.testing import CliRunner

from llx.main import app


runner = CliRunner()


class _FakeClient:
    server_url = "http://localhost:5002"

    def post(self, endpoint: str, json=None, **kwargs):
        if endpoint == "/api/search/semantic":
            return {"answer": "ok", "sources": []}
        if endpoint == "/api/tasks":
            return {"data": {"id": 1, "name": "t1"}}
        if endpoint.startswith("/api/tasks/") and endpoint.endswith("/start"):
            return {"message": "started"}
        if endpoint == "/api/meta/rules/import":
            return {"created": 1, "updated": 0, "skipped": 0}
        if endpoint == "/api/enhanced-chat":
            return {"data": {"response": "hello"}}
        if endpoint == "/api/rules":
            return {"data": {"id": 3, "name": "r1"}}
        return {"success": True}

    def get(self, endpoint: str, **params):
        if endpoint == "/api/tasks":
            return {"tasks": [{"id": 1, "name": "t1", "task_type": "code_task", "status": "queued", "progress": 0}]}
        if endpoint == "/api/clients":
            return {"clients": [{"id": 1, "name": "c1", "project_count": 1}]}
        if endpoint == "/api/rules":
            return {"data": [{"id": 1, "name": "r1"}]}
        if endpoint.startswith("/api/enhanced-chat/") and endpoint.endswith("/history"):
            return {"messages": [{"role": "user", "content": "hi"}]}
        return {}


def test_search_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.search.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["search", "hello", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "answer" in payload["data"]


def test_clients_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.clients.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["clients", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "clients" in payload["data"]


def test_tasks_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.tasks.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["tasks", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "tasks" in payload["data"]


def test_rules_list_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.rules.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["rules", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert "rules" in payload["data"]


def test_chat_json_envelope(monkeypatch):
    monkeypatch.setattr("llx.commands.chat.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["chat", "hi", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["response"] == "hello"
