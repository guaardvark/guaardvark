"""Stop, or a new message, answers a waiting approval card with no.

Before, the stopped turn held its thread until APPROVAL_TIMEOUT_S (300 s) ran
out. Drives the real ``_await_tool_approval``; the emit callback is the only
stand-in.
"""

import threading
import time

import pytest

import backend.services.unified_chat_engine as uce
from backend.services.agent_tools import ToolRegistry

SID = "approval-abort"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # Long enough that only the stop can end the wait inside the test's budget.
    monkeypatch.setattr(uce, "APPROVAL_TIMEOUT_S", 30)
    for d in (uce._approval_events, uce._approval_responses, uce._approval_batch_meta,
              uce._session_tool_grants, uce._task_tool_grants, uce._stop_counts,
              uce._newest_turn_marks):
        d.pop(SID, None)
    yield
    for d in (uce._approval_events, uce._approval_responses, uce._approval_batch_meta,
              uce._session_tool_grants, uce._task_tool_grants, uce._stop_counts,
              uce._newest_turn_marks):
        d.pop(SID, None)


def _wait_for_card():
    engine = uce.UnifiedChatEngine(ToolRegistry(), object())
    shown = threading.Event()
    result = {}

    def emit(event, payload):
        if event == "chat:tool_approval_request":
            shown.set()

    t = threading.Thread(target=lambda: result.update(
        ok=engine._await_tool_approval(SID, emit, "req-1", 1, [{"tool": "system_command", "params": {}}])))
    t.start()
    assert shown.wait(2)
    return t, result


@pytest.mark.parametrize("stop", [uce.set_abort_flag, uce.begin_new_turn])
def test_a_stop_declines_the_waiting_card_at_once(stop):
    t, result = _wait_for_card()
    started = time.monotonic()
    stop(SID)
    t.join(2)
    assert not t.is_alive()
    assert result["ok"] is False
    assert time.monotonic() - started < 2


def test_an_answer_after_the_stop_grants_nothing():
    t, result = _wait_for_card()
    approval_id = uce._approval_batch_meta[SID]["approval_id"]
    uce.set_abort_flag(SID)
    t.join(2)
    assert uce.set_approval_response(SID, True, scope="session", approval_id=approval_id) is False
    assert "system_command" not in uce._session_tool_grants.get(SID, set())


def test_a_stop_with_no_card_waiting_is_harmless():
    uce.set_abort_flag(SID)
    assert SID not in uce._approval_responses
