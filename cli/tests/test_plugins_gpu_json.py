"""JSON contracts for plugins and gpu command groups."""

import json

from typer.testing import CliRunner

from llx.main import app

runner = CliRunner()


class _FakeClient:
    server_url = "http://localhost:5002"

    def get(self, endpoint: str, **params):
        if endpoint == "/api/plugins":
            return {"data": {"plugins": [{"id": "ollama", "status": "running", "enabled": True, "port": 11434}]}}
        if endpoint == "/api/gpu/status":
            return {"data": {"gpu_name": "RTX", "available": True, "owner": "none", "vram_used": 1, "vram_total": 16}}
        return {"data": {}}

    def post(self, path: str, json=None, **kwargs):
        return {"success": True, "message": "ok", "data": {"ok": True}}


def test_plugins_list_json(monkeypatch):
    monkeypatch.setattr("llx.commands.plugins.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["plugins", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["plugins"][0]["id"] == "ollama"


def test_gpu_status_json(monkeypatch):
    monkeypatch.setattr("llx.commands.gpu.get_client", lambda server=None: _FakeClient())
    result = runner.invoke(app, ["gpu", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["owner"] == "none"


def test_catalog_includes_new_commands():
    from llx.command_catalog import COMMAND_TREE
    from llx.slash import SlashRouter

    router = SlashRouter({"server": "http://localhost:5002", "session_id": "t", "message_count": 0, "agent_mode": False})
    names = set(router.get_command_names())
    for cmd in ("plugins", "gpu", "mcp", "audio", "swarm", "lessons"):
        assert cmd in COMMAND_TREE
        assert cmd in names
