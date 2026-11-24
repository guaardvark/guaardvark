"""
Install-snippet generator for external MCP clients.

Usage::

    python -m backend.mcp config --client claude-desktop
    python -m backend.mcp config --client claude-code
    python -m backend.mcp config --client cursor
    python -m backend.mcp config --client zed

Prints what to paste, or the command to run, to wire Guaardvark into the
client by hand, along with where it goes. ``python -m backend.mcp install``
does the same without the pasting.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import sys
from pathlib import Path
from typing import Any

SERVER_NAME = "guaardvark"

CLIENT_CHOICES = ("claude-desktop", "claude-code", "cursor", "zed")


def _python_executable() -> str:
    """
    Resolve the python the snippet should point at. Must be a python where
    ``mcp`` is installed — otherwise Claude Desktop's first startup fails
    with an import error and the user has no idea why.

    Preference order:
      1. Project venv at ``backend/venv/bin/python`` (standard Guaardvark layout).
      2. The currently running interpreter (``sys.executable``).
    """
    venv_python = _project_root() / "backend" / "venv" / "bin" / "python"
    if venv_python.is_file() and os.access(venv_python, os.X_OK):
        return str(venv_python)
    return os.path.realpath(sys.executable)


def _server_cmd() -> list[str]:
    return [_python_executable(), "-m", "backend.mcp"]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _shell_wrapper() -> tuple[str, list[str]]:
    """
    Command that launches the stdio server from any working directory.

    ``python -m backend.mcp`` needs the repo root on ``sys.path`` (and several
    tools resolve ``data/`` relative to the cwd), so the entry must cd first.
    Not every client honours a ``cwd`` key, so wrap in ``sh -c`` — portable
    across every POSIX client we target.
    """
    root = shlex.quote(str(_project_root()))
    python = shlex.quote(_python_executable())
    return "sh", ["-c", f"cd {root} && exec {python} -m backend.mcp"]


def _launch() -> dict[str, Any]:
    """The ``command``/``args`` a client entry needs to start the server."""
    if platform.system() == "Windows":
        # No ``sh`` to wrap with, so ``cwd`` is the only way to set the directory.
        cmd = _server_cmd()
        return {"command": cmd[0], "args": cmd[1:], "cwd": str(_project_root())}
    command, args = _shell_wrapper()
    return {"command": command, "args": args}


def _claude_desktop_config_path() -> Path:
    """OS-specific path to Claude Desktop's config file."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude/claude_desktop_config.json"
    # Linux (unofficial — Claude Desktop isn't shipped for Linux yet, but the
    # community packagers follow the XDG path.)
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def _claude_code_add_command() -> list[str]:
    """The ``claude mcp add`` line that registers the server for every project."""
    launch = _launch()
    return ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--",
            launch["command"], *launch["args"]]


def _snippet(client: str) -> tuple[dict[str, Any], str]:
    """Return (snippet, where it goes) for ``client``."""
    launch = _launch()

    if client == "claude-desktop":
        return {"mcpServers": {SERVER_NAME: launch}}, str(_claude_desktop_config_path())

    if client == "claude-code":
        # Claude Code keeps user-scope servers in its own state file, written
        # by ``claude mcp add``. The file it reads that is meant to be edited
        # by hand is a project's ``.mcp.json``.
        return {"mcpServers": {SERVER_NAME: launch}}, ".mcp.json in the root of that project"

    if client == "cursor":
        # Cursor: Settings → MCP Servers, or ~/.cursor/mcp.json.
        return {"mcpServers": {SERVER_NAME: launch}}, str(Path.home() / ".cursor/mcp.json")

    if client == "zed":
        # Zed: settings.json → context_servers.
        command = {"path": launch["command"], "args": launch["args"], "env": {}}
        return ({"context_servers": {SERVER_NAME: {"command": command}}},
                str(Path.home() / ".config/zed/settings.json"))

    raise ValueError(f"Unknown client: {client}")


def print_snippet(client: str) -> int:
    try:
        snippet, where = _snippet(client)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"choose one of: {', '.join(CLIENT_CHOICES)}", file=sys.stderr)
        return 2

    if client == "claude-code":
        print("# Claude Code registers MCP servers through its own CLI. To add Guaardvark for every project, run:")
        print(" ".join(shlex.quote(part) for part in _claude_code_add_command()))
        print("#")
        print(f"# Or, for one project only, save the following as {where}:")
    else:
        print(f"# Paste the following into: {where}")
    print(json.dumps(snippet, indent=2))
    return 0


def snippet_report(client: str) -> tuple[int, dict[str, Any]]:
    """What print_snippet prints, as ``(exit code, {status, data[, error]})``:
    the entry, where it goes and, for Claude Code, the command that adds it."""
    try:
        snippet, where = _snippet(client)
    except ValueError as exc:
        return 2, {"status": "error", "error": {
            "code": "UNKNOWN_CLIENT", "message": f"{exc}; choose one of: {', '.join(CLIENT_CHOICES)}"}}
    data: dict[str, Any] = {"client": client, "where": where, "snippet": snippet}
    if client == "claude-code":
        data["command"] = _claude_code_add_command()
    return 0, {"status": "success", "data": data}
