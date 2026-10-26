"""Plan mode's research and plan writing, without a model or database.

The research registry must make action tools impossible to run, not merely
unlisted; the compose fallbacks and the validator are code a small model
leans on, so their behaviour is pinned here.
"""
import pytest

from backend.services import plan_mode as pm
from backend.services import plan_mode_data as pmd
from backend.services.agent_tools import BaseTool, ToolParameter, ToolRegistry, ToolResult


def _tool(name, read_only, params=None, required=()):
    ran = []

    def execute(self, **kw):
        ran.append(kw)
        return ToolResult(success=True, output=f"{name} ran")

    params = params or {}
    cls = type(name, (BaseTool,), {
        "name": name, "description": name, "read_only": read_only, "execute": execute,
        "parameters": {p: ToolParameter(name=p, type="string", required=p in required) for p in params},
    })
    tool = cls()
    tool.ran = ran
    return tool


@pytest.fixture
def registry():
    reg = ToolRegistry()
    for tool in (_tool("read_code", True, ["filepath"], ["filepath"]),
                 _tool("edit_code", False, ["filepath", "old_text", "new_text"]),
                 _tool("generate_file", False, ["filename", "content_description"], ["filename", "content_description"]),
                 _tool("agent_task_execute", False, ["task"]),
                 _tool("mystery", None)):
        reg.register(tool)
    return reg


def test_research_registry_lists_and_runs_research_tools_only(registry):
    view = pm.PlanResearchRegistry(registry)
    assert view.list_tools() == ["read_code"]
    assert view.get_tool("edit_code") is None
    assert view.execute_tool("read_code", filepath="a.py").success


def test_an_action_named_anyway_is_refused_and_kept_for_the_plan(registry):
    view = pm.PlanResearchRegistry(registry)
    result = view.execute_tool("edit_code", filepath="a.py", old_text="1", new_text="2")
    assert not result.success
    assert registry.get_tool("edit_code").ran == []
    assert view.attempted and view.attempted[0]["tool"] == "edit_code"
    # An undeclared tool counts as an action too.
    assert not view.execute_tool("mystery").success
    assert registry.get_tool("mystery").ran == []


def test_client_options_cannot_switch_plan_mode():
    for key in ("plan_mode", "plan_step", "plan_mode_research", "complete_extra", "finalize_fn"):
        assert key in pm.SERVER_ONLY_OPTIONS


@pytest.mark.parametrize("data,problem", [
    (None, "not a JSON object"),
    ({"kind": "maybe"}, "kind must be"),
    ({"kind": "question"}, "question text"),
    ({"kind": "plan", "steps": []}, "at least one step"),
    ({"kind": "plan", "steps": [{"action": "edit_code"}]}, "say in words"),
])
def test_shape_problems_name_what_to_fix(data, problem):
    assert problem in pm.shape_problem(data)


def test_a_plain_instruction_and_a_tool_call_are_both_usable():
    assert pm.shape_problem({"kind": "plan", "steps": [{"action": "Change STEPS from 8 to 9 in config.py"}]}) == ""
    assert pm.shape_problem({"kind": "plan", "steps": [{"action": "generate_image(prompt='a fox')"}]}) == ""


def test_a_call_with_line_breaks_in_its_text_is_read():
    call = pm._tool_call("generate_file(file_content='line one,\nline two', file_name='poem.txt')")
    assert call == ("generate_file", {"file_content": "line one,\nline two", "file_name": "poem.txt"})
    assert pm._tool_call("Change the value") is None
    assert pm._tool_call("f('positional')") is None


def test_call_arguments_are_fitted_to_the_tool_or_refused(registry):
    tool = registry.get_tool("generate_file")
    assert pm._fit_params(tool, {"filename": "a.txt", "content_description": "x"}) == {
        "filename": "a.txt", "content_description": "x"}
    assert pm._fit_params(tool, {"file_name": "a.txt", "content_description": "x"}) == {
        "filename": "a.txt", "content_description": "x"}
    # file_content is not close to anything the tool takes.
    assert pm._fit_params(tool, {"file_name": "a.txt", "file_content": "x"}) is None


