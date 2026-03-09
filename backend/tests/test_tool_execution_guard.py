#!/usr/bin/env python3
"""Tests for ToolExecutionGuard — circuit breaker, duplicate detection, fallback suggestions."""

import pytest
from backend.services.tool_execution_guard import ToolExecutionGuard, FALLBACK_MAP, SLOW_TOOLS


class TestCircuitBreaker:
    """Circuit breaker blocks a tool after N consecutive failures."""

    def test_allows_first_call(self):
        guard = ToolExecutionGuard(max_failures_per_tool=2)
        allowed, reason = guard.check_call("browser_navigate", {"url": "https://example.com"})
        assert allowed is True
        assert reason is None

    def test_allows_after_one_failure(self):
        guard = ToolExecutionGuard(max_failures_per_tool=2)
        guard.check_call("browser_navigate", {"url": "https://a.com"})
        guard.record_result("browser_navigate", {"url": "https://a.com"}, False, "timeout", 1)

        # Second call with different params should still be allowed
        allowed, _ = guard.check_call("browser_navigate", {"url": "https://b.com"})
        assert allowed is True

    def test_blocks_after_max_failures(self):
        guard = ToolExecutionGuard(max_failures_per_tool=2)

        # First call + failure
        guard.check_call("browser_navigate", {"url": "https://a.com"})
        guard.record_result("browser_navigate", {"url": "https://a.com"}, False, "timeout", 1)

        # Second call + failure — triggers circuit breaker
        guard.check_call("browser_navigate", {"url": "https://b.com"})
        guard.record_result("browser_navigate", {"url": "https://b.com"}, False, "timeout", 2)

        # Third call should be blocked
        allowed, reason = guard.check_call("browser_navigate", {"url": "https://c.com"})
        assert allowed is False
        assert "BLOCKED" in reason
        assert "browser_navigate" in reason

    def test_success_resets_failure_count(self):
        guard = ToolExecutionGuard(max_failures_per_tool=2)

        # One failure
        guard.check_call("web_search", {"query": "test1"})
        guard.record_result("web_search", {"query": "test1"}, False, "error", 1)

        # One success — resets count
        guard.check_call("web_search", {"query": "test2"})
        guard.record_result("web_search", {"query": "test2"}, True, None, 2)

        # Another failure — count is back to 1, not 2
        guard.check_call("web_search", {"query": "test3"})
        guard.record_result("web_search", {"query": "test3"}, False, "error", 3)

        # Should still be allowed (only 1 consecutive failure)
        allowed, _ = guard.check_call("web_search", {"query": "test4"})
        assert allowed is True

    def test_different_tools_tracked_independently(self):
        guard = ToolExecutionGuard(max_failures_per_tool=2)

        # Break browser_navigate
        for i in range(2):
            guard.check_call("browser_navigate", {"url": f"https://a{i}.com"})
            guard.record_result("browser_navigate", {"url": f"https://a{i}.com"}, False, "err", i + 1)

        # browser_navigate blocked
        allowed, _ = guard.check_call("browser_navigate", {"url": "https://a2.com"})
        assert allowed is False

        # web_search should still work
        allowed, _ = guard.check_call("web_search", {"query": "hello"})
        assert allowed is True

    def test_slow_tools_have_higher_threshold(self):
        """Slow/expensive tools (generate_image, etc.) need more failures before circuit break."""
        guard = ToolExecutionGuard(max_failures_per_tool=2)

        # generate_image is in SLOW_TOOLS — threshold should be 4, not 2
        assert "generate_image" in SLOW_TOOLS

        # Two failures should NOT block generate_image (would block a normal tool)
        for i in range(2):
            guard.check_call("generate_image", {"prompt": f"test{i}"})
            guard.record_result("generate_image", {"prompt": f"test{i}"}, False, "OOM", i + 1)

        allowed, _ = guard.check_call("generate_image", {"prompt": "test_after_2"})
        assert allowed is True, "generate_image should not be blocked after only 2 failures"

        # Third failure — still allowed
        guard.record_result("generate_image", {"prompt": "test_after_2"}, False, "OOM", 3)
        allowed, _ = guard.check_call("generate_image", {"prompt": "test_after_3"})
        assert allowed is True, "generate_image should not be blocked after 3 failures"

        # Fourth failure — NOW blocked
        guard.record_result("generate_image", {"prompt": "test_after_3"}, False, "OOM", 4)
        allowed, reason = guard.check_call("generate_image", {"prompt": "test_after_4"})
        assert allowed is False
        assert "BLOCKED" in reason

    def test_slow_tool_success_resets_count(self):
        """A successful call resets the failure count even for slow tools."""
        guard = ToolExecutionGuard(max_failures_per_tool=2)

        # 3 failures
        for i in range(3):
            guard.check_call("generate_image", {"prompt": f"fail{i}"})
            guard.record_result("generate_image", {"prompt": f"fail{i}"}, False, "OOM", i + 1)

        # 1 success resets
        guard.check_call("generate_image", {"prompt": "success"})
        guard.record_result("generate_image", {"prompt": "success"}, True, None, 4)

        # Should be allowed again
        allowed, _ = guard.check_call("generate_image", {"prompt": "after_reset"})
        assert allowed is True


