import json
import pytest
from unittest.mock import patch

try:
    from flask import Flask
    from backend.api.node_api import node_api_bp
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(node_api_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_get_hardware_profile_reads_from_disk(client, tmp_path, monkeypatch):
    profile = {"node_id": "abc", "arch": "x86_64", "services": {}}
    p = tmp_path / ".guaardvark"
    p.mkdir()
    (p / "hardware.json").write_text(json.dumps(profile))
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.get("/api/node/hardware-profile")
    assert r.status_code == 200
    assert r.get_json() == profile


def test_get_hardware_profile_404_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.get("/api/node/hardware-profile")
    assert r.status_code == 404


def test_get_live_state_returns_fresh_snapshot(client):
    r = client.get("/api/node/live-state")
    assert r.status_code == 200
    data = r.get_json()
    for key in ("gpu", "ram", "cpu_percent", "services_running", "loaded_models"):
        assert key in data


def test_get_cluster_fleet_requires_master(client, monkeypatch):
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    r = client.get("/api/cluster/fleet")
    assert r.status_code == 403
    monkeypatch.setenv("CLUSTER_ROLE", "master")
    r = client.get("/api/cluster/fleet")
    assert r.status_code == 200
