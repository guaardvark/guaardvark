"""Deterministic chat auto-capture: intents, rejects, dedupe, engine wrap."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.db]

try:
    from flask import Flask

    from backend.models import AgentMemory, db
    from backend.services.memory_capture import capture_from_message
except Exception:  # pragma: no cover - environment without backend deps
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

ENGINE_PATH = (
    Path(__file__).resolve().parents[2] / "services" / "unified_chat_engine.py"
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


@pytest.mark.parametrize(
    "message, expected",
    [
        ("remember that the sky is blue", "the sky is blue"),
        ("Remember that the sky is blue", "the sky is blue"),
        ("remember: the sky is blue", "the sky is blue"),
        ("Remember: the sky is blue", "the sky is blue"),
        ("note that we prefer pytest", "we prefer pytest"),
        ("Note that we prefer pytest", "we prefer pytest"),
        ("from now on use dark mode", "use dark mode"),
        ("From now on, always use dark mode", "always use dark mode"),
        ("for future reference the API lives in settings", "the API lives in settings"),
        ("For future reference, the API lives in settings", "the API lives in settings"),
        ("my name is Alice", "my name is Alice"),
        ("our timezone is Pacific", "our timezone is Pacific"),
    ],
)
def test_intent_forms_store_a_chat_fact(app, message, expected):
    mem_id = capture_from_message(message, session_id="s1", user_id="u1")
    assert mem_id is not None
    row = db.session.get(AgentMemory, mem_id)
    assert row is not None
    assert row.content == expected
    assert row.type == "fact"
    assert row.source == "chat"
    assert abs((row.importance or 0) - 0.7) < 1e-6
    assert row.session_id == "s1"
    assert row.user_id == "u1"


@pytest.mark.parametrize(
    "message",
    [
        "remember that the sky is blue?",
        "note that we prefer pytest?",
        "is my name Alice?",
        "what is our timezone?",
    ],
)
def test_questions_are_not_captured(app, message):
    assert capture_from_message(message) is None
    assert AgentMemory.query.count() == 0


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "remember that x",
        "note that hi",
        "my name is",
        "ok thanks",
    ],
)
def test_short_messages_are_not_captured(app, message):
    assert capture_from_message(message) is None
    assert AgentMemory.query.count() == 0


def test_plain_chat_without_intent_is_ignored(app):
    assert capture_from_message("please index the docs folder now") is None
    assert AgentMemory.query.count() == 0


def test_dedupe_returns_existing_id(app):
    first = capture_from_message("remember that the sky is blue")
    second = capture_from_message("Remember that the sky is blue.")
    assert first is not None
    assert second == first
    assert AgentMemory.query.filter_by(type="fact").count() == 1


def test_engine_call_site_is_exception_safe(monkeypatch):
    """Capture sits in a try/except Exception immediately after the user save."""
    text = ENGINE_PATH.read_text()
    marker = "# 5. Save user message to DB"
    idx = text.find(marker)
    assert idx != -1
    window = text[idx : idx + 900]
    assert "self._save_message" in window
    save_at = window.find("self._save_message")
    capture_at = window.find("capture_from_message")
    except_at = window.find("except Exception")
    assert capture_at != -1
    assert except_at != -1
    assert save_at < capture_at < except_at

    from backend.services import memory_capture as mc

    def boom(*_a, **_k):
        raise RuntimeError("storage down")

    monkeypatch.setattr(mc, "capture_from_message", boom)

    try:
        from backend.services.memory_capture import capture_from_message as _capture

        _capture("remember that the sky is blue")
        raised = False
    except Exception:
        raised = True
    # The raw helper may raise; the engine wrap must not. Reproduce that wrap.
    assert raised is True

    logged = {"hit": False}

    def engine_call_site(message):
        try:
            from backend.services.memory_capture import capture_from_message

            capture_from_message(message)
        except Exception:
            logged["hit"] = True

    engine_call_site("remember that the sky is blue")
    assert logged["hit"] is True
