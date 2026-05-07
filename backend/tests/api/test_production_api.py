import pytest
import json

try:
    from flask import Flask
    from backend.models import db, Production
    from backend.api.production_api import bp as production_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    app.register_blueprint(production_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_production_returns_201(client, monkeypatch):
    """create() now advances state immediately (post-C1 fix); dispatch is mocked."""
    from backend.services.production_service import ProductionService
    monkeypatch.setattr(
        ProductionService, "dispatch_agent",
        lambda self, prod_id, agent_name: None,
    )
    resp = client.post("/api/production", json={
        "name": "Hello World",
        "script_text": "INT. ROOM. Hi.",
        "project_id": None,
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "Hello World"
    # After C1, create advances to screenwriting and dispatches.
    assert data["current_stage"] == "screenwriting"
    assert data["status"] == "screenwriting"
    assert data["id"] > 0


def test_create_advances_and_dispatches_screenwriter(client, monkeypatch):
    """C1: pipeline must actually start. Without this, productions sit in draft forever."""
    from backend.services.production_service import ProductionService
    dispatched = []
    monkeypatch.setattr(
        ProductionService, "dispatch_agent",
        lambda self, prod_id, agent_name: dispatched.append((prod_id, agent_name)),
    )
    resp = client.post("/api/production", json={
        "name": "X", "script_text": "x", "project_id": None,
    })
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    assert dispatched == [(pid, "screenwriter")]


def test_create_tolerates_dispatch_not_implemented(client, monkeypatch):
    """If swarm dispatch stub raises NotImplementedError, create still 201s.
    State has advanced; resume_all on the next boot will retry."""
    from backend.services.production_service import ProductionService

    def not_yet(self, prod_id, agent_name):
        raise NotImplementedError("swarm not wired yet")

    monkeypatch.setattr(ProductionService, "dispatch_agent", not_yet)
    resp = client.post("/api/production", json={
        "name": "X", "script_text": "x", "project_id": None,
    })
    assert resp.status_code == 201
    assert resp.get_json()["current_stage"] == "screenwriting"


def test_create_rejects_unknown_project_id(client):
    """M5: non-existent project_id → 400, not 500 from IntegrityError."""
    resp = client.post("/api/production", json={
        "name": "X", "script_text": "x", "project_id": 99999,
    })
    assert resp.status_code == 400
    err = resp.get_json().get("error", "").lower()
    assert "project_id" in err or "project" in err


def test_create_production_requires_name_and_script(client):
    resp = client.post("/api/production", json={"name": "X"})
    assert resp.status_code == 400
    resp2 = client.post("/api/production", json={"script_text": "x"})
    assert resp2.status_code == 400


def test_get_production_404_for_unknown(client):
    resp = client.get("/api/production/9999")
    assert resp.status_code == 404


def test_get_production_returns_full_state(client, app):
    with app.app_context():
        prod = Production(name="X", script_text="INT. KITCHEN.",
                          status="draft", current_stage="draft", settings_json={})
        db.session.add(prod); db.session.commit()
        prod_id = prod.id
    resp = client.get(f"/api/production/{prod_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == prod_id
    assert data["name"] == "X"
    assert data["current_stage"] == "draft"
    assert data["shots"] == []
