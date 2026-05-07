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

def test_cast_use_existing_lora(client, app):
    """A subject with a trained LoRA can be referenced by another."""
    with app.app_context():
        from backend.models import Subject
        # Existing trained subject
        trained = Subject(kind="character", name="Dean", description="x",
                          lora_path="/loras/dean.safetensors", training_status="trained",
                          ref_image_paths=[])
        # New subject we want to cast
        new_subj = Subject(kind="character", name="Dean", description="y",
                           training_status="untrained", ref_image_paths=[])
        # A Production
        from backend.models import Production
        prod = Production(name="P", script_text="x", status="casting",
                          current_stage="casting", settings_json={})
        db.session.add_all([trained, new_subj, prod]); db.session.commit()
        prod_id, new_id, trained_id = prod.id, new_subj.id, trained.id

    resp = client.post(f"/api/production/{prod_id}/cast/{new_id}", json={
        "action": "use_existing_lora", "existing_lora_id": trained_id,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["training_status"] == "trained"
    assert data["training_job_id"] is None

    # Verify DB
    with app.app_context():
        from backend.models import Subject
        s = db.session.get(Subject, new_id)
        assert s.training_status == "trained"
        assert s.lora_path == "/loras/dean.safetensors"


def test_cast_use_existing_lora_404_for_unknown_lora(client, app):
    with app.app_context():
        from backend.models import Subject, Production
        subj = Subject(kind="character", name="X", description="x",
                       training_status="untrained", ref_image_paths=[])
        prod = Production(name="P", script_text="x", status="casting",
                          current_stage="casting", settings_json={})
        db.session.add_all([subj, prod]); db.session.commit()
        prod_id, subj_id = prod.id, subj.id
    resp = client.post(f"/api/production/{prod_id}/cast/{subj_id}", json={
        "action": "use_existing_lora", "existing_lora_id": 99999,
    })
    assert resp.status_code == 404


def test_cast_train_from_uploads_dispatches(client, app, monkeypatch):
    """When dispatch succeeds, returns the task id."""
    with app.app_context():
        from backend.models import Subject, Production
        subj = Subject(kind="character", name="X", description="x",
                       training_status="untrained", ref_image_paths=[])
        prod = Production(name="P", script_text="x", status="casting",
                          current_stage="casting", settings_json={})
        db.session.add_all([subj, prod]); db.session.commit()
        prod_id, subj_id = prod.id, subj.id

    from backend.api import production_api
    monkeypatch.setattr(production_api, "_dispatch_lora_train", lambda sid: "task-abc")

    resp = client.post(f"/api/production/{prod_id}/cast/{subj_id}", json={
        "action": "train_from_uploads",
        "ref_image_paths": ["/uploads/a.jpg", "/uploads/b.jpg"],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["training_status"] == "training"
    assert data["training_job_id"] == "task-abc"


def test_cast_train_from_uploads_tolerates_unwired_dispatcher(client, app):
    """If lora_trainer isn't wired (NotImplementedError), state still moves;
    training_job_id is null."""
    with app.app_context():
        from backend.models import Subject, Production
        subj = Subject(kind="character", name="X", description="x",
                       training_status="untrained", ref_image_paths=[])
        prod = Production(name="P", script_text="x", status="casting",
                          current_stage="casting", settings_json={})
        db.session.add_all([subj, prod]); db.session.commit()
        prod_id, subj_id = prod.id, subj.id

    resp = client.post(f"/api/production/{prod_id}/cast/{subj_id}", json={
        "action": "train_from_uploads",
        "ref_image_paths": ["/uploads/a.jpg"],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["training_status"] == "training"
    assert data["training_job_id"] is None


def test_cast_train_from_uploads_requires_refs(client, app):
    with app.app_context():
        from backend.models import Subject, Production
        subj = Subject(kind="character", name="X", description="x",
                       training_status="untrained", ref_image_paths=[])
        prod = Production(name="P", script_text="x", status="casting",
                          current_stage="casting", settings_json={})
        db.session.add_all([subj, prod]); db.session.commit()
        prod_id, subj_id = prod.id, subj.id
    resp = client.post(f"/api/production/{prod_id}/cast/{subj_id}", json={
        "action": "train_from_uploads",
    })
    assert resp.status_code == 400


def test_cast_invalid_action(client, app):
    with app.app_context():
        from backend.models import Subject, Production
        subj = Subject(kind="character", name="X", description="x",
                       training_status="untrained", ref_image_paths=[])
        prod = Production(name="P", script_text="x", status="casting",
                          current_stage="casting", settings_json={})
        db.session.add_all([subj, prod]); db.session.commit()
        prod_id, subj_id = prod.id, subj.id
    resp = client.post(f"/api/production/{prod_id}/cast/{subj_id}", json={
        "action": "telepathic_lora_acquisition",
    })
    assert resp.status_code == 400


def test_cast_404_for_unknown_production(client):
    resp = client.post("/api/production/9999/cast/1", json={
        "action": "use_existing_lora", "existing_lora_id": 1,
    })
    assert resp.status_code == 404

def test_storyboard_approve_advances_and_dispatches(client, app, monkeypatch):
    with app.app_context():
        from backend.models import Production, ProductionShot
        prod = Production(name="P", script_text="x", status="awaiting_approval",
                          current_stage="awaiting_approval", settings_json={})
        db.session.add(prod); db.session.commit()
        s1 = ProductionShot(production_id=prod.id, scene_number=1, shot_number=1,
                            description="x", duration_seconds=3.0, approved=False)
        s2 = ProductionShot(production_id=prod.id, scene_number=1, shot_number=2,
                            description="y", duration_seconds=3.0, approved=False)
        db.session.add_all([s1, s2]); db.session.commit()
        prod_id = prod.id

    from backend.services.production_service import ProductionService
    dispatched = []
    monkeypatch.setattr(
        ProductionService, "dispatch_agent",
        lambda self, pid, agent: dispatched.append((pid, agent)),
    )

    resp = client.post(f"/api/production/{prod_id}/storyboard/approve")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_stage"] == "rendering"
    assert data["shots_approved"] == 2
    assert dispatched == [(prod_id, "editor")]


def test_storyboard_approve_409_when_not_at_awaiting_approval(client, app):
    with app.app_context():
        from backend.models import Production
        prod = Production(name="P", script_text="x", status="storyboard_gen",
                          current_stage="storyboard_gen", settings_json={})
        db.session.add(prod); db.session.commit()
        prod_id = prod.id
    resp = client.post(f"/api/production/{prod_id}/storyboard/approve")
    assert resp.status_code == 409


def test_storyboard_approve_tolerates_unwired_dispatcher(client, app):
    with app.app_context():
        from backend.models import Production
        prod = Production(name="P", script_text="x", status="awaiting_approval",
                          current_stage="awaiting_approval", settings_json={})
        db.session.add(prod); db.session.commit()
        prod_id = prod.id
    # No monkeypatch — uses the real stub which raises NotImplementedError
    resp = client.post(f"/api/production/{prod_id}/storyboard/approve")
    assert resp.status_code == 200
    assert resp.get_json()["current_stage"] == "rendering"


def test_storyboard_approve_404(client):
    resp = client.post("/api/production/9999/storyboard/approve")
    assert resp.status_code == 404
