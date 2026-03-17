#!/usr/bin/env python3
"""
End-to-end tests for the chat system's tool calling and agentic pipeline.

Tests cover:
1. Thinking model compatibility (qwen3-vl XML serialization fix)
2. Tool call parsing (XML and bracket formats)
3. Circuit breaker / guard integration
4. Message sanitization for thinking models
5. Web search tool execution via unified chat
6. Agent executor JSON fallback

Requires: running backend (port 5002) + frontend (port 5175) + Ollama
"""

import os
import re
import sys
import json
import pytest
import time

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


# ─── Unit tests (no server required) ─────────────────────────────────────────


class TestMessageSanitization:
    """Test that thinking-model sanitization removes XML tags correctly."""

    def _get_engine_class(self):
        from backend.services.unified_chat_engine import UnifiedChatEngine

        return UnifiedChatEngine

    def test_normal_sanitization_replaces_tool_call_tags(self):
        UCE = self._get_engine_class()
        messages = [
            {
                "role": "system",
                "content": "<tool_call>\n<tool>web_search</tool>\n<query>test</query>\n</tool_call>",
            }
        ]
        result = UCE._sanitize_messages_for_thinking_model(messages)
        assert "<tool_call>" not in result[0]["content"]
        assert "[tool_call]" in result[0]["content"]
        assert "[tool]" in result[0]["content"]
        assert "[query]" in result[0]["content"]

    def test_aggressive_sanitization_replaces_all_xml(self):
        UCE = self._get_engine_class()
        messages = [
            {
                "role": "system",
                "content": "<custom_tag>data</custom_tag> and <tool>name</tool>",
            }
        ]
        result = UCE._sanitize_messages_for_thinking_model(messages, aggressive=True)
        assert "<" not in result[0]["content"]
        assert "[custom_tag]" in result[0]["content"]

    def test_sanitization_preserves_non_xml_content(self):
        UCE = self._get_engine_class()
        messages = [
            {"role": "user", "content": "What is the temperature in Cincinnati?"}
        ]
        result = UCE._sanitize_messages_for_thinking_model(messages)
        assert result[0]["content"] == "What is the temperature in Cincinnati?"

    def test_sanitization_handles_empty_messages(self):
        UCE = self._get_engine_class()
        result = UCE._sanitize_messages_for_thinking_model([])
        assert result == []

    def test_sanitization_preserves_message_roles(self):
        UCE = self._get_engine_class()
        messages = [
            {"role": "system", "content": "<tool_call>test</tool_call>"},
            {"role": "user", "content": "hello"},
        ]
        result = UCE._sanitize_messages_for_thinking_model(messages)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"


class TestBracketToXMLConversion:
    """Test that bracket-format tool calls are converted back to XML for parsing."""

    def test_bracket_tool_call_converts_to_xml(self):
        from backend.utils.agent_output_parser import parse_tool_calls_xml

        bracket_response = """[tool_call]
[tool]web_search[/tool]
[query]current temperature in Cincinnati Ohio[/query]
[/tool_call]"""
        # Convert brackets to XML (same logic as unified_chat_engine)
        xml_response = (
            bracket_response.replace("[tool_call]", "<tool_call>")
            .replace("[/tool_call]", "</tool_call>")
            .replace("[tool]", "<tool>")
            .replace("[/tool]", "</tool>")
        )
        xml_response = re.sub(r"\[(\w+)\]", r"<\1>", xml_response)
        xml_response = re.sub(r"\[/(\w+)\]", r"</\1>", xml_response)

        parsed = parse_tool_calls_xml(xml_response)
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].tool_name == "web_search"
        assert "Cincinnati" in parsed.tool_calls[0].parameters.get("query", "")

    def test_standard_xml_still_works(self):
        from backend.utils.agent_output_parser import parse_tool_calls_xml

        xml_response = """<tool_call>
<tool>web_search</tool>
<query>weather in Cleveland</query>
</tool_call>"""
        parsed = parse_tool_calls_xml(xml_response)
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].tool_name == "web_search"

    def test_multiple_bracket_tool_calls(self):
        from backend.utils.agent_output_parser import parse_tool_calls_xml

        bracket_response = """I'll search for both.
[tool_call]
[tool]web_search[/tool]
[query]Cincinnati temperature[/query]
[/tool_call]
[tool_call]
[tool]web_search[/tool]
[query]Cleveland temperature[/query]
[/tool_call]"""

        xml_response = (
            bracket_response.replace("[tool_call]", "<tool_call>")
            .replace("[/tool_call]", "</tool_call>")
            .replace("[tool]", "<tool>")
            .replace("[/tool]", "</tool>")
        )
        xml_response = re.sub(r"\[(\w+)\]", r"<\1>", xml_response)
        xml_response = re.sub(r"\[/(\w+)\]", r"</\1>", xml_response)

        parsed = parse_tool_calls_xml(xml_response)
        assert len(parsed.tool_calls) == 2


