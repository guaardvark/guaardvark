"""Rule bundles are re-applied, not merely seeded.

`seed_rules_from_file` skips any name it has seen, so it can never update text or
activate a rule that shipped inactive. `load_rule_bundle` upserts by identity
and applies the bundle's `is_active`, retiring any other active row of the same
name first so the partial unique index on active identity holds.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.rules, pytest.mark.db]

try:
    from flask import Flask

    from backend.models import Rule, db
    from backend.seed_data import load_rule_bundle
except Exception:  # pragma: no cover - environment without backend deps
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _bundle(tmp_path, rules):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({"bundle": "test", "rules": rules}))
    return str(path)


def _persona(text="You are the test persona.", active=True):
    return {
        "name": "global_default_chat_system_prompt",
        "level": "SYSTEM",
        "type": "PROMPT_TEMPLATE",
        "target_models_json": '["__ALL__"]',
        "is_active": active,
        "rule_text": text,
    }


def test_inserts_then_updates_in_place(app, tmp_path):
    first = load_rule_bundle(_bundle(tmp_path, [_persona("v1")]))
    assert first == {"inserted": 1, "updated": 0, "deactivated": 0}

    second = load_rule_bundle(_bundle(tmp_path, [_persona("v2")]))
    assert second == {"inserted": 0, "updated": 1, "deactivated": 0}

    rows = Rule.query.filter_by(name="global_default_chat_system_prompt").all()
    assert len(rows) == 1
    assert rows[0].rule_text == "v2"
    assert rows[0].is_active is True


def test_activates_a_rule_that_shipped_inactive(app, tmp_path):
    db.session.add(Rule(**{**_persona("seeded", active=False), "target_models_json": '["__ALL__"]'}))
    db.session.commit()

    load_rule_bundle(_bundle(tmp_path, [_persona("bundle")]))

    row = Rule.query.filter_by(name="global_default_chat_system_prompt").one()
    assert row.is_active is True
    assert row.rule_text == "bundle"


def test_retires_a_different_identity_with_the_same_name(app, tmp_path):
    # The engine seed carries qa_default as PROMPT_TEMPLATE; the bundle types it
    # QA_TEMPLATE. Both cannot be active under the same name.
    db.session.add(
        Rule(
            name="qa_default",
            level="SYSTEM",
            type="PROMPT_TEMPLATE",
            rule_text="old wrapper",
            is_active=True,
            target_models_json='["__ALL__"]',
        )
    )
    db.session.commit()

    counts = load_rule_bundle(
        _bundle(
            tmp_path,
            [
                {
                    "name": "qa_default",
                    "level": "SYSTEM",
                    "type": "QA_TEMPLATE",
                    "is_active": True,
                    "rule_text": "new wrapper {context_str} {query_str}",
                }
            ],
        )
    )
    assert counts == {"inserted": 1, "updated": 0, "deactivated": 1}

    active = Rule.query.filter_by(name="qa_default", is_active=True).all()
    assert [r.type for r in active] == ["QA_TEMPLATE"]


def test_bundle_can_deactivate(app, tmp_path):
    load_rule_bundle(_bundle(tmp_path, [_persona("on")]))
    counts = load_rule_bundle(_bundle(tmp_path, [_persona("off", active=False)]))
    assert counts["updated"] == 1
    row = Rule.query.filter_by(name="global_default_chat_system_prompt").one()
    assert row.is_active is False
