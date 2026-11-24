"""Server-side client heartbeat — keeps a worker online without a browser.

Guards on config, enrols with the master using this machine's key (pending
until the operator approves), saves the certificate and the master's pinned
key, heartbeats signed with it, renews it through the heartbeat, and enrols
again when the master refuses it.

The master here is a stand-in that speaks the same protocol with its own key,
checking every signature (backend/utils/node_auth.py). The real master routes
are covered in test_interconnector_enrolment.py.
"""
import json
import time
from unittest.mock import MagicMock, patch
from urllib.parse import unquote, urlsplit

import pytest
import requests

try:
    from flask import Flask
    from backend.models import db, Setting
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import backend.tasks.interconnector_client_heartbeat as hb
from backend.utils import node_auth, node_identity

MASTER_URL = "http://master.local:5000"


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


@pytest.fixture(autouse=True)
def _client_identity(tmp_path, monkeypatch):
    # This machine's key and fleet state live under tmp.
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "node_id")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, "stable-machine-id")
    monkeypatch.setattr(node_auth, "is_master", lambda: False)
    # Avoid probing real hardware; give a deterministic profile.
    monkeypatch.setattr(hb, "_local_profile", lambda: {"node_id": "prof-id", "hostname": "node-b"})


def _set_config(cfg):
    db.session.add(Setting(key="interconnector_config", value=json.dumps(cfg)))
    db.session.commit()


CLIENT_CFG = {
    "is_enabled": True, "node_mode": "client",
    "master_url": MASTER_URL + "/", "master_api_key": "secret-key",
    "node_name": "node-b",
}


