"""
Tests for SemanticToolSelector in unified_chat_engine.
Mocks ollama.embeddings so the suite runs without a live Ollama instance.
"""
import threading
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(name: str, description: str, params: dict | None = None):
    """Build a minimal mock tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    params = params or {}
    tool.parameters = {
        k: MagicMock(type=v, required=True)
        for k, v in params.items()
    }
    return tool


def _make_registry(tools: list):
    reg = MagicMock()
    reg.list_tools.return_value = [t.name for t in tools]
    reg.get_tool.side_effect = lambda name: next(
        (t for t in tools if t.name == name), None
    )
    return reg


def _unit_vec(dim: int, idx: int) -> list:
    """Return a unit vector in dimension `dim` pointing along axis `idx`."""
    v = [0.0] * dim
    v[idx] = 1.0
    return v


# ---------------------------------------------------------------------------
# The class under test — import inline so we can patch first
# ---------------------------------------------------------------------------

def _import_selector():
    from backend.services.unified_chat_engine import SemanticToolSelector
    return SemanticToolSelector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_select_returns_core_tools_always():
    """CORE_TOOLS are always included even if their similarity is zero."""
    SemanticToolSelector = _import_selector()

    tools = [
        _make_tool("web_search",           "Search the internet",          {"query": "string"}),
        _make_tool("search_knowledge_base", "Search local knowledge base",  {"query": "string"}),
        _make_tool("system_command",        "Run shell commands",           {"command": "string"}),
        _make_tool("generate_file",         "Create and write files",       {"filename": "string"}),
        _make_tool("totally_unrelated",     "Something completely unrelated"),
    ]
    registry = _make_registry(tools)

    embedding_map = {
        "web_search":            _unit_vec(5, 0),
        "search_knowledge_base": _unit_vec(5, 1),
        "system_command":        _unit_vec(5, 2),
        "generate_file":         _unit_vec(5, 3),
        "totally_unrelated":     _unit_vec(5, 4),
    }

    def fake_embed(text: str) -> list:
        return _unit_vec(5, 4)

    sel = SemanticToolSelector()
    sel._embed = fake_embed
    sel._tool_embeddings = {
        name: embedding_map[name] for name in embedding_map
    }
    sel._initialized = True

    result = sel.select("any message", registry, max_tools=5)

    for core in ("web_search", "search_knowledge_base", "system_command", "generate_file"):
        assert core in result, f"CORE tool '{core}' missing from {result}"


def test_select_ranks_by_similarity():
    """Tool most similar to the message ranks first (after CORE_TOOLS)."""
    SemanticToolSelector = _import_selector()

    tools = [
        _make_tool("web_search",       "Search the internet"),
        _make_tool("search_knowledge_base", "Search local knowledge base"),
        _make_tool("system_command",   "Run shell commands"),
        _make_tool("generate_file",    "Create files"),
        _make_tool("browser_navigate", "Open a web page in a browser"),
        _make_tool("codegen",          "Generate source code"),
    ]
    registry = _make_registry(tools)

    def fake_embed(text: str) -> list:
        if "browser" in text or "navigate" in text:
            return _unit_vec(6, 4)
        return _unit_vec(6, 5)

    sel = SemanticToolSelector()
    sel._embed = fake_embed
    sel._tool_embeddings = {
        "web_search":            _unit_vec(6, 0),
        "search_knowledge_base": _unit_vec(6, 1),
        "system_command":        _unit_vec(6, 2),
        "generate_file":         _unit_vec(6, 3),
        "browser_navigate":      _unit_vec(6, 4),
        "codegen":               _unit_vec(6, 5),
    }
    sel._initialized = True

    result = sel.select("open a web page and navigate to google.com", registry, max_tools=6)
    assert "browser_navigate" in result, f"Expected browser_navigate in {result}"
    # Verify browser_navigate ranks first among non-core tools
    core = {"web_search", "search_knowledge_base", "system_command", "generate_file"}
    non_core = [t for t in result if t not in core]
    assert non_core and non_core[0] == "browser_navigate", (
        f"Expected browser_navigate ranked first among non-core tools, got {non_core}"
    )


def test_select_respects_max_tools():
    """Result never exceeds max_tools."""
    SemanticToolSelector = _import_selector()

    tools = [_make_tool(f"tool_{i}", f"Description {i}") for i in range(20)]
    registry = _make_registry(tools)

    def fake_embed(text: str) -> list:
        return _unit_vec(20, 0)

    sel = SemanticToolSelector()
    sel._embed = fake_embed
    sel._tool_embeddings = {f"tool_{i}": _unit_vec(20, i) for i in range(20)}
    sel._initialized = True

    result = sel.select("anything", registry, max_tools=10)
    assert len(result) <= 10, f"Expected ≤10 tools, got {len(result)}"


def test_select_fallback_on_embed_failure():
    """Falls back to keyword selection when embedding raises."""
    SemanticToolSelector = _import_selector()
    from backend.services.unified_chat_engine import select_tools_for_context

    tools = [
        _make_tool("web_search",       "Search the internet"),
        _make_tool("search_knowledge_base", "Search local knowledge base"),
        _make_tool("system_command",   "Run shell commands"),
        _make_tool("generate_file",    "Create files"),
    ]
    registry = _make_registry(tools)
    tool_names = [t.name for t in tools]

    sel = SemanticToolSelector()
    sel._initialized = True
    sel._tool_embeddings = {}

    def broken_embed(text: str) -> list:
        raise RuntimeError("ollama not available")

    sel._embed = broken_embed

    message = "search the web for news"
    result = sel.select(message, registry, max_tools=15)
    expected = select_tools_for_context(message, tool_names, max_tools=15)
    assert sorted(result) == sorted(expected), (
        f"Fallback should match keyword selection.\n"
        f"Got:      {sorted(result)}\nExpected: {sorted(expected)}"
    )


def test_lazy_init_is_thread_safe():
    """Concurrent callers only initialise tool embeddings once."""
    SemanticToolSelector = _import_selector()

    tools = [_make_tool(f"tool_{i}", f"Description {i}") for i in range(5)]
    registry = _make_registry(tools)

    count_lock = threading.Lock()
    call_count = {"n": 0}

    def counting_embed(text: str) -> list:
        with count_lock:
            call_count["n"] += 1
        return _unit_vec(5, 0)

    sel = SemanticToolSelector()
    sel._embed = counting_embed

    errors = []

    def _call():
        try:
            sel.select("test message", registry, max_tools=5)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread safety errors: {errors}"
    # Tool embeddings are initialised exactly once (5 calls) across all threads,
    # plus 1 message embedding per select() call (8 threads × 1 = 8 calls).
    # Total expected: 5 + 8 = 13.
    # If the lock is absent, each thread re-embeds all tools: up to 8×5 + 8 = 48 calls.
    TOOLS_COUNT = 5
    THREAD_COUNT = 8
    assert TOOLS_COUNT <= call_count["n"] <= TOOLS_COUNT + THREAD_COUNT, (
        f"Expected between {TOOLS_COUNT} and {TOOLS_COUNT + THREAD_COUNT} embed calls, "
        f"got {call_count['n']} — lazy init may not be guarded"
    )
