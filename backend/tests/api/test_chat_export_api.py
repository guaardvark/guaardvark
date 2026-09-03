"""HTTP contract and file-layout tests for the chat export."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

try:
    from flask import Flask

    from backend.api.chat_sessions_api import chat_sessions_bp
    from backend.models import LLMMessage, LLMSession, LLMSessionSummary, db
    from backend.services import chat_export_service
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app(tmp_path, monkeypatch):
    application = Flask(__name__)
    application.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        }
    )
    db.init_app(application)
    application.register_blueprint(chat_sessions_bp)
    monkeypatch.setattr(chat_export_service.config, "OUTPUT_DIR", str(tmp_path / "outputs"))
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed(app):
    base = datetime(2026, 9, 3, 12, 0, 0)
    with app.app_context():
        first = LLMSession(id="sess-one", user="default", mode="chat", created_at=base)
        second = LLMSession(id="sess/two", user="default", mode="agent", created_at=base + timedelta(minutes=5))
        empty = LLMSession(id="sess-empty", user="default", mode="chat", created_at=base + timedelta(minutes=9))
        db.session.add_all([first, second, empty])
        db.session.flush()
        db.session.add_all(
            [
                LLMMessage(session_id=first.id, role="user", content="How much ice and water for 30 squares?\nSecond line", timestamp=base + timedelta(seconds=1)),
                LLMMessage(session_id=first.id, role="assistant", content="About 3 rolls at eaves only.", extra_data={"model": "gemma"}, timestamp=base + timedelta(seconds=2)),
                LLMMessage(session_id=second.id, role="system", content="context", timestamp=base + timedelta(minutes=5, seconds=1)),
                LLMMessage(session_id=second.id, role="user", content="Run the takeoff", timestamp=base + timedelta(minutes=5, seconds=2)),
            ]
        )
        db.session.add(LLMSessionSummary(session_id=first.id, summary="Talked about ice and water.", message_count=2))
        db.session.commit()


def test_export_writes_index_and_one_pair_of_files_per_session(app, client, tmp_path):
    _seed(app)

    response = client.post("/api/chat-sessions/export")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["sessions"] == 3
    assert body["messages"] == 4
    assert body["relative_directory"].startswith("chat-exports/chats-")
    assert body["directory"].startswith(str(tmp_path / "outputs" / "chat-exports"))

    with open(os.path.join(body["directory"], "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    assert index["session_count"] == 3
    assert index["message_count"] == 4
    assert [entry["id"] for entry in index["sessions"]] == ["sess-one", "sess/two", "sess-empty"]

    files = sorted(os.listdir(os.path.join(body["directory"], "sessions")))
    assert files == [
        "sess-empty.json", "sess-empty.md",
        "sess-one.json", "sess-one.md",
        "sess_two.json", "sess_two.md",
    ]


def test_session_json_carries_ordered_messages_summaries_and_title(app, client):
    _seed(app)
    body = client.post("/api/chat-sessions/export").get_json()

    with open(os.path.join(body["directory"], "sessions", "sess-one.json"), encoding="utf-8") as fh:
        session = json.load(fh)
    assert session["title"] == "How much ice and water for 30 squares?"
    assert session["mode"] == "chat"
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]
    assert session["messages"][1]["extra_data"] == {"model": "gemma"}
    assert session["summaries"][0]["summary"] == "Talked about ice and water."
    assert session["first_message_at"] < session["last_message_at"]

    with open(os.path.join(body["directory"], "sessions", "sess-one.md"), encoding="utf-8") as fh:
        markdown = fh.read()
    assert markdown.startswith("# How much ice and water for 30 squares?")
    assert "## user" in markdown and "## assistant" in markdown
    assert "About 3 rolls at eaves only." in markdown
    assert "## Rolling summaries" in markdown

    with open(os.path.join(body["directory"], "sessions", "sess-empty.json"), encoding="utf-8") as fh:
        empty = json.load(fh)
    assert empty["title"] == "(empty session)"
    assert empty["messages"] == []


def test_export_with_no_sessions_still_writes_an_index(client):
    body = client.post("/api/chat-sessions/export").get_json()

    assert body["success"] is True
    assert body["sessions"] == 0
    assert body["messages"] == 0
    with open(os.path.join(body["directory"], "index.json"), encoding="utf-8") as fh:
        assert json.load(fh)["sessions"] == []


def test_export_failure_returns_500(app, client, monkeypatch):
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(chat_export_service, "export_chats", boom)

    response = client.post("/api/chat-sessions/export")

    assert response.status_code == 500
    assert response.get_json() == {"success": False, "error": "disk full"}