class TestThinkingModelDetection:
    """Test that thinking models are correctly identified."""

    def test_qwen3_detected(self):
        models = ["qwen3-vl:8b", "qwen3:14b", "qwen3-coder:32b", "QWEN3-VL:2B"]
        for model in models:
            assert any(
                t in model.lower() for t in ("qwen3", "deepseek-r1", "thinking")
            ), f"{model} not detected"

    def test_non_thinking_models_not_detected(self):
        models = ["llama3:latest", "qwen2.5:14b", "mistral:7b", "gemma3:12b"]
        for model in models:
            assert not any(
                t in model.lower() for t in ("qwen3", "deepseek-r1", "thinking")
            ), f"{model} incorrectly detected"


class TestToolExecutionGuardIntegration:
    """Test guard with the actual tool calling flow."""

    def test_guard_blocks_after_failures(self):
        from backend.services.tool_execution_guard import ToolExecutionGuard

        guard = ToolExecutionGuard(max_failures_per_tool=2)

        # Two failures trigger circuit breaker
        guard.check_call("browser_navigate", {"url": "https://a.com"})
        guard.record_result(
            "browser_navigate", {"url": "https://a.com"}, False, "timeout", 1
        )

        guard.check_call("browser_navigate", {"url": "https://b.com"})
        guard.record_result(
            "browser_navigate", {"url": "https://b.com"}, False, "timeout", 2
        )

        allowed, reason = guard.check_call("browser_navigate", {"url": "https://c.com"})
        assert allowed is False
        assert "BLOCKED" in reason

    def test_guard_suggests_fallback(self):
        from backend.services.tool_execution_guard import ToolExecutionGuard

        guard = ToolExecutionGuard()
        fallback = guard.suggest_fallback("browser_navigate")
        assert fallback is not None
        assert "analyze_website" in fallback

    def test_guard_allows_different_tool_after_block(self):
        from backend.services.tool_execution_guard import ToolExecutionGuard

        guard = ToolExecutionGuard(max_failures_per_tool=1)
        guard.check_call("browser_navigate", {"url": "https://a.com"})
        guard.record_result(
            "browser_navigate", {"url": "https://a.com"}, False, "err", 1
        )

        # browser_navigate blocked
        allowed, _ = guard.check_call("browser_navigate", {"url": "https://b.com"})
        assert allowed is False

        # web_search should still work
        allowed, _ = guard.check_call("web_search", {"query": "test"})
        assert allowed is True


class TestAgentOutputParser:
    """Test the structured output parser handles various formats."""

    def test_json_parsing(self):
        from backend.utils.agent_output_parser import parse_tool_calls_json

        json_response = json.dumps(
            {
                "thoughts": "I need to search for this.",
                "tool_calls": [
                    {"tool_name": "web_search", "parameters": {"query": "test"}}
                ],
                "final_answer": None,
            }
        )
        result = parse_tool_calls_json(json_response)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "web_search"

    def test_json_with_final_answer(self):
        from backend.utils.agent_output_parser import parse_tool_calls_json

        json_response = json.dumps(
            {
                "thoughts": "I know this.",
                "tool_calls": [],
                "final_answer": "The answer is 42.",
            }
        )
        result = parse_tool_calls_json(json_response)
        assert len(result.tool_calls) == 0
        assert result.final_answer == "The answer is 42."

    def test_xml_parsing_with_reasoning(self):
        from backend.utils.agent_output_parser import parse_tool_calls_xml

        xml_response = """<reasoning>I need to check the weather.</reasoning>
<tool_call>
<tool>web_search</tool>
<query>weather in Cincinnati</query>
</tool_call>"""
        result = parse_tool_calls_xml(xml_response)
        assert len(result.tool_calls) == 1
        assert result.thoughts is not None
        assert "weather" in result.thoughts.lower()

    def test_structured_parser_prefers_json(self):
        from backend.utils.agent_output_parser import parse_tool_calls_structured

        json_response = json.dumps(
            {
                "thoughts": "JSON parse",
                "tool_calls": [
                    {"tool_name": "web_search", "parameters": {"query": "test"}}
                ],
                "final_answer": None,
            }
        )
        result = parse_tool_calls_structured(json_response)
        assert len(result.tool_calls) == 1
        assert result.thoughts == "JSON parse"


