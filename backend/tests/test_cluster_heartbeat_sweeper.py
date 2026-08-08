from datetime import datetime, timedelta
from unittest.mock import patch
import pytest

try:
    from flask import Flask
    from backend.models import db, InterconnectorNode
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_sweeper_skips_when_not_master(app, monkeypatch):
    from backend.tasks.cluster_heartbeat_sweeper import sweep_node_heartbeats
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    result = sweep_node_heartbeats()
    assert result.get("skipped") == "not_master"


def test_sweeper_marks_stale_node_offline(app):
    # Call the core sweep directly, inside the app context, so it operates on
    # THIS test's in-memory DB (the entry point rebuilds a fresh app otherwise).
    from backend.tasks.cluster_heartbeat_sweeper import _run_sweep, DEFAULT_TIMEOUT_S

    with app.app_context():
        # Clock matches the heartbeat writer (datetime.now, local); age must
        # exceed the 150s timeout to count as stale.
        stale_ts = datetime.now() - timedelta(seconds=DEFAULT_TIMEOUT_S + 50)
        node = InterconnectorNode(node_id="stale-node", node_name="stale-node",
                                  node_mode="client", host="x", port=5002,
                                  online=True, last_heartbeat=stale_ts)
        db.session.add(node)
        db.session.commit()

        with patch("backend.services.cluster_routing.recompute_and_broadcast", create=True):
            result = _run_sweep(DEFAULT_TIMEOUT_S)

        refreshed = InterconnectorNode.query.filter_by(node_id="stale-node").first()
        assert refreshed.online is False
        assert refreshed.status == "disconnected"  # liveness fields kept in sync
        assert "stale-node" in result.get("marked_offline", [])


def test_sweeper_marks_recovered_node_online(app):
    from backend.tasks.cluster_heartbeat_sweeper import _run_sweep, DEFAULT_TIMEOUT_S

    with app.app_context():
        fresh_ts = datetime.now()  # within the timeout → healthy
        node = InterconnectorNode(node_id="recovered-node", node_name="recovered-node",
                                  node_mode="client", host="x", port=5002,
                                  online=False, last_heartbeat=fresh_ts)
        db.session.add(node)
        db.session.commit()

        with patch("backend.services.cluster_routing.recompute_and_broadcast", create=True):
            result = _run_sweep(DEFAULT_TIMEOUT_S)

        refreshed = InterconnectorNode.query.filter_by(node_id="recovered-node").first()
        assert refreshed.online is True
        assert refreshed.status == "active"
        assert "recovered-node" in result.get("marked_online", [])


def test_sweeper_noop_when_no_changes(app):
    # In-context against this test's empty DB (the entry point would rebuild an
    # app bound to the real database and pick up unrelated rows).
    from backend.tasks.cluster_heartbeat_sweeper import _run_sweep, DEFAULT_TIMEOUT_S
    with app.app_context():
        result = _run_sweep(DEFAULT_TIMEOUT_S)
        assert result.get("marked_offline") == []
        assert result.get("marked_online") == []
