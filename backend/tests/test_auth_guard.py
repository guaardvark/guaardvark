import pytest
from flask import Flask
from importlib import util
from pathlib import Path


_AUTH_GUARD_PATH = Path(__file__).resolve().parents[1] / "utils" / "auth_guard.py"
_AUTH_GUARD_SPEC = util.spec_from_file_location("auth_guard_under_test", _AUTH_GUARD_PATH)
auth_guard = util.module_from_spec(_AUTH_GUARD_SPEC)
assert _AUTH_GUARD_SPEC and _AUTH_GUARD_SPEC.loader
_AUTH_GUARD_SPEC.loader.exec_module(auth_guard)


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.before_request(auth_guard.check_endpoint_auth)

    @test_app.route("/api/files/write", methods=["POST"])
    def files_write():
        return {"ok": True}

    @test_app.route("/api/files/browse", methods=["GET"])
    def files_browse():
        return {"ok": True}

    @test_app.route("/api/self-code/file", methods=["GET"])
    def self_code_file():
        return {"ok": True}

    @test_app.route("/api/memory/clear", methods=["DELETE"])
    def memory_clear():
        return {"ok": True}

    @test_app.route("/api/memory", methods=["GET"])
    def memory_list():
        return {"ok": True}

    @test_app.route("/api/meta/clear-pycache", methods=["POST"])
    def clear_pycache():
        return {"ok": True}

    @test_app.route("/api/meta/rebuild-index", methods=["POST"])
    def meta_mutation():
        return {"ok": True}

    @test_app.route("/api/meta/generation-history", methods=["GET", "DELETE"])
    def generation_history():
        return {"ok": True}

    @test_app.route("/api/audio-foundry/jobs", methods=["GET", "DELETE"])
    def audio_jobs():
        return {"ok": True}

    @test_app.route("/api/settings/profile", methods=["GET", "POST"])
    def settings_profile():
        return {"ok": True}

    return test_app


def test_profile_write_blocked_from_remote_host_but_readable(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()
    assert client.post("/api/settings/profile", json={"name": "creator"}, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/settings/profile", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.post("/api/settings/profile", json={"name": "creator"}, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_delete_generation_history_blocked_from_remote_host(app, monkeypatch):
    # Deleting every generated image, video and audio file is not a "safe"
    # maintenance op like clear-pycache; it stays behind the /api/meta guard.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.delete("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403

    # The counts stay readable so the LAN UI can show what would be deleted.
    response = client.get("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 200

    response = client.delete("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200


def test_clear_audio_jobs_blocked_from_remote_host_but_listing_open(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    assert client.delete("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.delete("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_clear_pycache_allowed_from_remote_host(app, monkeypatch):
    # Safe maintenance op: exempt from the host check even though /api/meta is
    # otherwise mutation-protected, so the operator can clear cache from the LAN UI.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/meta/clear-pycache",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_other_meta_mutation_still_blocked_from_remote_host(app, monkeypatch):
    # The carve-out must be narrow: other /api/meta mutations stay protected.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/meta/rebuild-index",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied from remote host"


def test_lan_device_via_local_proxy_is_still_treated_as_remote(app, monkeypatch):
    # Request arrives from the loopback Vite proxy (REMOTE_ADDR=127.0.0.1) but
    # X-Forwarded-For carries the real LAN client → must still be blocked.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied from remote host"


def test_forged_xff_from_direct_remote_peer_is_ignored(app, monkeypatch):
    # A LAN attacker connecting straight to the backend (non-loopback peer) cannot
    # spoof localhost by setting X-Forwarded-For — the header is only trusted from
    # a loopback peer (our own proxy).
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied from remote host"


def test_genuine_localhost_via_proxy_is_allowed(app, monkeypatch):
    # Operator on the box: browser → loopback proxy → backend, XFF also loopback.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_remote_file_mutation_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied from remote host"


def test_remote_self_code_read_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/self-code/file",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied from remote host"


def test_read_only_file_browser_get_remains_unprotected(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/files/browse",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_api_key_required_for_protected_file_endpoint(app, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")

    client = app.test_client()
    missing_key = client.post(
        "/api/files/write",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )
    with_key = client.post(
        "/api/files/write",
        headers={"X-API-Key": "secret"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert missing_key.status_code == 401
    assert with_key.status_code == 200


def test_remote_memory_clear_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.delete(
        "/api/memory/clear",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied from remote host"


def test_remote_memory_read_remains_unprotected(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/memory",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