class TestDuplicateDetection:
    """Duplicate detection blocks identical (tool, params) calls."""

    def test_blocks_duplicate_params(self):
        guard = ToolExecutionGuard()
        guard.check_call("web_search", {"query": "test"})

        # Same tool + same params → blocked
        allowed, reason = guard.check_call("web_search", {"query": "test"})
        assert allowed is False
        assert "Already called" in reason

    def test_allows_same_tool_different_params(self):
        guard = ToolExecutionGuard()
        guard.check_call("web_search", {"query": "test1"})

        allowed, _ = guard.check_call("web_search", {"query": "test2"})
        assert allowed is True

    def test_normalizes_url_trailing_slash(self):
        guard = ToolExecutionGuard()
        guard.check_call("browser_navigate", {"url": "https://example.com/"})

        # Same URL without trailing slash → should be treated as duplicate
        allowed, reason = guard.check_call("browser_navigate", {"url": "https://example.com"})
        assert allowed is False

    def test_normalizes_whitespace(self):
        guard = ToolExecutionGuard()
        guard.check_call("web_search", {"query": "hello world"})

        # Same query with extra whitespace → different hash (we only strip, not collapse)
        # This is intentional — "hello world" and "hello  world" are different queries
        allowed, _ = guard.check_call("web_search", {"query": "hello  world"})
        assert allowed is True

    def test_strips_whitespace(self):
        guard = ToolExecutionGuard()
        guard.check_call("web_search", {"query": "hello "})

        # Same query with trailing space stripped → duplicate
        allowed, _ = guard.check_call("web_search", {"query": "hello"})
        assert allowed is False


class TestFallbackSuggestions:
    """Fallback suggestions guide the LLM to alternatives."""

    def test_browser_navigate_fallback(self):
        guard = ToolExecutionGuard()
        fallback = guard.suggest_fallback("browser_navigate")
        assert fallback is not None
        assert "analyze_website" in fallback

    def test_browser_screenshot_fallback(self):
        guard = ToolExecutionGuard()
        fallback = guard.suggest_fallback("browser_screenshot")
        assert "analyze_website" in fallback or "web_search" in fallback

    def test_unknown_tool_no_fallback(self):
        guard = ToolExecutionGuard()
        assert guard.suggest_fallback("nonexistent_tool") is None

    def test_all_browser_tools_have_fallbacks(self):
        browser_tools = [
            "browser_navigate", "browser_click", "browser_fill",
            "browser_screenshot", "browser_extract", "browser_get_html",
            "browser_wait", "browser_execute_js",
        ]
        for tool in browser_tools:
            assert tool in FALLBACK_MAP, f"Missing fallback for {tool}"


class TestBlockedToolsSummary:
    """Summary message for injection into LLM prompts."""

    def test_empty_when_nothing_blocked(self):
        guard = ToolExecutionGuard()
        assert guard.get_blocked_tools_summary() == ""

    def test_includes_blocked_tools(self):
        guard = ToolExecutionGuard(max_failures_per_tool=1)
        guard.check_call("browser_navigate", {"url": "https://a.com"})
        guard.record_result("browser_navigate", {"url": "https://a.com"}, False, "err", 1)

        summary = guard.get_blocked_tools_summary()
        assert "browser_navigate" in summary
        assert "BLOCKED TOOLS" in summary

    def test_blocked_tools_property(self):
        guard = ToolExecutionGuard(max_failures_per_tool=1)
        assert len(guard.blocked_tools) == 0

        guard.check_call("browser_navigate", {"url": "https://a.com"})
        guard.record_result("browser_navigate", {"url": "https://a.com"}, False, "err", 1)

        assert "browser_navigate" in guard.blocked_tools
