"""Deterministic chat auto-capture: intents, scope, rejects, dedupe, engine wrap."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.db]

try:
    from flask import Flask

    from backend.api.memory_api import get_memories_for_context, pop_last_selected_ids
    from backend.models import AgentMemory, db
    from backend.services.memory_capture import _extract_fact, capture_from_message, will_capture
except Exception:  # pragma: no cover - environment without backend deps
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

ENGINE_PATH = (
    Path(__file__).resolve().parents[2] / "services" / "unified_chat_engine.py"
)


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


@pytest.mark.parametrize(
    "message, expected",
    [
        ("remember that the sky is blue", "the sky is blue"),
        ("Remember that the sky is blue", "the sky is blue"),
        ("remember: the sky is blue", "the sky is blue"),
        ("Remember: the sky is blue", "the sky is blue"),
        ("from now on use dark mode", "use dark mode"),
        ("From now on, always use dark mode", "always use dark mode"),
        ("for future reference the API lives in settings", "the API lives in settings"),
        ("For future reference, the API lives in settings", "the API lives in settings"),
    ],
)
def test_remember_forms_store_a_fact_for_every_chat(app, message, expected):
    mem_id = capture_from_message(message, session_id="s1", user_id="u1")
    assert mem_id is not None
    row = db.session.get(AgentMemory, mem_id)
    assert row is not None
    assert row.content == expected
    assert row.type == "fact"
    assert row.source == "chat"
    assert abs((row.importance or 0) - 0.7) < 1e-6
    assert row.session_id is None
    assert row.project_id is None
    assert row.user_id == "u1"


@pytest.mark.parametrize(
    "message, expected",
    [
        ("note that we prefer pytest", "we prefer pytest"),
        ("Note that we prefer pytest", "we prefer pytest"),
        ("my name is Alice", "my name is Alice"),
        ("our timezone is Pacific", "our timezone is Pacific"),
    ],
)
def test_other_intent_forms_store_a_fact_for_this_chat(app, message, expected):
    mem_id = capture_from_message(message, session_id="s1", user_id="u1")
    assert mem_id is not None
    row = db.session.get(AgentMemory, mem_id)
    assert row is not None
    assert row.content == expected
    assert row.type == "fact"
    assert row.source == "chat"
    assert abs((row.importance or 0) - 0.7) < 1e-6
    assert row.session_id == "s1"
    assert row.user_id == "u1"


@pytest.mark.parametrize(
    "message",
    [
        "remember that the sky is blue?",
        "note that we prefer pytest?",
        "is my name Alice?",
        "what is our timezone?",
    ],
)
def test_questions_are_not_captured(app, message):
    assert capture_from_message(message) is None
    assert AgentMemory.query.count() == 0


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "remember that x",
        "note that hi",
        "my name is",
        "ok thanks",
    ],
)
def test_short_messages_are_not_captured(app, message):
    assert capture_from_message(message) is None
    assert AgentMemory.query.count() == 0


def test_plain_chat_without_intent_is_ignored(app):
    assert capture_from_message("please index the docs folder now") is None
    assert AgentMemory.query.count() == 0


def test_dedupe_returns_existing_id(app):
    first = capture_from_message("remember that the sky is blue")
    second = capture_from_message("Remember that the sky is blue.")
    assert first is not None
    assert second == first
    assert AgentMemory.query.filter_by(type="fact").count() == 1


def test_note_in_another_chat_is_kept_for_that_chat(app):
    """A copy kept for one chat is not recalled in another, so it does not
    stop the same note from being stored there."""
    first = capture_from_message("note that the deploy target is staging", session_id="chat-one")
    second = capture_from_message("Note that the deploy target is staging.", session_id="chat-two")
    assert second not in (None, first)
    assert db.session.get(AgentMemory, second).session_id == "chat-two"
    assert capture_from_message("note that the deploy target is staging", session_id="chat-two") == second
    assert AgentMemory.query.count() == 2


def test_note_reuses_a_fact_every_chat_already_recalls(app):
    remembered = capture_from_message("remember that the deploy target is staging", session_id="chat-one")
    noted = capture_from_message("note that the deploy target is staging", session_id="chat-two")
    assert noted == remembered
    assert AgentMemory.query.count() == 1


def test_remember_in_a_project_chat_stays_in_that_project(app):
    mem_id = capture_from_message(
        "remember that the build uses ninja", session_id="s1", project_id=7
    )
    row = db.session.get(AgentMemory, mem_id)
    assert row.session_id is None
    assert row.project_id == 7


def test_other_intent_in_a_project_chat_keeps_chat_and_project(app):
    mem_id = capture_from_message(
        "note that the build uses ninja", session_id="s1", project_id=7
    )
    row = db.session.get(AgentMemory, mem_id)
    assert row.session_id == "s1"
    assert row.project_id == 7


def test_remembered_fact_is_recalled_in_a_new_chat(app):
    mem_id = capture_from_message(
        "remember that my favourite colour is teal", session_id="chat-one"
    )
    assert mem_id is not None
    pop_last_selected_ids()

    block = get_memories_for_context(
        query="what is my favourite colour?", session_id="chat-two"
    )

    assert "my favourite colour is teal" in block
    assert mem_id in pop_last_selected_ids()


def test_chat_scoped_capture_is_not_recalled_in_a_new_chat(app):
    capture_from_message("note that the deploy target is staging", session_id="chat-one")

    elsewhere = get_memories_for_context(query="deploy target", session_id="chat-two")
    same_chat = get_memories_for_context(query="deploy target", session_id="chat-one")

    assert "deploy target is staging" not in elsewhere
    assert "deploy target is staging" in same_chat


def test_project_remember_is_not_recalled_in_another_project(app):
    capture_from_message(
        "remember that the build uses ninja", session_id="chat-one", project_id=7
    )

    other_project = get_memories_for_context(
        query="which build tool?", session_id="chat-two", project_id=9
    )
    same_project = get_memories_for_context(
        query="which build tool?", session_id="chat-two", project_id=7
    )

    assert "build uses ninja" not in other_project
    assert "build uses ninja" in same_project


def test_remember_does_not_reuse_a_copy_scoped_to_another_chat(app):
    db.session.add(AgentMemory(
        id="old-chat-copy",
        content="my favourite colour is teal",
        source="chat",
        type="fact",
        importance=0.7,
        status="active",
        session_id="chat-zero",
    ))
    db.session.commit()

    mem_id = capture_from_message(
        "remember that my favourite colour is teal", session_id="chat-one"
    )

    assert mem_id not in (None, "old-chat-copy")
    assert db.session.get(AgentMemory, mem_id).session_id is None
    assert db.session.get(AgentMemory, "old-chat-copy").session_id == "chat-zero"


def test_remember_reuses_a_copy_every_chat_already_recalls(app):
    db.session.add_all([
        AgentMemory(
            id="global-copy", content="the sky is blue", source="chat",
            type="fact", importance=0.7, status="active",
        ),
        AgentMemory(
            id="project-five-copy", content="the build uses ninja", source="chat",
            type="fact", importance=0.7, status="active", project_id=5,
        ),
    ])
    db.session.commit()

    assert capture_from_message("remember that the sky is blue", session_id="s1") == "global-copy"
    assert capture_from_message(
        "remember that the sky is blue", session_id="s1", project_id=7
    ) == "global-copy"

    in_project_seven = capture_from_message(
        "remember that the build uses ninja", session_id="s1", project_id=7
    )
    assert in_project_seven != "project-five-copy"
    assert db.session.get(AgentMemory, in_project_seven).project_id == 7


def test_engine_call_site_is_exception_safe(monkeypatch):
    """Capture sits in a try/except Exception immediately after the user save."""
    text = ENGINE_PATH.read_text()
    marker = "# 5. Save user message to DB"
    idx = text.find(marker)
    assert idx != -1
    step_end = text.find("# 6. ReACT loop", idx)
    assert step_end != -1
    window = text[idx:step_end]
    assert "self._save_message" in window
    save_at = window.find("self._save_message")
    capture_at = window.find("capture_from_message")
    except_at = window.find("except Exception")
    assert capture_at != -1
    assert except_at != -1
    assert save_at < capture_at < except_at

    from backend.services import memory_capture as mc

    def boom(*_a, **_k):
        raise RuntimeError("storage down")

    monkeypatch.setattr(mc, "capture_from_message", boom)

    try:
        from backend.services.memory_capture import capture_from_message as _capture

        _capture("remember that the sky is blue")
        raised = False
    except Exception:
        raised = True
    # The raw helper may raise; the engine wrap must not. Reproduce that wrap.
    assert raised is True

    logged = {"hit": False}

    def engine_call_site(message):
        try:
            from backend.services.memory_capture import capture_from_message

            capture_from_message(message)
        except Exception:
            logged["hit"] = True

    engine_call_site("remember that the sky is blue")
    assert logged["hit"] is True


@pytest.mark.parametrize(
    "message, stored",
    [
        ("remember that the sky is blue", True),
        ("from now on use dark mode", True),
        ("for future reference the API lives in settings", True),
        ("note that the build takes ten minutes", False),
        ("my goal is to research local models", False),
        ("our problem is the build keeps failing", False),
    ],
)
def test_explicit_only_keeps_just_the_asked_for_forms(app, message, stored):
    """The brain paths (Tier 3, screen-direct) carry task requests, so they
    pass explicit_only and "my X is Y" / "note that" are left alone there."""
    mem_id = capture_from_message(message, session_id="s1", explicit_only=True)
    assert (mem_id is not None) is stored
    with app.app_context():
        assert (AgentMemory.query.count() == 1) is stored


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Remember that the oven runs ten degrees hot. First add 2 and 3, then multiply by 4.",
         "the oven runs ten degrees hot."),
        ("remember that the office printer is on the second floor, then open the printer page",
         "the office printer is on the second floor"),
        ("remember that the backup runs at 2am; and then check the log", "the backup runs at 2am"),
        ("from now on reply in French. Now summarise this page", "reply in French."),
        ("remember that if the build fails, then rerun it with --clean",
         "if the build fails, then rerun it with --clean"),
        ("remember that the server is at 10.0.0.5", "the server is at 10.0.0.5"),
    ],
)
def test_explicit_only_stops_the_fact_where_the_task_begins(message, expected):
    """A brain-path turn is a task request: the stored fact is the part the
    person asked to keep, not the instructions that follow it."""
    assert _extract_fact(message, explicit_only=True) == expected


def test_engine_path_keeps_the_whole_remainder():
    message = "remember that the oven runs hot. It was serviced in May."
    assert _extract_fact(message) == "the oven runs hot. It was serviced in May."


@pytest.mark.parametrize(
    "message, captured",
    [
        ("Remember that my favourite tea is peppermint.", True),
        ("note that the build takes ten minutes", True),
        ("my favourite colour is green", True),
        ("remember my favourite tea is peppermint", True),
        ("my code is throwing an error", False),
        ("Save a memory that my desk lamp is warm white.", False),
        ("what is my favourite tea?", False),
    ],
)
def test_will_capture_matches_what_capture_stores(message, captured):
    """The engine drops save_memory from a turn the capture already stores,
    so the two must agree on which messages those are."""
    assert will_capture(message) is captured



@pytest.mark.parametrize("explicit_only", [False, True])
def test_remember_my_x_is_y_is_kept_for_every_chat(app, explicit_only):
    mem_id = capture_from_message(
        "remember my favourite colour is teal", session_id="s1", project_id=7,
        explicit_only=explicit_only,
    )
    row = db.session.get(AgentMemory, mem_id)
    assert row.content == "my favourite colour is teal"
    assert row.session_id is None
    assert row.project_id == 7


@pytest.mark.parametrize("message", [
    "remember my keys before leaving",
    "remember to call the plumber tomorrow",
])
def test_remember_without_a_fact_is_not_captured(app, message):
    assert capture_from_message(message, session_id="s1") is None
    assert AgentMemory.query.count() == 0


@pytest.mark.parametrize("message", [
    "my code is throwing an error",
    "our build is failing on the second step",
    "my script is not working after the update",
    "my app is broken since this morning",
    "my laptop is in the shop until Friday",
    "my connection is slow right now",
])
def test_a_problem_report_or_passing_state_is_not_a_fact(app, message):
    """The chat's "my X is Y" capture keeps lasting facts only; the memory_fact
    question in decision_data.py names these as not facts."""
    assert capture_from_message(message, session_id="s1") is None
    assert AgentMemory.query.count() == 0


@pytest.mark.parametrize("message", [
    "my favourite colour is teal",
    "our office is in Leeds",
    "my server is at 10.0.0.5",
])
def test_lasting_facts_are_still_captured(app, message):
    assert capture_from_message(message, session_id="s1") is not None


def test_an_explicit_remember_keeps_whatever_it_says(app):
    mem_id = capture_from_message("remember that my code is throwing an error", session_id="s1")
    assert db.session.get(AgentMemory, mem_id).content == "my code is throwing an error"


def test_a_chat_outside_any_project_does_not_recall_project_facts(app):
    capture_from_message("remember that the build uses ninja", session_id="chat-one", project_id=7)
    capture_from_message("remember that the build log lives in ci", session_id="chat-one")

    no_project = get_memories_for_context(query="build", session_id="chat-two")
    in_project = get_memories_for_context(query="build", session_id="chat-two", project_id=7)
    no_session = get_memories_for_context(query="build")

    assert "build uses ninja" not in no_project
    assert "build log lives in ci" in no_project
    assert "build uses ninja" in in_project and "build log lives in ci" in in_project
    # A caller with no session (the agent executor's prompt) may not know its
    # project, so it is not narrowed.
    assert "build uses ninja" in no_session


def test_save_memory_in_a_project_chat_keeps_the_project(app):
    from backend.tools.memory_tools import SaveMemoryTool

    result = SaveMemoryTool().execute(
        content="Prefers metric units",
        _agent_context={"transport": "chat", "project_id": 7},
    )
    assert result.success
    row = AgentMemory.query.filter_by(content="Prefers metric units").one()
    assert row.project_id == 7
    assert row.session_id is None


def test_the_engine_hands_save_memory_the_chats_project():
    """The model's tool calls and the direct path (/remember) both pass the
    chat's project to the tool."""
    text = ENGINE_PATH.read_text()
    loop = text[text.index('"transport": "chat",'):]
    assert '"project_id": getattr(self, "_project_id", None)' in loop[:400]
    direct = text[text.index("def _run_direct_tool_execution"):]
    direct = direct[direct.index("self.registry.execute_tool("):][:300]
    assert '"project_id": getattr(self, "_project_id", None)' in direct
