"""
Smoke tests for the Guaardvark MCP server.

Covers:
  * Policy gate logic (unit, no subprocess).
  * Tool adapter: schema generation.
  * Resources adapter: URI encoding + chroot escape defense.
  * End-to-end stdio round-trip: ``initialize`` → ``tools/list`` →
    ``resources/list`` against a subprocess.

Run with::

    backend/venv/bin/python -m pytest backend/mcp/tests/ -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ─────────────────────── policy gate unit tests ───────────────────────


def test_policy_denies_desktop_category_by_default():
    from backend.mcp.config import MCPConfig, tool_is_exposed
    cfg = MCPConfig()
    ok, reason = tool_is_exposed("gui_click", "desktop", False, False, cfg.tools)
    assert not ok
    assert "desktop" in reason


def test_policy_allows_web_category():
    from backend.mcp.config import MCPConfig, tool_is_exposed
    cfg = MCPConfig()
    ok, _ = tool_is_exposed("web_search", "web", False, False, cfg.tools)
    assert ok


def test_policy_hides_dangerous_tools():
    from backend.mcp.config import MCPConfig, tool_is_exposed
    cfg = MCPConfig()
    ok, reason = tool_is_exposed("some_tool", "web", True, False, cfg.tools)
    assert not ok
    assert "dangerous" in reason


def test_policy_explicit_allow_beats_category_deny():
    from backend.mcp.config import MCPConfig, tool_is_exposed
    cfg = MCPConfig()
    cfg.tools.allow = ["gui_click"]
    ok, _ = tool_is_exposed("gui_click", "desktop", False, False, cfg.tools)
    assert ok


def test_policy_deny_wins_over_allow():
    from backend.mcp.config import MCPConfig, tool_is_exposed
    cfg = MCPConfig()
    cfg.tools.allow = ["web_search"]
    cfg.tools.deny = ["web_search"]
    ok, _ = tool_is_exposed("web_search", "web", False, False, cfg.tools)
    assert not ok


# ─────────────────────── adapter unit tests ───────────────────────


def test_input_schema_maps_types():
    from backend.mcp.tools_adapter import _tool_input_schema
    from backend.services.agent_tools import BaseTool, ToolParameter

    class _Fake(BaseTool):
        name = "fake"
        description = "x"
        parameters = {
            "q": ToolParameter(name="q", type="string", required=True, description="query"),
            "limit": ToolParameter(name="limit", type="int", required=False, default=10),
        }

        def execute(self, **kwargs):
            raise NotImplementedError

    schema = _tool_input_schema(_Fake())
    assert schema["type"] == "object"
    assert schema["properties"]["q"]["type"] == "string"
    assert schema["properties"]["limit"]["type"] == "integer"
    assert schema["properties"]["limit"]["default"] == 10
    assert schema["required"] == ["q"]


def test_resource_uri_roundtrips_through_chroot(tmp_path):
    from backend.mcp.resources_adapter import _path_for_uri, _uri_for

    root = tmp_path.resolve()
    (root / "sub").mkdir()
    target = root / "sub" / "file name.png"
    target.write_bytes(b"\x00\x01")

    uri = _uri_for(target, root)
    assert uri.startswith("guaardvark://outputs/")
    assert "%20" in uri  # space encoded

    resolved = _path_for_uri(uri, root)
    assert resolved == target


def test_resource_chroot_rejects_parent_escape(tmp_path):
    from backend.mcp.resources_adapter import _path_for_uri

    root = tmp_path.resolve()
    bad = "guaardvark://outputs/..%2F..%2Fetc%2Fpasswd"
    assert _path_for_uri(bad, root) is None


# ─────────────────────── stdio round-trip ───────────────────────


def _rpc_line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


async def _stdio_roundtrip(timeout: float = 45.0) -> dict:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("GUAARDVARK_MCP_ENABLED", "true")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "backend.mcp", "stdio",
        cwd=str(PROJECT_ROOT),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # One JSON-RPC message per line, and a full tools/list already exceeds
        # asyncio's 64 KiB default readline limit.
        limit=4 * 1024 * 1024,
    )
    assert proc.stdin and proc.stdout

    proc.stdin.write(_rpc_line({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "guaardvark-smoke", "version": "0.0.1"},
        },
    }))
    proc.stdin.write(_rpc_line({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    }))
    proc.stdin.write(_rpc_line({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }))
    proc.stdin.write(_rpc_line({
        "jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {},
    }))
    await proc.stdin.drain()
    # Leave stdin open — closing it prematurely can make the server race to
    # shut down before flushing the last response. We'll kill the process
    # after collecting all expected responses.

    results: dict[int, dict] = {}
    needed = {1, 2, 3}
    try:
        async with asyncio.timeout(timeout):
            while not needed.issubset(results.keys()):
                raw = await proc.stdout.readline()
                if not raw:
                    break  # EOF — something died
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    raise AssertionError(f"non-JSON on stdout: {line!r}")
                if "id" in msg:
                    results[msg["id"]] = msg
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()
        await proc.wait()

    return results


@pytest.mark.asyncio
async def test_stdio_initialize_and_list_tools():
    results = await _stdio_roundtrip()

    init = results.get(1)
    assert init is not None, "no response to initialize"
    assert "result" in init
    assert init["result"]["serverInfo"]["name"] == "guaardvark"

    tools = results.get(2)
    assert tools is not None, "no response to tools/list"
    assert "result" in tools
    tool_list = tools["result"]["tools"]
    assert len(tool_list) > 0, "server exposed no tools"
    for t in tool_list:
        assert "name" in t
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"
    exposed_names = {t["name"] for t in tool_list}
    for hidden in ("gui_click", "system_command", "agent_task_execute", "execute_python"):
        assert hidden not in exposed_names, f"{hidden} should be hidden by default"

    resources = results.get(3)
    assert resources is not None, "no response to resources/list"
    assert "result" in resources
    assert isinstance(resources["result"]["resources"], list)


# ─────────────────────── tools/call dispatch ───────────────────────


def _build_fake_tool_handlers(monkeypatch, execute, config=None):
    """Build mcp 2.x tool handlers exposing a single fake tool."""
    import mcp.types as mcp_types
    from backend.mcp import tools_adapter
    from backend.mcp.config import MCPConfig
    from backend.services.agent_tools import BaseTool

    class _Fake(BaseTool):
        name = "fake_echo"
        description = "echo"
        parameters = {}

        def execute(self, **kwargs):
            return execute(**kwargs)

    base = _Fake()
    pair = (base, mcp_types.Tool(name="fake_echo", description="echo",
                                 input_schema={"type": "object", "properties": {}}))
    monkeypatch.setattr(tools_adapter, "collect_exposed_tools", lambda cfg: [pair])

    on_list, on_call, count = tools_adapter.build_tool_handlers(config or MCPConfig())
    assert count == 1
    return on_list, on_call


def _call_params(name: str, arguments: dict):
    import mcp.types as mcp_types
    return mcp_types.CallToolRequestParams(name=name, arguments=arguments)


@pytest.mark.asyncio
async def test_tools_call_returns_output_on_success(monkeypatch):
    from backend.services.agent_tools import ToolResult

    _on_list, on_call = _build_fake_tool_handlers(
        monkeypatch, lambda **kw: ToolResult(success=True, output="pong"))
    result = await on_call(None, _call_params("fake_echo", {}))

    assert not result.is_error
    assert [b.text for b in result.content] == ["pong"]


@pytest.mark.asyncio
async def test_tools_call_reports_tool_exception_without_crashing(monkeypatch):
    def _boom(**kwargs):
        raise ValueError("kaboom")

    _on_list, on_call = _build_fake_tool_handlers(monkeypatch, _boom)
    result = await on_call(None, _call_params("fake_echo", {}))

    assert result.is_error
    assert "ValueError" in result.content[0].text
    assert "kaboom" in result.content[0].text


@pytest.mark.asyncio
async def test_tools_call_rejects_unexposed_tool(monkeypatch):
    from backend.services.agent_tools import ToolResult

    _on_list, on_call = _build_fake_tool_handlers(
        monkeypatch, lambda **kw: ToolResult(success=True, output="pong"))
    result = await on_call(None, _call_params("not_a_tool", {}))

    assert result.is_error
    assert "not exposed" in result.content[0].text


# ---------------------------------------------------------------------------
# Argument defaults, worker thread, enforced timeout (2026-09-11)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_call_applies_argument_defaults_when_key_omitted(monkeypatch):
    from backend.services.agent_tools import ToolResult
    from backend.mcp.config import MCPConfig

    seen = {}

    def _echo(**kw):
        seen.update(kw)
        return ToolResult(success=True, output="ok")

    cfg = MCPConfig()
    cfg.tools.argument_defaults = {"fake_echo": {"wait_for_result": False}}
    _on_list, on_call = _build_fake_tool_handlers(monkeypatch, _echo, config=cfg)
    await on_call(None, _call_params("fake_echo", {"prompt": "x"}))
    assert seen == {"prompt": "x", "wait_for_result": False}

    seen.clear()
    await on_call(None, _call_params("fake_echo", {"prompt": "x", "wait_for_result": True}))
    assert seen["wait_for_result"] is True, "a value the caller passed must win"


@pytest.mark.asyncio
async def test_tools_call_times_out_with_an_honest_message(monkeypatch):
    import time
    from backend.services.agent_tools import ToolResult
    from backend.mcp.config import MCPConfig

    def _slow(**kw):
        time.sleep(0.5)
        return ToolResult(success=True, output="late")

    cfg = MCPConfig()
    cfg.timeout_seconds = 0.05
    _on_list, on_call = _build_fake_tool_handlers(monkeypatch, _slow, config=cfg)
    result = await on_call(None, _call_params("fake_echo", {}))
    assert result.is_error
    text = result.content[0].text
    assert "did not finish" in text and "still running" in text and "get_generation_status" in text


@pytest.mark.asyncio
async def test_wait_for_result_true_gets_the_wait_ceiling(monkeypatch):
    from backend.mcp.config import MCPConfig, WAIT_TIMEOUT_SECONDS
    from backend.mcp.tools_adapter import _call_timeout

    cfg = MCPConfig()
    cfg.timeout_seconds = 7
    assert _call_timeout(cfg, {}) == 7.0
    assert _call_timeout(cfg, {"wait_for_result": "true"}) == float(WAIT_TIMEOUT_SECONDS)
    assert _call_timeout(cfg, {"wait_for_result": False}) == 7.0


def test_description_names_the_mcp_default(monkeypatch):
    """The schema a client sees must say that generate_image queues here."""
    from backend.mcp.config import MCPConfig
    from backend.mcp.tools_adapter import collect_exposed_tools
    from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

    class _Gen(BaseTool):
        name = "generate_image"
        description = "Generate an image."
        parameters = {"wait_for_result": ToolParameter(name="wait_for_result", type="bool",
                                                       description="", required=False, default=True)}

        def execute(self, **kw):
            return ToolResult(success=True, output="")

    class _Registry:
        def list_tools(self):
            return ["generate_image"]

        def get_tool(self, name):
            return _Gen()

    monkeypatch.setattr("backend.mcp.tools_adapter.get_tool_registry", lambda: _Registry())
    monkeypatch.setattr("backend.mcp.tools_adapter._category_lookup", lambda: {"generate_image": "image"})
    (_base, mcp_tool), = collect_exposed_tools(MCPConfig())
    assert "wait_for_result=false" in mcp_tool.description


@pytest.mark.asyncio
async def test_idempotent_tool_may_be_polled_with_identical_arguments(monkeypatch):
    """get_generation_status is called again with the same batch id on purpose."""
    import mcp.types as mcp_types
    from backend.mcp import tools_adapter
    from backend.mcp.config import MCPConfig
    from backend.services.agent_tools import BaseTool, ToolResult

    class _Status(BaseTool):
        name = "get_generation_status"
        description = "status"
        parameters = {}
        idempotent = True

        def execute(self, **kwargs):
            return ToolResult(success=True, output="running")

    base = _Status()
    pair = (base, mcp_types.Tool(name=base.name, description="status",
                                 input_schema={"type": "object", "properties": {}}))
    monkeypatch.setattr(tools_adapter, "collect_exposed_tools", lambda cfg: [pair])
    _on_list, on_call, _n = tools_adapter.build_tool_handlers(MCPConfig())
    for _ in range(3):
        result = await on_call(None, _call_params("get_generation_status", {"batch_id": "b1"}))
        assert not result.is_error and result.content[0].text == "running"


@pytest.mark.asyncio
async def test_failed_tool_result_carries_its_error_text(monkeypatch):
    """A ToolResult(success=False, error=...) used to reach the client as '(no output)'."""
    from backend.services.agent_tools import ToolResult

    _on_list, on_call = _build_fake_tool_handlers(
        monkeypatch, lambda **kw: ToolResult(success=False, error="GPU busy: try again"))
    result = await on_call(None, _call_params("fake_echo", {}))
    assert result.is_error
    assert result.content[0].text == "GPU busy: try again"
