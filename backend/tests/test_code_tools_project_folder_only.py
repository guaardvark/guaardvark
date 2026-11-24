""""Project folder only" covers the code tools that read and edit by path."""
import pytest

from backend.tools.agent_tools import code_manipulation_tools as cmt


@pytest.fixture
def confined(monkeypatch, tmp_path):
    install = tmp_path / "install"
    project = tmp_path / "project"
    outside = tmp_path / "elsewhere"
    for folder in (install, project, outside):
        folder.mkdir()
    (outside / "notes.py").write_text("x = 1\n")
    (project / "app.py").write_text("y = 2\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(install))
    monkeypatch.setattr("backend.utils.settings_utils.get_confine_tool_paths", lambda: True)
    monkeypatch.setattr("backend.tools.code_tools._confine_candidates", lambda paths: [])
    monkeypatch.setattr(cmt, "_turn_project_root", lambda tool: str(project))
    return project, outside


def test_a_path_outside_the_project_is_refused(confined):
    project, outside = confined
    reason = cmt._outside_project_reason(object(), str(outside / "notes.py"))
    assert reason and "Project folder only" in reason


def test_the_chats_project_folder_is_inside(confined):
    project, _outside = confined
    assert cmt._outside_project_reason(object(), str(project / "app.py")) is None
    assert cmt._outside_project_reason(object(), "app.py") is None


def test_a_relative_path_that_climbs_out_is_refused(confined):
    assert cmt._outside_project_reason(object(), "../elsewhere/notes.py")


def test_the_setting_off_refuses_nothing(confined, monkeypatch):
    _project, outside = confined
    monkeypatch.setattr("backend.utils.settings_utils.get_confine_tool_paths", lambda: False)
    assert cmt._outside_project_reason(object(), str(outside / "notes.py")) is None


@pytest.mark.parametrize("tool_class, params", [
    (cmt.ReadCodeTool, {}),
    (cmt.EditCodeTool, {"old_text": "x = 1", "new_text": "x = 2"}),
    (cmt.VerifyChangeTool, {"expected_text": "x = 1"}),
])
def test_each_tool_refuses_before_touching_the_file(confined, tool_class, params):
    _project, outside = confined
    target = outside / "notes.py"
    result = tool_class().execute(filepath=str(target), **params)
    assert not result.success and "Project folder only" in result.error
    assert target.read_text() == "x = 1\n"
