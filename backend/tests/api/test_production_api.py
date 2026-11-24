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

def test_list_productions(client, app):
    with app.app_context():
        p1 = Production(name="A", script_text="x")
        p2 = Production(name="B", script_text="y")
        db.session.add_all([p1, p2]); db.session.commit()
    
    resp = client.get("/api/production")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["productions"]) >= 2
    names = [p["name"] for p in data["productions"]]
    assert "A" in names
    assert "B" in names

def test_cast_use_existing_lora(client, app):
    """A subject with a trained LoRA can be referenced by another."""
    with app.app_context():
        from backend.models import Subject
        # Existing trained subject
        trained = Subject(kind="character", name="Alex", description="x",
                          lora_path="/loras/alex.safetensors", training_status="trained",
                          ref_image_paths=[])
        # New subject we want to cast
        new_subj = Subject(kind="character", name="Alex", description="y",
                           training_status="failed", training_error="Cancelled by user",
                           ref_image_paths=[])
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
        assert s.lora_path == "/loras/alex.safetensors"
        assert s.training_error is None


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
    """When dispatch succeeds, returns the unified progress job id, the one the
    Casting panel matches against live progress (lora_train_dispatch, the same
    path as Cast Studio). The Celery task gets the subject and that job id."""
    sent = {}

    class _FakeTask:
        id = "fake-task-id-abc"

    def _fake_send_task(name, args=None, **kw):
        sent["name"] = name
        sent["args"] = args
        return _FakeTask()

    class _FakeProgress:
        def create_process(self, *args, **kwargs):
            return "training_job_x"

        def error_process(self, *args, **kwargs):
            pass

    from backend import celery_app as celery_app_module
    monkeypatch.setattr(celery_app_module.celery, "send_task", _fake_send_task)
    monkeypatch.setattr("backend.utils.unified_progress_system.get_unified_progress",
                        lambda: _FakeProgress())

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
        "ref_image_paths": ["/uploads/a.jpg", "/uploads/b.jpg"],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["training_status"] == "training"
    assert data["training_job_id"] == "training_job_x"
    assert sent["name"] == "lora_trainer.train_lora"
    assert sent["args"] == [subj_id, "training_job_x"]
    with app.app_context():
        assert db.session.get(Subject, subj_id).current_training_job_id == "training_job_x"


def test_cast_train_from_uploads_tolerates_unwired_dispatcher(client, app, monkeypatch):
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

    from backend.api import production_api
    def _raise_not_implemented(*args, **kwargs):
        raise NotImplementedError("lora_trainer plugin not yet wired (Phase B)")
    monkeypatch.setattr(production_api, "_dispatch_lora_train", _raise_not_implemented)

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

