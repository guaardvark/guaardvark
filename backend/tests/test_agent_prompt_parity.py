"""The unified prompt must not change when its blocks are shared with split mode."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests._gen_unified_golden import build  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "unified_prompt_golden.txt")


def test_unified_prompt_is_byte_identical_to_the_golden():
    with open(GOLDEN) as f:
        expected = f.read()
    got = build()
    assert got == expected, "the unified prompt changed; if intentional, regenerate the golden deliberately"


def _svc():
    from backend.services.agent_control_service import AgentControlService
    svc = AgentControlService()
    svc._pending_world_observed = "WORLD_OBSERVED: fixture"
    svc._failure_reports = []
    svc._current_budget = None
    return svc


def test_split_prompt_has_what_unified_has():
    from unittest.mock import patch
    from backend.services.agent_control_service import AgentControlService, ActionStep, AgentAction
    svc = _svc()
    hist = [ActionStep(iteration=1, action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
            ActionStep(iteration=2, action=AgentAction(action_type="scroll", scroll_amount=3), failed=True)]
    with patch.object(AgentControlService, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
         patch.object(AgentControlService, "_format_dom_grounding_for_prompt", lambda self: ""):
        p = svc._build_decision_prompt("open youtube", "a page with a video", hist,
                                       training_mode=True, chat_context="user said hi")
    assert svc._proof_contract is True, "split mode must ask for a proof so the done-guard enforces one"
    assert '"success_proof"' in p and '"tool"' in p
    assert "STOP." in p, "the 2-identical pivot must reach the brain before the loop breaker fires"
    assert "TRAINING MODE" in p and "Earlier messages in this chat" in p
    assert "WORLD_OBSERVED: fixture" in p
    assert svc._pending_world_observed == "", "re-grounding output must be consumed, not left to pad every prompt"
    assert "target_description rules" in p


def test_mouse_only_schema_still_asks_for_a_proof():
    from unittest.mock import patch
    from backend.services.agent_control_service import AgentControlService
    svc = _svc(); svc._mouse_only = True
    with patch.object(AgentControlService, "_get_desktop_state", staticmethod(lambda display=None: "d")), \
         patch.object(AgentControlService, "_format_dom_grounding_for_prompt", lambda self: ""):
        p = svc._build_decision_prompt("t", "s", [])
    assert "MOUSE ONLY" in p and '"success_proof"' in p


def test_full_knowledge_is_a_superset_of_compact():
    from backend.services.agent_control_service import AgentControlService as A
    compact = A._build_persistent_knowledge_system()
    full = A._build_persistent_knowledge_system(task="open youtube", full=True)
    assert full.startswith(compact)


def test_split_prompt_carries_the_same_task_memory():
    """The non-consecutive repeat (A, B, C, D, A) reaches a blind brain too."""
    from unittest.mock import patch
    from backend.services.agent_control_service import AgentControlService, ActionStep, AgentAction
    svc = _svc()
    hist = [ActionStep(iteration=i, action=AgentAction(action_type="click", target_description=t))
            for i, t in enumerate(("red dot A", "blue dot B", "green dot C", "orange dot D", "red dot A"), 1)]
    with patch.object(AgentControlService, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
         patch.object(AgentControlService, "_format_dom_grounding_for_prompt", lambda self: ""):
        unified = svc._build_unified_prompt("click each dot", hist, training_mode=False)
        svc._pending_world_observed = "WORLD_OBSERVED: fixture"
        split = svc._build_decision_prompt("click each dot", "five dots", hist, training_mode=False)
    done = svc._history_block(hist, svc.config.max_iterations)
    assert done.startswith("Done (steps: 5, click attempts: 5):")
    assert done in unified and done in split
    note = 'Already clicked [OK] more than once: "red dot A" x2'
    assert note in unified and note in split


def test_the_budget_line_is_gone_from_both_screen_prompts():
    """The screen loop stops on its own stall rule; "[BUDGET: n/20 steps left]"
    beside a task that states its own budget only misled the model."""
    from unittest.mock import patch
    from backend.services.agent_control_service import AgentControlService
    from backend.services.step_budget import StepBudget
    svc = _svc()
    svc._current_budget = StepBudget(total=5)
    with patch.object(AgentControlService, "_get_desktop_state", staticmethod(lambda display=None: "d")), \
         patch.object(AgentControlService, "_format_dom_grounding_for_prompt", lambda self: ""):
        unified = svc._build_unified_prompt("t", [])
        split = svc._build_decision_prompt("t", "s", [])
    assert "[BUDGET:" not in unified and "[BUDGET:" not in split

