"""Real stdio calls against `python -m backend.mcp`, the command MCP clients run.

The first test needs only this checkout. The integration test calls tools that
ask the running backend, and runs only with GUAARDVARK_MCP_LIVE=1.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


async def _session(requests: list[tuple[str, dict]], timeout: float = 120.0) -> list[dict]:
    """Initialize a fresh stdio server, send each (method, params), return the responses."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "backend.mcp", "stdio",
        cwd=str(PROJECT_ROOT), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,
    )

    def line(obj: dict) -> bytes:
        return (json.dumps(obj) + "\n").encode()

    async def send(msg_id: int, method: str, params: dict) -> dict:
        proc.stdin.write(line({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}))
        await proc.stdin.drain()
        async with asyncio.timeout(timeout):
            while True:
                raw = await proc.stdout.readline()
                assert raw, "the MCP server exited"
                text = raw.decode().strip()
                if not text:
                    continue
                msg = json.loads(text)  # anything that is not JSON-RPC on stdout fails here
                if msg.get("id") == msg_id:
                    return msg

    try:
        await send(0, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "guaardvark-contract-test", "version": "0"},
        })
        proc.stdin.write(line({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        return [await send(i, method, params) for i, (method, params) in enumerate(requests, start=1)]
    finally:
        proc.kill()
        await proc.wait()


def _call(name: str, arguments: dict) -> tuple[str, dict]:
    return "tools/call", {"name": name, "arguments": arguments}


def _text(response: dict) -> str:
    return " ".join(block.get("text", "") for block in response["result"].get("content", []))


def _is_error(response: dict) -> bool:
    result = response["result"]
    return bool(result.get("isError", result.get("is_error")))


@pytest.mark.asyncio
async def test_tools_answer_over_stdio_without_the_backend():
    tools, files, verified, invalid, resources = await _session([
        ("tools/list", {}),
        _call("list_code_files", {"directory": "backend/mcp", "max_depth": 1}),
        _call("verify_change", {"filepath": "backend/mcp/config.py", "expected_text": "def tool_is_exposed"}),
        _call("list_code_files", {"directory": "backend/mcp", "max_depth": "deep"}),
        ("resources/list", {}),
    ])

    listed = {t["name"]: t for t in tools["result"]["tools"]}
    hints = {name: (t.get("annotations") or {}).get("readOnlyHint") for name, t in listed.items()}
    assert hints["search_memory"] is True
    assert hints["save_memory"] is False
    assert [name for name, hint in hints.items() if hint is None] == []

    assert not _is_error(files) and "tools_adapter.py" in _text(files)
    assert not _is_error(verified) and "VERIFIED" in _text(verified)
    assert _is_error(invalid) and "Invalid arguments" in _text(invalid)

    page = resources["result"]
    assert isinstance(page["resources"], list)
    if len(page["resources"]) == 200:
        assert page.get("nextCursor")


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("GUAARDVARK_MCP_LIVE") != "1",
                    reason="calls the running backend; set GUAARDVARK_MCP_LIVE=1")
@pytest.mark.asyncio
async def test_backend_backed_tools_answer_over_stdio():
    responses = await _session([
        _call("swarm_status", {}),
        _call("self_improvement_status", {}),
        _call("search_memory", {"query": "guaardvark", "limit": 1}),
        _call("outreach_list_queue", {"status": "drafted", "limit": 1}),
        _call("list_code_repositories", {}),
    ])
    for response in responses:
        text = _text(response)
        assert "outside of application context" not in text
        assert "outside of request context" not in text
    failures = [(i, _text(r)[:200]) for i, r in enumerate(responses) if _is_error(r)]
    assert failures == []
