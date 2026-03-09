# test_rule_utils.py   Version 1.001
import pytest

pytestmark = [pytest.mark.rules, pytest.mark.db]
from datetime import datetime, timedelta, timezone

try:
    from flask import Flask
    from sqlalchemy.exc import IntegrityError

    from backend import rule_utils
    from backend.models import Rule, db
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


def test_get_formatted_rules_order_and_filter(app):
    with app.app_context():
        base_time = datetime.now(timezone.utc)
        rules = [
            Rule(
                level="SYSTEM",
                rule_text="System rule 1",
                is_active=True,
                target_models_json='["__ALL__"]',
                created_at=base_time,
            ),
            Rule(
                level="LEARNED",
                rule_text="Learned rule 1",
                is_active=True,
                target_models_json='["__ALL__"]',
                created_at=base_time + timedelta(minutes=1),
            ),
            Rule(
                level="SYSTEM",
                rule_text="System rule 2",
                is_active=True,
                target_models_json='["modelX"]',
                created_at=base_time + timedelta(minutes=2),
            ),
            Rule(
                level="LEARNED",
                rule_text="Learned rule 2",
                is_active=False,
                target_models_json='["modelX"]',
                created_at=base_time + timedelta(minutes=3),
            ),
            Rule(
                level="LEARNED",
                rule_text="Learned rule 3",
                is_active=True,
                target_models_json='["modelX"]',
                created_at=base_time + timedelta(minutes=4),
            ),
        ]
        db.session.add_all(rules)
        db.session.commit()

        result = rule_utils.get_formatted_rules(
            levels=["SYSTEM", "LEARNED"], model_name="modelX"
        )
        lines = [l.strip() for l in result.splitlines() if l.strip()]
        assert "Learned rule 2" not in result
        expected_order = [
            "--- Applicable Rules & Guidelines ---",
            "## System Rules:",
            "- System rule 1",
            "## Learned Rules:",
            "- Learned rule 1",
            "## System Rules:",
            "- System rule 2",
            "## Learned Rules:",
            "- Learned rule 3",
        ]
        assert lines == expected_order


def test_get_active_system_prompt_filters_and_ignores_inactive(app):
    with app.app_context():
        r1 = Rule(
            name="Prompt1",
            level="SYSTEM",
            type="PROMPT_TEMPLATE",
            rule_text="Text for X",
            target_models_json='["modelX"]',
            is_active=True,
        )
        r3 = Rule(
            name="Prompt2",
            level="SYSTEM",
            type="PROMPT_TEMPLATE",
            rule_text="Inactive",
            target_models_json='["modelX"]',
            is_active=False,
        )
        db.session.add_all([r1, r3])
        db.session.commit()

        duplicate = Rule(
            name="Prompt1",
            level="SYSTEM",
            type="PROMPT_TEMPLATE",
            rule_text="Text for Y",
            target_models_json='["modelY"]',
            is_active=True,
        )
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        # Binary prompt system now returns hardcoded prompts instead of database rules
        text, rule_id = rule_utils.get_active_system_prompt(
            "Prompt1", db.session, model_name="modelX"
        )
        # Should return binary prompt system response, not database rule
        assert "You are a professional AI assistant" in text
        assert rule_id == -1  # Binary system uses -1 as rule_id
        missing_text, missing_id = rule_utils.get_active_system_prompt(
            "Prompt2", db.session, model_name="modelX"
        )
        # Should also return binary prompt system response
        assert "You are a professional AI assistant" in missing_text
        assert missing_id == -1


def test_get_active_system_prompt_supports_new_type(app):
    with app.app_context():
        rule = Rule(
            name="PromptNew",
            level="SYSTEM",
            type="SYSTEM_PROMPT",
            rule_text="System text",
            target_models_json='["__ALL__"]',
            is_active=True,
        )
        db.session.add(rule)
        db.session.commit()

        # Binary prompt system now returns hardcoded prompts instead of database rules
        text, rule_id = rule_utils.get_active_system_prompt("PromptNew", db.session)
        # Should return binary prompt system response, not database rule
        assert "You are a professional AI assistant" in text
        assert rule_id == -1  # Binary system uses -1 as rule_id


