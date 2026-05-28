import sys
from types import SimpleNamespace


class FakeDbSession:
    def __init__(self, sessions):
        self._sessions = sessions

    def get(self, _model, session_id):
        return self._sessions.get(session_id)


def install_fake_models(monkeypatch, sessions):
    fake_models = SimpleNamespace(
        LLMSession=object,
        db=SimpleNamespace(session=FakeDbSession(sessions)),
    )
    monkeypatch.setitem(sys.modules, "backend.models", fake_models)


def test_agent_session_mode_enables_agent_screen_options(monkeypatch):
    from backend.api.unified_chat_api import _merge_session_mode_options

    install_fake_models(
        monkeypatch,
        {"session_agent": SimpleNamespace(mode="agent")},
    )

    options = _merge_session_mode_options(
        "session_agent",
        {"agent_screen_active": False, "screen_viewer_open": False},
    )

    assert options["session_mode"] == "agent"
    assert options["agent_screen_active"] is True
    assert options["screen_viewer_open"] is False


def test_chat_session_mode_preserves_client_agent_screen_option(monkeypatch):
    from backend.api.unified_chat_api import _merge_session_mode_options

    install_fake_models(
        monkeypatch,
        {"session_chat": SimpleNamespace(mode="chat")},
    )

    options = _merge_session_mode_options(
        "session_chat",
        {"agent_screen_active": True},
    )

    assert options["session_mode"] == "chat"
    assert options["agent_screen_active"] is True


def test_missing_session_defaults_to_chat_without_forcing_agent_screen(monkeypatch):
    from backend.api.unified_chat_api import _merge_session_mode_options

    install_fake_models(monkeypatch, {})

    options = _merge_session_mode_options(
        "missing_session",
        {"agent_screen_active": False},
    )

    assert options["session_mode"] == "chat"
    assert options["agent_screen_active"] is False
