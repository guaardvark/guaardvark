"""Stable, IP-INDEPENDENT node identity for the interconnector.

Regression guard for the "ALPACA keeps cutting out" bug: a node reachable via
multiple paths (LAN / VPN / Tailscale) or with a changed DHCP lease used to
register as a brand-new node each time, piling up duplicate stale rows. Identity
is now keyed on the machine-stable node_id (hardware_profile["node_id"]) and
matched by hostname fingerprint, never by IP.
"""
import json
import pytest

try:
    from flask import Flask
    from backend.models import db, InterconnectorNode, Setting
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


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


def _register(client, *, stable_id, hostname, client_ip, name=None):
    profile = {"node_id": stable_id, "hostname": hostname, "arch": "x86_64", "services": {}}
    return client.post("/api/interconnector/nodes/register", json={
        "node_id": stable_id, "node_name": name or hostname, "node_mode": "client",
        "client_ip": client_ip, "port": 5002, "hardware_profile": profile,
    })


def test_same_machine_two_ips_yields_one_row(app):
    """The core fix: same machine reachable at a different IP (LAN vs VPN) must
    stay ONE node, not spawn a duplicate."""
    client = app.test_client()
    r1 = _register(client, stable_id="alpaca-stable", hostname="alpaca", client_ip="192.168.1.112")
    assert r1.status_code in (200, 201), r1.get_data(as_text=True)
    r2 = _register(client, stable_id="alpaca-stable", hostname="alpaca", client_ip="10.100.0.2")
    assert r2.status_code in (200, 201), r2.get_data(as_text=True)
    # Same node_id returned both times — identity is IP-independent.
    assert r1.get_json()["data"]["node_id"] == "alpaca-stable"
    assert r2.get_json()["data"]["node_id"] == "alpaca-stable"
    with app.app_context():
        rows = InterconnectorNode.query.all()
        assert len(rows) == 1, f"expected 1 row, got {[n.node_id for n in rows]}"
        assert rows[0].host == "10.100.0.2"  # address updated to the latest path
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
        for old_id, ip in [("old-1", "192.168.1.112"), ("old-2", "10.100.0.2")]:
            db.session.add(InterconnectorNode(
                node_id=old_id, node_name="GX1-Alpaca", node_mode="client",
                host=ip, port=5002, status="disconnected", online=True,
                hardware_profile=json.dumps({"hostname": "alpaca"})))
        db.session.commit()
        assert InterconnectorNode.query.count() == 2

    client = app.test_client()
    r = _register(client, stable_id="alpaca-stable", hostname="alpaca", client_ip="192.168.1.112")
    assert r.status_code in (200, 201)
    with app.app_context():
        rows = InterconnectorNode.query.all()
        assert len(rows) == 1, f"duplicates not pruned: {[n.node_id for n in rows]}"
        assert rows[0].node_id == "alpaca-stable"


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


def _hostname(node):
    return json.loads(node.hardware_profile or "{}").get("hostname")
