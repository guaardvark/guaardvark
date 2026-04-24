#!/usr/bin/env python3
"""Tests for backend.api.lessons_api — Begin/End lesson bracket + distillation.

Covers the four routes (start, end, get, active) plus the module-level
ACTIVE_LESSONS registry, concurrency on start, and the in-memory
_distill_lesson_pearls helper (LLM call mocked).
"""

import os
import sys
import threading

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask
    from backend.models import db, ToolFeedback, AgentMemory, LLMMessage
    from backend.api.lessons_api import (
        lessons_bp,
        ACTIVE_LESSONS,
        get_active_lesson_id,
        _distill_lesson_pearls,
    )
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    """Flask app with in-memory SQLite and the lessons blueprint registered."""
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(lessons_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clear_active_lessons():
    """ACTIVE_LESSONS is module-level state; isolate each test."""
    ACTIVE_LESSONS.clear()
    yield
    ACTIVE_LESSONS.clear()


# ---------------------------------------------------------------------------
# POST /api/lessons/start
# ---------------------------------------------------------------------------

class TestStartLesson:
    def test_start_success_mints_lesson_id(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            resp = client.post("/api/lessons/start", json={"session_id": "sess-abc"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["lesson_id"]) == 32  # uuid4().hex

    def test_start_tracks_in_active_lessons(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            resp = client.post("/api/lessons/start", json={"session_id": "sess-track"})
        lesson_id = resp.get_json()["lesson_id"]
        assert "sess-track" in ACTIVE_LESSONS
        assert ACTIVE_LESSONS["sess-track"]["id"] == lesson_id
        assert ACTIVE_LESSONS["sess-track"]["session_id"] == "sess-track"

    def test_start_missing_session_id_returns_400(self, client):
        resp = client.post("/api/lessons/start", json={})
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_start_empty_session_id_returns_400(self, client):
        resp = client.post("/api/lessons/start", json={"session_id": "   "})
        assert resp.status_code == 400

    def test_start_when_already_active_returns_409(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            first = client.post("/api/lessons/start", json={"session_id": "sess-dup"})
            second = client.post("/api/lessons/start", json={"session_id": "sess-dup"})
        assert first.status_code == 200
        assert second.status_code == 409
        body = second.get_json()
        assert body["success"] is False
        # Conflict response echoes the existing lesson id
        assert body["lesson_id"] == first.get_json()["lesson_id"]

    def test_start_accepts_optional_title(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            client.post(
                "/api/lessons/start",
                json={"session_id": "sess-title", "title": "  Teach Subscribe  "},
            )
        # Whitespace stripped on store
        assert ACTIVE_LESSONS["sess-title"]["title"] == "Teach Subscribe"

    def test_start_empty_title_stored_as_none(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            client.post("/api/lessons/start", json={"session_id": "sess-notitle", "title": "   "})
        assert ACTIVE_LESSONS["sess-notitle"]["title"] is None

    def test_start_socketio_emit_failure_is_swallowed(self, client):
        """Emit is best-effort — failure must not break start."""
        with patch("backend.socketio_events.emit_lesson_event", side_effect=RuntimeError("boom")):
            resp = client.post("/api/lessons/start", json={"session_id": "sess-noemit"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_start_concurrent_same_session_only_one_wins(self, app):
        """Two threads starting the same session_id simultaneously: one 200, one 409."""
        results = []

        def worker():
            with app.test_client() as c, patch("backend.socketio_events.emit_lesson_event"):
                r = c.post("/api/lessons/start", json={"session_id": "sess-race"})
                results.append(r.status_code)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(200) == 1
        assert results.count(409) == 4


# ---------------------------------------------------------------------------
# GET /api/lessons/active
# ---------------------------------------------------------------------------

class TestGetActiveForSession:
    def test_active_returns_record_when_present(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            start = client.post("/api/lessons/start", json={"session_id": "sess-live"})
        resp = client.get("/api/lessons/active?session_id=sess-live")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["active"] is True
        assert data["id"] == start.get_json()["lesson_id"]

    def test_active_none_when_no_lesson(self, client):
        resp = client.get("/api/lessons/active?session_id=sess-empty")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["active"] is False
        assert data["lesson_id"] is None

    def test_active_missing_session_id_returns_400(self, client):
        resp = client.get("/api/lessons/active")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/lessons/<id>
# ---------------------------------------------------------------------------

class TestGetLesson:
    def test_get_lesson_returns_pearls_in_order(self, client, app):
        with app.app_context():
            for i, text in enumerate(["click Search", "type Guaardvark", "press Enter"]):
                fb = ToolFeedback(
                    session_id="sess-g",
                    lesson_id="lesson-x",
                    tool_name="agent_task_execute",
                    task=text,
                    positive=True,
                )
                db.session.add(fb)
            db.session.commit()

        resp = client.get("/api/lessons/lesson-x")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["lesson_id"] == "lesson-x"
        assert data["active"] is False
        assert len(data["pearls"]) == 3
        # created_at asc → insertion order preserved
        assert [p["task"] for p in data["pearls"]] == ["click Search", "type Guaardvark", "press Enter"]

    def test_get_lesson_reports_active_flag(self, client):
        with patch("backend.socketio_events.emit_lesson_event"):
            lid = client.post(
                "/api/lessons/start", json={"session_id": "sess-act"}
            ).get_json()["lesson_id"]
        resp = client.get(f"/api/lessons/{lid}")
        assert resp.get_json()["active"] is True

    def test_get_lesson_empty_for_unknown_id_still_200(self, client):
        # By design: returns empty pearls rather than 404 — UI can distinguish
        # "never existed" from "expired" via the pearls count.
        resp = client.get("/api/lessons/ghost-id")
        assert resp.status_code == 200
        assert resp.get_json()["pearls"] == []


# ---------------------------------------------------------------------------
# POST /api/lessons/<id>/end
# ---------------------------------------------------------------------------

class TestEndLesson:
    def _seed_pearls(self, app, lesson_id, session_id, tasks):
        with app.app_context():
            for t in tasks:
                db.session.add(ToolFeedback(
                    session_id=session_id,
                    lesson_id=lesson_id,
                    tool_name="agent_task_execute",
                    task=t,
                    positive=True,
                ))
            db.session.commit()

    def test_end_happy_path_creates_memory(self, client, app):
        self._seed_pearls(app, "lesson-h", "sess-h", ["open YT", "search Guaardvark", "click Subscribe"])

        with patch("backend.socketio_events.emit_lesson_event"), \
             patch("requests.post") as mock_post, \
             patch("backend.api.lessons_api.ACTIVE_LESSONS", {"sess-h": {"id": "lesson-h", "session_id": "sess-h"}}):
            mock_post.return_value = MagicMock(
                json=lambda: {"response": '{"title": "Subscribe to {channel}", "steps": [{"order": 1, "text": "open YT"}, {"order": 2, "text": "search {channel}"}, {"order": 3, "text": "click Subscribe"}], "parameters": [{"name": "channel", "description": "channel name", "example": "Guaardvark"}]}'},
                raise_for_status=lambda: None,
            )
            resp = client.post("/api/lessons/lesson-h/end")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["summary"]["title"] == "Subscribe to {channel}"
        assert len(body["summary"]["steps"]) == 3
        assert body["summary"]["parameters"][0]["name"] == "channel"
        assert "memory_id" in body

        # Memory persisted with source=lesson_summary
        with app.app_context():
            mem = db.session.query(AgentMemory).filter_by(id=body["memory_id"]).first()
            assert mem is not None
            assert mem.source == "lesson_summary"
            assert mem.session_id == "lesson-h"  # lesson-id reused as key

    def test_end_removes_from_active_lessons(self, client, app):
        self._seed_pearls(app, "lesson-r", "sess-r", ["step a"])
        ACTIVE_LESSONS["sess-r"] = {"id": "lesson-r", "session_id": "sess-r"}

        with patch("backend.socketio_events.emit_lesson_event"), \
             patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"response": '{"title":"t","steps":[{"order":1,"text":"step a"}]}'},
                raise_for_status=lambda: None,
            )
            client.post("/api/lessons/lesson-r/end")

        assert "sess-r" not in ACTIVE_LESSONS

    def test_end_after_restart_recovers_via_pearl_lookup(self, client, app):
        """Registry is empty (backend restarted) but pearls exist → still works."""
        self._seed_pearls(app, "lesson-restart", "sess-restart", ["step one"])
        # Deliberately leave ACTIVE_LESSONS empty

        with patch("backend.socketio_events.emit_lesson_event"), \
             patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"response": '{"title":"recovered","steps":[{"order":1,"text":"step one"}]}'},
                raise_for_status=lambda: None,
            )
            resp = client.post("/api/lessons/lesson-restart/end")

        assert resp.status_code == 200
        assert resp.get_json()["summary"]["title"] == "recovered"

    def test_end_unknown_lesson_returns_404(self, client):
        resp = client.post("/api/lessons/nonexistent-id/end")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    def test_end_with_no_positive_pearls_returns_422(self, client, app):
        """Only negative pearls → distiller returns None → 422."""
        with app.app_context():
            db.session.add(ToolFeedback(
                session_id="sess-neg", lesson_id="lesson-neg",
                tool_name="t", task="nope", positive=False,
            ))
            db.session.commit()
        ACTIVE_LESSONS["sess-neg"] = {"id": "lesson-neg", "session_id": "sess-neg"}

        with patch("backend.socketio_events.emit_lesson_event"):
            resp = client.post("/api/lessons/lesson-neg/end")
        assert resp.status_code == 422

    def test_end_falls_back_when_llm_returns_garbage(self, client, app):
        """Unparseable LLM output → synthesize summary from raw pearl text."""
        self._seed_pearls(app, "lesson-fb", "sess-fb", ["tap A", "tap B"])
        ACTIVE_LESSONS["sess-fb"] = {"id": "lesson-fb", "session_id": "sess-fb"}

        with patch("backend.socketio_events.emit_lesson_event"), \
             patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"response": "I am not JSON at all"},
                raise_for_status=lambda: None,
            )
            resp = client.post("/api/lessons/lesson-fb/end")

        assert resp.status_code == 200
        steps = resp.get_json()["summary"]["steps"]
        assert [s["text"] for s in steps] == ["tap A", "tap B"]

    def test_end_falls_back_when_llm_call_raises(self, client, app):
        """LLM unreachable → fallback path still produces a summary."""
        self._seed_pearls(app, "lesson-down", "sess-down", ["only step"])
        ACTIVE_LESSONS["sess-down"] = {"id": "lesson-down", "session_id": "sess-down"}

        with patch("backend.socketio_events.emit_lesson_event"), \
             patch("requests.post", side_effect=ConnectionError("nope")):
            resp = client.post("/api/lessons/lesson-down/end")

        assert resp.status_code == 200
        assert resp.get_json()["summary"]["steps"][0]["text"] == "only step"


# ---------------------------------------------------------------------------
# get_active_lesson_id() helper
# ---------------------------------------------------------------------------

class TestGetActiveLessonIdHelper:
    def test_returns_id_when_active(self):
        ACTIVE_LESSONS["sess-h1"] = {"id": "l-h1", "session_id": "sess-h1"}
        assert get_active_lesson_id("sess-h1") == "l-h1"

    def test_returns_none_when_inactive(self):
        assert get_active_lesson_id("sess-missing") is None

    def test_returns_none_for_empty_input(self):
        assert get_active_lesson_id("") is None
        assert get_active_lesson_id(None) is None


# ---------------------------------------------------------------------------
# _distill_lesson_pearls — direct tests (no HTTP)
# ---------------------------------------------------------------------------

class TestDistillPearls:
    def test_normalizes_steps_and_parameters(self, app):
        with app.app_context():
            db.session.add(ToolFeedback(
                session_id="s", lesson_id="L1", tool_name="t",
                task="click Search", positive=True,
            ))
            db.session.add(ToolFeedback(
                session_id="s", lesson_id="L1", tool_name="t",
                task="type {query}", positive=True,
            ))
            db.session.commit()

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"response": '{"title":"Search {query}","steps":[{"order":1,"text":"click Search"},{"order":2,"text":"type {query}"}],"parameters":[{"name":"query","description":"search term","example":"guaardvark"}]}'},
                raise_for_status=lambda: None,
            )
            out = _distill_lesson_pearls(app, "L1", "s")

        assert out is not None
        assert out["title"] == "Search {query}"
        assert [s["order"] for s in out["steps"]] == [1, 2]
        assert out["parameters"][0]["name"] == "query"

    def test_clamps_overlong_title_and_step_text(self, app):
        with app.app_context():
            db.session.add(ToolFeedback(
                session_id="s", lesson_id="L2", tool_name="t",
                task="x", positive=True,
            ))
            db.session.commit()

        long_title = "T" * 300
        long_step = "S" * 500
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"response": f'{{"title":"{long_title}","steps":[{{"order":1,"text":"{long_step}"}}]}}'},
                raise_for_status=lambda: None,
            )
            out = _distill_lesson_pearls(app, "L2", "s")

        assert len(out["title"]) == 120
        assert len(out["steps"][0]["text"]) == 300

    def test_parameter_dedup_by_name(self, app):
        with app.app_context():
            db.session.add(ToolFeedback(
                session_id="s", lesson_id="L3", tool_name="t", task="x", positive=True,
            ))
            db.session.commit()

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                json=lambda: {"response": '{"title":"T","steps":[{"order":1,"text":"s"}],"parameters":[{"name":"a","description":"first"},{"name":"A","description":"dup"},{"name":"b","description":"second"}]}'},
                raise_for_status=lambda: None,
            )
            out = _distill_lesson_pearls(app, "L3", "s")

        names = [p["name"] for p in out["parameters"]]
        assert names == ["a", "b"]

    def test_returns_none_when_no_pearls(self, app):
        """No ToolFeedback rows for this lesson → no distillation attempt."""
        out = _distill_lesson_pearls(app, "empty-lesson", "s")
        assert out is None
