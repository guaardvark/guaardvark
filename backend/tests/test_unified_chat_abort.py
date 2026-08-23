"""Tests for unified chat hard abort and in-flight slot release."""

import threading
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def app_ctx():
    app = Flask("test_unified_chat_abort")
    with app.app_context():
        yield app


@pytest.fixture(autouse=True)
def _clear_inflight():
    from backend.api import unified_chat_api as api

    with api._inflight_lock:
        api._inflight.clear()
    yield
    with api._inflight_lock:
        api._inflight.clear()


def test_abort_clears_inflight_slot(app_ctx):
    from backend.api import unified_chat_api as api

    session_id = "abort-test-session"
    blocker = threading.Event()

    def _wedge():
        blocker.wait(timeout=30)

    thread = threading.Thread(target=_wedge, daemon=True)
    with api._inflight_lock:
        api._inflight[session_id] = thread
    thread.start()

    with patch("backend.services.unified_chat_engine.set_abort_flag") as mock_flag, \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio") as mock_sio:
        mock_svc.return_value = MagicMock(_active=False)
        result = api.abort_chat(session_id)

    blocker.set()
    assert result.get_json()["success"] is True
    assert result.get_json()["inflight_cleared"] is True
    mock_flag.assert_called_once_with(session_id)
    mock_sio.emit.assert_called_once_with(
        "chat:aborted", {"session_id": session_id}, room=session_id
    )
    with api._inflight_lock:
        assert session_id not in api._inflight


def test_abort_when_no_inflight_still_succeeds(app_ctx):
    from backend.api import unified_chat_api as api

    session_id = "empty-abort-session"
    with patch("backend.services.unified_chat_engine.set_abort_flag"), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio"):
        mock_svc.return_value = MagicMock(_active=False)
        result = api.abort_chat(session_id)

    assert result.get_json()["success"] is True
    assert result.get_json()["inflight_cleared"] is False


def test_inflight_cleared_allows_new_claim(app_ctx):
    """After hard abort, a new thread can claim the same session slot."""
    from backend.api import unified_chat_api as api

    session_id = "reclaim-session"
    blocker = threading.Event()

    def _wedge():
        blocker.wait(timeout=30)

    old = threading.Thread(target=_wedge, daemon=True)
    with api._inflight_lock:
        api._inflight[session_id] = old
    old.start()

    with patch("backend.services.unified_chat_engine.set_abort_flag"), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio"):
        mock_svc.return_value = MagicMock(_active=False)
        api.abort_chat(session_id)

    new = threading.Thread(target=lambda: None, daemon=True)
    with api._inflight_lock:
        existing = api._inflight.get(session_id)
        assert existing is None or not existing.is_alive()
        api._inflight[session_id] = new

    blocker.set()
    with api._inflight_lock:
        assert api._inflight.get(session_id) is new
