from unittest.mock import patch
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
    from backend.api.cluster_api import cluster_api_bp
    if cluster_api_bp.name not in app.blueprints:
        app.register_blueprint(cluster_api_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_get_routing_table_master_only(app, monkeypatch):
    client = app.test_client()
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    r = client.get("/api/cluster/routing-table")
    assert r.status_code == 403
    monkeypatch.setenv("CLUSTER_ROLE", "master")
    r = client.get("/api/cluster/routing-table")
    # 204 if no table yet, 200 with payload if one exists
    assert r.status_code in (200, 204)


def test_post_recompute_master_only(app, monkeypatch):
    client = app.test_client()
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    r = client.post("/api/cluster/routing-table/recompute", json={})
    assert r.status_code == 403


def test_post_recompute_triggers_build(app, monkeypatch):
    client = app.test_client()
    monkeypatch.setenv("CLUSTER_ROLE", "master")
    with patch("backend.services.cluster_routing.recompute_and_broadcast") as rec:
        r = client.post("/api/cluster/routing-table/recompute",
                        json={"reason": "test"})
        assert r.status_code in (200, 202)
        rec.assert_called_once_with(reason="test")


def test_get_cluster_metrics_master_only(app, monkeypatch):
    client = app.test_client()
    monkeypatch.setenv("CLUSTER_ROLE", "worker")
    r = client.get("/api/cluster/metrics")
    assert r.status_code == 403
    monkeypatch.setenv("CLUSTER_ROLE", "master")
    r = client.get("/api/cluster/metrics")
    assert r.status_code == 200
    data = r.get_json()
    assert "per_workload" in data
    assert "fallback_rate" in data
