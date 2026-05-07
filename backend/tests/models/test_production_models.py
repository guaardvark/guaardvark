import pytest

try:
    from flask import Flask
    from backend.models import (
        db, Production, Subject, ProductionShot,
        ProductionShotSubject, SwarmMessage,
    )
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


def test_production_model_exists(app):
    with app.app_context():
        p = Production(name="Test", script_text="INT. ROOM.", status="draft", current_stage="draft")
        db.session.add(p)
        db.session.commit()
        assert p.id is not None
        assert p.created_at is not None


def test_subject_kinds(app):
    with app.app_context():
        for kind in ("character", "environment", "prop"):
            s = Subject(kind=kind, name=f"test_{kind}", description="x")
            db.session.add(s)
        db.session.commit()
        assert Subject.query.count() == 3


def test_shot_subject_join(app):
    with app.app_context():
        prod = Production(name="P", script_text="x", status="draft", current_stage="draft")
        char = Subject(kind="character", name="hero", description="x")
        db.session.add_all([prod, char]); db.session.commit()
        shot = ProductionShot(production_id=prod.id, scene_number=1, shot_number=1,
                              description="x", duration_seconds=3.0)
        db.session.add(shot); db.session.commit()
        link = ProductionShotSubject(shot_id=shot.id, subject_id=char.id)
        db.session.add(link); db.session.commit()
        assert ProductionShotSubject.query.count() == 1


def test_swarm_message_persists_io(app):
    with app.app_context():
        prod = Production(name="P", script_text="x", status="draft", current_stage="draft")
        db.session.add(prod); db.session.commit()
        msg = SwarmMessage(production_id=prod.id, agent_name="screenwriter",
                           input_json={"script": "x"}, output_json={"shots": []},
                           latency_ms=100, model="gemma4", status="ok")
        db.session.add(msg); db.session.commit()
        assert msg.id is not None
        assert msg.input_json == {"script": "x"}
