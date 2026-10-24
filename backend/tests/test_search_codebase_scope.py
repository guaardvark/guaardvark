"""search_codebase names what it searched, keeps MCP callers inside the
Guaardvark checkout, and honours limit when it falls back to literal search.

The checkout is a folder under tmp_path standing in for the Guaardvark root;
no zvec_grep server, backend or database is involved."""
from unittest.mock import patch

import pytest

import backend.tools.llama_code_tools as lct
from backend.tools import code_search_tools as cst


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "a.py").write_text("".join(f"def is_mcp_transport_{i}(): pass\n" for i in range(10)))
    monkeypatch.setattr(cst, "_default_root", lambda: str(root))
    monkeypatch.setattr(lct, "PROJECT_ROOT", root)
    return root


@pytest.fixture
def outside(tmp_path):
    other = tmp_path / "someone_elses_repo"
    other.mkdir()
    return other


def _mcp_tool():
    tool = cst.SearchCodebaseTool()
    tool.set_context({"transport": "mcp"})
    return tool


def _chat_tool():
    tool = cst.SearchCodebaseTool()
    tool.set_context({"transport": "chat"})
    return tool


def test_description_names_guaardvarks_own_source():
    desc = cst.SearchCodebaseTool.description
    assert "this Guaardvark install's own source" in desc
    assert "current project" not in desc
    # The description may name the zvec_grep engine; what must be gone is the
    # steer away from the client's own grep/ls, which searched a different tree.
    assert "instead of shell commands" not in desc
    assert "grep or ls" not in desc


def test_mcp_refuses_a_root_outside_the_checkout(checkout, outside):
    called = []
    with patch.object(cst, "_hybrid_search_via_backend", lambda *a: called.append(a) or "hit"):
        res = _mcp_tool().execute(query="auth check", root=str(outside))
    assert not res.success
    assert "outside this Guaardvark install's own source checkout" in res.error
    assert called == []


def test_mcp_ignores_a_project_root_passed_in_the_arguments(checkout, outside):
    seen = {}
    with patch.object(cst, "_hybrid_search_via_backend", lambda root, q, l: seen.setdefault("root", root) and "hit"):
        res = _mcp_tool().execute(query="q", _agent_context={"project_root": str(outside)})
    assert res.success
    assert seen["root"] == str(checkout.resolve())


def test_mcp_root_inside_the_checkout_searches_the_checkout(checkout):
    seen = {}
    with patch.object(cst, "_hybrid_search_via_backend", lambda root, q, l: seen.setdefault("root", root) and "hit"):
        res = _mcp_tool().execute(query="q", root="backend")
    assert res.success
    assert seen["root"] == str(checkout.resolve())


def test_chat_keeps_an_explicit_outside_root(checkout, outside):
    seen = {}
    with patch.object(cst, "_hybrid_search", lambda root, q, l: seen.setdefault("root", root) and "hit"):
        res = _chat_tool().execute(query="q", root=str(outside))
    assert res.success
    assert seen["root"] == str(outside.resolve())


def test_hybrid_result_names_the_checkout_without_an_absolute_path(checkout):
    with patch.object(cst, "_hybrid_search_via_backend", lambda root, q, l: "#1 backend/a.py:1"):
        res = _mcp_tool().execute(query="q")
    first = res.output.splitlines()[0]
    assert first.startswith("[Searched Guaardvark's own source checkout (.)")
    assert "zvec_grep" in first
    assert str(checkout) not in res.output


def test_regex_fallback_lists_at_most_limit_hits(checkout):
    with patch.object(cst, "_hybrid_search", lambda root, q, l: None):
        res = _chat_tool().execute(query="is_mcp_transport", limit=3)
    assert res.success and res.metadata["engine"] == "regex"
    first = res.output.splitlines()[0]
    assert first.startswith("[Searched Guaardvark's own source checkout (.): literal search")
    assert "Found 10 matches" in res.output
    assert res.output.count("backend/a.py:") == 3
    assert "showing first 3" in res.output


def test_regex_fallback_answers_from_the_tree_it_was_asked_about(checkout, outside):
    (outside / "lib.py").write_text("def is_mcp_transport_elsewhere(): pass\n")
    with patch.object(cst, "_hybrid_search", lambda root, q, l: None):
        res = _chat_tool().execute(query="is_mcp_transport", root=str(outside))
    assert res.success and res.metadata["engine"] == "regex"
    assert "lib.py:1" in res.output
    assert "backend/a.py" not in res.output
