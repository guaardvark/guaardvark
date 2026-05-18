#!/usr/bin/env python3
"""Tests for narration-instead-of-action bug fix — pattern matching + param inference."""

from unittest.mock import MagicMock

import pytest

from backend.services.agent_brain import AgentBrain, NARRATION_PATTERNS, TOOL_PARAM_EXTRACTORS
from backend.services.brain_state import BrainState


@pytest.fixture(autouse=True)
def reset_singleton():
    BrainState.reset()
    yield
    BrainState.reset()


@pytest.fixture
def brain_with_tools():
    """AgentBrain with a mock tool registry that has common tools."""
    state = BrainState.get_instance()

    registry = MagicMock()
    # Simulate tools that exist
    existing_tools = {
        "web_search", "analyze_website", "generate_image", "codegen",
        "generate_file", "media_play",
    }
    registry.get_tool.side_effect = lambda name: (
        MagicMock() if name in existing_tools else None
    )
    registry.execute_tool.return_value = MagicMock(
        success=True, output="Tool executed successfully"
    )

    state.tool_registry = registry
    state.health.tools_available = True
    return AgentBrain(state=state)


# ---------------------------------------------------------------------------
# Narration pattern matching
# ---------------------------------------------------------------------------

class TestNarrationPatterns:
    def test_should_use(self):
        for pattern in NARRATION_PATTERNS:
            m = pattern.search("I should use the web_search tool to find information")
            if m:
                assert m.group(1) == "web_search"
                return
        pytest.fail("No pattern matched 'I should use the web_search tool'")

    def test_will_use(self):
        for pattern in NARRATION_PATTERNS:
            m = pattern.search("I will use the analyze_website tool")
            if m:
                assert m.group(1) == "analyze_website"
                return
        pytest.fail("No pattern matched 'I will use the analyze_website tool'")

    def test_let_me_use(self):
        for pattern in NARRATION_PATTERNS:
            m = pattern.search("Let me use the generate_image tool")
            if m:
                assert m.group(1) == "generate_image"
                return
        pytest.fail("No pattern matched 'Let me use the generate_image tool'")

    def test_ill_call(self):
        for pattern in NARRATION_PATTERNS:
            m = pattern.search("I'll call web_search to look this up")
            if m:
                assert m.group(1) == "web_search"
                return
        pytest.fail("No pattern matched \"I'll call web_search\"")

    def test_need_to_use(self):
        for pattern in NARRATION_PATTERNS:
            m = pattern.search("I need to use the codegen tool for this")
            if m:
                assert m.group(1) == "codegen"
                return
        pytest.fail("No pattern matched 'I need to use the codegen tool'")

    def test_no_match_for_normal_response(self):
        text = "The capital of France is Paris."
        for pattern in NARRATION_PATTERNS:
            assert not pattern.search(text)

    def test_no_match_for_tool_result(self):
        text = "Based on the web search results, here is what I found..."
        for pattern in NARRATION_PATTERNS:
            assert not pattern.search(text)


# ---------------------------------------------------------------------------
# Parameter inference
# ---------------------------------------------------------------------------

class TestParameterInference:
    def test_web_search_extracts_query(self):
        extractor = TOOL_PARAM_EXTRACTORS["web_search"]
        params = extractor("what is quantum computing")
        assert params == {"query": "what is quantum computing"}

    def test_analyze_website_extracts_url(self):
        extractor = TOOL_PARAM_EXTRACTORS["analyze_website"]
        params = extractor("analyze https://example.com for SEO")
        assert params["url"] == "https://example.com"

    def test_analyze_website_extracts_domain(self):
        extractor = TOOL_PARAM_EXTRACTORS["analyze_website"]
        params = extractor("check out www.example.com")
        assert params["url"] == "www.example.com"

    def test_analyze_website_no_url(self):
        extractor = TOOL_PARAM_EXTRACTORS["analyze_website"]
        params = extractor("analyze something")
        assert params["url"] is None

    def test_generate_image_extracts_prompt(self):
        extractor = TOOL_PARAM_EXTRACTORS["generate_image"]
        params = extractor("a sunset over mountains")
        assert params == {"prompt": "a sunset over mountains"}

    def test_codegen_extracts_description(self):
        extractor = TOOL_PARAM_EXTRACTORS["codegen"]
        params = extractor("write a function to sort a list")
        assert params == {"description": "write a function to sort a list"}


# ---------------------------------------------------------------------------
# Full narration extraction flow
# ---------------------------------------------------------------------------

class TestNarrationExtraction:
    def test_extracts_known_tool(self, brain_with_tools):
        result = brain_with_tools._extract_narrated_tool_intent(
            "I should use the web_search tool to find this information",
            "what is quantum computing",
        )
        assert result is not None
        tool_name, params = result
        assert tool_name == "web_search"
        assert params["query"] == "what is quantum computing"

    def test_extracts_analyze_website(self, brain_with_tools):
        result = brain_with_tools._extract_narrated_tool_intent(
            "I will use the analyze_website tool to check this",
            "analyze https://example.com",
        )
        assert result is not None
        tool_name, params = result
        assert tool_name == "analyze_website"
        assert params["url"] == "https://example.com"

    def test_returns_none_for_unknown_tool(self, brain_with_tools):
        result = brain_with_tools._extract_narrated_tool_intent(
            "I should use the quantum_analyzer tool",
            "analyze quantum states",
        )
        assert result is None

    def test_returns_none_for_no_narration(self, brain_with_tools):
        result = brain_with_tools._extract_narrated_tool_intent(
            "The answer is 42.",
            "what is the meaning of life",
        )
        assert result is None

    def test_returns_none_when_params_cant_be_inferred(self, brain_with_tools):
        """Tool exists but has no parameter extractor -- should not guess."""
        result = brain_with_tools._extract_narrated_tool_intent(
            "I should use the generate_file tool",
            "create a configuration",
        )
        assert result is None

    def test_returns_none_when_no_registry(self):
        state = BrainState.get_instance()
        state.tool_registry = None
        brain = AgentBrain(state=state)
        result = brain._extract_narrated_tool_intent(
            "I should use web_search", "test"
        )
        assert result is None

    def test_analyze_website_no_url_returns_none(self, brain_with_tools):
        """If analyze_website can't find a URL, don't attempt the call."""
        result = brain_with_tools._extract_narrated_tool_intent(
            "I should use the analyze_website tool to check this",
            "analyze the competition",  # no URL in message
        )
        assert result is None
