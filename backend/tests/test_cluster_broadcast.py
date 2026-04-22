import os
from unittest.mock import patch, MagicMock
import pytest

try:
    from flask import Flask
    from backend.models import db
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


def test_recompute_and_broadcast_noop_on_worker(app, monkeypatch):
    from backend.services.cluster_routing import recompute_and_broadcast
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    result = recompute_and_broadcast(reason="test")
    assert result is None


def test_recompute_and_broadcast_emits_on_master(app, monkeypatch):
    from backend.services.fleet_map import get_fleet_map
    fm = get_fleet_map()
    # Clean any previous state
    fm._profiles.clear()
    fm._live_state.clear()
    fm._flap_history.clear()
    fm.register("master-node", {"arch": "x86_64",
                                "gpu": {"vendor": "nvidia", "vram_mb": 16384},
                                "ram": {"total_gb": 64},
                                "services": {"ollama": {"installed": True}},
                                "cpu": {"cores": 8}})
    monkeypatch.setenv("CLUSTER_ROLE", "master")
    monkeypatch.setenv("CLUSTER_NODE_ID", "master-node")
    with patch("backend.socketio_instance.socketio.emit") as emit:
        from backend.services.cluster_routing import recompute_and_broadcast
        table = recompute_and_broadcast(reason="test")
        assert table is not None
        assert emit.called
        args, kwargs = emit.call_args
        assert args[0] == "cluster:routing_table"
        assert kwargs.get("to") == "cluster:masters-broadcast"


def test_recompute_skips_broadcast_when_fleet_hash_unchanged(app, monkeypatch):
    """If fleet_hash doesn't change between calls, we shouldn't spam workers."""
    from backend.services.fleet_map import get_fleet_map
    fm = get_fleet_map()
    fm._profiles.clear()
    fm._live_state.clear()
    fm.register("master-node", {"arch": "x86_64",
                                "gpu": {"vendor": "nvidia", "vram_mb": 16384},
                                "services": {"ollama": {"installed": True}}})
    monkeypatch.setenv("CLUSTER_ROLE", "master")
    monkeypatch.setenv("CLUSTER_NODE_ID", "master-node")

    from backend.services.cluster_routing import recompute_and_broadcast, get_routing_store
    get_routing_store()._table = None  # clean

    with patch("backend.socketio_instance.socketio.emit"):
        recompute_and_broadcast(reason="first")

    with patch("backend.socketio_instance.socketio.emit") as emit:
        result = recompute_and_broadcast(reason="second-unchanged")
        # Same fleet, same hash — should not re-emit
        assert not emit.called


def test_worker_handler_rejects_spoofed_sender(app, monkeypatch):
    from backend.socketio_events import handle_cluster_routing_table
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    monkeypatch.setenv("CLUSTER_MASTER_NODE_ID", "real-master")
    from backend.services.cluster_routing import get_routing_store
    get_routing_store()._table = None
    handle_cluster_routing_table({"computed_by": "evil-spoofer",
                                  "routes": {}, "computed_at": "2026-04-22T00:00:00",
                                  "node_count": 1, "fleet_hash": "x"})
    assert get_routing_store().get() is None


def test_worker_handler_accepts_known_master(app, monkeypatch):
    from backend.socketio_events import handle_cluster_routing_table
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    monkeypatch.setenv("CLUSTER_MASTER_NODE_ID", "real-master")
    from backend.services.cluster_routing import get_routing_store
    get_routing_store()._table = None
    handle_cluster_routing_table({
        "computed_by": "real-master",
        "routes": {"llm_chat": {"workload": "llm_chat", "mode": "singular",
                                "primary": "real-master", "fallback": [],
                                "workers": [], "required_services": ["ollama"],
                                "min_vram_mb": 4096, "cpu_acceptable": False}},
        "computed_at": "2026-04-22T00:00:00",
        "node_count": 1, "fleet_hash": "abc",
    })
    assert get_routing_store().get() is not None
    assert get_routing_store().get().fleet_hash == "abc"
