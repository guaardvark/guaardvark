"""Stop ends a Tier 3 run: the executor runs no further step, and the brain
neither sends nor saves a reply the chat has already closed."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.services import agent_control_service
from backend.services.agent_brain import AgentBrain
from backend.services.agent_executor import AgentExecutor
from backend.services.agent_tools import ToolRegistry


class _UncalledLLM:
    model = ""

    def chat(self, *args, **kwargs):
        raise AssertionError("the model was called after Stop")

    def chat_with_tools(self, *args, **kwargs):
        raise AssertionError("the model was called after Stop")


def test_executor_runs_no_step_once_stopped():
    agent_control_service.set_chat_stop_check(lambda: True)
    try:
        result = AgentExecutor(ToolRegistry(), _UncalledLLM(), max_iterations=3).execute("look this up")
    finally:
        agent_control_service.set_chat_stop_check(None)
    assert result.success is False
    assert result.error == "Stopped."
    assert result.iterations == 0


def test_deliberate_sends_and_saves_nothing_after_stop():
    brain = AgentBrain.__new__(AgentBrain)
    brain.state = SimpleNamespace(
        health=SimpleNamespace(llm_available=True, tools_available=True),
        tool_registry=MagicMock(), llm=SimpleNamespace(model="m"),
        max_agent_iterations=3, active_model="m",
    )
    executor = MagicMock()
    executor.execute.return_value = SimpleNamespace(
        success=True, final_answer="Late answer.", error=None, verified=None, iterations=2,
    )
    saved, sent = [], []
    with patch("backend.services.agent_executor.AgentExecutor", return_value=executor), \
            patch("backend.services.agent_brain.is_aborted", return_value=True), \
            patch("backend.services.agent_brain._persist_turn",
                  side_effect=lambda app, sid, role, content, *a, **k: saved.append(role)), \
            patch("backend.services.agent_control_service.get_agent_control_service",
                  return_value=MagicMock(drain_thinking_steps=lambda: [], drain_recipe_usage=lambda: {})), \
            patch.object(AgentBrain, "_emit_response", lambda *a, **k: sent.append(a)):
        result = brain._deliberate("sess-9", "research this and then write it up", {},
                                   lambda *a, **k: None, Flask(__name__))
    assert result["aborted"] is True
    assert "assistant" not in saved
    assert sent == []
