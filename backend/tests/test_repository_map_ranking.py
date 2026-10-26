"""The repository map ranks what a reader looks for, and the folder tools reach
Guaardvark's own source.

Before: names defined everywhere (execute, get, __init__) and ordinary words
linked every file to every other, so test files and a minified bundle topped
the map of this repository, and "return" was listed as a function. The map,
graph and AST tools refused Guaardvark's own checkout. Small trees under
tmp_path; no backend, database or model.
"""
import json

import pytest

from backend import config
from backend.services.repository_analysis_service import RepositoryAnalysisService, project_code_map
from backend.utils.code_symbol_extractor import extract_symbols


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _listed(repo_map):
    return [line[3:] for line in repo_map.splitlines() if line.startswith("## ")]


def test_a_keyword_in_a_comment_is_not_a_definition():
    code = "// the function return (value) is checked below\nfunction realOne() { return 1; }\n"
    names = [s["name"] for s in extract_symbols(code, "javascript")]
    assert names == ["realOne"]


def test_imported_files_rank_above_files_nothing_uses(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "repo"
    _write(root, "core/settings.py", "def get_setting(key):\n    return key\n")
    for i in range(4):
        _write(root, f"features/f{i}.py", f"from core.settings import get_setting\n\ndef feature_{i}():\n    return get_setting('x')\n")
    # Shares only an ordinary word with everything; nothing imports it.
    _write(root, "scripts/lonely.py", "def first():\n    return 1\n\ndef show():\n    return 2\n")
    _write(root, "tests/test_settings.py", "from core.settings import get_setting\n\ndef test_it():\n    assert get_setting('a')\n")
    _write(root, "web/vendor.min.js", "function a(){return 1}function b(){return 2}\n")

    listed = _listed(project_code_map("ranking", str(root))["repository_map"])

    assert listed[0] == "core/settings.py"
    assert listed.index("core/settings.py") < listed.index("scripts/lonely.py")
    assert "tests/test_settings.py" not in listed
    assert "web/vendor.min.js" not in listed


def test_a_name_defined_in_many_files_links_nothing(tmp_path):
    files = []

    class F:
        def __init__(self, path, content):
            self.path, self.filename, self.content = path, path.rsplit("/", 1)[-1], content

    for i in range(5):
        files.append(F(f"tools/t{i}.py", "class T:\n    def execute(self):\n        return 1\n"))
    files.append(F("runner.py", "def run(tool):\n    return tool.execute()\n"))
    listed = _listed(RepositoryAnalysisService._repository_map("r", files))
    # runner.py names execute, which five files define. Were that a link, the
    # five tool files would outrank runner.py; with none, ties keep path order.
    assert listed == ["runner.py"] + [f"tools/t{i}.py" for i in range(5)]


@pytest.fixture
def own_checkout(tmp_path, monkeypatch):
    import backend.tools.llama_code_tools as lct

    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "checkout"
    _write(root, "backend/services/engine.py", "from backend.utils.helpers import tidy\n\ndef run():\n    return tidy()\n")
    _write(root, "backend/utils/helpers.py", "def tidy():\n    return 'ok'\n")
    monkeypatch.setattr(lct, "PROJECT_ROOT", root)
    return root


def _tool(tool_class):
    tool = tool_class()
    tool.set_context({"transport": "mcp"})
    return tool


def test_the_folder_tools_map_guaardvarks_own_source_without_a_folder_id(own_checkout):
    import backend.tools.agent_tools.code_manipulation_tools as cmt

    graph = _tool(cmt.GetDependencyGraphTool).execute(file="backend/utils/helpers.py")
    assert graph.success, graph.error
    body = json.loads(graph.output)
    assert body["imported_by"] == ["backend/services/engine.py"]

    repo_map = _tool(cmt.GetRepositoryMapTool).execute()
    assert repo_map.success, repo_map.error
    assert repo_map.output.startswith("Mapped from Guaardvark's own source")

    node = _tool(cmt.ReadASTNodeTool).execute(filepath="backend/utils/helpers.py", node_name="tidy")
    assert node.success, node.error
    assert "def tidy():" in node.output
