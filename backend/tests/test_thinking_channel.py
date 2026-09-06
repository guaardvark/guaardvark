"""A thinking model's reasoning rides chat:reasoning, never chat:token or content.

Exercises _call_llm_streaming against a stubbed ollama.chat generator that
yields message.thinking and message.content chunks the way Ollama does.
"""
from unittest.mock import MagicMock, patch

import pytest

import backend.services.unified_chat_engine as uce

THINKING_TEXT = "The user wants to know the first two sentences, so I will plan them. "
ANSWER_TEXT = "Here are the first two sentences of the story you asked for."


def _done(reason="stop", prompt_tokens=12, eval_tokens=7):
    return {
        "message": {"content": ""},
        "done": True,
        "done_reason": reason,
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_tokens,
    }


@pytest.fixture
def engine():
    """UnifiedChatEngine without its heavy __init__; only streaming state is set."""
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.llm = MagicMock(model="gemma4:12b", context_window=8192)
    e._native_toolcalls_active = False
    e._native_tools_schema = None
    e._native_pending_tool_calls = None
    e._think = True
    return e


def _stream(engine, chat_impl, max_tokens=256, iteration=2):
    events = []

    def emit(name, payload):
        events.append((name, payload))

    with patch("ollama.chat", chat_impl), \
            patch("backend.services.unified_chat_engine.is_aborted", return_value=False), \
            patch("backend.services.llm_provider.is_mistral_active", return_value=False):
        result = engine._call_llm_streaming(
            [{"role": "user", "content": "write two sentences"}],
            emit, "sess-think", emit_tokens=True,
            max_tokens=max_tokens, iteration=iteration,
        )
    return result, events


def _reasoning(events):
    return [p for n, p in events if n == "chat:reasoning"]


def _tokens(events):
    return [p for n, p in events if n == "chat:token"]


class TestReasoningChannel:
    def test_thinking_never_reaches_chat_token_or_content(self, engine):
        def chat(**_kw):
            for _ in range(4):
                yield {"message": {"thinking": THINKING_TEXT}}
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        (content, in_tok, out_tok), events = _stream(engine, chat)

        assert content == ANSWER_TEXT
        assert (in_tok, out_tok) == (12, 7)
        for tok in _tokens(events):
            assert THINKING_TEXT.strip() not in tok["content"]
        assert "".join(t["content"] for t in _tokens(events)) == ANSWER_TEXT

    def test_reasoning_deltas_and_done_are_emitted(self, engine):
        def chat(**_kw):
            for _ in range(4):
                yield {"message": {"thinking": THINKING_TEXT}}
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        _, events = _stream(engine, chat, iteration=3)
        reasoning = _reasoning(events)

        deltas = [p for p in reasoning if "delta" in p]
        done = [p for p in reasoning if p.get("done")]
        assert deltas, "batched reasoning deltas expected"
        assert len(done) == 1
        # Deltas carry the raw stream; the done event carries the stripped whole.
        assert "".join(p["delta"] for p in deltas) == THINKING_TEXT * 4
        assert done[0]["text"] == (THINKING_TEXT * 4).strip()
        for p in reasoning:
            assert p["session_id"] == "sess-think"
            assert p["iteration"] == 3
        # The done event closes the channel after the last delta.
        assert reasoning[-1] is done[0]

    def test_done_event_present_without_reasoning(self, engine):
        def chat(**_kw):
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        _, events = _stream(engine, chat)
        done = [p for p in _reasoning(events) if p.get("done")]
        assert len(done) == 1 and done[0]["text"] == ""

    def test_call_meta_records_thinking(self, engine):
        def chat(**_kw):
            yield {"message": {"thinking": THINKING_TEXT}}
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        _stream(engine, chat)
        meta = engine._last_llm_call_meta
        assert meta["thinking"] == THINKING_TEXT.strip()
        assert meta["done_reason"] == "stop"
        assert meta["truncated"] is False


class TestReasoningOnlyFallback:
    def test_empty_content_recalls_with_think_off(self, engine):
        calls = []

        def chat(**kw):
            calls.append(kw)
            if kw.get("think", True):
                yield {"message": {"thinking": THINKING_TEXT}}
                yield _done()
            else:
                yield {"message": {"content": ANSWER_TEXT}}
                yield _done()

        (content, _, _), events = _stream(engine, chat, max_tokens=256)

        assert content == ANSWER_TEXT
        assert [c.get("think") for c in calls] == [True, False]
        retry_messages = calls[1]["messages"]
        assert retry_messages[-1]["role"] == "system"
        assert retry_messages[-1]["content"] == uce._ANSWER_AFTER_REASONING_NUDGE
        # The answer-only retry drops the thinking budget.
        assert calls[1]["options"]["num_predict"] == 256
        assert calls[0]["options"]["num_predict"] > 256
        assert "".join(t["content"] for t in _tokens(events)) == ANSWER_TEXT

    def test_still_empty_returns_visible_notice_not_thinking(self, engine):
        def chat(**kw):
            # The think=False retry produces no reasoning, like Ollama does.
            if kw.get("think", True):
                yield {"message": {"thinking": THINKING_TEXT}}
            yield _done()

        (content, _, _), events = _stream(engine, chat)

        assert content == uce._REASONING_ONLY_FALLBACK_TEXT
        assert THINKING_TEXT.strip() not in content
        assert any(t["content"] == content for t in _tokens(events))
        assert engine._last_llm_call_meta["thinking"] == THINKING_TEXT.strip()


class TestTruncation:
    def test_done_reason_length_marks_truncated(self, engine):
        def chat(**_kw):
            yield {"message": {"thinking": THINKING_TEXT}}
            yield {"message": {"content": ANSWER_TEXT[:20]}}
            yield _done(reason="length", eval_tokens=300)

        _stream(engine, chat)
        meta = engine._last_llm_call_meta
        assert meta["done_reason"] == "length"
        assert meta["truncated"] is True


class TestThinkingBudget:
    def test_num_predict_grows_by_thinking_budget(self, engine):
        seen = {}

        def chat(**kw):
            seen.update(kw)
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        from backend.config import AGENTIC_THINKING_TOKEN_BUDGET
        _stream(engine, chat, max_tokens=100)
        assert seen["options"]["num_predict"] == 100 + AGENTIC_THINKING_TOKEN_BUDGET
        assert seen["think"] is True

    def test_num_predict_unchanged_with_thinking_off(self, engine):
        engine._think = False
        seen = {}

        def chat(**kw):
            seen.update(kw)
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        _stream(engine, chat, max_tokens=100)
        assert seen["options"]["num_predict"] == 100

    def test_budget_capped_to_context_window(self, engine):
        engine.llm = MagicMock(model="gemma4:12b", context_window=600)
        seen = {}

        def chat(**kw):
            seen.update(kw)
            yield {"message": {"content": ANSWER_TEXT}}
            yield _done()

        _stream(engine, chat, max_tokens=100)
        num_predict = seen["options"]["num_predict"]
        prompt_estimate = uce.UnifiedChatEngine._estimate_tokens(seen["messages"])
        assert 100 <= num_predict
        assert prompt_estimate + num_predict <= 600
