"""Code questions: the code-search nudge outranks document RAG.

With document RAG on, the model answered code questions from the indexed docs
and made no search_codebase calls (2026-09-08 trial). The docs are not
dropped: a code question's first prompt carries the nudge and the tools but
not the knowledge-base block, and the block joins once a code search has run
(with hits or without), labelled as secondary. Non-code questions keep the
block in the first prompt.
"""

import pytest

import backend.services.unified_chat_engine as uce
from backend.services.chat_prompt_blocks import CHAT_KB_CONTEXT_HEADER

KB = "KB PASSAGE: the chat sessions route is documented in the API guide."
HITS = "backend/api/chat_sessions_api.py:42: def list_sessions():"
NO_HITS = "No matches found for pattern 'route handler' in **/*.py"


class _Tool:
    requires_approval = False
    read_only = True
    observation_chars = 4000

    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.parameters = {}
        self.category = "test"


class _Result:
    def __init__(self, output):
        self.success = True
        self.output = output
        self.error = None
        self.metadata = {}


class _Registry:
    def __init__(self, names, search_output):
        self._tools = {n: _Tool(n) for n in names}
        self.search_output = search_output
        self.executions = []

    def list_tools(self):
        return list(self._tools)

    def get_tool(self, name):
        return self._tools.get(name)

    def get_tool_names(self):
        return list(self._tools)

    def as_ollama_tools(self, tool_names=None):
        return []

    def execute_tool(self, name, on_output=None, agent_context=None, **params):
        self.executions.append((name, params))
        return _Result(self.search_output if name == "search_codebase" else "web result")


class _LLM:
    model = "test-model"


def _tool_call(tool, query):
    return f"<tool_call>\n<tool>{tool}</tool>\n<query>{query}</query>\n</tool_call>"


def _engine(monkeypatch, replies, search_output=HITS):
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry(["search_codebase", "read_code", "web_search"], search_output)
    e.llm = _LLM()
    e.max_iterations = 4
    e._image_data = None
    e._skip_tools = False
    e._brain_state = None
    e.calls = {"llm_messages": [], "saved": []}

    class _Selector:
        def select(self, message, registry):
            return registry.list_tools()

    e._semantic_selector = _Selector()
    e._load_history = lambda session_id, limit=None: []
    e._load_rules = lambda model_name: "ENGINE PERSONA"
    e._get_routed_tools = lambda message: []
    e._retrieve_rag_context = lambda message: KB
    e._should_skip_rag = lambda message: False
    e._format_interface_context = lambda options: ""
    e._compact_history = lambda history, *a, **k: history
    e._analyze_pasted_image = lambda *a, **k: None
    e._warmup_chat_llm_async = lambda *a, **k: None
    e._maybe_summarize_session = lambda session_id: None
    e._save_message = lambda session_id, role, content, extra_data=None: e.calls["saved"].append((role, content))
    e._try_direct_tool = lambda *a, **k: None
    for name in (
        "_try_image_generate_retry", "_try_image_edit_retry", "_try_media_direct",
        "_try_image_edit_direct", "_try_music_video_direct", "_try_film_crew_direct",
        "_try_video_generate_direct", "_try_image_generate_direct", "_try_named_image_direct",
    ):
        setattr(e, name, lambda *a, **k: None)

    scripted = list(replies)

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        e.calls["llm_messages"].append(list(messages))
        reply = scripted.pop(0) if scripted else "Final answer."
        return reply, 1, 1

    e._call_llm_streaming = _llm
    e._last_llm_call_meta = {}

    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "match_workstation_direct", lambda message: None)
    import backend.utils.settings_utils as su
    monkeypatch.setattr(su, "get_setting", lambda key, default=None: default)
    return e


def _run(e, message):
    events = []
    e._run_chat("sess-code", message, {"think": False}, lambda n, p: events.append((n, p)), "req-1", [])
    return events


def _text(messages):
    return "\n".join(m.get("content", "") for m in messages)


CODE_QUESTION = "which file defines the route handler for chat sessions"
DOC_QUESTION = "what does the warranty document say about the ridge cap"


