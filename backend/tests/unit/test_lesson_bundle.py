"""Lesson bundles upsert procedures by title and surface them in the prompt."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.db]

try:
    from flask import Flask

    from backend.api.memory_api import get_memories_for_context
    from backend.models import AgentMemory, db
    from backend.seed_data import load_lesson_bundle
except Exception:  # pragma: no cover - environment without backend deps
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

SHIPPED_BUNDLE = (
    Path(__file__).resolve().parents[2] / "lesson_bundles" / "guaardvark.json"
)


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


def _bundle(tmp_path, lessons):
    path = tmp_path / "lessons.json"
    path.write_text(json.dumps({"bundle": "test", "lessons": lessons}))
    return str(path)


def _index_lesson(steps=None):
    return {
        "title": "Index a folder of documents",
        "steps": steps
        or [
            {"order": 1, "text": "Ask for the folder path if the user did not give one."},
            {"order": 2, "text": "Index the folder with the document indexing tool."},
        ],
        "parameters": [],
        "tags": ["indexing"],
        "importance": 0.8,
    }


def test_inserts_then_updates_in_place(app, tmp_path):
    first = load_lesson_bundle(_bundle(tmp_path, [_index_lesson()]))
    assert first == {"inserted": 1, "updated": 0, "invalid": 0}

    second = load_lesson_bundle(
        _bundle(tmp_path, [_index_lesson(steps=[{"order": 1, "text": "Use the indexing tool."}])])
    )
    assert second == {"inserted": 0, "updated": 1, "invalid": 0}

    rows = AgentMemory.query.filter_by(type="lesson").all()
    assert len(rows) == 1
    payload = json.loads(rows[0].content)
    assert payload["steps"][0]["text"] == "Use the indexing tool."
    assert rows[0].source == "bundle"


def test_second_apply_does_not_duplicate(app, tmp_path):
    path = _bundle(tmp_path, [_index_lesson()])
    load_lesson_bundle(path)
    again = load_lesson_bundle(path)
    assert again["inserted"] == 0
    assert again["updated"] == 1
    assert AgentMemory.query.filter_by(type="lesson").count() == 1


def test_invalid_lessons_are_counted_and_skipped(app, tmp_path):
    counts = load_lesson_bundle(
        _bundle(
            tmp_path,
            [
                {"title": "", "steps": ["do a thing"]},
                {"title": "No steps", "steps": []},
                "not a dict",
                _index_lesson(),
            ],
        )
    )
    assert counts == {"inserted": 1, "updated": 0, "invalid": 3}
    assert AgentMemory.query.filter_by(type="lesson").count() == 1


def test_loaded_lesson_reaches_prompt(app, tmp_path):
    load_lesson_bundle(_bundle(tmp_path, [_index_lesson()]))
    out = get_memories_for_context(query="Index a folder of documents")
    assert "LESSON (Index a folder of documents)" in out
    assert "Index the folder with the document indexing tool." in out


def test_shipped_bundle_lesson_reaches_prompt(app):
    counts = load_lesson_bundle(str(SHIPPED_BUNDLE))
    assert counts["inserted"] == 2
    assert counts["invalid"] == 0

    out = get_memories_for_context(query="Index a folder of documents")
    assert "LESSON (Index a folder of documents)" in out
    assert "Index the folder with the document indexing tool." in out
    assert "Report how many files were indexed" in out

    again = load_lesson_bundle(str(SHIPPED_BUNDLE))
    assert again["inserted"] == 0
    assert again["updated"] == 2
    assert AgentMemory.query.filter_by(type="lesson").count() == 2