def test_regenerate_shot_increments_regen_count(client, app, monkeypatch):
    with app.app_context():
        from backend.models import Production, ProductionShot
        prod = Production(name="P", script_text="x", status="awaiting_approval",
                          current_stage="awaiting_approval", settings_json={})
        db.session.add(prod); db.session.commit()
        shot = ProductionShot(production_id=prod.id, scene_number=1, shot_number=1,
                              description="x", duration_seconds=3.0, approved=True, regen_count=0)
        db.session.add(shot); db.session.commit()
        prod_id, shot_id = prod.id, shot.id

    from backend.api import production_api
    monkeypatch.setattr(production_api, "_dispatch_storyboard_regen",
                        lambda sid, prompt: "regen-task-1")

    resp = client.post(
        f"/api/production/{prod_id}/storyboard/shot/{shot_id}/regenerate",
        json={"prompt_override": "tighter framing"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["regen_count"] == 1
    assert data["regen_job_id"] == "regen-task-1"

    # Verify the shot is no longer approved
    with app.app_context():
        from backend.models import ProductionShot
        s = db.session.get(ProductionShot, shot_id)
        assert s.approved is False
        assert s.regen_count == 1


def test_regenerate_shot_404_for_unknown_shot(client, app):
    with app.app_context():
        from backend.models import Production
        prod = Production(name="P", script_text="x", status="awaiting_approval",
                          current_stage="awaiting_approval", settings_json={})
        db.session.add(prod); db.session.commit()
        prod_id = prod.id
    resp = client.post(f"/api/production/{prod_id}/storyboard/shot/99999/regenerate")
    assert resp.status_code == 404


def test_regenerate_shot_rejects_mismatched_production(client, app):
    """Defensive: if shot exists but belongs to a different production, 404."""
    with app.app_context():
        from backend.models import Production, ProductionShot
        prod_a = Production(name="A", script_text="x", status="awaiting_approval",
                            current_stage="awaiting_approval", settings_json={})
        prod_b = Production(name="B", script_text="x", status="awaiting_approval",
                            current_stage="awaiting_approval", settings_json={})
        db.session.add_all([prod_a, prod_b]); db.session.commit()
        shot = ProductionShot(production_id=prod_a.id, scene_number=1, shot_number=1,
                              description="x", duration_seconds=3.0)
        db.session.add(shot); db.session.commit()
        prod_b_id, shot_id = prod_b.id, shot.id

    resp = client.post(f"/api/production/{prod_b_id}/storyboard/shot/{shot_id}/regenerate")
    assert resp.status_code == 404


def test_regenerate_shot_dispatches_celery_task(client, app, monkeypatch):
    sent = {}

    class _FakeTask:
        id = "fake-task-id-123"

    def _fake_send_task(name, args=None, **kw):
        sent["name"] = name
        sent["args"] = args
        return _FakeTask()

    from backend import celery_app as celery_app_module
    monkeypatch.setattr(celery_app_module.celery, "send_task", _fake_send_task)

    with app.app_context():
        from backend.models import Production, ProductionShot
        prod = Production(name="P", script_text="x", status="awaiting_approval",
                          current_stage="awaiting_approval", settings_json={})
        db.session.add(prod); db.session.commit()
        shot = ProductionShot(production_id=prod.id, scene_number=1, shot_number=1,
                              description="x", duration_seconds=3.0, regen_count=0)
        db.session.add(shot); db.session.commit()
        prod_id, shot_id = prod.id, shot.id

    resp = client.post(f"/api/production/{prod_id}/storyboard/shot/{shot_id}/regenerate")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["regen_count"] == 1
    assert data["regen_job_id"] == "fake-task-id-123"
    assert sent["name"] == "production.regen_storyboard_shot"
    assert sent["args"] == [shot_id, None]


def test_get_production_subjects_returns_screenwriter_extracted_subjects(client, app):
    """CastingPanel needs real Subject ids to upload refs against. Source of
    truth is the ProductionSubject join table."""
    with app.app_context():
        from backend.models import Production, Subject, ProductionSubject
        prod = Production(name="P", script_text="x",
                          status="casting", current_stage="casting", settings_json={})
        # Two cast-library entries — one matching the script, one not.
        alice = Subject(kind="character", name="Alice", description="hero",
                        ref_image_paths=[], training_status="untrained")
        bob = Subject(kind="character", name="Bob", description="other",
                      ref_image_paths=[], training_status="untrained")
        db.session.add_all([prod, alice, bob]); db.session.commit()

        ps = ProductionSubject(production_id=prod.id, subject_id=alice.id)
        db.session.add(ps); db.session.commit()
        prod_id = prod.id

    resp = client.get(f"/api/production/{prod_id}/subjects")
    assert resp.status_code == 200
    subjects = resp.get_json()["subjects"]
    assert len(subjects) == 1
    assert subjects[0]["name"] == "Alice"
    assert subjects[0]["id"] > 0


def test_get_production_subjects_empty_when_screenwriter_never_succeeded(client, app):
    """No screenwriter:ok message → empty list, not 500."""
    with app.app_context():
        from backend.models import Production
        prod = Production(name="P", script_text="x",
                          status="screenwriting", current_stage="screenwriting", settings_json={})
        db.session.add(prod); db.session.commit()
        prod_id = prod.id
    resp = client.get(f"/api/production/{prod_id}/subjects")
    assert resp.status_code == 200
    assert resp.get_json() == {"subjects": []}


def test_get_production_subjects_404_for_unknown(client):
    resp = client.get("/api/production/9999/subjects")
    assert resp.status_code == 404


def _library_anna_in_two_productions(pin_first, pin_second):
    """One Cast Library subject linked to two productions with different pins."""
    from backend.models import Production, Subject, ProductionSubject
    anna = Subject(kind="character", name="Anna", description="Hand-written look",
                   cast_required=False, ref_image_paths=[], training_status="untrained")
    first = Production(name="First", script_text="x", status="casting",
                       current_stage="casting", settings_json={})
    second = Production(name="Second", script_text="x", status="casting",
                        current_stage="casting", settings_json={})
    db.session.add_all([anna, first, second]); db.session.commit()
    db.session.add_all([
        ProductionSubject(production_id=first.id, subject_id=anna.id,
                          script_description="A barista", cast_required=pin_first),
        ProductionSubject(production_id=second.id, subject_id=anna.id,
                          script_description="A pilot", cast_required=pin_second),
    ])
    db.session.commit()
    return first.id, second.id


def test_get_production_subjects_shows_this_production_s_script_text_and_pin(client, app):
    with app.app_context():
        first_id, second_id = _library_anna_in_two_productions(True, None)

    first = client.get(f"/api/production/{first_id}/subjects").get_json()["subjects"][0]
    assert first["description"] == "Hand-written look"
    assert first["script_description"] == "A barista"
    assert first["cast_required"] is True

    second = client.get(f"/api/production/{second_id}/subjects").get_json()["subjects"][0]
    assert second["script_description"] == "A pilot"
    assert second["cast_required"] is False  # no pin on the link: the Subject's setting


def test_casting_confirm_follows_this_production_s_pin(client, app, monkeypatch):
    """The script's pin lives on the link, so one film can require Anna's LoRA
    while another casting the same library Anna does not."""
    with app.app_context():
        first_id, second_id = _library_anna_in_two_productions(True, False)

    from backend.services.production_service import ProductionService
    dispatched = []
    monkeypatch.setattr(
        ProductionService, "dispatch_agent",
        lambda self, pid, agent: dispatched.append((pid, agent)),
    )

    blocked = client.post(f"/api/production/{first_id}/casting/confirm")
    assert blocked.status_code == 400
    assert [s["name"] for s in blocked.get_json()["incomplete_subjects"]] == ["Anna"]

    passed = client.post(f"/api/production/{second_id}/casting/confirm")
    assert passed.status_code == 200
    assert passed.get_json()["subjects_confirmed"] == 1
    assert dispatched == [(second_id, "cinematographer")]


def test_create_stores_a_video_model_and_rejects_a_companion(client, monkeypatch):
    from backend.services.production_service import ProductionService
    monkeypatch.setattr(ProductionService, "dispatch_agent", lambda self, prod_id, agent_name: None)
    resp = client.post("/api/production", json={
        "name": "Talkie", "script_text": "INT. ROOM - DAY", "settings": {"video_model": "minimax-h3-int8"},
    })
    assert resp.status_code == 201, resp.get_json()
    prod = Production.query.filter_by(name="Talkie").one()
    assert prod.settings_json == {"video_model": "minimax-h3-int8"}
    bad = client.post("/api/production", json={
        "name": "Nope", "script_text": "INT. ROOM - DAY", "settings": {"video_model": "minimax-h3-vae"},
    })
    assert bad.status_code == 400 and "not a video model" in bad.get_json()["error"]


def _awaiting_storyboard(app, shots):
    """A production at awaiting_approval with the given ProductionShot kwargs."""
    with app.app_context():
        from backend.models import ProductionShot
        prod = Production(name="P", script_text="x", status="awaiting_approval",
                          current_stage="awaiting_approval", settings_json={})
        db.session.add(prod); db.session.commit()
        rows = [ProductionShot(production_id=prod.id, scene_number=1, shot_number=n,
                               description=f"shot {n}", duration_seconds=3.0, **kw)
                for n, kw in enumerate(shots, start=1)]
        db.session.add_all(rows); db.session.commit()
        return prod.id, [r.id for r in rows]


def test_shot_detail_carries_the_curator_advice_and_who_approved(client, app):
    advice = {"verdict": "flag", "reason": "distorted face", "confidence": 20}
    prod_id, _ = _awaiting_storyboard(app, [
        {"approved": True, "approved_by": "curator",
         "curator_advice": {"verdict": "approve", "reason": "clean", "confidence": 90}},
        {"approved": False, "curator_advice": advice},
    ])
    shots = client.get(f"/api/production/{prod_id}").get_json()["shots"]
    by_number = {s["shot_number"]: s for s in shots}
    assert by_number[1]["approved_by"] == "curator"
    assert by_number[2]["approved_by"] is None
    assert by_number[2]["curator_advice"] == advice


def test_regenerate_clears_the_curator_advice_and_its_tick(client, app, monkeypatch):
    prod_id, (shot_id,) = _awaiting_storyboard(app, [
        {"approved": True, "approved_by": "curator",
         "curator_advice": {"verdict": "approve", "reason": "clean", "confidence": 90}},
    ])
    from backend.api import production_api
    monkeypatch.setattr(production_api, "_dispatch_storyboard_regen", lambda sid, prompt: "regen-1")

    resp = client.post(f"/api/production/{prod_id}/storyboard/shot/{shot_id}/regenerate", json={})
    assert resp.status_code == 200
    with app.app_context():
        from backend.models import ProductionShot
        s = db.session.get(ProductionShot, shot_id)
        assert s.approved is False
        assert s.approved_by is None
        assert s.curator_advice is None


def test_storyboard_approve_marks_the_shots_as_the_person_s(client, app, monkeypatch):
    prod_id, shot_ids = _awaiting_storyboard(app, [
        {"approved": True, "approved_by": "curator"},
        {"approved": False},
    ])
    from backend.services.production_service import ProductionService
    monkeypatch.setattr(ProductionService, "dispatch_agent", lambda self, pid, agent: None)

    resp = client.post(f"/api/production/{prod_id}/storyboard/approve")
    assert resp.status_code == 200
    with app.app_context():
        from backend.models import ProductionShot
        for sid in shot_ids:
            s = db.session.get(ProductionShot, sid)
            assert s.approved is True
            assert s.approved_by == "person"


_FLAG_ADVICE = {"verdict": "flag", "reason": "distorted face", "confidence": 20}


def test_storyboard_approve_refuses_flagged_shots_without_a_yes(client, app, monkeypatch):
    prod_id, (ok_id, flagged_id) = _awaiting_storyboard(app, [
        {"approved": True, "approved_by": "curator"},
        {"approved": False, "curator_advice": _FLAG_ADVICE},
    ])
    from backend.services.production_service import ProductionService
    dispatched = []
    monkeypatch.setattr(ProductionService, "dispatch_agent",
                        lambda self, pid, agent: dispatched.append((pid, agent)))

    resp = client.post(f"/api/production/{prod_id}/storyboard/approve")
    assert resp.status_code == 409
    data = resp.get_json()
    assert [s["id"] for s in data["flagged_shots"]] == [flagged_id]
    assert data["flagged_shots"][0]["reason"] == "distorted face"
    assert dispatched == []
    with app.app_context():
        from backend.models import ProductionShot
        assert db.session.get(Production, prod_id).current_stage == "awaiting_approval"
        assert db.session.get(ProductionShot, flagged_id).approved is False


def test_storyboard_approve_renders_flagged_shots_when_confirmed(client, app, monkeypatch):
    prod_id, (ok_id, flagged_id) = _awaiting_storyboard(app, [
        {"approved": True, "approved_by": "curator"},
        {"approved": False, "curator_advice": _FLAG_ADVICE},
    ])
    from backend.services.production_service import ProductionService
    dispatched = []
    monkeypatch.setattr(ProductionService, "dispatch_agent",
                        lambda self, pid, agent: dispatched.append((pid, agent)))

    resp = client.post(f"/api/production/{prod_id}/storyboard/approve", json={"confirm_flagged": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_stage"] == "rendering"
    assert data["flagged_approved"] == [flagged_id]
    assert dispatched == [(prod_id, "editor")]
    with app.app_context():
        from backend.models import ProductionShot
        s = db.session.get(ProductionShot, flagged_id)
        assert s.approved is True and s.approved_by == "person"


def test_storyboard_approve_is_one_click_when_nothing_is_flagged(client, app, monkeypatch):
    prod_id, _ = _awaiting_storyboard(app, [
        {"approved": True, "approved_by": "curator",
         "curator_advice": {"verdict": "approve", "reason": "clean", "confidence": 90}},
        {"approved": True, "approved_by": "person", "curator_advice": _FLAG_ADVICE},
    ])
    from backend.services.production_service import ProductionService
    monkeypatch.setattr(ProductionService, "dispatch_agent", lambda self, pid, agent: None)

    resp = client.post(f"/api/production/{prod_id}/storyboard/approve")
    assert resp.status_code == 200
    assert resp.get_json()["flagged_approved"] == []