class _Reply:
    def __init__(self, status, data=None, error=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {"data": data} if data is not None else {"error": {"message": error or ""}}

    def json(self):
        return self._body


class FakeMaster:
    """Enrols and heartbeats like the real master, with its own key."""

    def __init__(self, approved=False, lifetime=node_auth.CERT_LIFETIME_S):
        self.key = Ed25519PrivateKey.generate()
        self.approved = approved
        self.lifetime = lifetime
        self.revoked = False
        self.calls = []
        self.cache = node_auth.NonceCache()

    @property
    def public_key(self):
        return node_auth.public_key_text(self.key)

    def _credentials(self, node_id, pub):
        return {
            "certificate": node_auth.issue_certificate(self.key, node_id, pub, "master-id",
                                                       lifetime_s=self.lifetime),
            "master_public_key": self.public_key,
            "master_node_id": "master-id",
        }

    def post(self, url, data=None, headers=None, timeout=None, auth=None, **_kw):
        prepared = requests.Request("POST", url, data=data, headers=headers, auth=auth).prepare()
        parts = urlsplit(prepared.url)
        path = node_auth.path_with_query(unquote(parts.path), parts.query)
        body = prepared.body
        self.calls.append((parts.path, dict(prepared.headers)))
        if parts.path.endswith("/nodes/register"):
            payload = json.loads(body)
            if prepared.headers.get("X-API-Key") != "secret-key":
                return _Reply(401, error="Invalid API key")
            node_auth.verify_signature("POST", path, prepared.headers, body,
                                       payload["public_key"], self.cache)
            if self.revoked:
                return _Reply(403, error="revoked")
            if not self.approved:
                return _Reply(202, {"status": "pending", "node_id": payload["node_id"]})
            return _Reply(200, {"status": "approved", "node_id": payload["node_id"],
                                **self._credentials(payload["node_id"], payload["public_key"])})
        try:
            cert = node_auth.verify_request("POST", path, prepared.headers, body,
                                            self.public_key, self.cache)
        except node_auth.NodeAuthError as exc:
            return _Reply(exc.status, error=exc.reason)
        if self.revoked:
            return _Reply(403, error="not approved")
        assert parts.path == f"/api/interconnector/nodes/{cert.node}/heartbeat"
        data = {"node_id": cert.node}
        if cert.exp - int(time.time()) < node_auth.CERT_RENEW_BEFORE_S:
            data.update(self._credentials(cert.node, cert.pub))
        return _Reply(200, data)

    def paths(self):
        return [path for path, _headers in self.calls]


def _run(master):
    with patch.object(hb.requests, "post", side_effect=master.post):
        return hb._do_client_heartbeat()


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


def test_waits_for_approval_without_heartbeating(app):
    master = FakeMaster(approved=False)
    with app.app_context():
        _set_config(CLIENT_CFG)
        result = _run(master)
    assert result["pending"] is True
    assert result["node_id"] == "stable-machine-id"
    assert result["fingerprint"] == node_auth.local_fingerprint()
    assert master.paths() == ["/api/interconnector/nodes/register"]
    state = node_auth.read_fleet_state()
    assert state["status"] == "pending" and "certificate" not in state


def test_enrolment_sends_the_fleet_key_the_node_key_and_a_signature(app):
    master = FakeMaster(approved=False)
    with app.app_context():
        _set_config(CLIENT_CFG)
        _run(master)
    _path, headers = master.calls[0]
    assert headers["X-API-Key"] == "secret-key"
    assert headers[node_auth.HEADER_SIG] and node_auth.HEADER_CERT not in headers


def test_once_approved_it_saves_the_certificate_and_heartbeats_signed(app):
    master = FakeMaster(approved=True)
    with app.app_context():
        _set_config(CLIENT_CFG)
        result = _run(master)
        assert result == {"ok": True, "node_id": "stable-machine-id"}
        assert master.paths() == ["/api/interconnector/nodes/register",
                                  "/api/interconnector/nodes/stable-machine-id/heartbeat"]
        state = node_auth.read_fleet_state()
        assert state["master_public_key"] == master.public_key
        assert state["master_url"] == MASTER_URL
        assert node_auth.certificate_usable(state)

        # Next beat: no enrolment, only a signed heartbeat that carries no fleet key.
        master.calls.clear()
        assert _run(master)["ok"] is True
    assert master.paths() == ["/api/interconnector/nodes/stable-machine-id/heartbeat"]
    assert "X-API-Key" not in master.calls[0][1]


def test_the_heartbeat_renews_the_certificate(app):
    master = FakeMaster(approved=True, lifetime=3600)
    with app.app_context():
        _set_config(CLIENT_CFG)
        _run(master)
        first = node_auth.read_fleet_state()["certificate"]
        master.lifetime = node_auth.CERT_LIFETIME_S
        _run(master)
        renewed = node_auth.read_fleet_state()
    assert renewed["certificate"] != first
    assert renewed["certificate_expires"] - int(time.time()) > node_auth.CERT_LIFETIME_S - 60


def test_a_refused_certificate_sends_it_back_to_enrolment(app):
    master = FakeMaster(approved=True)
    with app.app_context():
        _set_config(CLIENT_CFG)
        _run(master)
        master.revoked = True
        master.calls.clear()
        result = _run(master)
        assert result["error"] == "enrol_status_403"
        state = node_auth.read_fleet_state()
    assert master.paths() == ["/api/interconnector/nodes/stable-machine-id/heartbeat",
                              "/api/interconnector/nodes/register"]
    assert "certificate" not in state and state["status"] == "refused"
    # The pinned master key survives a refusal.
    assert state["master_public_key"] == master.public_key


def test_a_master_answering_with_another_key_is_not_trusted(app):
    master = FakeMaster(approved=True, lifetime=60)
    with app.app_context():
        _set_config(CLIENT_CFG)
        _run(master)
        impostor = FakeMaster(approved=True)
        result = _run(impostor)
        state = node_auth.read_fleet_state()
    assert result["error"] == "master_key_changed"
    assert state["master_public_key"] == master.public_key


def test_the_id_the_master_assigns_is_kept(app):
    master = FakeMaster(approved=False)

    def assigning(url, **kwargs):
        reply = master.post(url, **kwargs)
        if reply.status_code == 202:
            reply._body["data"]["node_id"] = "assigned-id"
        return reply

    with app.app_context():
        _set_config(CLIENT_CFG)
        with patch.object(hb.requests, "post", side_effect=assigning):
            hb._do_client_heartbeat()
            hb._do_client_heartbeat()
    second_body = master.calls[1]
    assert node_auth.read_fleet_state()["node_id"] == "assigned-id"
    assert second_body[1][node_auth.HEADER_NODE] == "assigned-id"


def test_master_unreachable_is_non_fatal(app):
    with app.app_context():
        _set_config(CLIENT_CFG)
        with patch.object(hb.requests, "post",
                          side_effect=requests.ConnectionError("down")):
            result = hb._do_client_heartbeat()
        assert result == {"error": "master_unreachable"}


def test_uses_the_node_id_file_without_env(app, monkeypatch, tmp_path):
    monkeypatch.delenv(node_identity.NODE_ID_ENV, raising=False)
    (tmp_path / "node_id").write_text("file-id\n")
    master = FakeMaster(approved=True)
    with app.app_context():
        _set_config(CLIENT_CFG)
        result = _run(master)
    assert result["node_id"] == "file-id"
    assert master.paths()[-1] == "/api/interconnector/nodes/file-id/heartbeat"


def test_a_new_master_url_starts_a_new_enrolment(app):
    master = FakeMaster(approved=True)
    with app.app_context():
        _set_config(CLIENT_CFG)
        _run(master)
        setting = db.session.get(Setting, "interconnector_config")
        setting.value = json.dumps({**CLIENT_CFG, "master_url": "http://other:5000"})
        db.session.commit()
        other = FakeMaster(approved=True)
        assert _run(other)["ok"] is True
        state = node_auth.read_fleet_state()
    assert state["master_url"] == "http://other:5000"
    assert state["master_public_key"] == other.public_key


def test_enrol_route_runs_the_same_step(app, monkeypatch):
    from backend.api import interconnector_api as ic_api

    monkeypatch.setattr(hb, "_do_client_heartbeat", MagicMock(return_value={"pending": True}))
    app.register_blueprint(ic_api.interconnector_bp)
    response = app.test_client().post("/api/interconnector/enrol")
    assert response.status_code == 200
    assert response.get_json()["data"] == {"pending": True}