def test_validate_cleans_steps_and_warns(registry, monkeypatch):
    monkeypatch.setattr(pm, "preview_route", lambda *a, **k: None)
    data = {
        "goal": "Make a poem file",
        "steps": [
            {"title": "Write it", "action": "Write a two-line poem", "kind": "write", "tools": ["agent_task_execute"]},
            {"title": "Save", "action": "generate_file(filename='poem.txt', content_description='the poem')",
             "kind": "file", "tools": ["nonexistent"]},
            {"title": "Edit", "action": "Change backend/missing.py line 3", "kind": "code_change", "tools": ["edit_code"]},
        ] + [{"title": f"x{i}", "action": "Write more text here", "kind": "write"} for i in range(pmd.PLAN_MAX_STEPS)],
    }
    fields, warnings = pm.validate(data, registry=registry, haystack="make a poem file", session_id=None)
    steps = fields["steps"]
    assert len(steps) == pmd.PLAN_MAX_STEPS
    assert steps[0]["tools"] == []  # a writing step cannot carry a screen tool into the grants
    assert steps[1]["direct"] == {"direct_tool": "generate_file",
                                  "direct_tool_params": {"filename": "poem.txt", "content_description": "the poem"}}
    assert steps[2]["tools"] == ["edit_code"]
    assert any("only the first" in w for w in warnings)
    assert any("backend/missing.py" in w for w in warnings)
    # A file the plan will create is not "invented".
    assert not any("poem.txt" in w for w in warnings)


def test_paths_on_disk_are_not_invented(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "real.py").write_text("x = 1\n")
    assert not pm._invented("pkg/real.py", "", [str(tmp_path)])
    assert pm._invented("pkg/missing.py", "", [str(tmp_path)])
    assert not pm._invented("notes.txt", "", [str(tmp_path)])
    assert pm._invented("https://example.com/x", "", [str(tmp_path)])
    assert not pm._invented("https://example.com/x", "see https://example.com/x", [str(tmp_path)])


def test_compose_falls_back_to_a_numbered_list(monkeypatch):
    monkeypatch.setattr(pm, "_chat_json", lambda *a, **k: (None, "the model call failed"))
    result = pm.compose_plan("m", "s", "u", reply="I would:\n1. Read config.py\n2. Change the value to 9\n")
    assert result.kind == "plan" and result.path == "list"
    assert [s["action"] for s in result.data["steps"]] == ["Read config.py", "Change the value to 9"]


def test_compose_repairs_once_with_the_problem_quoted(monkeypatch):
    calls = []

    def fake(model, system, user, fmt, num_ctx):
        calls.append(user)
        if len(calls) == 1:
            return {"kind": "plan", "steps": [{"action": "edit_code"}]}, ""
        return {"kind": "plan", "steps": [{"action": "Change STEPS to 9 in config.py", "kind": "code_change"}]}, ""

    monkeypatch.setattr(pm, "_chat_json", fake)
    result = pm.compose_plan("m", "s", "u")
    assert result.path == "repair" and result.kind == "plan"
    assert "say in words" in calls[1]


def test_compose_gives_up_honestly(monkeypatch):
    monkeypatch.setattr(pm, "_chat_json", lambda *a, **k: (None, "the answer was not JSON"))
    result = pm.compose_plan("m", "s", "u", reply="no list here")
    assert result.kind is None


def test_wording_hash_changes_with_the_wording(monkeypatch):
    before = pmd.wording_sha()
    monkeypatch.setattr(pmd, "PLAN_MODE_RESEARCH_NOTE", pmd.PLAN_MODE_RESEARCH_NOTE + " ")
    assert pmd.wording_sha() != before


def test_availability_follows_the_measured_rows(monkeypatch):
    import backend.services.model_capability_data as mcd

    monkeypatch.setattr(mcd, "PLAN_MODE_MEASURED", {
        "low": {"lcb": 0.4, "wording_sha": pmd.wording_sha()},
        "high": {"lcb": 0.95, "wording_sha": pmd.wording_sha()},
    }, raising=False)
    assert pm.availability("low")[0] is False
    assert pm.availability("high")[0] is True
    assert pm.availability("never-measured") == (True, "not measured yet on this model")


def test_discord_chats_cannot_plan():
    assert not pm.plan_mode_supported("discord_123")
    assert pm.plan_mode_supported("web-abc")
