"""Tests for the lazy session summarisation in MemoryManager.

`create_session_summary` was dead code for ages — defined and never called.
We added `maybe_summarize_session` (which only summarises the *new* tail)
and wired it into `enhanced_chat_api`'s post-turn hook. These tests pin the
behaviour: idempotent, threshold-gated, indices-aware.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.utils.memory_manager import MemoryManager


def _msg(i: int) -> dict:
    role = "user" if i % 2 == 0 else "assistant"
    return {
        "role": role,
        "content": f"Message {i} discussing the YouTube redesign in detail.",
        "timestamp": datetime.now(),
    }


def test_below_threshold_is_noop():
    mgr = MemoryManager()
    msgs = [_msg(i) for i in range(10)]
    assert mgr.maybe_summarize_session("s1", msgs, threshold=30, window_size=20) == 0
    assert mgr.session_summaries.get("s1", []) == []


def test_summarises_complete_windows_only():
    mgr = MemoryManager()
    # 50 messages, window 20 → two complete windows (0-20, 20-40); tail 40-50 deferred.
    msgs = [_msg(i) for i in range(50)]
    appended = mgr.maybe_summarize_session("s2", msgs, threshold=30, window_size=20)
    assert appended == 2
    summaries = mgr.session_summaries.get("s2", [])
    assert len(summaries) == 2
    assert summaries[0]["start_index"] == 0
    assert summaries[0]["end_index"] == 20
    assert summaries[1]["start_index"] == 20
    assert summaries[1]["end_index"] == 40


def test_repeat_call_is_idempotent():
    mgr = MemoryManager()
    msgs = [_msg(i) for i in range(50)]
    mgr.maybe_summarize_session("s3", msgs, threshold=30, window_size=20)
    # Second call with same input adds nothing
    appended = mgr.maybe_summarize_session("s3", msgs, threshold=30, window_size=20)
    assert appended == 0
    assert len(mgr.session_summaries["s3"]) == 2


def test_growth_summarises_new_windows():
    mgr = MemoryManager()
    msgs = [_msg(i) for i in range(50)]
    mgr.maybe_summarize_session("s4", msgs, threshold=30, window_size=20)
    # Add 20 more messages → one new complete window 40-60
    msgs.extend(_msg(i) for i in range(50, 70))
    appended = mgr.maybe_summarize_session("s4", msgs, threshold=30, window_size=20)
    assert appended == 1
    summaries = mgr.session_summaries["s4"]
    assert len(summaries) == 3
    assert summaries[-1]["start_index"] == 40
    assert summaries[-1]["end_index"] == 60


def test_summary_text_is_populated():
    mgr = MemoryManager()
    msgs = [_msg(i) for i in range(40)]
    mgr.maybe_summarize_session("s5", msgs, threshold=30, window_size=20)
    summary_text = mgr.session_summaries["s5"][0]["summary"]
    # Don't pin exact wording — the underlying ConversationSummarizer is
    # deterministic-ish but we don't want to brittle the test on its prose.
    assert isinstance(summary_text, str)
    assert len(summary_text) > 0


def test_partial_tail_does_not_count():
    mgr = MemoryManager()
    msgs = [_msg(i) for i in range(35)]  # 1 complete window + 15 partial
    appended = mgr.maybe_summarize_session("s6", msgs, threshold=30, window_size=20)
    assert appended == 1
    assert mgr.session_summaries["s6"][-1]["end_index"] == 20
