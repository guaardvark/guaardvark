"""tool_graph reads the tool inventory from the source and never runs it.

On-disk fake repos shaped like Guaardvark's tool layer: tool classes with a
`name` attribute, a tool_registry_init.py that registers them in the ways the
real one does, and a chat engine with tool lists, a pin and a selector.
"""
import subprocess
from pathlib import Path

from backend.services.system_mapper import tool_graph
from backend.services.system_mapper.core import FindingKind


def _write(p: Path, text: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


REGISTRY = '''\
from backend.services.agent_tools import register_tool

def register_content_tools():
    registered = []
    category = "content"
    from backend.tools.content_tools import WordPressContentTool, OtherTool
    register_tool(WordPressContentTool())
    registered.append("generate_wordpress_content")
    register_tool(OtherTool())
    registered.append("other_tool")
    return registered

def register_code_tools():
    from backend.tools.code_manipulation_tools import CODE_MANIPULATION_TOOLS
    for tool in CODE_MANIPULATION_TOOLS:
        register_tool(tool)

def register_photo_tools():
    pairs = [("CutoutTool", "remove_background")]
    for cls_name, tool_name in pairs:
        from backend.tools import image_tools as _image_tools
        register_tool(getattr(_image_tools, cls_name)())

def register_audio_tools():
    from backend.utils.backend_http import in_mcp_process
    registered = []
    if not in_mcp_process():
        return registered
    from backend.tools.audio_tools import MusicTool
    register_tool(MusicTool())
    return registered
'''


def _fake_repo(tmp_path: Path, engine: str | None = None) -> Path:
    _write(tmp_path / "backend" / "tools" / "tool_registry_init.py", REGISTRY)
    _write(tmp_path / "backend" / "tools" / "content_tools.py",
           "class BaseTool: pass\n\n"
           "class WordPressContentTool(BaseTool):\n    name = 'generate_wordpress_content'\n    read_only = False\n\n"
           "class OtherTool(BaseTool):\n    name = 'other_tool'\n\n"
           "class OrphanTool(BaseTool):\n    name = 'orphan'\n")
    _write(tmp_path / "backend" / "tools" / "code_manipulation_tools.py",
           "class EditCodeTool:\n    name = 'edit_code'\n\n"
           "class ReadCodeTool:\n    name = 'read_code'\n    read_only = True\n\n"
           "CODE_MANIPULATION_TOOLS = [EditCodeTool(), ReadCodeTool()]\n")
    _write(tmp_path / "backend" / "tools" / "image_tools.py", "class CutoutTool:\n    name = 'remove_background'\n")
    _write(tmp_path / "backend" / "tools" / "audio_tools.py", "class MusicTool:\n    name = 'generate_music'\n")
    _write(tmp_path / "backend" / "services" / "unified_chat_engine.py", engine if engine is not None else (
        "CORE_TOOLS = ['generate_wordpress_content']\n"
        "CODE_TOOLS = ['edit_code', 'read_code']\n"
        "SERIAL_TOOLS = ['remove_background']\n"
        "TOOL_CONTEXT_KEYWORDS = {'code': (['code'], CODE_TOOLS)}\n"
        "class SemanticToolSelector:\n    pass\n"))
    return tmp_path


def _tools(result):
    return {t["name"]: t for t in result["graph"]["registered_tools"]}


def test_registrations_are_read_from_source_in_every_shape(tmp_path):
    result = tool_graph.analyze(_fake_repo(tmp_path))
    tools = _tools(result)
    assert set(tools) == {"generate_wordpress_content", "other_tool", "edit_code", "read_code",
                          "remove_background", "generate_music"}
    assert result["stats"]["tool_registry_source"] == "static"


def test_each_tool_names_its_own_class_file_and_line(tmp_path):
    """Names come from each class's own `name`, not from the order of
    register/append calls (which paired 46 of 55 tools with the wrong class)."""
    tools = _tools(tool_graph.analyze(_fake_repo(tmp_path)))
    assert tools["other_tool"]["class"] == "OtherTool"
    assert tools["generate_wordpress_content"]["class"] == "WordPressContentTool"
    assert tools["read_code"]["file"] == "backend/tools/code_manipulation_tools.py"
    assert tools["read_code"]["line"] == 4
    assert tools["read_code"]["attrs"]["read_only"] is True
    assert tools["generate_wordpress_content"]["category"] == "content"


def test_mcp_only_registration_is_marked(tmp_path):
    tools = _tools(tool_graph.analyze(_fake_repo(tmp_path)))
    assert tools["generate_music"]["surface"] == "mcp"
    assert tools["generate_music"]["conditions"] == ["not in_mcp_process()"]
    assert tools["edit_code"]["surface"] == "chat+mcp"


def test_the_selector_reaches_every_chat_tool(tmp_path):
    result = tool_graph.analyze(_fake_repo(tmp_path))
    assert result["graph"]["unwired"] == []
    assert not [f for f in result["findings"] if f.kind == FindingKind.UNWIRED_TOOL]
    tools = _tools(result)
    assert "every turn (CORE_TOOLS)" in tools["generate_wordpress_content"]["reach"]
    assert "list CODE_TOOLS" in tools["edit_code"]["reach"]
    # SERIAL_TOOLS classifies tools; it offers nothing.
    assert tools["remove_background"]["reach"] == ["semantic selector"]


def test_without_a_selector_an_unlisted_tool_is_unwired(tmp_path):
    engine = "CORE_TOOLS = ['generate_wordpress_content', 'edit_code', 'read_code', 'remove_background']\n"
    result = tool_graph.analyze(_fake_repo(tmp_path, engine))
    assert result["graph"]["unwired"] == ["other_tool"]
    unwired = [f for f in result["findings"] if f.kind == FindingKind.UNWIRED_TOOL]
    assert [f.evidence["tool"] for f in unwired] == ["other_tool"]
    assert unwired[0].evidence["key"] == "other_tool"
    # MCP-only tools are not the chat's to reach.
    assert "generate_music" not in result["graph"]["unwired"]


def test_a_listed_name_nothing_registers_is_reported(tmp_path):
    engine = ("CORE_TOOLS = ['generate_wordpress_content', 'ghost_tool']\n"
              "MCP_NATIVE_TOOLS = ['mcp__fs__read']\n"
              "class SemanticToolSelector:\n    pass\n")
    result = tool_graph.analyze(_fake_repo(tmp_path, engine))
    assert result["graph"]["unregistered"] == ["ghost_tool"]


def test_defined_but_never_registered_classes_are_listed(tmp_path):
    result = tool_graph.analyze(_fake_repo(tmp_path))
    assert [t["name"] for t in result["graph"]["defined_not_registered"]] == ["orphan"]


def test_no_process_is_started(tmp_path, monkeypatch):
    """The tool pass never imports or runs the mapped tree."""
    from backend.services.system_mapper import core

    def refuse(*_a, **_k):
        raise AssertionError("tool_graph started a process")

    # The file listing may ask git; walk the folder instead so that any
    # process at all fails the test.
    monkeypatch.setattr(core, "_git_listed_files", lambda start: None)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    result = tool_graph.analyze(_fake_repo(tmp_path))
    assert result["stats"]["applicable"] is True
    assert len(result["graph"]["registered_tools"]) == 6


def test_mcp_exposure_follows_the_default_policy(tmp_path):
    root = _fake_repo(tmp_path)
    _write(root / "backend" / "mcp" / "config.py",
           "DEFAULT_DENY_CATEGORIES = ['content']\nDEFAULT_ALLOW_TOOLS = ['other_tool']\nDEFAULT_DENY_TOOLS = []\n")
    tools = _tools(tool_graph.analyze(root))
    assert tools["generate_wordpress_content"]["mcp"]["exposed"] is False
    assert tools["other_tool"]["mcp"]["exposed"] is True
    assert tools["edit_code"]["mcp"]["exposed"] is True


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
        "KNOWLEDGE_NAV_TOOLS_PINNED = ['get_document_outline']\n"
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
    assert breakdown["KNOWLEDGE_NAV_TOOLS_PINNED"] == ["get_document_outline"]
    assert "list_documents" in union


def test_a_tree_without_the_tool_layer_is_not_applicable(tmp_path):
    _write(tmp_path / "backend" / "app.py", "x = 1\n")
    result = tool_graph.analyze(tmp_path)
    assert result["stats"]["applicable"] is False
    assert result["findings"] == []
