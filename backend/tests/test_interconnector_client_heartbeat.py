"""Server-side client heartbeat daemon — keeps a worker online without a browser.

Guards on config, heartbeats to the master with the stable node id, and
self-registers on a 404 (first contact) before retrying.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

try:
    from flask import Flask
    from backend.models import db, Setting
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

import backend.tasks.interconnector_client_heartbeat as hb


@pytest.fixture
def app():
    application = Flask(__name__)
    application.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(application)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def _set_config(cfg):
    db.session.add(Setting(key="interconnector_config", value=json.dumps(cfg)))
    db.session.commit()


CLIENT_CFG = {
    "is_enabled": True, "node_mode": "client",
    "master_url": "http://master.local:5000/", "master_api_key": "secret-key",
    "node_name": "alpaca",
}


@pytest.fixture(autouse=True)
def _stub_profile(monkeypatch):
    # Avoid probing real hardware; give a deterministic profile.
    monkeypatch.setattr(hb, "_local_profile", lambda: {"node_id": "prof-id", "hostname": "alpaca"})


def test_skips_when_not_enabled(app):
    with app.app_context():
        _set_config({**CLIENT_CFG, "is_enabled": False})
        assert hb._do_client_heartbeat()["skipped"] == "not_enabled_client"


def test_skips_when_master_mode(app):
    with app.app_context():
        _set_config({**CLIENT_CFG, "node_mode": "master"})
        assert hb._do_client_heartbeat()["skipped"] == "not_enabled_client"


def test_skips_when_no_master_url(app):
    with app.app_context():
        _set_config({**CLIENT_CFG, "master_url": ""})
        assert hb._do_client_heartbeat()["skipped"] == "no_master_url"


def test_heartbeats_with_stable_id_and_api_key(app, monkeypatch):
    monkeypatch.setenv("CLUSTER_NODE_ID", "stable-machine-id")
    with app.app_context():
        _set_config(CLIENT_CFG)
        with patch.object(hb.requests, "post",
                          return_value=MagicMock(status_code=200, ok=True)) as post:
            result = hb._do_client_heartbeat()
        assert result == {"ok": True, "node_id": "stable-machine-id"}
        url, kwargs = post.call_args[0][0], post.call_args[1]
        assert url == "http://master.local:5000/api/interconnector/nodes/stable-machine-id/heartbeat"
        assert kwargs["headers"]["X-API-Key"] == "secret-key"


def test_self_registers_on_404_then_heartbeats(app, monkeypatch):
    monkeypatch.setenv("CLUSTER_NODE_ID", "stable-machine-id")
    responses = [
        MagicMock(status_code=404, ok=False),   # first heartbeat: unknown node
        MagicMock(status_code=200, ok=True),     # register
        MagicMock(status_code=200, ok=True),     # heartbeat retry
    ]
    with app.app_context():
        _set_config(CLIENT_CFG)
        with patch.object(hb.requests, "post", side_effect=responses) as post:
            result = hb._do_client_heartbeat()
        assert result["ok"] is True
        called = [c[0][0] for c in post.call_args_list]
        assert any(u.endswith("/nodes/register") for u in called), called
        assert called[-1].endswith("/nodes/stable-machine-id/heartbeat")


def test_master_unreachable_is_non_fatal(app, monkeypatch):
    import requests as real_requests
    monkeypatch.setenv("CLUSTER_NODE_ID", "stable-machine-id")
    with app.app_context():
        _set_config(CLIENT_CFG)
        with patch.object(hb.requests, "post",
                          side_effect=real_requests.ConnectionError("down")):
            result = hb._do_client_heartbeat()
        assert result == {"error": "master_unreachable"}


def test_falls_back_to_profile_node_id_without_env(app, monkeypatch):
    monkeypatch.delenv("CLUSTER_NODE_ID", raising=False)
    with app.app_context():
        _set_config(CLIENT_CFG)
        with patch.object(hb.requests, "post",
                          return_value=MagicMock(status_code=200, ok=True)) as post:
            result = hb._do_client_heartbeat()
        assert result["node_id"] == "prof-id"  # from the hardware profile
        assert post.call_args[0][0].endswith("/nodes/prof-id/heartbeat")
