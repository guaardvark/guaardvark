"""
Diagnostics: ``python -m backend.mcp doctor``.

Answers "why doesn't my agent see Guaardvark?" in one command:

  1. Server-side self-test — venv python, SDK version, tool registry,
     server construction, and a real stdio initialize/tools-list round-trip
     against a subprocess (exactly what an external client does).
  2. Client-side scan — finds ``guaardvark`` entries in the config of every
     client ``install`` can set up and flags dead paths (stale checkouts,
     deleted venvs). A config that exists but cannot be parsed is named, since
     its entry went unchecked.
  3. With ``--call``, real read-only tool calls through a fresh stdio server,
     one per tool family. A tool whose prerequisite is down (backend, plugin,
     media player) is reported as a warning, not a failure.

Plain-text PASS/FAIL output; exit code 0 only when every check passes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Iterator

from backend.mcp.cli import _claude_desktop_config_path, _project_root, _python_executable
from backend.mcp.installer import (
    SERVER_NAME,
    _antigravity_config_path,
    _claude_code_config_path,
    _codex_config_path,
    _grok_config_path,
    _load_config_file,
    _opencode_config_path,
)

_PASS = "PASS"
_FAIL = "FAIL"
_WARN = "warn"


def _report(status: str, name: str, detail: str = "") -> bool:
    line = f"  [{status:>4}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return status != _FAIL


# ─────────────────────── server-side checks ───────────────────────


def _check_python() -> bool:
    from backend.utils.display_paths import display_path

    python = _python_executable()
    if not Path(python).is_file():
        return _report(_FAIL, "python executable", f"{display_path(python)} does not exist")
    return _report(_PASS, "python executable", display_path(python))


def _check_sdk() -> bool:
    try:
        import importlib.metadata as im
        version = im.version("mcp")
    except Exception as exc:
        return _report(_FAIL, "mcp SDK import", f"{exc} — run: pip install 'mcp>=2.1,<3'")
    major = int(version.split(".")[0])
    if major < 2:
        return _report(_FAIL, "mcp SDK version",
                       f"{version} installed but the adapters need 2.x — "
                       "run: pip install --upgrade 'mcp>=2.1,<3'")
    return _report(_PASS, "mcp SDK version", version)


def _check_build() -> bool:
    try:
        # Tool registry boot prints banners; keep them off the report.
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            from backend.mcp.server import build_server
            _server, stats = build_server()
    except Exception as exc:
        return _report(_FAIL, "build_server()", f"{exc.__class__.__name__}: {exc}")
    # The server's own figure is capped for its startup banner; the operator
    # asking what an agent can read gets the full count.
    try:
        from backend.mcp.config import load_config
        from backend.mcp.resources_adapter import count_resources
        resources = str(count_resources(load_config()))
    except Exception as exc:
        resources = f"unknown ({exc.__class__.__name__}: {exc})"
    return _report(_PASS, "build_server()",
                   f"{stats['tools']} tools, {resources} resources exposed")


async def _stdio_roundtrip(timeout: float = 60.0) -> tuple[bool, str]:
    """Spawn the stdio server exactly as a client would and do the handshake."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    proc = await asyncio.create_subprocess_exec(
        _python_executable(), "-m", "backend.mcp", "stdio",
        cwd=str(_project_root()),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=4 * 1024 * 1024,
    )
    assert proc.stdin and proc.stdout

    def line(obj: dict) -> bytes:
        return (json.dumps(obj) + "\n").encode()

    proc.stdin.write(line({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "guaardvark-doctor", "version": "0"}},
    }))
    proc.stdin.write(line({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
    proc.stdin.write(line({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
    await proc.stdin.drain()

    results: dict[int, dict] = {}
    try:
        async with asyncio.timeout(timeout):
            while not {1, 2}.issubset(results):
                raw = await proc.stdout.readline()
                if not raw:
                    stderr = (await proc.stderr.read()).decode(errors="replace")
                    return False, f"server exited early; stderr tail: {stderr[-500:]}"
                text = raw.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    return False, f"non-JSON on stdout: {text[:200]!r}"
                if "id" in msg:
                    results[msg["id"]] = msg
    except TimeoutError:
        return False, f"no handshake response within {timeout:.0f}s"
    finally:
        proc.kill()
        await proc.wait()

    if "result" not in results.get(1, {}):
        return False, f"initialize failed: {results.get(1)}"
    tools = results.get(2, {}).get("result", {}).get("tools", [])
    if not tools:
        return False, "tools/list returned no tools"
    return True, f"handshake ok, {len(tools)} tools listed"


def _check_stdio() -> bool:
    ok, detail = asyncio.run(_stdio_roundtrip())
    return _report(_PASS if ok else _FAIL, "stdio round-trip", detail)


# One read-only probe per tool family. A probe runs only when tools/list
# advertises that tool with readOnlyHint true, so nothing here can change state.
_CALL_PROBES: list[tuple[str, dict[str, Any]]] = [
    ("list_code_files", {"directory": "backend/mcp", "max_depth": 1}),
    ("list_documents", {"limit": 1}),
    ("search_memory", {"query": "doctor probe", "limit": 1}),
    ("outreach_status", {}),
    ("inspect_gpu", {}),
    ("read_logs", {"lines": 10}),
    ("swarm_status", {}),
    ("self_improvement_status", {}),
    ("list_code_repositories", {}),
    ("search_codebase", {"query": "def run_doctor", "limit": 1}),
    ("media_status", {}),
]
# Answers that mean a prerequisite is down rather than the tool being broken.
_UNAVAILABLE_MARKERS = ("not answering", "not running", "No media player", "Start it from")


async def _stdio_calls(probes: list[tuple[str, dict[str, Any]]], timeout: float = 90.0) -> list[tuple[str, str, str]]:
    """Call each probe through a fresh stdio server. Returns (status, tool, detail) rows."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = await asyncio.create_subprocess_exec(
        _python_executable(), "-m", "backend.mcp", "stdio",
        cwd=str(_project_root()),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=16 * 1024 * 1024,
    )
    assert proc.stdin and proc.stdout

    def line(obj: dict) -> bytes:
        return (json.dumps(obj) + "\n").encode()

    async def request(msg_id: int, method: str, params: dict) -> dict:
        proc.stdin.write(line({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}))
        await proc.stdin.drain()
        async with asyncio.timeout(timeout):
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    raise RuntimeError("server exited")
                text = raw.decode(errors="replace").strip()
                if text:
                    msg = json.loads(text)
                    if msg.get("id") == msg_id:
                        return msg

    rows: list[tuple[str, str, str]] = []
    try:
        await request(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "guaardvark-doctor", "version": "0"},
        })
        proc.stdin.write(line({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        listed = await request(2, "tools/list", {})
        read_only = set()
        for tool in listed.get("result", {}).get("tools", []):
            hints = tool.get("annotations") or {}
            if hints.get("readOnlyHint", hints.get("read_only_hint")):
                read_only.add(tool["name"])

        for msg_id, (name, arguments) in enumerate(probes, start=3):
            if name not in read_only:
                rows.append((_WARN, name, "not listed as a read-only tool; skipped"))
                continue
            try:
                msg = await request(msg_id, "tools/call", {"name": name, "arguments": arguments})
            except TimeoutError:
                rows.append((_FAIL, name, f"no answer within {timeout:.0f}s"))
                continue
            if "error" in msg:
                rows.append((_FAIL, name, str(msg["error"])[:160]))
                continue
            result = msg.get("result") or {}
            text = " ".join(block.get("text", "") for block in result.get("content", [])).strip()
            if result.get("isError", result.get("is_error")):
                status = _WARN if any(marker in text for marker in _UNAVAILABLE_MARKERS) else _FAIL
                rows.append((status, name, text[:160]))
            else:
                rows.append((_PASS, name, (text.splitlines()[0] if text else "(empty)")[:100]))
    except (RuntimeError, TimeoutError, json.JSONDecodeError) as exc:
        rows.append((_FAIL, "stdio session", str(exc)[:200]))
    finally:
        proc.kill()
        await proc.wait()
    return rows


def _check_calls() -> bool:
    ok = True
    for status, name, detail in asyncio.run(_stdio_calls(_CALL_PROBES)):
        ok = _report(status, f"call: {name}", detail) and ok
    return ok


# ─────────────────────── client config scan ───────────────────────


def _paths_referenced(command: str, args: list[str]) -> Iterator[str]:
    """Yield absolute paths an MCP server entry depends on."""
    tokens = [command, *args]
    for arg in args:
        # `sh -c "cd /x && exec /y -m backend.mcp"` — unpack the inner script.
        if "&&" in arg or arg.strip().startswith("cd "):
            try:
                tokens.extend(shlex.split(arg))
            except ValueError:
                pass
    for tok in tokens:
        if tok.startswith("/"):
            yield tok


def _entry_problems(command: str, args: list[str]) -> list[str]:
    return [f"missing path: {p}" for p in _paths_referenced(command, args)
            if not Path(p).exists()]


def _scan_mcp_servers_obj(obj: Any) -> Iterator[tuple[str, list[str]]]:
    """Yield (command, args) for every guaardvark entry in an mcpServers-style dict."""
    if not isinstance(obj, dict):
        return
    entry = obj.get(SERVER_NAME)
    if not isinstance(entry, dict):
        return
    command = entry.get("command", "")
    if isinstance(command, dict):  # zed shape: {"path": ..., "args": ...}
        yield str(command.get("path", "")), [str(a) for a in (command.get("args") or [])]
    elif isinstance(command, list):  # opencode shape: [executable, *args]
        yield str(command[0]) if command else "", [str(a) for a in command[1:]]
    else:
        yield str(command), [str(a) for a in (entry.get("args") or [])]


def _client_config_sources() -> list[tuple[str, Path, list[str]]]:
    """(label, file, tables-to-check) for every client ``install`` supports.
    JSON and TOML files alike; a table maps server names to entries."""
    home = Path.home()
    return [
        ("cursor (user)", home / ".cursor/mcp.json", ["mcpServers"]),
        ("cursor (project)", _project_root() / ".cursor/mcp.json", ["mcpServers"]),
        ("claude-code (user)", _claude_code_config_path(), ["mcpServers"]),
        ("codex", _codex_config_path(), ["mcp_servers"]),
        ("grok", _grok_config_path(), ["mcp_servers"]),
        ("antigravity", _antigravity_config_path(), ["mcpServers"]),
        ("opencode", _opencode_config_path(), ["mcp"]),
        ("claude-desktop", _claude_desktop_config_path(), ["mcpServers"]),
        ("zed", home / ".config/zed/settings.json", ["context_servers"]),
        ("gemini", home / ".gemini/settings.json", ["mcpServers"]),
    ]


def _scan_clients() -> tuple[list[tuple[str, str, list[str]]], list[tuple[str, Path]]]:
    """``(entries, unreadable)``: a (client_label, command, args) row for each
    guaardvark entry found, and the configs that exist but could not be parsed."""
    entries: list[tuple[str, str, list[str]]] = []
    unreadable: list[tuple[str, Path]] = []
    for label, path, tables in _client_config_sources():
        if not path.is_file() or path.stat().st_size == 0:
            continue
        data = _load_config_file(path)
        if data is None:
            unreadable.append((label, path))
            continue
        for table in tables:
            for command, args in _scan_mcp_servers_obj(data.get(table)):
                entries.append((label, command, args))
        # claude-code also nests per-project server maps.
        for proj in (data.get("projects") or {}).values() if label.startswith("claude-code") else ():
            if isinstance(proj, dict):
                for command, args in _scan_mcp_servers_obj(proj.get("mcpServers")):
                    entries.append((f"{label} project", command, args))
    return entries, unreadable


def _check_clients() -> bool:
    # Shown relative to the checkout and home: doctor output gets pasted into
    # issues and shared on screen.
    from backend.utils.display_paths import display_path, display_text

    found, unreadable = _scan_clients()
    for label, path in unreadable:
        _report(_WARN, f"client: {label}",
                f"could not parse {display_path(path)} (comments or invalid syntax); "
                "its entry was not checked")
    if not found:
        _report(_WARN, "client configs",
                f"no client has a '{SERVER_NAME}' entry — run: python -m backend.mcp install")
        return True  # informational, not a server fault

    ok = True
    for label, command, args in found:
        problems = _entry_problems(command, args)
        if problems:
            ok = _report(_FAIL, f"client: {label}", display_text("; ".join(problems))) and ok
        else:
            preview = display_text(" ".join([command, *args]))
            _report(_PASS, f"client: {label}", preview[:100])
    return ok


# ─────────────────────── entry point ───────────────────────


def run_doctor(call: bool = False) -> int:
    print("Guaardvark MCP doctor\n")
    print("Server:")
    ok = _check_python()
    ok = _check_sdk() and ok
    if ok:
        ok = _check_build() and ok
        ok = _check_stdio() and ok
        if call and ok:
            print("\nTool calls (read-only):")
            ok = _check_calls() and ok
    else:
        print("  (skipping server build / stdio checks until the above pass)")

    print("\nClients:")
    ok = _check_clients() and ok

    print()
    if ok:
        print("All checks passed. If an agent still can't connect, restart it so it")
        print("re-reads its MCP config, then check its own MCP logs.")
        return 0
    print("Some checks FAILED — fix the items above and re-run.")
    return 1
