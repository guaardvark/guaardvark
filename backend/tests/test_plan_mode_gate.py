"""Plan mode sits in front of everything that can act in a chat turn, and an
approved plan's run changes which calls ask for approval.

No model, no database: the gate is driven through chat_turn.route_turn with
the plan-mode handler replaced, and the approval rules through the engine's
own functions.
"""
import pytest

import backend.services.unified_chat_engine as uce
from backend.services import chat_turn


class _Brain:
    def __init__(self):
        self.calls = []

    def process(self, **kw):
        self.calls.append(kw)


@pytest.fixture
def gate(monkeypatch):
    seen = []

    def handle_turn(**kw):
        seen.append(kw)
        return kw["options"].get("_handled", True)

    import backend.services.plan_mode as pm
    monkeypatch.setattr(pm, "handle_turn", handle_turn)
    monkeypatch.setattr("backend.services.slash_command_executor.run_direct_tool_turn",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("slash shortcut ran in plan mode")))
    return seen


def _route(options, brain):
    chat_turn.route_turn(session_id="s1", message="make it so", options=options,
                         emit_fn=lambda *a: None, app=None, agent_brain=brain)


def test_plan_mode_takes_the_turn_before_the_slash_shortcut_and_the_brain(gate):
    brain = _Brain()
    _route({"plan_mode": True, "slash_command": "imagine", "slash_args": "a fox"}, brain)
    assert len(gate) == 1 and brain.calls == []


def test_a_plan_step_is_not_planned_again(gate, monkeypatch):
    monkeypatch.setattr("backend.services.slash_command_executor.run_direct_tool_turn", lambda *a, **k: False)
    brain = _Brain()
    _route({"plan_mode": True, "plan_step": {"plan_id": "p", "index": 1}}, brain)
    assert gate == [] and len(brain.calls) == 1


def test_a_research_slash_command_passes_through(gate, monkeypatch):
    monkeypatch.setattr("backend.services.slash_command_executor.run_direct_tool_turn", lambda *a, **k: True)
    brain = _Brain()
    _route({"plan_mode": True, "_handled": False}, brain)
    assert len(gate) == 1 and brain.calls == []  # the shortcut handled it


def test_plan_mode_off_changes_nothing(gate, monkeypatch):
    monkeypatch.setattr("backend.services.slash_command_executor.run_direct_tool_turn", lambda *a, **k: False)
    brain = _Brain()
    _route({}, brain)
    assert gate == [] and len(brain.calls) == 1


class _T:
    def __init__(self, name, read_only, requires_approval=False):
        self.name, self.read_only, self.requires_approval = name, read_only, requires_approval


@pytest.fixture
def plan_run():
    uce._plan_runs.pop("s1", None)
    yield
    uce._plan_runs.pop("s1", None)


def test_ask_mode_shows_a_card_for_every_action(plan_run):
    uce.begin_plan_run("s1", "ask", ["generate_image"])
    assert uce._needs_card("s1", _T("generate_image", False), "generate_image", {}, set())
    assert not uce._needs_card("s1", _T("read_code", True), "read_code", {}, set())
    uce.end_plan_run("s1")
    assert not uce._needs_card("s1", _T("generate_image", False), "generate_image", {}, set())


def test_allow_mode_grants_the_plans_tools_for_the_run(plan_run, monkeypatch):
    monkeypatch.setattr("backend.services.tool_approval_prefs.always_approved", lambda: set())
    monkeypatch.setattr("backend.services.tool_approval_prefs.consent_gated", lambda t: t == "generate_identity")
    uce.begin_plan_run("s1", "allow_plan_tools", ["edit_code", "generate_identity"])
    uce.clear_task_scoped_tool_grants("s1")  # every step's turn clears task grants
    pre = uce._preapproved_tool_names("s1")
    assert "edit_code" in pre and "generate_identity" not in pre
    uce.end_plan_run("s1")
    assert "edit_code" not in uce._preapproved_tool_names("s1")
