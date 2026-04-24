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
