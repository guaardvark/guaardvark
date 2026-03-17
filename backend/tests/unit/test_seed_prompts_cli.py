import importlib
import os
import sys

import pytest

pytest.importorskip("flask_cors")
pytest.importorskip("flask_migrate")
pytest.importorskip("flask_executor")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from flask import Flask

from backend.models import Rule, db


def init_app(tmp_path):
    os.environ["GUAARDVARK_ROOT"] = str(tmp_path)
    os.environ["IS_TESTING"] = "1"
    if "backend.app" in sys.modules:
        del sys.modules["backend.app"]
    app_module = importlib.import_module("backend.app")
    return app_module


@pytest.mark.skip(
    reason="Backend no longer deactivates duplicate rules at runtime; test is legacy."
)
@pytest.mark.prompt_seeding
@pytest.mark.db
@pytest.mark.rules
def test_seed_prompts_cli_qa_default_unique(tmp_path):
    app_module = init_app(tmp_path)
    with app_module.app.app_context():
        db.create_all()
        dup = Rule(
            name="qa_default",
            level="PROMPT",
            type="PROMPT_TEMPLATE",
            rule_text="old",
            is_active=True,
        )
        db.session.add(dup)
        db.session.commit()
        dup_id = dup.id

    runner = app_module.app.test_cli_runner()
    result = runner.invoke(app_module.seed_prompts_cli, ["--force"])
    assert result.exit_code == 0

    with app_module.app.app_context():
        qa_active = (
            db.session.query(Rule).filter_by(name="qa_default", is_active=True).all()
        )
        assert len(qa_active) == 1
        rule = qa_active[0]
        assert rule.level == "SYSTEM"
        assert rule.type == "QA_TEMPLATE"
        legacy = db.session.get(Rule, dup_id)
        assert not legacy.is_active
