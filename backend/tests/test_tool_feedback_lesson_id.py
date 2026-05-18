#!/usr/bin/env python3
"""Tests for the ToolFeedback.lesson_id column added in migration 004.

Model-layer only — just the column, indexing, nullability, and to_dict()
echoing. The API consumers (lessons_api) are covered elsewhere.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask
    from backend.models import db, ToolFeedback
except Exception:
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


class TestLessonIdColumn:
    def test_lesson_id_is_nullable(self, app):
        """Backfilled rows must remain valid — lesson_id is optional."""
        with app.app_context():
            fb = ToolFeedback(
                session_id="s", tool_name="t", task="go", positive=True,
            )
            db.session.add(fb)
            db.session.commit()

            fetched = db.session.query(ToolFeedback).filter_by(id=fb.id).first()
            assert fetched.lesson_id is None

    def test_lesson_id_persists(self, app):
        with app.app_context():
            fb = ToolFeedback(
                session_id="s", tool_name="t", task="go",
                positive=True, lesson_id="L-abc",
            )
            db.session.add(fb)
            db.session.commit()

            fetched = db.session.query(ToolFeedback).filter_by(id=fb.id).first()
            assert fetched.lesson_id == "L-abc"

    def test_to_dict_includes_lesson_id(self, app):
        with app.app_context():
            fb = ToolFeedback(
                session_id="s", tool_name="t", task="go",
                positive=True, lesson_id="L-1",
            )
            db.session.add(fb)
            db.session.commit()
            d = fb.to_dict()
        assert "lesson_id" in d
        assert d["lesson_id"] == "L-1"

    def test_to_dict_lesson_id_none_when_unset(self, app):
        with app.app_context():
            fb = ToolFeedback(session_id="s", tool_name="t", task="go", positive=True)
            db.session.add(fb)
            db.session.commit()
            d = fb.to_dict()
        assert d["lesson_id"] is None

    def test_query_by_lesson_id_finds_group(self, app):
        """lessons_api orders pearls by created_at for a given lesson_id —
        prove the filter actually partitions correctly."""
        with app.app_context():
            for i in range(3):
                db.session.add(ToolFeedback(
                    session_id="s", tool_name="t", task=f"p{i}",
                    positive=True, lesson_id="L-this",
                ))
            # Noise in the same table that must NOT come back
            for i in range(2):
                db.session.add(ToolFeedback(
                    session_id="s", tool_name="t", task=f"other{i}",
                    positive=True, lesson_id="L-other",
                ))
            db.session.add(ToolFeedback(
                session_id="s", tool_name="t", task="no-lesson",
                positive=True, lesson_id=None,
            ))
            db.session.commit()

            rows = db.session.query(ToolFeedback).filter_by(lesson_id="L-this").all()

        assert len(rows) == 3
        assert {r.task for r in rows} == {"p0", "p1", "p2"}

    def test_lesson_id_column_is_indexed(self):
        """Migration 004 adds ix_tool_feedback_lesson_id. Verify the column
        is marked indexed in the model (SQLite driver honors index=True)."""
        col = ToolFeedback.__table__.columns["lesson_id"]
        assert col.index is True
        assert col.nullable is True
