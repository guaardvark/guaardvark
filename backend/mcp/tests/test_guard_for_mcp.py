"""ToolExecutionGuard in the mode the MCP server uses, and the default the ReACT loops keep."""

import types

from backend.services import tool_execution_guard as guard_module
from backend.services.tool_execution_guard import ToolExecutionGuard


def _mcp_guard() -> ToolExecutionGuard:
    return ToolExecutionGuard(
        max_failures_per_tool=2, max_duplicate_calls=1,
        dedupe_completed=False, breaker_cooldown_s=60, history_limit=5,
    )


def test_the_default_guard_still_blocks_a_repeated_call():
    guard = ToolExecutionGuard()
    assert guard.check_call("search_code", {"pattern": "x"})[0]
    guard.record_result("search_code", {"pattern": "x"}, True, None, 1)
    ok, reason = guard.check_call("search_code", {"pattern": "x"})
    assert not ok and "Already called" in reason


def test_a_finished_call_may_be_repeated():
    guard = _mcp_guard()
    for i in range(3):
        assert guard.check_call("search_code", {"pattern": "x"})[0]
        guard.record_result("search_code", {"pattern": "x"}, True, None, i)


def test_an_identical_call_is_blocked_only_while_the_first_runs():
    guard = _mcp_guard()
    assert guard.check_call("generate_video", {"prompt": "x"})[0]
    ok, reason = guard.check_call("generate_video", {"prompt": "x"})
    assert not ok and "still running" in reason
    assert guard.check_call("generate_video", {"prompt": "y"})[0], "a different call is not a duplicate"
    guard.record_result("generate_video", {"prompt": "x"}, True, None, 1)
    assert guard.check_call("generate_video", {"prompt": "x"})[0]


def test_the_breaker_lets_one_call_through_after_the_cooldown(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(guard_module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    guard = _mcp_guard()
    for i in range(2):
        guard.check_call("web_search", {"query": str(i)})
        guard.record_result("web_search", {"query": str(i)}, False, "down", i)

    ok, reason = guard.check_call("web_search", {"query": "z"})
    assert not ok and "paused for 60 s" in reason

    clock[0] += 61
    assert guard.check_call("web_search", {"query": "z"})[0]
    guard.record_result("web_search", {"query": "z"}, False, "down", 3)
    assert not guard.check_call("web_search", {"query": "w"})[0], "a failure after the cooldown trips it again"


def test_a_success_after_the_cooldown_clears_the_breaker(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(guard_module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    guard = _mcp_guard()
    for i in range(2):
        guard.check_call("web_search", {"query": str(i)})
        guard.record_result("web_search", {"query": str(i)}, False, "down", i)
    clock[0] += 61
    guard.check_call("web_search", {"query": "z"})
    guard.record_result("web_search", {"query": "z"}, True, None, 3)
    guard.check_call("web_search", {"query": "a"})
    guard.record_result("web_search", {"query": "a"}, False, "down", 4)
    assert guard.check_call("web_search", {"query": "b"})[0], "one failure after a success is not a trip"


def test_skipping_deduplication_still_honours_the_breaker():
    guard = _mcp_guard()
    for i in range(2):
        guard.check_call("get_generation_status", {"batch_id": "b"}, dedupe=False)
        guard.record_result("get_generation_status", {"batch_id": "b"}, False, "gone", i)
    assert not guard.check_call("get_generation_status", {"batch_id": "b"}, dedupe=False)[0]


def test_history_is_bounded():
    guard = _mcp_guard()
    for i in range(20):
        guard.record_result("read_logs", {"i": i}, True, None, i)
    assert len(guard._call_history) == 5
