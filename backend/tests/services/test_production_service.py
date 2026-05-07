import pytest

try:
    from flask import Flask
    from backend.models import db, Production
    from backend.services.production_service import ProductionService, VALID_TRANSITIONS
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


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


def test_create_production_initial_state(app):
    svc = ProductionService(db.session)
    prod = svc.create(name="Test", script_text="INT. ROOM. Hi.", project_id=None)
    assert prod.status == "draft"
    assert prod.current_stage == "draft"
    assert prod.id is not None


def test_advance_rejects_when_predecessor_mismatched(app):
    svc = ProductionService(db.session)
    prod = svc.create(name="X", script_text="x", project_id=None)
    prod.current_stage = "complete"
    db.session.commit()
    # Idempotency: dispatching with the wrong predecessor is a no-op
    result = svc.advance_if_predecessor(prod.id, expected_predecessor="rendering")
    assert result is False


def test_advance_succeeds_when_predecessor_matches(app):
    svc = ProductionService(db.session)
    prod = svc.create(name="X", script_text="x", project_id=None)
    # draft → screenwriting
    result = svc.advance_if_predecessor(prod.id, expected_predecessor="draft")
    assert result is True
    db.session.refresh(prod)
    assert prod.current_stage == "screenwriting"


def test_advance_full_chain(app):
    svc = ProductionService(db.session)
    prod = svc.create(name="X", script_text="x", project_id=None)
    chain = ["draft", "screenwriting", "casting", "cinematography",
             "storyboard_gen", "awaiting_approval", "rendering"]
    for predecessor in chain:
        assert svc.advance_if_predecessor(prod.id, expected_predecessor=predecessor) is True
    db.session.refresh(prod)
    assert prod.current_stage == "complete"


def test_advance_from_terminal_stage_is_noop(app):
    svc = ProductionService(db.session)
    prod = svc.create(name="X", script_text="x", project_id=None)
    prod.current_stage = "complete"
    db.session.commit()
    result = svc.advance_if_predecessor(prod.id, expected_predecessor="complete")
    assert result is False


def test_state_transitions_in_order():
    expected = {
        "draft": "screenwriting",
        "screenwriting": "casting",
        "casting": "cinematography",
        "cinematography": "storyboard_gen",
        "storyboard_gen": "awaiting_approval",
        "awaiting_approval": "rendering",
        "rendering": "complete",
    }
    for src, dst in expected.items():
        assert VALID_TRANSITIONS.get(src) == dst


# --- Resumability -----------------------------------------------------------


def test_find_non_terminal_excludes_complete_and_failed(app):
    svc = ProductionService(db.session)
    p_active = Production(name="A", script_text="x", status="rendering",
                          current_stage="rendering", settings_json={})
    p_done = Production(name="B", script_text="x", status="complete",
                        current_stage="complete", settings_json={})
    p_fail = Production(name="C", script_text="x", status="failed",
                        current_stage="storyboard_gen", settings_json={})
    db.session.add_all([p_active, p_done, p_fail])
    db.session.commit()

    ids = {p.id for p in svc.find_non_terminal()}
    assert p_active.id in ids
    assert p_done.id not in ids
    assert p_fail.id not in ids


def test_resume_all_dispatches_at_current_stage(app, monkeypatch):
    p1 = Production(name="A", script_text="x", status="screenwriting",
                    current_stage="screenwriting", settings_json={})
    p2 = Production(name="B", script_text="x", status="storyboard_gen",
                    current_stage="storyboard_gen", settings_json={})
    db.session.add_all([p1, p2])
    db.session.commit()

    calls = []
    monkeypatch.setattr(
        ProductionService, "dispatch_agent",
        lambda self, prod_id, agent_name: calls.append((prod_id, agent_name)),
    )
    svc = ProductionService(db.session)
    count = svc.resume_all()
    assert count == 2
    assert (p1.id, "screenwriter") in calls
    assert (p2.id, "storyboard_artist") in calls


def test_resume_all_skips_user_gated_stages(app, monkeypatch):
    p_casting = Production(name="A", script_text="x", status="casting",
                           current_stage="casting", settings_json={})
    p_approval = Production(name="B", script_text="x", status="awaiting_approval",
                            current_stage="awaiting_approval", settings_json={})
    db.session.add_all([p_casting, p_approval])
    db.session.commit()

    calls = []
    monkeypatch.setattr(
        ProductionService, "dispatch_agent",
        lambda self, prod_id, agent_name: calls.append((prod_id, agent_name)),
    )
    svc = ProductionService(db.session)
    count = svc.resume_all()
    assert count == 0
    assert calls == []


# --- GPU gate ---------------------------------------------------------------


def test_gpu_stage_no_gate_just_runs():
    svc = ProductionService(session=None, gate=None)
    result = svc.gpu_stage("op-1", lambda x: x * 2, 21)
    assert result == 42


def test_gpu_stage_acquires_and_releases_gate():
    class FakeGate:
        def __init__(self):
            self.acquired = []
            self.released = []
        def acquire(self, op_id):
            self.acquired.append(op_id)
        def release(self, op_id):
            self.released.append(op_id)

    gate = FakeGate()
    svc = ProductionService(session=None, gate=gate)
    result = svc.gpu_stage("storyboard:42", lambda: "done")
    assert result == "done"
    assert gate.acquired == ["storyboard:42"]
    assert gate.released == ["storyboard:42"]


def test_gpu_stage_releases_gate_on_exception():
    class FakeGate:
        def __init__(self):
            self.acquired = []
            self.released = []
        def acquire(self, op_id):
            self.acquired.append(op_id)
        def release(self, op_id):
            self.released.append(op_id)

    gate = FakeGate()
    svc = ProductionService(session=None, gate=gate)

    def boom():
        raise RuntimeError("CUDA OOM")

    with pytest.raises(RuntimeError, match="CUDA OOM"):
        svc.gpu_stage("op-1", boom)
    # Released even after exception
    assert gate.released == ["op-1"]
