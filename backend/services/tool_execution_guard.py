#!/usr/bin/env python3
"""
Tool Execution Guard
Programmatic enforcement of circuit breaker, duplicate detection, and fallback
suggestions for agent tool calls. Both UnifiedChatEngine and AgentExecutor
instantiate this per-request to prevent infinite retry loops and wasted iterations.
"""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Fallback suggestions when a tool is blocked
FALLBACK_MAP = {
    "browser_navigate": "Use 'analyze_website' with the URL instead (no browser needed).",
    "browser_screenshot": "Use 'analyze_website' to get page content, or 'web_search' for site info.",
    "browser_click": "Use 'analyze_website' or 'web_search' instead.",
    "browser_fill": "Use 'analyze_website' or 'web_search' instead.",
    "browser_extract": "Use 'analyze_website' with the URL to extract content.",
    "browser_get_html": "Use 'analyze_website' with the URL to get page content.",
    "browser_wait": "Use 'analyze_website' or 'web_search' instead.",
    "browser_execute_js": "Use 'analyze_website' or 'web_search' instead.",
    "web_search": "Tell the user you couldn't find this information right now.",
    "analyze_website": "Use 'web_search' to find cached or alternative sources.",
}

# Tools that are inherently slow/expensive and whose failures are often
# transient (OOM, GPU busy, model loading).  These get a higher circuit
# breaker threshold so a single OOM doesn't block all subsequent attempts.
SLOW_TOOLS = {"generate_image", "generate_video", "generate_animation", "codegen"}


@dataclass
class ToolCallRecord:
    """Record of a single tool call attempt."""
    tool_name: str
    params_hash: str
    success: bool
    error: Optional[str]
    iteration: int


class ToolExecutionGuard:
    """
    Session-scoped guard that enforces:
    1. Circuit breaker: block a tool after N consecutive failures
    2. Duplicate detection: block identical (tool, params) calls
    3. Fallback suggestions: guide the LLM toward working alternatives
    """

    def __init__(self, max_failures_per_tool: int = 2, max_duplicate_calls: int = 1):
        self._max_failures = max_failures_per_tool
        self._max_failures_slow = max(max_failures_per_tool * 2, 4)  # Higher threshold for slow tools
        self._max_duplicates = max_duplicate_calls
        self._call_history: List[ToolCallRecord] = []
        self._failure_counts: Dict[str, int] = {}  # tool_name -> consecutive failures
        self._blocked_tools: Set[str] = set()
        self._seen_hashes: Dict[str, int] = {}  # params_hash -> call count
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_params(params: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize parameters for consistent hashing."""
        normalized = {}
        for k, v in sorted(params.items()):
            if isinstance(v, str):
                v = v.strip()
                # Normalize URLs: strip trailing slash
                if k in ("url", "href", "link") and v.endswith("/"):
                    v = v.rstrip("/")
            normalized[k] = v
        return normalized

    @staticmethod
    def _hash_call(tool_name: str, params: Dict[str, Any]) -> str:
        """Create a deterministic hash for a (tool, params) pair."""
        normalized = ToolExecutionGuard._normalize_params(params)
        key = json.dumps({"tool": tool_name, "params": normalized}, sort_keys=True)
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def check_call(self, tool_name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Check if a tool call should be allowed.

        Returns:
            (allowed, block_reason) — block_reason is None if allowed.
        """
        with self._lock:
            # 1. Circuit breaker check
            if tool_name in self._blocked_tools:
                fallback = FALLBACK_MAP.get(tool_name, "Try a different approach.")
                reason = (
                    f"[BLOCKED] '{tool_name}' has failed {self._failure_counts.get(tool_name, 0)} "
                    f"times and is disabled for this session. {fallback}"
                )
                logger.info(f"Guard blocked (circuit breaker): {tool_name}")
                return False, reason

            # 2. Duplicate detection
            call_hash = self._hash_call(tool_name, params)
            count = self._seen_hashes.get(call_hash, 0)
            if count >= self._max_duplicates:
                fallback = FALLBACK_MAP.get(tool_name, "Try different parameters or a different tool.")
                reason = (
                    f"[BLOCKED] Already called '{tool_name}' with these exact parameters. "
                    f"{fallback}"
                )
                logger.info(f"Guard blocked (duplicate): {tool_name} hash={call_hash}")
                return False, reason

            # Allowed — pre-register the hash so concurrent threads don't double-fire
            self._seen_hashes[call_hash] = count + 1
            return True, None

    def record_result(
        self, tool_name: str, params: Dict[str, Any],
        success: bool, error: Optional[str], iteration: int
    ) -> None:
        """Record the outcome of a tool call for circuit breaker tracking."""
        with self._lock:
            call_hash = self._hash_call(tool_name, params)
            self._call_history.append(ToolCallRecord(
                tool_name=tool_name,
                params_hash=call_hash,
                success=success,
                error=error,
                iteration=iteration,
            ))

            if success:
                # Reset failure count on success
                self._failure_counts[tool_name] = 0
            else:
                self._failure_counts[tool_name] = self._failure_counts.get(tool_name, 0) + 1
                threshold = (
                    self._max_failures_slow
                    if tool_name in SLOW_TOOLS
                    else self._max_failures
                )
                if self._failure_counts[tool_name] >= threshold:
                    self._blocked_tools.add(tool_name)
                    logger.warning(
                        f"Guard circuit-broke '{tool_name}' after "
                        f"{self._failure_counts[tool_name]} failures"
                    )

    def suggest_fallback(self, tool_name: str) -> Optional[str]:
        """Return a fallback suggestion string for a tool, or None."""
        return FALLBACK_MAP.get(tool_name)

    def get_blocked_tools_summary(self) -> str:
        """
        Return a summary of blocked tools for injection into the LLM prompt.
        Returns empty string if nothing is blocked.
        """
        with self._lock:
            if not self._blocked_tools:
                return ""
            lines = ["BLOCKED TOOLS (do NOT call these):"]
            for tool_name in sorted(self._blocked_tools):
                fallback = FALLBACK_MAP.get(tool_name, "Try a different approach.")
                lines.append(f"  - {tool_name}: {fallback}")
            return "\n".join(lines)

    @property
    def blocked_tools(self) -> Set[str]:
        with self._lock:
            return set(self._blocked_tools)
