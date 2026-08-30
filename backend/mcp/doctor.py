"""
Diagnostics: ``python -m backend.mcp doctor``.

Answers "why doesn't my agent see Guaardvark?" in one command:

  1. Server-side self-test — venv python, SDK version, tool registry,
     server construction, and a real stdio initialize/tools-list round-trip
     against a subprocess (exactly what an external client does).
  2. Client-side scan — finds ``guaardvark`` entries in known agent config
     files and flags dead paths (stale checkouts, deleted venvs).

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
from backend.mcp.installer import SERVER_NAME

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
    python = _python_executable()
    if not Path(python).is_file():
        return _report(_FAIL, "python executable", f"{python} does not exist")
    return _report(_PASS, "python executable", python)


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
    return _report(_PASS, "build_server()",
                   f"{stats['tools']} tools, {stats['resources']} resources exposed")


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
        yield str(command.get("path", "")), [str(a) for a in command.get("args", [])]
    else:
        yield str(command), [str(a) for a in entry.get("args", [])]


def _client_config_sources() -> list[tuple[str, Path, list[str]]]:
    """(label, file, json-keys-to-check). Keys are tried in order, first hit wins."""
    home = Path.home()
    return [
        ("cursor (user)", home / ".cursor/mcp.json", ["mcpServers"]),
        ("cursor (project)", _project_root() / ".cursor/mcp.json", ["mcpServers"]),
        ("claude-code (user)", home / ".claude.json", ["mcpServers"]),
        ("claude-desktop", _claude_desktop_config_path(), ["mcpServers"]),
        ("zed", home / ".config/zed/settings.json", ["context_servers"]),
        ("gemini", home / ".gemini/settings.json", ["mcpServers"]),
    ]


def _scan_json_clients() -> Iterator[tuple[str, str, list[str]]]:
    """Yield (client_label, command, args) for each guaardvark entry found."""
    for label, path, keys in _client_config_sources():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        for key in keys:
            for command, args in _scan_mcp_servers_obj(data.get(key)):
                yield label, command, args
        # claude-code also nests per-project server maps.
        for proj in (data.get("projects") or {}).values() if label.startswith("claude-code") else ():
            if isinstance(proj, dict):
                for command, args in _scan_mcp_servers_obj(proj.get("mcpServers")):
                    yield f"{label} project", command, args


def _scan_grok() -> Iterator[tuple[str, str, list[str]]]:
    path = Path.home() / ".grok/config.toml"
    if not path.is_file():
        return
    try:
        import tomllib
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    entry = (data.get("mcp_servers") or {}).get(SERVER_NAME)
    if isinstance(entry, dict):
        yield "grok", str(entry.get("command", "")), [str(a) for a in entry.get("args", [])]


def _check_clients() -> bool:
    found = list(_scan_json_clients()) + list(_scan_grok())
    if not found:
        _report(_WARN, "client configs",
                f"no client has a '{SERVER_NAME}' entry — run: python -m backend.mcp install")
        return True  # informational, not a server fault

    ok = True
    for label, command, args in found:
        problems = _entry_problems(command, args)
        if problems:
            ok = _report(_FAIL, f"client: {label}", "; ".join(problems)) and ok
        else:
            preview = " ".join([command, *args])
            _report(_PASS, f"client: {label}", preview[:100])
    return ok


# ─────────────────────── entry point ───────────────────────


def run_doctor() -> int:
    print("Guaardvark MCP doctor\n")
    print("Server:")
    ok = _check_python()
    ok = _check_sdk() and ok
    if ok:
        ok = _check_build() and ok
        ok = _check_stdio() and ok
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
