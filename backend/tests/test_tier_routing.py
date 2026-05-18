#!/usr/bin/env python3
"""Tests for tier routing — escalation, deliberation heuristic, vision detection."""

import pytest

from backend.services.agent_brain import (
    AgentBrain,
    CONVERSATIONAL_PASSTHROUGH,
    DELIBERATION_SIGNALS,
    VISION_PATTERNS,
)
from backend.services.brain_state import BrainState, _build_default_reflexes


@pytest.fixture(autouse=True)
def reset_singleton():
    BrainState.reset()
    yield
    BrainState.reset()


@pytest.fixture
def brain():
    state = BrainState.get_instance()
    state.reflexes = _build_default_reflexes(tool_registry=None)
    state.health.reflexes_loaded = True
    state.health.llm_available = False  # prevent actual LLM calls
    state.health.tools_available = False
    return AgentBrain(state=state)


# ---------------------------------------------------------------------------
# Deliberation heuristic
# ---------------------------------------------------------------------------

class TestNeedsDeliberation:
    def test_multi_step_detected(self, brain):
        assert brain._needs_deliberation(
            "First research the topic, then write a report"
        )

    def test_research_and_create(self, brain):
        assert brain._needs_deliberation(
            "Research quantum computing and create a summary"
        )

    def test_analyze_and_improve(self, brain):
        assert brain._needs_deliberation(
            "Analyze the code and then improve its performance"
        )

    def test_compare_and_recommend(self, brain):
        assert brain._needs_deliberation(
            "Compare these two approaches and then recommend the best one"
        )

    def test_help_figure_out(self, brain):
        assert brain._needs_deliberation(
            "Help me figure out the best approach for this"
        )

    def test_simple_question_not_deliberation(self, brain):
        assert not brain._needs_deliberation("What is the weather today?")

    def test_simple_command_not_deliberation(self, brain):
        assert not brain._needs_deliberation("Analyze this website")

    def test_empty_not_deliberation(self, brain):
        assert not brain._needs_deliberation("")

    def test_greeting_not_deliberation(self, brain):
        assert not brain._needs_deliberation("hello")


# ---------------------------------------------------------------------------
# Vision detection
# ---------------------------------------------------------------------------

class TestVisionDetection:
    def test_virtual_screen(self, brain):
        assert brain._is_vision_task("Check the virtual screen")

    def test_agent_screen(self, brain):
        assert brain._is_vision_task("Use the agent screen to navigate")

    def test_your_screen(self, brain):
        assert brain._is_vision_task("What's on your screen?")

    def test_slash_vision(self, brain):
        assert brain._is_vision_task("/vision take a screenshot")

    def test_slash_agent(self, brain):
        assert brain._is_vision_task("/agent open youtube")

    def test_image_data_is_vision(self, brain):
        assert brain._is_vision_task("describe this", image_data="base64data")

    def test_normal_message_not_vision(self, brain):
        assert not brain._is_vision_task("What is the capital of France?")

    def test_website_analysis_not_vision(self, brain):
        assert not brain._is_vision_task("Analyze this website for SEO")


# ---------------------------------------------------------------------------
# Conversational passthrough
# ---------------------------------------------------------------------------

class TestConversationalPassthrough:
    def test_yes_is_conversational(self):
        assert CONVERSATIONAL_PASSTHROUGH.match("yes")

    def test_no_is_conversational(self):
        assert CONVERSATIONAL_PASSTHROUGH.match("no")

    def test_ok_is_conversational(self):
        assert CONVERSATIONAL_PASSTHROUGH.match("ok")

    def test_sure_is_conversational(self):
        assert CONVERSATIONAL_PASSTHROUGH.match("sure")

    def test_sounds_good_is_conversational(self):
        assert CONVERSATIONAL_PASSTHROUGH.match("sounds good")

    def test_complex_sentence_not_conversational(self):
        assert not CONVERSATIONAL_PASSTHROUGH.match(
            "Yes, please analyze the website"
        )

    def test_question_not_conversational(self):
        assert not CONVERSATIONAL_PASSTHROUGH.match(
            "What do you think about this approach?"
        )


# ---------------------------------------------------------------------------
# Tier routing integration
# ---------------------------------------------------------------------------

class TestTierRouting:
    def test_greeting_routes_to_tier1(self, brain):
        """Greeting should be handled by reflex and return immediately."""
        emit_calls = []
        def mock_emit(event, data):
            emit_calls.append((event, data))

        result = brain.process(
            session_id="test",
            message="hello",
            options={},
            emit_fn=mock_emit,
        )

        assert result["success"] is True
        assert result["tier"] == 1
        assert len(result["response"]) > 0

    def test_farewell_routes_to_tier1(self, brain):
        emit_calls = []
        result = brain.process(
            session_id="test",
            message="goodbye",
            options={},
            emit_fn=lambda e, d: emit_calls.append((e, d)),
        )
        assert result["tier"] == 1

    def test_thanks_routes_to_tier1(self, brain):
        emit_calls = []
        result = brain.process(
            session_id="test",
            message="thanks!",
            options={},
            emit_fn=lambda e, d: emit_calls.append((e, d)),
        )
        assert result["tier"] == 1

    def test_complex_message_routes_to_tier2(self, brain):
        """Non-greeting, non-deliberation message should go to Tier 2."""
        # Since LLM is unavailable, Tier 2 will return an error
        result = brain.process(
            session_id="test",
            message="What is the capital of France?",
            options={},
            emit_fn=lambda e, d: None,
        )
        # Should attempt Tier 2 (which fails due to no LLM)
        assert result.get("tier") == 2

    def test_multi_step_routes_to_tier3(self, brain):
        """Multi-step request should route to Tier 3."""
        result = brain.process(
            session_id="test",
            message="Research quantum computing and then write a summary",
            options={},
            emit_fn=lambda e, d: None,
        )
        # Tier 3 falls back to Tier 2 (no tools), which fails (no LLM)
        assert result.get("tier") in (2, 3)

    def test_force_tier3(self, brain):
        """force_tier=3 should skip reflexes and go to Tier 3."""
        result = brain.process(
            session_id="test",
            message="hello",  # would normally be a reflex
            options={},
            emit_fn=lambda e, d: None,
            force_tier=3,
        )
        # Tier 3 degrades because no tools and no LLM
        assert result.get("tier") in (2, 3)

    def test_conversational_passthrough_to_tier2(self, brain):
        """Bare 'yes' should go to Tier 2 (not Tier 1)."""
        result = brain.process(
            session_id="test",
            message="yes",
            options={},
            emit_fn=lambda e, d: None,
        )
        assert result.get("tier") == 2

    def test_emit_events_on_tier1(self, brain):
        """Tier 1 should emit chat:response and chat:complete."""
        events = []
        brain.process(
            session_id="test",
            message="hello",
            options={},
            emit_fn=lambda e, d: events.append(e),
        )
        assert "chat:response" in events
        assert "chat:complete" in events
