"""Plugin control and agent control under the endpoint guard."""
import pytest
from flask import Flask
from importlib import util
from pathlib import Path


_AUTH_GUARD_PATH = Path(__file__).resolve().parents[1] / "utils" / "auth_guard.py"
_AUTH_GUARD_SPEC = util.spec_from_file_location("auth_guard_plugins_agent", _AUTH_GUARD_PATH)
auth_guard = util.module_from_spec(_AUTH_GUARD_SPEC)
assert _AUTH_GUARD_SPEC and _AUTH_GUARD_SPEC.loader
_AUTH_GUARD_SPEC.loader.exec_module(auth_guard)

LAN_DEVICE = "192.168.1.20"

PLUGIN_WRITES = [
    ("POST", "/api/plugins/comfyui/start"),
    ("POST", "/api/plugins/comfyui/stop"),
    ("POST", "/api/plugins/ollama/restart"),
    ("POST", "/api/plugins/discord/enable"),
    ("PUT", "/api/plugins/ollama/config"),
    ("PATCH", "/api/plugins/ollama/config"),
    ("POST", "/api/plugins/comfyui/install"),
    ("POST", "/api/plugins/comfyui/update"),
    ("POST", "/api/plugins/refresh"),
]
PLUGIN_READS = [
    ("GET", "/api/plugins"),
    ("GET", "/api/plugins/"),
    ("GET", "/api/plugins/comfyui"),
    ("GET", "/api/plugins/comfyui/health"),
    ("GET", "/api/plugins/ollama/config"),
    ("GET", "/api/plugins/status"),
]
AGENT_DRIVING = [
    ("POST", "/api/agent-control/execute"),
    ("POST", "/api/agent-control/steer"),
    ("POST", "/api/agent-control/learn/input"),
    ("POST", "/api/agent-control/learn/demonstrations/3/attempt"),
    ("POST", "/api/agent-control/capture/raw"),
    ("POST", "/api/agent-control/install-display"),
    ("DELETE", "/api/agent-control/learn/demonstrations/3"),
    ("GET", "/api/agent-control/stream"),
    ("GET", "/api/agent-control/stream/"),
]
AGENT_READS = [
    ("GET", "/api/agent-control/status"),
    ("GET", "/api/agent-control/runs"),
    ("GET", "/api/agent-control/learn/status"),
]
AGENT_STOP = ("POST", "/api/agent-control/kill")


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.url_map.strict_slashes = False
    test_app.before_request(auth_guard.check_endpoint_auth)

    @test_app.route("/api/plugins", methods=["GET"])
    @test_app.route("/api/plugins/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @test_app.route("/api/agent-control/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def anything(rest=""):
        return {"ok": True}

    return test_app


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)


def _from(client, method, path, addr, **headers):
    return client.open(path, method=method, json={}, headers=headers, environ_base={"REMOTE_ADDR": addr})


def _through_proxy(client, method, path, addr):
    return client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": addr},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )


@pytest.mark.parametrize("method,path", PLUGIN_WRITES + AGENT_DRIVING)
def test_another_host_is_refused_without_the_key(app, no_key, method, path):
    client = app.test_client()

    for response in (_from(client, method, path, LAN_DEVICE), _through_proxy(client, method, path, LAN_DEVICE)):
        assert response.status_code == 403, f"{method} {path}"
        assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", PLUGIN_WRITES + AGENT_DRIVING + PLUGIN_READS + AGENT_READS + [AGENT_STOP])
def test_this_machine_passes_without_the_key(app, no_key, method, path):
    client = app.test_client()

    assert _from(client, method, path, "127.0.0.1").status_code == 200
    assert _through_proxy(client, method, path, "127.0.0.1").status_code == 200


@pytest.mark.parametrize("method,path", PLUGIN_READS + AGENT_READS + [AGENT_STOP])
def test_reads_and_the_stop_stay_open_to_another_host(app, no_key, method, path):
    client = app.test_client()

    assert _from(client, method, path, LAN_DEVICE).status_code == 200


@pytest.mark.parametrize("method,path", PLUGIN_WRITES)
def test_network_access_opens_plugin_control_to_the_local_network(app, no_key, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    client = app.test_client()

    assert _from(client, method, path, LAN_DEVICE).status_code == 200


@pytest.mark.parametrize("method,path", AGENT_DRIVING)
def test_network_access_does_not_open_driving_the_agent(app, no_key, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")
    client = app.test_client()

    response = _from(client, method, path, LAN_DEVICE)

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.RUNS_CODE_CODE


@pytest.mark.parametrize("method,path", PLUGIN_WRITES + AGENT_DRIVING)
def test_the_key_or_a_signed_in_browser_opens_them_from_another_host(app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    client = app.test_client()

    assert _from(client, method, path, LAN_DEVICE).status_code == 401
    assert _from(client, method, path, LAN_DEVICE, **{"X-API-Key": "secret"}).status_code == 200
    monkeypatch.setattr(auth_guard, "request_has_valid_session", lambda: True)
    assert _from(client, method, path, LAN_DEVICE).status_code == 200


def test_the_settings_page_lists_both():
    summary = auth_guard.protected_summary()

    assert any("plugins" in item for item in summary)
    assert any("agent" in item and "stopping it stays open" in item for item in summary)