class TestCodeQuestion:
    def test_first_prompt_has_the_nudge_and_no_kb_block(self, monkeypatch):
        e = _engine(monkeypatch, [_tool_call("search_codebase", "route handler"), "Final answer."])
        _run(e, CODE_QUESTION)
        first = e.calls["llm_messages"][0]
        assert uce._code_search_nudge() in _text(first)
        assert KB not in _text(first)
        assert CHAT_KB_CONTEXT_HEADER not in _text(first)
        assert uce._KB_SECONDARY_LABEL not in _text(first)

    def test_kb_block_joins_after_a_code_search_with_the_secondary_label(self, monkeypatch):
        e = _engine(monkeypatch, [_tool_call("search_codebase", "route handler"), "Final answer."])
        _run(e, CODE_QUESTION)
        assert e.registry.executions[0][0] == "search_codebase"
        second = e.calls["llm_messages"][1]
        last_user = [m for m in second if m["role"] == "user"][-1]["content"]
        assert HITS in last_user
        assert uce._KB_SECONDARY_LABEL in last_user
        assert KB in last_user
        assert last_user.index("Latest tool results:") < last_user.index(uce._KB_SECONDARY_LABEL)
        # Attached once, not on every later iteration.
        assert _text(second).count(KB) == 1

    def test_a_code_search_with_zero_hits_also_brings_the_kb_block(self, monkeypatch):
        e = _engine(
            monkeypatch,
            [_tool_call("search_codebase", "route handler"), "Final answer."],
            search_output=NO_HITS,
        )
        _run(e, CODE_QUESTION)
        second = e.calls["llm_messages"][1]
        last_user = [m for m in second if m["role"] == "user"][-1]["content"]
        assert NO_HITS in last_user
        assert uce._KB_SECONDARY_LABEL in last_user
        assert KB in last_user

    def test_a_web_search_does_not_release_the_kb_block(self, monkeypatch):
        e = _engine(
            monkeypatch,
            [_tool_call("web_search", "chat sessions"), _tool_call("search_codebase", "route handler"), "Final."],
        )
        _run(e, CODE_QUESTION)
        after_web = _text(e.calls["llm_messages"][1])
        assert KB not in after_web
        after_code = _text(e.calls["llm_messages"][2])
        assert uce._KB_SECONDARY_LABEL in after_code and KB in after_code

    def test_a_direct_answer_never_sees_the_docs(self, monkeypatch):
        """The trade the operator chose: no code search, no knowledge-base block."""
        e = _engine(monkeypatch, ["Final answer without searching."])
        _run(e, CODE_QUESTION)
        assert len(e.calls["llm_messages"]) == 1
        assert KB not in _text(e.calls["llm_messages"][0])


class TestNonCodeQuestion:
    def test_kb_block_stays_in_the_first_prompt(self, monkeypatch):
        e = _engine(monkeypatch, ["Final answer."])
        _run(e, DOC_QUESTION)
        first = e.calls["llm_messages"][0]
        user = [m for m in first if m["role"] == "user"][-1]["content"]
        assert f"{CHAT_KB_CONTEXT_HEADER}\n{KB}" in user
        assert "answer from your own knowledge" in user
        assert uce._code_search_nudge() not in _text(first)

    def test_no_passages_means_no_kb_block(self, monkeypatch):
        e = _engine(monkeypatch, ["Canberra."])
        e._retrieve_rag_context = lambda message: ""
        _run(e, DOC_QUESTION)
        first = e.calls["llm_messages"][0]
        assert CHAT_KB_CONTEXT_HEADER not in _text(first)

    def test_code_question_without_the_tool_keeps_the_block_too(self, monkeypatch):
        """No search_codebase in the registry: nothing to hold the docs for."""
        e = _engine(monkeypatch, ["Final answer."])
        e.registry = _Registry(["web_search"], HITS)
        _run(e, CODE_QUESTION)
        first = e.calls["llm_messages"][0]
        assert KB in _text(first)
        assert uce._code_search_nudge() not in _text(first)
