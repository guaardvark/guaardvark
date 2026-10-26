"""system_map_query: focused questions answered from the map, and how chat,
slash commands, REST and MCP reach it.

A small Guaardvark-shaped tree in tmp_path is mapped for real (static
analysis only); the store writes under tmp_path. No backend, model or GPU.
"""
from pathlib import Path

import pytest

from backend.services.system_mapper import query, store


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def mapped(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "checkout"
    _write(root, "backend/__init__.py")
    _write(root, "backend/services/__init__.py")
    _write(root, "backend/services/chat_turn.py",
           '"""Start and route one chat turn."""\n'
           "def run_chat_turn(body):\n    return route_turn(body)\n\n"
           "def route_turn(body):\n    return body\n")
    _write(root, "backend/services/engine.py",
           "from backend.services.chat_turn import run_chat_turn\n"
           "class Engine:\n"
           "    def ask(self, m):\n        return self._await_approval(m)\n"
           "    def _await_approval(self, m):\n        return run_chat_turn(m)\n")
    _write(root, "backend/api/chat_api.py",
           "from flask import Blueprint\n"
           "bp = Blueprint('chat', __name__, url_prefix='/api/chat')\n"
           "@bp.route('/unified', methods=['POST'])\n"
           "def unified_chat():\n"
           "    from backend.services import chat_turn\n"
           "    return chat_turn.run_chat_turn({})\n")
    _write(root, "backend/tests/test_chat_turn.py",
           "from backend.services.chat_turn import run_chat_turn\n"
           "def test_it():\n    assert run_chat_turn(1) == 1\n")
    _write(root, "frontend/src/api/apiClient.js", 'export const BASE_URL = "/api";\n')
    _write(root, "frontend/src/api/chatService.js",
           'import { BASE_URL } from "./apiClient";\n'
           "export const send = async (m) => {\n  return fetch(`${BASE_URL}/chat/unified`, { method: 'POST' });\n};\n")
    _write(root, "frontend/src/pages/ImagesPage.jsx",
           'import { send } from "../api/chatService";\n'
           "export default function ImagesPage() {\n"
           "  send();\n"
           "  return <TextField label={t('imagesBatch.seed.label')} />;\n"
           "}\n")
    _write(root, "frontend/src/locales/en/imagesBatch.json", '{"seed": {"label": "Seed"}}\n')
    _write(root, "backend/services/system_mapper/subsystems.json",
           '{"subsystems": ['
           '{"name": "chat engine", "description": "Turns a chat message into an answer.", '
           '"globs": ["backend/services/chat_turn.py", "backend/services/engine.py", "backend/api/chat_api.py"]},'
           '{"name": "image generation", "description": "Images and the seed box.", "globs": ["frontend/src/pages/ImagesPage.jsx"]},'
           '{"name": "rest", "description": "Everything else.", "globs": ["*"]}]}\n')
    snapshot = store.build(root)
    index = store.load_index(root, snapshot)
    assert index is not None
    return root, snapshot, index


def _ask(mapped, mode, q):
    root, snapshot, index = mapped
    return query.answer(root, snapshot, index, mode, q)


def test_symbol_lists_definition_callers_and_callees(mapped):
    text = _ask(mapped, "symbol", "run_chat_turn")
    assert "backend/services/chat_turn.py:2-3  function run_chat_turn" in text
    assert "backend/services/engine.py:6 in Engine._await_approval()" in text
    assert "backend/api/chat_api.py:6 in unified_chat()" in text
    assert "route_turn → backend/services/chat_turn.py:5" in text


def test_self_calls_resolve_to_the_method(mapped):
    text = _ask(mapped, "symbol", "_await_approval")
    assert "backend/services/engine.py:4 in Engine.ask()" in text


def test_module_lists_importers_by_kind_and_tests(mapped):
    text = _ask(mapped, "module", "backend/services/chat_turn.py")
    assert "subsystem: chat engine" in text
    assert "Imported at load time by (1): backend/services/engine.py:1" in text
    assert "Imported inside functions by (1): backend/api/chat_api.py:5" in text
    assert "Imported by tests (1): backend/tests/test_chat_turn.py" in text
    assert "Defines (2): run_chat_turn 2, route_turn 5" in text


def test_module_accepts_a_dotted_name(mapped):
    assert _ask(mapped, "module", "backend.services.chat_turn").startswith("backend/services/chat_turn.py")


def test_route_names_handler_callers_and_pages(mapped):
    text = _ask(mapped, "route", "/api/chat/unified")
    assert "POST /api/chat/unified → backend/api/chat_api.py:4 unified_chat()" in text
    assert "frontend/src/api/chatService.js:3" in text
    assert "Pages that reach it: frontend/src/pages/ImagesPage.jsx" in text


def test_impact_lists_dependents_and_tests_to_run(mapped):
    text = _ask(mapped, "impact", "backend/services/chat_turn.py")
    assert "backend/services/engine.py" in text
    assert "backend/tests/test_chat_turn.py" in text


def test_where_finds_the_ui_line_by_its_label(mapped):
    text = _ask(mapped, "where", "image seed box")
    assert text.splitlines()[2].strip().startswith("frontend/src/pages/ImagesPage.jsx")
    assert "frontend/src/pages/ImagesPage.jsx:4" in text
    assert 'UI text "Seed"' in text
    assert "no semantic index for this checkout" in text


def test_subsystem_describes_its_files(mapped):
    text = _ask(mapped, "subsystem", "chat engine")
    assert text.startswith("Subsystem 'chat engine': Turns a chat message into an answer.")
    assert "backend/services/chat_turn.py" in text


def test_findings_for_a_folder(mapped):
    text = _ask(mapped, "findings", "backend/services")
    assert text.startswith("Findings for backend/services:")


def test_unknown_things_say_so(mapped):
    assert "No definition named 'nope_xyz'" in _ask(mapped, "symbol", "nope_xyz")
    assert "No backend route" in _ask(mapped, "route", "/api/none")
    assert "Unknown mode" in _ask(mapped, "explain", "x")


@pytest.mark.parametrize("question, mode, arg", [
    ("module backend/app.py", "module", "backend/app.py"),
    ("what calls run_chat_turn", "symbol", "run_chat_turn"),
    ("callers of run_chat_turn?", "symbol", "run_chat_turn"),
    ("what depends on backend/services/agent_tools.py", "impact", "backend/services/agent_tools.py"),
    ("where is the image seed box", "where", "is the image seed box"),
    ("image seed box", "where", "image seed box"),
])
def test_free_text_is_parsed(question, mode, arg):
    assert query.parse(question) == (mode, arg)


# ---- the tool ---------------------------------------------------------------------------

def test_the_tool_is_read_only_research_with_a_wide_observation():
    from backend.services.agent_tools import is_plan_research_tool
    from backend.tools.workstation_tools import WORKSTATION_TOOLS, SystemMapQueryTool

    tool = SystemMapQueryTool()
    assert tool.read_only is True and is_plan_research_tool(tool)
    assert tool.observation_chars >= 5000
    assert tool.chat_summary and len(tool.chat_summary) < 250
    assert any(isinstance(t, SystemMapQueryTool) for t in WORKSTATION_TOOLS)


def test_the_tool_answers_from_the_store(mapped, monkeypatch):
    from backend.tools import workstation_tools as wt

    root, _snapshot, _index = mapped
    monkeypatch.setattr(wt, "_repo_root", lambda: root)
    result = wt.SystemMapQueryTool().execute(q="what calls run_chat_turn")
    assert result.success
    assert isinstance(result.output, str) and "Callers resolved through imports" in result.output
    assert result.metadata["mode"] == "symbol"


def test_the_map_tool_is_offered_over_mcp_by_default():
    from backend.mcp.config import ToolPolicy, tool_is_exposed

    exposed, _why = tool_is_exposed("system_map_query", "workstation", False, False, ToolPolicy())
    assert exposed


# ---- chat and slash commands --------------------------------------------------------------

def test_sysmap_with_a_question_runs_the_query_tool():
    from backend.services.slash_command_executor import resolve_slash_direct_tool

    assert resolve_slash_direct_tool({"slash_command": "sysmap", "slash_args": ""}) == (
        "map_codebase", {"refresh": False})
    assert resolve_slash_direct_tool({"slash_command": "sysmap", "slash_args": "refresh"}) == (
        "map_codebase", {"refresh": True})
    assert resolve_slash_direct_tool({"slash_command": "sysmap", "slash_args": "module backend/app.py"}) == (
        "system_map_query", {"q": "module backend/app.py"})
    # The browser names map_codebase but leaves the question in slash_args.
    assert resolve_slash_direct_tool({"direct_tool": "map_codebase", "slash_command": "sysmap",
                                      "slash_args": "where is the seed box"}) == (
        "system_map_query", {"q": "where is the seed box"})
    # Explicit parameters still win.
    assert resolve_slash_direct_tool({"direct_tool": "system_map_query", "slash_command": "sysmap",
                                      "direct_tool_params": {"mode": "module", "q": "a.py"},
                                      "slash_args": "module a.py"}) == (
        "system_map_query", {"mode": "module", "q": "a.py"})


def test_code_questions_pin_the_query_tool():
    import backend.services.unified_chat_engine as uce

    names = ["search_knowledge_base", "search_codebase", "system_map_query", "read_code"]
    out = uce._pin_code_search_tools("what calls run_chat_turn in the backend", [], names)
    assert out[:3] == ["search_codebase", "system_map_query", "read_code"]
    assert "system_map_query" in uce.WORKSTATION_TOOLS


def test_the_code_nudge_claims_an_index_only_when_there_is_one(monkeypatch, tmp_path):
    import backend.config as cfg
    import backend.services.unified_chat_engine as uce

    monkeypatch.setattr(cfg, "GUAARDVARK_ROOT", str(tmp_path))
    assert "not set up on this machine" in uce._code_search_nudge()
    assert "indexed" not in uce._code_search_nudge()
    (tmp_path / ".zvec-grep").mkdir()
    assert "indexed for search by meaning" in uce._code_search_nudge()


def test_search_codebase_says_when_it_is_literal(monkeypatch, tmp_path):
    from backend.tools import code_search_tools as cst

    called = []
    monkeypatch.setattr(cst, "_default_root", lambda: str(tmp_path))
    monkeypatch.setattr(cst, "_hybrid_search", lambda *a: called.append(a) or "hit")
    monkeypatch.setattr(cst, "_regex_search", lambda q, limit, root=None: ("backend/a.py:3: def q(): ...", q))
    result = cst.SearchCodebaseTool().execute(query="q")
    assert result.success and result.metadata["engine"] == "regex"
    assert called == [], "no index, so zvec-grep is not asked"
    assert "no code search index has been built" in result.output
    assert "already indexed" not in cst.SearchCodebaseTool.description


# ---- REST --------------------------------------------------------------------------------

def test_rest_query_answers_text(mapped, monkeypatch):
    from flask import Flask

    import backend.config as cfg
    from backend.api.system_map_api import system_map_bp

    root, _snapshot, _index = mapped
    monkeypatch.setattr(cfg, "GUAARDVARK_ROOT", str(root))
    app = Flask(__name__)
    app.register_blueprint(system_map_bp)
    response = app.test_client().get("/api/system-map/query?mode=route&q=/api/chat/unified")
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] and "unified_chat()" in body["answer"]
    free = app.test_client().get("/api/system-map/query?q=what calls run_chat_turn").get_json()
    assert free["mode"] == "symbol"
