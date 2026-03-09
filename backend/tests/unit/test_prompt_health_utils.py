import pytest

try:
    import sys
    import types

    sys.modules.setdefault(
        "flask_socketio",
        types.SimpleNamespace(
            SocketIO=object, emit=lambda *a, **k: None, join_room=lambda *a, **k: None
        ),
    )
    from flask import Flask

    from backend.models import Rule, db
    from backend.utils.rule_utils import check_and_heal_prompts
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


def test_check_and_heal_prompts_creates_rules(app):
    with app.app_context():
        result = check_and_heal_prompts(db.session)
        qa_rules = (
            db.session.query(Rule).filter_by(name="qa_default", is_active=True).all()
        )
        sys_rules = (
            db.session.query(Rule)
            .filter_by(name="global_default_chat_system_prompt", is_active=True)
            .all()
        )
        assert len(qa_rules) == 1
        assert len(sys_rules) == 1
        assert qa_rules[0].level == "SYSTEM"
        assert qa_rules[0].type == "QA_TEMPLATE"
        assert sys_rules[0].level == "SYSTEM"
        assert sys_rules[0].type in ["SYSTEM_PROMPT", "PROMPT_TEMPLATE"]
        assert result["qa_details"].startswith(("created", "existing", "kept"))
        assert result["system_details"].startswith(("created", "existing"))
