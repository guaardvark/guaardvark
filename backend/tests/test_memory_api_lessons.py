#!/usr/bin/env python3
"""Tests for today's additions to backend.api.memory_api:

  - PATCH /api/memory/<id>           (edit in place)
  - get_memories_for_context()       — lesson_summary flattening with
                                        PARAMETERS tail + budget handling
  - Backward-compat: non-lesson memories still use the 300-char truncation
"""

import os
import sys
import json as _json

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask
    from backend.models import db, AgentMemory
    from backend.api.memory_api import memory_bp, get_memories_for_context
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(memory_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _mem(app, mem_id, content, source="manual", importance=0.5, tags=None):
    with app.app_context():
        m = AgentMemory(
            id=mem_id,
            content=content,
            source=source,
            importance=importance,
            tags=_json.dumps(tags) if tags else None,
        )
        db.session.add(m)
        db.session.commit()
    return mem_id


# ---------------------------------------------------------------------------
# PATCH /api/memory/<id>
# ---------------------------------------------------------------------------

class TestPatchMemory:
    def test_patch_updates_content(self, app, client):
        _mem(app, "m-1", "old content")
        resp = client.patch("/api/memory/m-1", json={"content": "new content"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["memory"]["content"] == "new content"

        with app.app_context():
            m = db.session.query(AgentMemory).filter_by(id="m-1").first()
            assert m.content == "new content"

    def test_patch_rejects_empty_content(self, app, client):
        _mem(app, "m-empty", "original")
        resp = client.patch("/api/memory/m-empty", json={"content": "   "})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False
        # Original unchanged
        with app.app_context():
            assert db.session.query(AgentMemory).filter_by(id="m-empty").first().content == "original"

    def test_patch_updates_tags_as_json_array(self, app, client):
        _mem(app, "m-tags", "c", tags=["a"])
        resp = client.patch("/api/memory/m-tags", json={"tags": ["b", "c"]})
        assert resp.status_code == 200
        with app.app_context():
            m = db.session.query(AgentMemory).filter_by(id="m-tags").first()
            assert _json.loads(m.tags) == ["b", "c"]

    def test_patch_empty_tags_stores_null(self, app, client):
        _mem(app, "m-tags-null", "c", tags=["a"])
        resp = client.patch("/api/memory/m-tags-null", json={"tags": []})
        assert resp.status_code == 200
        with app.app_context():
            m = db.session.query(AgentMemory).filter_by(id="m-tags-null").first()
            assert m.tags is None

    def test_patch_updates_type_and_importance(self, app, client):
        _mem(app, "m-ti", "c", importance=0.5)
        resp = client.patch("/api/memory/m-ti", json={"type": "fact", "importance": 0.9})
        assert resp.status_code == 200
        with app.app_context():
            m = db.session.query(AgentMemory).filter_by(id="m-ti").first()
            assert m.type == "fact"
            assert abs(m.importance - 0.9) < 1e-6

    def test_patch_rejects_non_numeric_importance(self, app, client):
        _mem(app, "m-imp-bad", "c", importance=0.5)
        resp = client.patch("/api/memory/m-imp-bad", json={"importance": "not-a-number"})
        assert resp.status_code == 400
        # Original importance preserved
        with app.app_context():
            assert db.session.query(AgentMemory).filter_by(id="m-imp-bad").first().importance == 0.5

    def test_patch_accepts_numeric_string_importance(self, app, client):
        _mem(app, "m-imp-str", "c", importance=0.5)
        resp = client.patch("/api/memory/m-imp-str", json={"importance": "0.7"})
        assert resp.status_code == 200
        with app.app_context():
            assert abs(db.session.query(AgentMemory).filter_by(id="m-imp-str").first().importance - 0.7) < 1e-6

    def test_patch_ignores_missing_optional_fields(self, app, client):
        """PATCH only updates keys present in the body."""
        _mem(app, "m-p", "original", importance=0.3)
        resp = client.patch("/api/memory/m-p", json={"content": "just content"})
        assert resp.status_code == 200
        with app.app_context():
            m = db.session.query(AgentMemory).filter_by(id="m-p").first()
            assert m.content == "just content"
            assert m.importance == 0.3  # unchanged

    def test_patch_404_for_unknown_id(self, client):
        resp = client.patch("/api/memory/ghost", json={"content": "x"})
        assert resp.status_code == 404

    def test_patch_empty_body_no_ops_successfully(self, app, client):
        """Empty body is silent no-op + bumps updated_at — don't 400 on it."""
        _mem(app, "m-noop", "unchanged")
        resp = client.patch("/api/memory/m-noop", json={})
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.query(AgentMemory).filter_by(id="m-noop").first().content == "unchanged"


# ---------------------------------------------------------------------------
# get_memories_for_context — lesson_summary flattening
# ---------------------------------------------------------------------------

class TestLessonFlattening:
    def _seed_lesson(self, app, mem_id, payload, importance=0.9):
        with app.app_context():
            db.session.add(AgentMemory(
                id=mem_id,
                content=_json.dumps(payload),
                source="lesson_summary",
                importance=importance,
            ))
            db.session.commit()

    def test_lesson_flattened_with_title_and_ordered_steps(self, app):
        self._seed_lesson(app, "L-1", {
            "title": "Subscribe to {channel}",
            "steps": [
                {"order": 2, "text": "search {channel}"},
                {"order": 1, "text": "open YouTube"},
                {"order": 3, "text": "click Subscribe"},
            ],
            "parameters": [],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        # Title echoed exactly
        assert "LESSON (Subscribe to {channel})" in out
        # Steps in order 1 → 2 → 3 joined with -> separator
        assert "1. open YouTube -> 2. search {channel} -> 3. click Subscribe" in out

    def test_lesson_parameters_line_appended(self, app):
        self._seed_lesson(app, "L-2", {
            "title": "Search for {query}",
            "steps": [{"order": 1, "text": "type {query}"}],
            "parameters": [
                {"name": "query", "description": "search term", "example": "guaardvark"},
            ],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        assert "PARAMETERS:" in out
        assert "{query}" in out
        assert "search term" in out
        assert "e.g. guaardvark" in out

    def test_lesson_parameter_without_example_omits_eg(self, app):
        self._seed_lesson(app, "L-3", {
            "title": "T",
            "steps": [{"order": 1, "text": "do thing"}],
            "parameters": [{"name": "slot", "description": "desc only"}],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        assert "{slot} (desc only)" in out
        assert "e.g." not in out

    def test_lesson_parameter_without_name_dropped(self, app):
        self._seed_lesson(app, "L-4", {
            "title": "T",
            "steps": [{"order": 1, "text": "do"}],
            "parameters": [{"name": "", "description": "ignored"}, {"name": "valid"}],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        # "ignored" param should not appear
        assert "ignored" not in out
        assert "{valid}" in out

    def test_lesson_no_parameters_omits_parameters_tail(self, app):
        self._seed_lesson(app, "L-noparam", {
            "title": "Simple",
            "steps": [{"order": 1, "text": "a"}],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        assert "LESSON (Simple)" in out
        assert "PARAMETERS:" not in out

    def test_malformed_lesson_json_falls_back_to_truncation(self, app):
        """Lesson marked as lesson_summary but content isn't valid JSON → fall back to raw truncation (not crash)."""
        with app.app_context():
            db.session.add(AgentMemory(
                id="L-bad",
                content="this is not json " + ("x" * 500),
                source="lesson_summary",
                importance=0.9,
            ))
            db.session.commit()
            out = get_memories_for_context(limit=5, max_tokens=500)
        # Falls back to the 300-char cap
        assert "..." in out
        assert "LESSON (" not in out
        # Header always present
        assert "User's saved memories" in out

    def test_non_lesson_memory_uses_300_char_truncation(self, app):
        """Regression check: plain memories must not go through the lesson branch."""
        with app.app_context():
            db.session.add(AgentMemory(
                id="plain-1",
                content="x" * 400,
                source="manual",
                importance=0.9,
            ))
            db.session.commit()
            out = get_memories_for_context(limit=5, max_tokens=500)
        # 297 chars + "..."
        assert "x" * 297 + "..." in out

    def test_empty_db_returns_empty_string(self, app):
        with app.app_context():
            assert get_memories_for_context(limit=5, max_tokens=500) == ""

    def test_step_order_missing_uses_position(self, app):
        """Steps without `order` key → use list position, no crash."""
        self._seed_lesson(app, "L-noorder", {
            "title": "T",
            "steps": [{"text": "first"}, {"text": "second"}],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        # None falls back to 0 in sort key — both stay at 0, stable order
        assert "first" in out
        assert "second" in out

    def test_string_step_is_accepted(self, app):
        """Distiller usually emits {order,text} dicts, but string steps shouldn't crash."""
        self._seed_lesson(app, "L-strstep", {
            "title": "T",
            "steps": ["just a string", {"order": 2, "text": "real one"}],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=500)
        assert "just a string" in out
        assert "real one" in out

    def test_lesson_content_budget_caps_at_1200(self, app):
        """A lesson with many long steps gets clamped to 1200 chars, not 300."""
        self._seed_lesson(app, "L-big", {
            "title": "Big",
            "steps": [{"order": i, "text": "step text " + "y" * 200} for i in range(1, 20)],
        })
        with app.app_context():
            out = get_memories_for_context(limit=5, max_tokens=2000)  # generous budget
        # Find the flattened line — strip header
        body = out.split("\n", 1)[1]
        # Line format: "- LESSON ...", so the memory content is body[2:]
        content_line = body[2:] if body.startswith("- ") else body
        # Either clamped at 1200 (exact) with trailing ..., or under because steps fit
        assert len(content_line) <= 1200
        if len(content_line) == 1200:
            assert content_line.endswith("...")
