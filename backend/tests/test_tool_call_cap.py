"""A reply that keeps writing tool calls is stopped once it passes
MAX_TOOL_CALLS_PER_REPLY, instead of being generated to the end.

Exercises _call_llm_streaming against a stubbed ollama.chat generator, as
test_thinking_channel.py does.
"""
from unittest.mock import MagicMock, patch

import backend.services.unified_chat_engine as uce


def _engine():
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.llm = MagicMock(model="gemma4:12b", context_window=8192)
    e._native_toolcalls_active = False
    e._native_tools_schema = None
    e._native_pending_tool_calls = None
    e._think = False
    return e


def _call(engine, chat):
    with patch("ollama.chat", chat), \
            patch("backend.services.unified_chat_engine.is_aborted", return_value=False):
        content, _, _ = engine._call_llm_streaming(
            [{"role": "user", "content": "where are paused runs resumed"}],
            lambda *a: None, "sess-cap", emit_tokens=True, max_tokens=4096, iteration=2,
        )
    return content


def test_a_reply_repeating_tool_calls_is_stopped():
    sent = []

    def chat(**kw):
        # The shape of the reply that ran to 196 calls: the same few calls
        # in bracket form, over and over.
        for i in range(200):
            for piece in ("[tool_call]\n", "[tool]search_codebase[/tool]\n",
                          f"<query>resume {i % 5}</query>\n", "</search_codebase]\n"):
                sent.append(piece)
                yield {"message": {"content": piece}}
        yield {"message": {"content": ""}, "done": True, "done_reason": "stop",
               "prompt_eval_count": 1, "eval_count": 1}

    content = _call(_engine(), chat)

    calls = content.count("[tool_call")
    assert uce.MAX_TOOL_CALLS_PER_REPLY < calls <= uce.MAX_TOOL_CALLS_PER_REPLY + 4
    # Stopped within a few calls of the limit, not after 200.
    assert len(sent) <= 4 * (uce.MAX_TOOL_CALLS_PER_REPLY + 4) + 1


def test_a_reply_with_a_few_calls_is_read_to_the_end():
    def chat(**kw):
        for name in ("read_code", "system_map_query", "search_codebase"):
            yield {"message": {"content": f"[tool_call]\n[tool]{name}[/tool]\n</{name}]\n"}}
        yield {"message": {"content": "Checking those now."}}
        yield {"message": {"content": ""}, "done": True, "done_reason": "stop",
               "prompt_eval_count": 1, "eval_count": 1}

    content = _call(_engine(), chat)
    assert content.count("[tool_call") == 3 and content.endswith("Checking those now.")
