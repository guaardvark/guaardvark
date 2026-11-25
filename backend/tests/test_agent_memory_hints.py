"""The agent executor and the screen agent's launcher recovery read saved
memories through search_memories: a list of dicts, ranked by how well each
memory matches the task, with low-importance and non-matching rows left out."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, AgentMemory


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _mem(mid, content, importance=0.8, mem_type="fact"):
    m = AgentMemory(id=mid, content=content, source="manual", type=mem_type,
                    importance=importance, confidence=1.0, status="active")
    db.session.add(m)
    db.session.commit()
    return m


NOTES_FACT = "Notes are stored in the Projects folder on the shared drive"


def test_search_returns_dicts_and_honours_min_importance(app):
    _mem("keep", NOTES_FACT, importance=0.8)
    _mem("low", "Old notes are stored in a drawer", importance=0.2)

    from backend.api.memory_api import search_memories
    found = search_memories(query="where are my notes stored?", limit=8, min_importance=0.4)

    assert [m["id"] for m in found] == ["keep"]
    assert found[0]["content"] == NOTES_FACT
    assert found[0]["type"] == "fact"


def test_match_text_scores_filters_and_counts_only_what_it_returns(app):
    hit = _mem("hit", "Firefox icon on the desktop needs a double click to launch", mem_type="note")
    miss = _mem("miss", "Double click a file to open it", mem_type="note")

    from backend.api.memory_api import search_memories
    # The screen agent's launcher-recovery call, argument for argument.
    found = search_memories(
        query="launcher icon click failed for Firefox icon",
        limit=3, min_importance=0.3,
        match_text="Firefox icon", min_match=0.2,
    )

    assert [m["id"] for m in found] == ["hit"]
    assert found[0]["match_score"] > 0.2
    assert hit.access_count == 1
    assert not miss.access_count


@pytest.fixture
def no_brain_state(monkeypatch):
    """The executor looks memories up itself only while BrainState is not
    initialized (its system prompt carries the memory block otherwise), so
    these tests start from a process where nothing has booted it."""
    from backend.services.brain_state import BrainState
    monkeypatch.setattr(BrainState, "_instance", None)


class _ScriptedLLM:
    """Answers at once and keeps every message it was sent."""
    model = ""

    def __init__(self):
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append([m.content for m in messages])
        reply = json.dumps({"thoughts": "done", "tool_calls": [], "final_answer": "On the shared drive."})
        return SimpleNamespace(message=SimpleNamespace(content=reply))


def test_agent_executor_puts_a_matching_memory_in_the_prompt(app, no_brain_state):
    _mem("m1", NOTES_FACT)
    _mem("m2", "The team meeting is on Tuesday at noon")

    from backend.services.agent_executor import AgentExecutor
    from backend.services.agent_tools import ToolRegistry

    llm = _ScriptedLLM()
    executor = AgentExecutor(ToolRegistry(), llm, max_iterations=1)
    executor.coordinator = None
    result = executor.execute("where are my notes stored?")

    assert result.success is True
    first_turn = "\n".join(llm.prompts[0])
    assert "Saved memories that match this task:" in first_turn
    assert f"- {NOTES_FACT}" in first_turn
    assert "team meeting" not in first_turn


def test_agent_executor_adds_no_memory_block_when_nothing_matches(app, no_brain_state):
    _mem("m2", "The team meeting is on Tuesday at noon")

    from backend.services.agent_executor import AgentExecutor
    from backend.services.agent_tools import ToolRegistry

    llm = _ScriptedLLM()
    executor = AgentExecutor(ToolRegistry(), llm, max_iterations=1)
    executor.coordinator = None
    executor.execute("where are my notes stored?")

    assert "Saved memories that match this task:" not in "\n".join(llm.prompts[0])
