"""A1 tests: runtime tool-registry enumeration vs AST fallback.

Pure-logic, on-disk fake repo. Monkeypatches the subprocess probe so the tests
don't depend on importing the real (heavy) registry.
"""
from pathlib import Path

from backend.services.system_mapper import tool_graph
from backend.services.system_mapper.core import FindingKind


def _write(p: Path, text: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _fake_repo(tmp_path: Path) -> Path:
    # tool_registry_init.py with BOTH explicit and loop registration.
    _write(
        tmp_path / "backend" / "tools" / "tool_registry_init.py",
        "from backend.tools.code_manipulation_tools import CODE_MANIPULATION_TOOLS\n"
        "def register_code_tools():\n"
        "    registered = []\n"
        "    register_tool(WordPressContentTool())\n"
        "    registered.append('generate_wordpress_content')\n"
        "    for tool in CODE_MANIPULATION_TOOLS:\n"
        "        register_tool(tool)\n"
        "        registered.append(tool.name)\n"
        "    return registered\n",
    )
    # The source module the loop iterates — each tool class has a `name` attr.
    _write(
        tmp_path / "backend" / "tools" / "code_manipulation_tools.py",
        "class EditCodeTool:\n    name = 'edit_code'\n\n"
        "class ReadCodeTool:\n    name = 'read_code'\n\n"
        "CODE_MANIPULATION_TOOLS = [EditCodeTool(), ReadCodeTool()]\n",
    )
    # unified_chat_engine lists all of them as wired CORE_TOOLS.
    _write(
        tmp_path / "backend" / "services" / "unified_chat_engine.py",
        "CORE_TOOLS = ['generate_wordpress_content', 'edit_code', 'read_code']\n",
    )
    return tmp_path


# ---- A1: runtime probe success suppresses false 'unregistered' --------------

def test_runtime_probe_resolves_loop_registered(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path)

    def fake_probe(_root, timeout=20.0):
        return ({"generate_wordpress_content", "edit_code", "read_code"},
                {"count": 3})

    monkeypatch.setattr(tool_graph, "_probe_runtime_registry", fake_probe)
    result = tool_graph.analyze(root)

    unreg = [f for f in result["findings"]
             if f.kind == FindingKind.UNREGISTERED_TOOL]
    unreg_names = {f.evidence.get("tool") for f in unreg}
    assert "edit_code" not in unreg_names
    assert "read_code" not in unreg_names
    assert result["stats"]["tool_registry_source"] == "runtime"


def test_probe_failure_falls_back_to_ast(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path)

    def failing_probe(_root, timeout=20.0):
        return set(), {"error": "boom"}

    monkeypatch.setattr(tool_graph, "_probe_runtime_registry", failing_probe)
    result = tool_graph.analyze(root)  # must not raise

    assert result["stats"]["tool_registry_source"] == "ast_fallback"
    # AST loop-detection should still have resolved edit_code/read_code, so no
    # false unregistered finding for them.
    unreg_names = {f.evidence.get("tool") for f in result["findings"]
                   if f.kind == FindingKind.UNREGISTERED_TOOL}
    assert "edit_code" not in unreg_names
    assert "read_code" not in unreg_names


def test_ast_loop_registration_detected(tmp_path):
    """Even with no probe monkeypatch, AST should resolve the loop list."""
    root = _fake_repo(tmp_path)
    registry = tool_graph._extract_registered_tools(
        root / "backend" / "tools" / "tool_registry_init.py"
    )
    assert "edit_code" in registry
    assert "read_code" in registry
    assert "generate_wordpress_content" in registry


def test_probe_parses_last_json_line(tmp_path, monkeypatch):
    """_probe_runtime_registry tolerates log noise before the JSON line."""
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "WARNING: noisy log\n{\"ok\": true, \"tools\": [\"a\", \"b\"]}\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    names, info = tool_graph._probe_runtime_registry(tmp_path)
    assert names == {"a", "b"}


def test_probe_nonzero_exit_returns_empty(tmp_path, monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "ImportError"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    names, info = tool_graph._probe_runtime_registry(tmp_path)
    assert names == set()
    assert "error" in info


# ---- module-level lists win over a function-local set of the same name -----

def test_extract_core_tools_ignores_function_local_set(tmp_path):
    """The chat engine also builds a function-local CORE_TOOLS set inside its
    semantic selector. Only the module-level list is the wiring; the inner set
    must not overwrite it (a tool added to the real list was reported as
    unwired for a week because it did)."""
    engine = tmp_path / "unified_chat_engine.py"
    engine.write_text(
        "CORE_TOOLS = [\n"
        "    'web_search',\n"
        "    'list_documents',  # added after a system-map finding\n"
        "]\n"
        "BROWSER_TOOLS = ['browser_navigate']\n"
        "\n"
        "def select_tools():\n"
        "    CORE_TOOLS = {\n"
        "        'web_search',\n"
        "    }\n"
        "    return CORE_TOOLS\n",
        encoding="utf-8",
    )
    union, breakdown = tool_graph._extract_core_tools(engine)
    assert breakdown["CORE_TOOLS"] == ["web_search", "list_documents"]
    assert breakdown["BROWSER_TOOLS"] == ["browser_navigate"]
    assert "list_documents" in union