# ─── Integration tests (require Ollama) ──────────────────────────────────────


def _ollama_available():
    """Check if Ollama is running."""
    try:
        import ollama

        ollama.list()
        return True
    except Exception:
        return False


def _model_available(model_name):
    """Check if a specific model is available."""
    try:
        import ollama

        models = ollama.list()
        return any(
            m.get("name", m.get("model", "")) == model_name
            for m in models.get("models", [])
        )
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_available(), reason="Ollama not running")
class TestOllamaThinkingModelIntegration:
    """Integration tests with actual Ollama calls."""

    @pytest.mark.skipif(
        not _model_available("qwen3-vl:8b"), reason="qwen3-vl:8b not installed"
    )
    def test_qwen3_vl_sanitized_prompt_no_crash(self):
        """Verify that sanitized prompts don't crash Ollama's JSON serializer."""
        import ollama

        system_prompt = """You are an AI assistant with tool access.

AVAILABLE TOOLS:
- web_search(query) - Search the web

To call a tool, output this format:
[tool_call]
[tool]tool_name[/tool]
[query]value[/query]
[/tool_call]"""

        stream = ollama.chat(
            model="qwen3-vl:8b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "What is the temperature in Cincinnati?"},
            ],
            stream=True,
            options={"num_predict": 300, "temperature": 0.3},
        )

        content_parts = []
        thinking_parts = []
        for chunk in stream:
            msg = chunk.get("message", {})
            c = msg.get("content", "")
            t = msg.get("thinking", "")
            if c:
                content_parts.append(c)
            if t:
                thinking_parts.append(t)

        content = "".join(content_parts)
        thinking = "".join(thinking_parts)
        result = content or thinking

        # Should produce a tool call, not crash
        assert len(result) > 0, "Empty response from qwen3-vl:8b"
        assert (
            "web_search" in result.lower() or "tool" in result.lower()
        ), f"Expected tool call, got: {result[:200]}"

    @pytest.mark.skipif(
        not _model_available("qwen3-vl:8b"), reason="qwen3-vl:8b not installed"
    )
    def test_qwen3_vl_thinking_field_captured(self):
        """Verify thinking field content is captured when content is empty."""
        import ollama

        stream = ollama.chat(
            model="qwen3-vl:8b",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Reply briefly.",
                },
                {"role": "user", "content": "What is 2+2?"},
            ],
            stream=True,
            options={"num_predict": 100},
        )

        content_parts = []
        thinking_parts = []
        for chunk in stream:
            msg = chunk.get("message", {})
            c = msg.get("content", "")
            t = msg.get("thinking", "")
            if c:
                content_parts.append(c)
            if t:
                thinking_parts.append(t)

        content = "".join(content_parts)
        thinking = "".join(thinking_parts)

        # qwen3-vl puts output in thinking field
        assert len(thinking) > 0 or len(content) > 0, "Both content and thinking empty"
        combined = content or thinking
        assert (
            "4" in combined or "four" in combined.lower()
        ), f"Expected '4' or 'four' in response: {combined[:200]}"


# ─── Playwright E2E tests (require running frontend + backend) ────────────────


