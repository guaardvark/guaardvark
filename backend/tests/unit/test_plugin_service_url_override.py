"""A service's address can come from the environment, not just the manifest.

Inside a container the backend cannot reach a host-run Ollama or ComfyUI at
``localhost:<port>``; the consumers honour ``OLLAMA_BASE_URL`` and
``GUAARDVARK_COMFYUI_URL``, but the plugin manager kept probing localhost, so
the card said "stopped" and dependants refused to start (#377).
"""

from types import SimpleNamespace

import pytest
import requests

import backend.plugins.plugin_manager as pm
from backend.plugins.plugin_manager import PluginManager


def _meta(plugin_id="comfyui", service_url="http://127.0.0.1:8188", port=8188, health="/"):
    return SimpleNamespace(
        id=plugin_id,
        name=plugin_id,
        type="service",
        endpoints={"health": health},
        config=SimpleNamespace(enabled=True, service_url=service_url),
        port=port,
    )


@pytest.mark.parametrize(
    "plugin_id,env_name",
    [("comfyui", "GUAARDVARK_COMFYUI_URL"), ("ollama", "OLLAMA_BASE_URL")],
)
def test_exported_address_wins_over_the_manifest(monkeypatch, plugin_id, env_name):
    monkeypatch.setenv(env_name, "http://host.containers.internal:9999/")
    resolved = PluginManager._service_url(_meta(plugin_id=plugin_id))
    assert resolved == "http://host.containers.internal:9999"


def test_comfyui_override_does_not_move_ollama(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_URL", "http://host.containers.internal:8188")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    assert PluginManager._service_url(_meta(plugin_id="ollama", service_url="http://127.0.0.1:11434", port=11434)) == "http://127.0.0.1:11434"


def test_blank_and_unset_env_leave_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_URL", "   ")
    assert PluginManager._service_url(_meta()) == "http://127.0.0.1:8188"

    monkeypatch.delenv("GUAARDVARK_COMFYUI_URL", raising=False)
    # No manifest URL: the old localhost:<port> fallback still applies.
    assert PluginManager._service_url(_meta(service_url=None)) == "http://localhost:8188"


def test_health_check_probes_the_exported_address(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_URL", "http://host.containers.internal:8188")
    seen = []

    class _Response:
        status_code = 200

        def json(self):
            return {"ok": True}

    def _get(url, timeout=None):
        seen.append(url)
        return _Response()

    monkeypatch.setattr(pm.requests, "get", _get)

    manager = PluginManager.__new__(PluginManager)
    manager.registry = SimpleNamespace(get_plugin=lambda plugin_id: _meta())
    manager._plugin_status = {}

    result = manager.health_check("comfyui")
    assert result["plugin_id"] == "comfyui"
    assert seen == ["http://host.containers.internal:8188/"]
