"""An approval answer applies only to the card it answers.

Drives the real ``UnifiedChatEngine._await_tool_approval`` and
``set_approval_response``; the only stand-in is the emit callback.
"""

import threading

import pytest

import backend.services.unified_chat_engine as uce
from backend.services.agent_tools import ToolRegistry

SID = "approval-binding"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(uce, "APPROVAL_TIMEOUT_S", 0.5)
    for d in (uce._approval_events, uce._approval_responses, uce._approval_batch_meta,
              uce._session_tool_grants, uce._task_tool_grants):
        d.pop(SID, None)
    yield
    for d in (uce._approval_events, uce._approval_responses, uce._approval_batch_meta,
              uce._session_tool_grants, uce._task_tool_grants):
        d.pop(SID, None)


def _ask(answer, tools=("system_command",), request_id="req-1"):
    """Show a card for ``tools``; ``answer(card)`` replies from the emit callback."""
    engine = uce.UnifiedChatEngine(ToolRegistry(), object())
    cards = []

    def emit(event, payload):
        if event == "chat:tool_approval_request":
            cards.append(payload)
            answer(payload)

    details = [{"tool": t, "params": {}} for t in tools]
    approved = engine._await_tool_approval(SID, emit, request_id, 1, details)
    return approved, cards


def test_the_card_carries_an_approval_id():
    approved, cards = _ask(lambda card: None)
    assert approved is False
    assert cards[0]["approval_id"]


def test_an_answer_bound_to_the_card_approves_it():
    approved, _ = _ask(lambda c: uce.set_approval_response(
        SID, True, scope="once", approval_id=c["approval_id"]))
    assert approved is True


def test_an_answer_for_another_card_is_ignored():
    applied = []
    approved, _ = _ask(lambda c: applied.append(uce.set_approval_response(
        SID, True, scope="session", approval_id="not-this-card")))
    assert applied == [False]
    assert approved is False
    assert "system_command" not in uce._session_tool_grants.get(SID, set())


def test_an_answer_for_another_request_is_ignored():
    applied = []
    approved, _ = _ask(lambda c: applied.append(uce.set_approval_response(
        SID, True, request_id="req-old")))
    assert applied == [False]
    assert approved is False


def test_a_grant_covers_only_the_tools_the_card_named():
    approved, _ = _ask(lambda c: uce.set_approval_response(
        SID, True, scope="session", approval_id=c["approval_id"],
        tools=["system_command", "edit_code"]))
    assert approved is True
    assert uce._session_tool_grants[SID] == {"system_command"}


def test_an_answer_naming_only_other_tools_is_ignored():
    approved, _ = _ask(lambda c: uce.set_approval_response(
        SID, True, scope="session", approval_id=c["approval_id"], tools=["edit_code"]))
    assert approved is False
    assert not uce._session_tool_grants.get(SID)


def test_an_unbound_answer_approves_once_and_grants_nothing():
    approved, _ = _ask(lambda c: uce.set_approval_response(SID, True, scope="session"))
    assert approved is True
    assert not uce._session_tool_grants.get(SID)


def test_an_answer_with_no_card_waiting_is_ignored():
    assert uce.set_approval_response(SID, True, scope="session", tools=["system_command"]) is False
    assert not uce._session_tool_grants.get(SID)


def test_a_late_answer_after_the_card_closed_grants_nothing():
    approved, cards = _ask(lambda c: None)
    assert approved is False
    late = uce.set_approval_response(
        SID, True, scope="session", approval_id=cards[0]["approval_id"])
    assert late is False
    assert not uce._session_tool_grants.get(SID)


def test_a_string_false_is_not_an_approval():
    approved, _ = _ask(lambda c: uce.set_approval_response(
        SID, "false", approval_id=c["approval_id"]))
    assert approved is False


def test_an_answer_from_another_thread_is_bound_too():
    """The real flow: the answer arrives on the socket thread while the turn waits."""
    engine = uce.UnifiedChatEngine(ToolRegistry(), object())
    cards = []
    shown = threading.Event()

    def emit(event, payload):
        if event == "chat:tool_approval_request":
            cards.append(payload)
            shown.set()

    result = {}
    t = threading.Thread(target=lambda: result.update(
        ok=engine._await_tool_approval(SID, emit, "req-1", 1, [{"tool": "system_command", "params": {}}])))
    t.start()
    assert shown.wait(2)
    assert uce.set_approval_response(SID, True, approval_id="stale") is False
    assert uce.set_approval_response(SID, True, approval_id=cards[0]["approval_id"]) is True
    t.join(2)
    assert result["ok"] is True


def test_a_waiting_card_can_be_read_until_it_closes():
    """A client that missed the socket event reads the same card while it waits."""
    seen = []

    def answer(card):
        seen.append(uce.pending_approval_cards())
        uce.set_approval_response(SID, False, approval_id=card["approval_id"])

    _, cards = _ask(answer)
    assert seen == [cards]
    assert uce.pending_approval_cards() == []
