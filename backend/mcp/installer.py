"""
One-command client setup: ``python -m backend.mcp install``.

Writes (or merges) a ``guaardvark`` MCP server entry into the config of each
detected agent client — Cursor, Claude Code, Grok, Claude Desktop, Zed,
Gemini — instead of asking the user to paste JSON by hand.

Safety rules:
  * Existing config files are backed up (``<file>.guaardvark-backup``) before
    the first rewrite, and other server entries are never touched.
  * CLI-based clients (claude, grok) are configured through their own
    ``mcp add`` commands so we never hand-edit files those tools own.
  * All paths are computed at runtime from this checkout's location; nothing
    machine-specific is baked in.
"""

from __future__ import annotations

import json
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.mcp.cli import (
    _claude_desktop_config_path,
    _project_root,
    _python_executable,
)

SERVER_NAME = "guaardvark"

CLIENTS = ("cursor", "claude-code", "grok", "claude-desktop", "zed", "gemini")


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


def _stdio_entry() -> dict[str, Any]:
    command, args = _shell_wrapper()
    return {"command": command, "args": args}


@dataclass
class InstallResult:
    client: str
    status: str  # "installed" | "skipped" | "failed" | "dry-run"
    detail: str


# ─────────────────────── detection ───────────────────────


def _detect(client: str) -> bool:
    home = Path.home()
    if client == "cursor":
        return bool(shutil.which("cursor-agent")) or (home / ".cursor").is_dir()
    if client == "claude-code":
        return bool(shutil.which("claude"))
    if client == "grok":
        return bool(shutil.which("grok"))
    if client == "claude-desktop":
        return _claude_desktop_config_path().parent.is_dir()
    if client == "zed":
        if platform.system() == "Darwin":
            return (home / ".config/zed").is_dir() or bool(shutil.which("zed"))
        return (home / ".config/zed").is_dir()
    if client == "gemini":
        return bool(shutil.which("gemini")) or (home / ".gemini").is_dir()
    return False


# ─────────────────────── JSON-file merge ───────────────────────


def _merge_json_config(
    path: Path,
    mutate: Callable[[dict[str, Any]], None],
    dry_run: bool,
) -> str:
    """
    Load ``path`` (empty dict if absent), apply ``mutate``, back up the
    original once, write back. Returns a human-readable summary.
    Raises on unparseable existing content — never clobber a file we can't read.
    """
    data: dict[str, Any] = {}
    if path.is_file() and path.stat().st_size > 0:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")

    mutate(data)

    if dry_run:
        return f"would write {SERVER_NAME} entry to {path}"

    if path.is_file():
        backup = path.with_name(path.name + ".guaardvark-backup")
        if not backup.exists():
            shutil.copy2(path, backup)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"wrote {SERVER_NAME} entry to {path}"


def _set_mcp_servers_entry(data: dict[str, Any]) -> None:
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("existing 'mcpServers' key is not an object")
    servers[SERVER_NAME] = _stdio_entry()


# ─────────────────────── per-client installers ───────────────────────


def _run_cli(argv: list[str], dry_run: bool) -> str:
    pretty = " ".join(shlex.quote(a) for a in argv)
    if dry_run:
        return f"would run: {pretty}"
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"`{pretty}` failed (exit {proc.returncode}): {detail}")
    return f"ran: {pretty}"


def _install_cursor(dry_run: bool) -> str:
    return _merge_json_config(
        Path.home() / ".cursor/mcp.json", _set_mcp_servers_entry, dry_run)


def _install_claude_code(dry_run: bool) -> str:
    command, args = _shell_wrapper()
    return _run_cli(
        ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", command, *args],
        dry_run,
    )


def _install_grok(dry_run: bool) -> str:
    command, args = _shell_wrapper()
    return _run_cli(
        ["grok", "mcp", "add", "--scope", "user", SERVER_NAME, command, "--", *args],
        dry_run,
    )


def _install_claude_desktop(dry_run: bool) -> str:
    return _merge_json_config(
        _claude_desktop_config_path(), _set_mcp_servers_entry, dry_run)


def _install_zed(dry_run: bool) -> str:
    def mutate(data: dict[str, Any]) -> None:
        servers = data.setdefault("context_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("existing 'context_servers' key is not an object")
        command, args = _shell_wrapper()
        servers[SERVER_NAME] = {"command": {"path": command, "args": args, "env": {}}}

    # Zed's settings.json may contain comments (JSONC); json.loads will raise
    # and we report a paste-it-yourself failure rather than corrupt the file.
    return _merge_json_config(Path.home() / ".config/zed/settings.json", mutate, dry_run)


def _install_gemini(dry_run: bool) -> str:
    return _merge_json_config(
        Path.home() / ".gemini/settings.json", _set_mcp_servers_entry, dry_run)


_INSTALLERS: dict[str, Callable[[bool], str]] = {
    "cursor": _install_cursor,
    "claude-code": _install_claude_code,
    "grok": _install_grok,
    "claude-desktop": _install_claude_desktop,
    "zed": _install_zed,
    "gemini": _install_gemini,
}


# ─────────────────────── entry point ───────────────────────


def install_client(client: str, dry_run: bool = False, force: bool = False) -> InstallResult:
    if client not in _INSTALLERS:
        return InstallResult(client, "failed", f"unknown client (choose from {', '.join(CLIENTS)})")
    if not force and not _detect(client):
        return InstallResult(client, "skipped", "client not detected on this machine")
    try:
        detail = _INSTALLERS[client](dry_run)
    except Exception as exc:
        return InstallResult(client, "failed", str(exc))
    return InstallResult(client, "dry-run" if dry_run else "installed", detail)


def run_install(clients: list[str] | None, dry_run: bool = False) -> int:
    """
    Install the guaardvark server entry. With no explicit ``clients``,
    auto-detect and configure everything present. Returns a process exit code.
    """
    explicit = bool(clients)
    targets = clients or [c for c in CLIENTS if _detect(c)]
    if not targets:
        print("No supported MCP clients detected "
              f"(looked for: {', '.join(CLIENTS)}).", file=sys.stderr)
        return 1

    results = [install_client(c, dry_run=dry_run, force=explicit) for c in targets]

    width = max(len(r.client) for r in results)
    failed = False
    for r in results:
        mark = {"installed": "ok", "dry-run": "dry", "skipped": "--", "failed": "FAIL"}[r.status]
        print(f"  [{mark:>4}] {r.client:<{width}}  {r.detail}")
        failed = failed or r.status == "failed"

    if not dry_run and any(r.status == "installed" for r in results):
        print("\nRestart the client (or reload its MCP servers) to pick up the change.")
        print("Verify with: python -m backend.mcp doctor")
    return 1 if failed else 0