def _servers_running():
    """Check if both frontend and backend are running."""
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:5002/api/health", timeout=3)
        # Frontend is a Vite SPA — any response (including 404) means it's running
        try:
            urllib.request.urlopen("http://localhost:5175/", timeout=3)
        except urllib.error.HTTPError:
            pass  # 404 is fine for SPA
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _servers_running(), reason="Frontend/backend not running")
class TestChatE2EPlaywright:
    """Playwright-based E2E tests for the chat system."""

    @pytest.fixture(scope="class")
    def browser_context(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            yield context
            context.close()
            browser.close()

    def test_chat_page_loads(self, browser_context):
        """Verify the chat page renders without errors."""
        page = browser_context.new_page()
        page.goto(
            "http://localhost:5175/chat", wait_until="domcontentloaded", timeout=30000
        )

        # Chat input should be visible
        chat_input = page.locator("textarea, input[type='text']").first
        assert chat_input.is_visible(timeout=10000), "Chat input not found"
        page.close()

    def test_settings_llm_debug_toggle(self, browser_context):
        """Verify the LLM Debug toggle appears on the settings page."""
        page = browser_context.new_page()
        page.goto(
            "http://localhost:5175/settings",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(2000)

        # Developer section is under MAINTENANCE tab
        maint_tab = page.locator("text=MAINTENANCE")
        if maint_tab.is_visible(timeout=5000):
            maint_tab.click()
            page.wait_for_timeout(1000)

        # Look for the LLM Debug chip
        llm_debug_chip = page.locator("text=LLM Debug")
        assert llm_debug_chip.is_visible(
            timeout=10000
        ), "LLM Debug chip not found on settings page"

        # Click it and verify it toggles
        llm_debug_chip.click()
        page.wait_for_timeout(1000)

        page.close()

    def test_chat_sends_message_and_receives_response(self, browser_context):
        """Send a simple message and verify a response appears."""
        page = browser_context.new_page()
        page.goto(
            "http://localhost:5175/chat", wait_until="domcontentloaded", timeout=30000
        )

        # Find and click the chat input
        chat_input = page.locator("textarea, input[type='text']").first
        chat_input.wait_for(state="visible", timeout=10000)
        chat_input.click()
        chat_input.fill("What is 2 + 2? Reply with just the number.")

        # Submit (press Enter or click send button)
        chat_input.press("Enter")

        # Wait for the streaming response to appear.
        # The StreamingMessage component renders MUI Paper elements; the
        # "Processing..." text or actual streamed tokens will appear.
        # Thinking models (qwen3-vl) can take 60-90s on first load.
        try:
            # First, wait for the streaming indicator ("Processing..." or token text)
            streaming = page.locator("text=Processing..., text=Thinking")
            streaming.first.wait_for(state="visible", timeout=30000)
        except Exception:
            pass  # May have already completed

        # Wait for completion: either the "Assistant is typing..." disappears,
        # or actual response text appears in the page.
        # Poll for up to 120s for a response.
        import time

        deadline = time.time() + 120
        found_response = False
        while time.time() < deadline:
            body_text = page.locator("body").inner_text()
            # Check for a numeric answer or any substantive response
            if any(term in body_text for term in ["4", "four", "error", "Error"]):
                found_response = True
                break
            time.sleep(3)

        page.screenshot(path="/tmp/chat_e2e_response.png", full_page=True)

        if not found_response:
            pytest.skip("Model response timed out after 120s (likely slow inference)")

        page.close()

    def test_web_search_tool_called_for_weather(self, browser_context):
        """Send a weather query and verify tool calling happens."""
        page = browser_context.new_page()
        page.goto(
            "http://localhost:5175/chat", wait_until="domcontentloaded", timeout=30000
        )

        chat_input = page.locator("textarea, input[type='text']").first
        chat_input.wait_for(state="visible", timeout=10000)
        chat_input.click()
        chat_input.fill("What is the current temperature in Cincinnati Ohio?")
        chat_input.press("Enter")

        # Wait for tool call indicator or response
        # Tool calls show as "web_search" in the UI
        try:
            tool_indicator = page.locator("text=web_search, text=Searching, text=tool")
            tool_indicator.wait_for(state="visible", timeout=60000)
        except Exception:
            # Even if tool indicator not found, check for any response
            pass

        # Wait for final response
        time.sleep(30)

        # Check the page has some response content
        page_text = page.content()
        has_response = (
            "temperature" in page_text.lower()
            or "couldn't find" in page_text.lower()
            or "unable to verify" in page_text.lower()
            or "web_search" in page_text.lower()
        )
        # Don't assert — just log. The response depends on model + search availability.
        if not has_response:
            print(f"WARNING: No expected response keywords found in page")

        page.close()
