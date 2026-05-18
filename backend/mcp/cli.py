"""
Install-snippet generator for external MCP clients.

Usage::

    python -m backend.mcp config --client claude-desktop
    python -m backend.mcp config --client claude-code
    python -m backend.mcp config --client cursor
    python -m backend.mcp config --client zed

Prints the exact JSON to paste into the client's config file along with
the path where it lives, so users can get Guaardvark wired up without
reading four different docs.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

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


def _snippet(client: str) -> tuple[dict[str, Any], Path | None]:
    """Return (snippet, config_file_path). Path is None if the client has no standard file."""
    cmd = _server_cmd()
    cwd = str(_project_root())

    if client == "claude-desktop":
        return (
            {
                "mcpServers": {
                    "guaardvark": {
                        "command": cmd[0],
                        "args": cmd[1:],
                        "cwd": cwd,
                    }
                }
            },
            _claude_desktop_config_path(),
        )

    if client == "claude-code":
        # Claude Code reads from ~/.claude/mcp_servers.json (user scope)
        # or <project>/.claude/mcp_servers.json (project scope).
        return (
            {
                "mcpServers": {
                    "guaardvark": {
                        "command": cmd[0],
                        "args": cmd[1:],
                        "cwd": cwd,
                    }
                }
            },
            Path.home() / ".claude/mcp_servers.json",
        )

    if client == "cursor":
        # Cursor: Settings → MCP Servers, or ~/.cursor/mcp.json.
        return (
            {
                "mcpServers": {
                    "guaardvark": {
                        "command": cmd[0],
                        "args": cmd[1:],
                        "cwd": cwd,
                    }
                }
            },
            Path.home() / ".cursor/mcp.json",
        )

    if client == "zed":
        # Zed: settings.json → context_servers.
        return (
            {
                "context_servers": {
                    "guaardvark": {
                        "command": {
                            "path": cmd[0],
                            "args": cmd[1:],
                            "env": {},
                        }
                    }
                }
            },
            Path.home() / ".config/zed/settings.json",
        )

    raise ValueError(f"Unknown client: {client}")


def print_snippet(client: str) -> int:
    try:
        snippet, path = _snippet(client)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"choose one of: {', '.join(CLIENT_CHOICES)}", file=sys.stderr)
        return 2

    if path is not None:
        print(f"# Paste the following into: {path}")
    print(json.dumps(snippet, indent=2))
    return 0
