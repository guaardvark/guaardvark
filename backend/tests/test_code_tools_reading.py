"""The read-only code tools as an MCP client sees them: read_code returns a
large file in pages and a line range on request, list_code_files refuses
private folders without showing which exist, and the repository tools give
checkout-relative labels and say when their analysis ran.

Each test works on a small tree under tmp_path, which is not a git checkout.
No backend, database, GPU or network."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.tools.agent_tools.code_manipulation_tools as cmt
import backend.tools.llama_code_tools as lct


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(lct, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _mcp(tool_class):
    tool = tool_class()
    tool.set_context({"transport": "mcp"})
    return tool


def _body(output):
    return output.split(cmt._CONTENT_START, 1)[1].rsplit(cmt._CONTENT_END, 1)[0]


# --- read_code ----------------------------------------------------------

def test_a_small_file_without_a_range_is_returned_as_before(checkout):
    (checkout / "small.py").write_text("a = 1\nb = 2\n")
    result = _mcp(cmt.ReadCodeTool).execute(filepath="small.py")
    assert result.success
    assert result.output == lct.read_code("small.py", allow_external=False)
    assert "Showing lines" not in result.output
    assert result.metadata["complete"] is True


def test_a_large_file_comes_back_in_pages_that_add_up_to_the_file(checkout, monkeypatch):
    monkeypatch.setattr(cmt, "READ_CODE_PAGE_CHARS", 200)
    source = "".join(f"line_{i:03d} = {i}\n" for i in range(1, 101))
    (checkout / "big.py").write_text(source)
    tool = _mcp(cmt.ReadCodeTool)

    first = tool.execute(filepath="big.py")
    assert first.success and first.metadata["complete"] is False
    assert "Showing lines 1-" in first.output and "of 100" in first.output
    assert f"start_line={first.metadata['next_start_line']}" in first.output

    pages, next_line = [_body(first.output)], first.metadata["next_start_line"]
    while next_line:
        page = tool.execute(filepath="big.py", start_line=next_line)
        assert page.success
        pages.append(_body(page.output))
        next_line = page.metadata["next_start_line"]
    assert len(pages) > 2
    assert all(len(page) <= 200 for page in pages)
    assert "\n".join(pages) + "\n" == source


def test_a_line_range_returns_exactly_those_lines(checkout):
    (checkout / "mod.py").write_text("".join(f"l{i}\n" for i in range(1, 21)))
    tool = _mcp(cmt.ReadCodeTool)

    result = tool.execute(filepath="mod.py", start_line=5, end_line=7)
    assert _body(result.output) == "l5\nl6\nl7"
    assert "Showing lines 5-7 of 20" in result.output
    assert "start_line=8" in result.output
    assert (result.metadata["start_line"], result.metadata["end_line"]) == (5, 7)

    to_the_end = tool.execute(filepath="mod.py", start_line=19, end_line=500)
    assert _body(to_the_end.output) == "l19\nl20"
    assert to_the_end.metadata["next_start_line"] is None

    as_strings = tool.execute(filepath="mod.py", start_line="2", end_line="2")
    assert _body(as_strings.output) == "l2"


TWENTY_LINES = "".join(f"l{i}\n" for i in range(1, 21))
# (read_code arguments on a 20-line file, text the error must contain).
RANGE_ERRORS = [
    ({"start_line": 3, "end_line": 1}, "before start_line"),
    ({"start_line": 0}, "1 or more"),
    ({"end_line": "many"}, "whole number"),
]


@pytest.mark.parametrize("arguments,expected", RANGE_ERRORS)
def test_a_range_outside_the_file_is_an_error(checkout, arguments, expected):
    (checkout / "mod.py").write_text(TWENTY_LINES)
    result = _mcp(cmt.ReadCodeTool).execute(filepath="mod.py", **arguments)
    assert not result.success
    assert expected in result.error


def test_a_start_past_the_end_says_where_the_file_ended(checkout):
    (checkout / "mod.py").write_text(TWENTY_LINES)
    result = _mcp(cmt.ReadCodeTool).execute(filepath="mod.py", start_line=21)
    assert result.success
    assert "Nothing after line 20" in result.output
    assert result.metadata["past_end"] is True


def test_one_line_longer_than_a_page_is_cut_and_said_so(checkout, monkeypatch):
    monkeypatch.setattr(cmt, "READ_CODE_PAGE_CHARS", 50)
    (checkout / "min.js").write_text("x" * 500 + "\nvar y = 1;\n")
    result = _mcp(cmt.ReadCodeTool).execute(filepath="min.js")
    assert result.success
    assert _body(result.output) == "x" * 50
    assert "is cut at 50" in result.output and "start_line=2" in result.output


# --- list_code_files ------------------------------------------------------

def test_a_private_folder_gets_the_same_answer_whether_or_not_it_exists(checkout):
    (checkout / "docs" / "local-workspace-only" / "plans").mkdir(parents=True)
    (checkout / "src").mkdir()
    (checkout / "src" / "a.py").write_text("x = 1\n")

    existing = lct.list_files("docs/local-workspace-only/plans")
    missing = lct.list_files("docs/local-workspace-only/zz-no-such-folder")
    assert existing.replace("plans", "NAME") == missing.replace("zz-no-such-folder", "NAME")
    assert "git-ignored local data" in missing

    assert "does not exist" in lct.list_files("zz-no-such-folder")
    assert "a.py" in lct.list_files("src")


# --- repositories, map and graph (the backend's answers are stubbed) -------

ANALYSED = {
    "id": 7, "path": "Repo", "is_repository": True,
    "metadata": {
        "analyzed_at": "2026-09-12T14:03:11.123456",
        "file_count": 42,
        "repository_map": "## Repo/app/main.py\n- class Worker",
        "dependency_graph": {"Repo/app/main.py": ["Repo/app/util.py"]},
    },
}
# Metadata written without an analysis time, and nothing to map.
UNDATED = {"id": 8, "path": "Old", "is_repository": True,
           "metadata": {"repository_map": "", "dependency_graph": {}}}


@pytest.fixture
def backend(monkeypatch):
    def request_json(method, path, **_kwargs):
        if path == "/api/files/repositories":
            data = {"repositories": [{"id": 7, "name": "Repo", "path": "Repo", "has_metadata": True, "description": ""}]}
        else:
            data = {7: ANALYSED, 8: UNDATED}[int(path.split("/")[4])]
        return SimpleNamespace(data=data)

    monkeypatch.setattr("backend.utils.backend_http.request_json", request_json)


def test_the_live_entry_is_a_relative_label_with_advice_that_can_be_followed(backend):
    result = _mcp(cmt.ListCodeRepositoriesTool).execute()
    assert result.success
    live = result.output[-1]
    assert (live["id"], live["path"]) == ("live", ".")
    assert "Mark as Code Repo" not in live["description"]
    assert "not a folder_id" in live["description"]
    # Nothing in the listing names where the checkout or the home folder is.
    assert not [entry for entry in result.output if os.path.isabs(str(entry["path"]))]
    assert str(Path.home()) not in json.dumps(result.output)


def test_the_repository_map_says_when_the_analysis_ran(backend):
    result = _mcp(cmt.GetRepositoryMapTool).execute(folder_id=7)
    assert result.success
    first_line, _blank, rest = result.output.partition("\n\n")
    assert first_line.startswith("Analysed 2026-09-12 14:03 (server local time), 42 files.")
    assert rest == ANALYSED["metadata"]["repository_map"]

    undated = _mcp(cmt.GetRepositoryMapTool).execute(folder_id=8)
    assert undated.success
    assert "no classes or functions" in undated.output
    assert "Analysis time not recorded" in undated.output


def test_the_dependency_graph_carries_the_analysis_time(backend):
    result = _mcp(cmt.GetDependencyGraphTool).execute(folder_id=7)
    assert result.success
    assert json.loads(result.output) == {
        "analyzed_at": "2026-09-12T14:03:11.123456",
        "file_count": 42,
        "graph": {"Repo/app/main.py": ["Repo/app/util.py"]},
    }

    undated = json.loads(_mcp(cmt.GetDependencyGraphTool).execute(folder_id=8).output)
    assert undated == {"analyzed_at": None, "file_count": None, "graph": {}}
