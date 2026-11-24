"""Stable, IP-INDEPENDENT node identity for the interconnector.

Regression guard for the "client node keeps cutting out" bug: a node reachable via
multiple paths (LAN / VPN / Tailscale) or with a changed DHCP lease used to
register as a brand-new node each time, piling up duplicate stale rows. Identity
is now keyed on the machine-stable node_id (hardware_profile["node_id"]) and
matched by hostname fingerprint, never by IP.

Each machine enrols with its own key (backend/utils/node_auth.py); _register
signs as that machine, and approves a waiting enrolment the way the operator
would, so these tests reach the node rows.
"""
import json
import pytest

try:
    from flask import Flask
    from backend.models import db, InterconnectorNode, InterconnectorNodeIdentity, Setting
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.utils import node_auth, node_identity

# One key per machine, by hostname.
_KEYS = {}


@pytest.fixture(autouse=True)
def _machine_keys(tmp_path, monkeypatch):
    # The master's own key lives under tmp, never in the home directory.
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "node_id")
    _KEYS.clear()
    yield
    _KEYS.clear()


@pytest.fixture
def app():
    import backend.api.interconnector_api as ic_api

    application = Flask(__name__)
    application.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(application)
    from backend.api.interconnector_api import interconnector_bp
    application.register_blueprint(interconnector_bp)

    with application.app_context():
        db.create_all()
        setting = Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "master", "require_api_key": False}))
        db.session.add(setting)
        db.session.commit()
        ic_api._config_cache["config"] = None
        ic_api._config_cache["expires"] = 0
        yield application
        db.session.remove()
        db.drop_all()
        ic_api._config_cache["config"] = None
        ic_api._config_cache["expires"] = 0


def _post_register(client, key, node_id, hostname, client_ip, name):
    path = "/api/interconnector/nodes/register"
    profile = {"node_id": node_id, "hostname": hostname, "arch": "x86_64", "services": {}}
    body = json.dumps({
        "node_id": node_id, "node_name": name or hostname, "node_mode": "client",
        "client_ip": client_ip, "port": 5002, "hardware_profile": profile,
        "public_key": node_auth.public_key_text(key),
    }).encode()
    headers = {"Content-Type": "application/json",
               **node_auth.sign_request(key, node_id, None, "POST", path, body)}
    return client.post(path, data=body, headers=headers)


def _register(client, *, stable_id, hostname, client_ip, name=None):
    """Register as the machine with this hostname; a first enrolment is
    approved and sent again under the id the master gave it."""
    key = _KEYS.setdefault(hostname, Ed25519PrivateKey.generate())
    response = _post_register(client, key, stable_id, hostname, client_ip, name)
    if response.status_code != 202:
        return response
    assigned = response.get_json()["data"]["node_id"]
    with client.application.app_context():
        db.session.get(InterconnectorNodeIdentity, assigned).status = "approved"
        db.session.commit()
    return _post_register(client, key, assigned, hostname, client_ip, name)


def test_same_machine_two_ips_yields_one_row(app):
    """The core fix: same machine reachable at a different IP (LAN vs VPN) must
    stay ONE node, not spawn a duplicate."""
    client = app.test_client()
    r1 = _register(client, stable_id="node-b-stable", hostname="node-b", client_ip="192.0.2.12")
    assert r1.status_code in (200, 201), r1.get_data(as_text=True)
    r2 = _register(client, stable_id="node-b-stable", hostname="node-b", client_ip="198.51.100.2")
    assert r2.status_code in (200, 201), r2.get_data(as_text=True)
    # Same node_id returned both times — identity is IP-independent.
    assert r1.get_json()["data"]["node_id"] == "node-b-stable"
    assert r2.get_json()["data"]["node_id"] == "node-b-stable"
    with app.app_context():
        rows = InterconnectorNode.query.all()
        assert len(rows) == 1, f"expected 1 row, got {[n.node_id for n in rows]}"
        assert rows[0].host == "198.51.100.2"  # address updated to the latest path
        assert rows[0].online is True


def test_different_machine_same_id_gets_fresh_id(app):
    """Backup-restore protection: a DIFFERENT machine (different hostname)
    presenting the same node_id must be given a fresh id, not hijack the row."""
    client = app.test_client()
    _register(client, stable_id="shared-id", hostname="boxA", client_ip="192.168.1.10")
    r2 = _register(client, stable_id="shared-id", hostname="boxB", client_ip="192.168.1.11")
    assert r2.status_code in (200, 201)
    assigned = r2.get_json()["data"]["node_id"]
    assert assigned != "shared-id", "different machine must not reuse the id"
    with app.app_context():
        by_id = {n.node_id: n for n in InterconnectorNode.query.all()}
        assert "shared-id" in by_id
        assert _hostname(by_id["shared-id"]).lower() == "boxa"
        assert _hostname(by_id[assigned]).lower() == "boxb"


def test_registration_prunes_leftover_duplicate_rows(app):
    """Self-heal: rows left by the old IP-coupled logic for the same machine
    are collapsed on the next registration."""
    with app.app_context():
        for old_id, ip in [("old-1", "192.0.2.12"), ("old-2", "198.51.100.2")]:
            db.session.add(InterconnectorNode(
                node_id=old_id, node_name="studio-client", node_mode="client",
                host=ip, port=5002, status="disconnected", online=True,
                hardware_profile=json.dumps({"hostname": "node-b"})))
        db.session.commit()
        assert InterconnectorNode.query.count() == 2

    client = app.test_client()
    r = _register(client, stable_id="node-b-stable", hostname="node-b", client_ip="192.0.2.12")
    assert r.status_code in (200, 201)
    with app.app_context():
        rows = InterconnectorNode.query.all()
        assert len(rows) == 1, f"duplicates not pruned: {[n.node_id for n in rows]}"
        assert rows[0].node_id == "node-b-stable"


def test_prune_skips_weak_fingerprints(app):
    """Two fresh installs both reporting a default/blank hostname must NOT be
    merged into one another."""
    with app.app_context():
        db.session.add(InterconnectorNode(
            node_id="other-node", node_name="other", node_mode="client",
            host="192.168.1.50", port=5002, online=True,
            hardware_profile=json.dumps({"hostname": "localhost"})))
        db.session.commit()
    client = app.test_client()
    _register(client, stable_id="mine", hostname="localhost", client_ip="192.168.1.51")
    with app.app_context():
        assert InterconnectorNode.query.count() == 2  # not merged


def test_local_identity_endpoint_returns_machine_id(app, monkeypatch):
    monkeypatch.setenv("CLUSTER_NODE_ID", "machine-stable-uuid")
    client = app.test_client()
    r = client.get("/api/interconnector/nodes/local-identity")
    assert r.status_code == 200
    assert r.get_json()["data"]["node_id"] == "machine-stable-uuid"


def test_ensure_local_self_node_creates_and_is_idempotent(app):
    """The self-row that lets local sync-history FK writes succeed (previously a
    ForeignKeyViolation) is created once and reused."""
    from backend.api.interconnector_api import _ensure_local_self_node
    with app.app_context():
        n1 = _ensure_local_self_node("self-id", "myhost", "client")
        db.session.commit()
        assert n1.node_id == "self-id" and n1.node_name == "myhost"
        n2 = _ensure_local_self_node("self-id", "myhost", "client")
        assert n2.node_id == "self-id"
        assert InterconnectorNode.query.filter_by(node_id="self-id").count() == 1


def _hostname(node):
    return json.loads(node.hardware_profile or "{}").get("hostname")
