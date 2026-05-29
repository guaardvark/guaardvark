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

    return test_app


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
