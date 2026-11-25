"""The Plugins page's Refresh re-reads state and never kills a process.

POST /api/plugins/refresh used to re-run the boot initialisation, whose first
pass kills whatever answers on a disabled plugin's port. A user who runs
Ollama themselves, or a second checkout's ComfyUI, lost that process to a
button that only promised to rescan the plugins folder. Boot keeps its orphan
cleanup; ``PluginManager.reload`` does not.
"""

import json

import pytest

from backend.plugins.plugin_manager import PluginManager, PluginStatus
from backend.plugins.plugin_registry import PluginRegistry

PLUGIN_ID = "orphaned"
PORT = 8999


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    plugin = root / PLUGIN_ID
    plugin.mkdir(parents=True)
    manifest = {
        "id": PLUGIN_ID,
        "name": "Orphaned Service",
        "version": "1.0.0",
        "type": "service",
        "port": PORT,
        "config": {"default_enabled": False, "service_url": f"http://127.0.0.1:{PORT}", "timeout": 5},
        "endpoints": {"health": "/health"},
    }
    (plugin / "plugin.json").write_text(json.dumps(manifest))
    return root


@pytest.fixture
def answering(monkeypatch):
    """Every health probe answers: the disabled plugin's port is held."""
    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: True)


@pytest.fixture
def kills(monkeypatch):
    killed = []
    monkeypatch.setattr(PluginManager, "_kill_by_port", lambda self, port: killed.append(port))
    return killed


def test_boot_still_kills_the_orphan(plugins_dir, answering, kills):
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))

    assert kills == [PORT]
    assert manager.get_status(PLUGIN_ID) is PluginStatus.DISABLED


def test_reload_never_kills(plugins_dir, answering, kills):
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))
    kills.clear()

    discovered = manager.reload()

    assert kills == []
    assert discovered == [PLUGIN_ID]
    assert manager.get_status(PLUGIN_ID) is PluginStatus.DISABLED


def test_reload_picks_up_a_new_plugin_and_drops_a_removed_one(plugins_dir, answering, kills):
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))
    kills.clear()  # boot's own orphan cleanup
    added = plugins_dir / "newcomer"
    added.mkdir()
    (added / "plugin.json").write_text(json.dumps({
        "id": "newcomer", "name": "Newcomer", "version": "0.1.0", "type": "tool",
        "config": {"default_enabled": True},
    }))
    (plugins_dir / PLUGIN_ID / "plugin.json").unlink()

    discovered = manager.reload()

    assert discovered == ["newcomer"]
    assert manager.get_status("newcomer") is PluginStatus.RUNNING
    assert manager.get_status(PLUGIN_ID) is PluginStatus.UNKNOWN
    assert kills == []


def test_reload_keeps_a_crashed_plugin_in_the_saved_running_set(plugins_dir, kills, monkeypatch):
    """A refresh on a plugin that crashed must not forget it was running:
    the next boot restores from that list, and nowhere else does the code
    shrink it on a read."""
    (plugins_dir / PLUGIN_ID / "plugin.json").write_text(json.dumps({
        "id": PLUGIN_ID, "name": "Crashed Service", "version": "1.0.0", "type": "service",
        "port": PORT, "config": {"default_enabled": True, "service_url": f"http://127.0.0.1:{PORT}"},
        "endpoints": {"health": "/health"},
    }))
    up = {"value": True}
    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: up["value"])
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))
    assert manager.state_store.get_running() == [PLUGIN_ID]

    up["value"] = False
    manager.reload()

    assert manager.get_status(PLUGIN_ID) is PluginStatus.STOPPED
    assert manager.state_store.get_running() == [PLUGIN_ID]
    assert kills == []


def test_refresh_endpoint_calls_reload(monkeypatch):
    from backend.api import plugins_api

    class _Manager:
        def __init__(self):
            self.reloaded = 0
            self.inited = 0

        def reload(self):
            self.reloaded += 1
            return ["a", "b"]

        def _init_plugin_status(self):
            self.inited += 1

    manager = _Manager()
    monkeypatch.setattr(plugins_api, "get_plugin_manager", lambda: manager)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(plugins_api.plugins_bp)

    response = app.test_client().post("/api/plugins/refresh")

    assert response.status_code == 200
    assert response.get_json()["data"]["count"] == 2
    assert manager.reloaded == 1
    assert manager.inited == 0