def test_get_active_command_rule_filters_and_ignores_inactive(app):
    with app.app_context():
        c1 = Rule(
            command_label="/cmd_x",
            level="SYSTEM",
            type="COMMAND_RULE",
            rule_text="Allow",
            target_models_json='["modelX"]',
            is_active=True,
        )
        c2 = Rule(
            command_label="/cmd_y",
            level="SYSTEM",
            type="COMMAND_RULE",
            rule_text="For Y",
            target_models_json='["modelY"]',
            is_active=True,
        )
        c3 = Rule(
            command_label="/cmd_global",
            level="SYSTEM",
            type="COMMAND_RULE",
            rule_text="Global",
            target_models_json='["__ALL__"]',
            is_active=True,
        )
        c4 = Rule(
            command_label="/cmd_inactive",
            level="SYSTEM",
            type="COMMAND_RULE",
            rule_text="Inactive",
            target_models_json='["modelX"]',
            is_active=False,
        )
        db.session.add_all([c1, c2, c3, c4])
        db.session.commit()

        rule = rule_utils.get_active_command_rule(
            "/cmd_x", db.session, model_name="modelX"
        )
        assert rule is not None and rule.rule_text == "Allow"
        missing_specific = rule_utils.get_active_command_rule(
            "/cmd_y", db.session, model_name="modelX"
        )
        assert missing_specific is None
        global_rule = rule_utils.get_active_command_rule(
            "/cmd_global", db.session, model_name="modelX"
        )
        assert global_rule is not None and global_rule.rule_text == "Global"
        inactive = rule_utils.get_active_command_rule(
            "/cmd_inactive", db.session, model_name="modelX"
        )
        assert inactive is None


def test_get_active_qa_default_template_unique(app):
    with app.app_context():
        rule = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="Hello",
            is_active=True,
            target_models_json='["__ALL__"]',
        )
        db.session.add(rule)
        db.session.commit()

        # Binary prompt system now returns hardcoded QA template instead of database rule
        text, rule_id = rule_utils.get_active_qa_default_template(db.session)
        # Should return binary prompt system response, not database rule
        assert "You are a professional AI assistant" in text
        assert rule_id == -1  # Binary system uses -1 as rule_id


def test_get_active_qa_default_template_duplicate_returns_fallback(app):
    with app.app_context():
        r1 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="A",
            is_active=True,
            target_models_json='["__ALL__"]',
        )
        r2 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="B",
            is_active=True,
            target_models_json='["__ALL__"]',
        )
        db.session.add_all([r1, r2])
        db.session.commit()

        text, rule_id = rule_utils.get_active_qa_default_template(db.session)
        assert rule_id is not None
        assert isinstance(text, str) and text != ""


@pytest.mark.skip(reason="ensure_qa_default_rule was removed as part of critical changes; runtime rule self-healing is no longer supported.")
def test_ensure_qa_default_rule_resets_duplicates(app):
    """Existing qa_default entries are all deactivated and replaced."""
    with app.app_context():
        r1 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="X",
            is_active=True,
        )
        r2 = Rule(
            name="qa_default",
            level="PROMPT",
            type="PROMPT_TEMPLATE",
            rule_text="Y",
            is_active=True,
        )
        r3 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="Z",
            is_active=False,
        )
        db.session.add_all([r1, r2, r3])
        db.session.commit()

        healed, details = rule_utils.ensure_qa_default_rule(db.session)
        assert healed is True
        assert "created:" in details

        active = (
            db.session.query(Rule)
            .filter(Rule.name == "qa_default", Rule.is_active == True)
            .all()
        )
        assert len(active) == 1
        rule = active[0]
        assert rule.level == "SYSTEM" and rule.type == "QA_TEMPLATE"


@pytest.mark.skip(reason="ensure_qa_default_rule was removed as part of critical changes; runtime rule self-healing is no longer supported.")
def test_ensure_qa_default_rule_creates_when_missing(app):
    """A new qa_default rule is created when none exist and returns healed=False."""
    with app.app_context():
        healed, details = rule_utils.ensure_qa_default_rule(db.session)
        assert healed is False
        active = (
            db.session.query(Rule)
            .filter(Rule.name == "qa_default", Rule.is_active == True)
            .all()
        )
        assert len(active) == 1


def test_rule_unique_constraint_enforced(app):
    """Inserting duplicate active rules with same name, level and type fails."""
    with app.app_context():
        r1 = Rule(
            name="dup_rule",
            level="SYSTEM",
            type="PROMPT_TEMPLATE",
            rule_text="A",
            is_active=True,
        )
        db.session.add(r1)
        db.session.commit()

        r2 = Rule(
            name="dup_rule",
            level="SYSTEM",
            type="PROMPT_TEMPLATE",
            rule_text="B",
            is_active=True,
        )
        db.session.add(r2)
        with pytest.raises(IntegrityError):
            db.session.commit()


@pytest.mark.skip(reason="ensure_qa_default_rule was removed as part of critical changes; runtime rule self-healing is no longer supported.")
def test_ensure_qa_default_rule_noop_when_single_valid(app):
    """No changes are made if exactly one valid qa_default rule exists."""
    with app.app_context():
        r1 = Rule(
            name="qa_default",
            level="SYSTEM",
            type="QA_TEMPLATE",
            rule_text="OK",
            is_active=True,
        )
        db.session.add(r1)
        db.session.commit()

        healed, details = rule_utils.ensure_qa_default_rule(db.session)
        assert healed is False
        assert details.startswith("existing:")
        active = (
            db.session.query(Rule)
            .filter(Rule.name == "qa_default", Rule.is_active == True)
            .all()
        )
        assert len(active) == 1
        assert active[0].id == r1.id
