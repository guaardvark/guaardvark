"""
One-command client setup: ``python -m backend.mcp install``.

Writes (or merges) a ``guaardvark`` MCP server entry into the config of each
detected agent client — Cursor, Claude Code, Codex, Grok, Antigravity,
opencode, Claude Desktop, Zed, Gemini — instead of asking the user to paste
JSON by hand.

Safety rules:
  * Existing config files are backed up (``<file>.guaardvark-backup``) before
    the first rewrite, and other server entries are never touched.
  * Only the launch command of the ``guaardvark`` entry is ours. Whatever else
    the user put on it (``env`` with an API key, ``disabled``, auto-approve
    lists) survives a re-run.
  * A re-run is safe: an entry that already launches this checkout is left
    alone and reported as already configured.
  * CLI-based clients (claude, codex, grok, agy) are configured through their own
    ``mcp add`` commands so we never hand-edit files those tools own. Their
    config is only read, to see what is already there.
  * All paths are computed at runtime from this checkout's location; nothing
    machine-specific is baked in.
"""

from __future__ import annotations

import copy
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.mcp.cli import (
    SERVER_NAME,
    _claude_desktop_config_path,
    _project_root,
    _shell_wrapper,
)

CLIENTS = ("cursor", "claude-code", "codex", "grok", "antigravity", "opencode",
           "claude-desktop", "zed", "gemini")

# An installer's detail line starts with this when the client already had the
# entry this checkout would write, so nothing was run or rewritten.
ALREADY_CONFIGURED = "already configured"


def _stdio_entry() -> dict[str, Any]:
    command, args = _shell_wrapper()
    return {"command": command, "args": args}


@dataclass
class InstallResult:
    client: str
    status: str  # "installed" | "unchanged" | "skipped" | "failed" | "dry-run"
    detail: str


# ─────────────────────── detection ───────────────────────


def _detect(client: str) -> bool:
    home = Path.home()
    if client == "cursor":
        return bool(shutil.which("cursor-agent")) or (home / ".cursor").is_dir()
    if client == "claude-code":
        return bool(shutil.which("claude"))
    if client == "codex":
        return bool(shutil.which("codex")) or (home / ".codex").is_dir()
    if client == "grok":
        return bool(shutil.which("grok"))
    if client == "antigravity":
        return bool(shutil.which("agy"))
    if client == "opencode":
        return bool(shutil.which("opencode")) or _opencode_config_path().parent.is_dir()
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


# Keys that point an entry at a remote server. They cannot stay beside a stdio
# command, so a merge drops them.
_REMOTE_KEYS = ("url", "serverUrl", "httpUrl", "headers")


def _merged_entry(existing: Any, launch: dict[str, Any]) -> dict[str, Any]:
    """``existing`` with ``launch`` written over it.

    Only the launch keys are replaced. Everything else on the entry is the
    user's and stays."""
    if not isinstance(existing, dict):
        return dict(launch)
    merged = {k: v for k, v in existing.items() if k not in _REMOTE_KEYS}
    merged.update(launch)
    if "type" not in launch and merged.get("type") not in (None, "stdio"):
        merged["type"] = "stdio"
    return merged


def _switched_off_note(entry: dict[str, Any]) -> str | None:
    """Says so when the user has the entry disabled; the merge keeps it that way."""
    if entry.get("disabled") is True or entry.get("enabled") is False:
        return "the entry is switched off in the client and was left that way"
    return None


def _merge_json_config(
    path: Path,
    mutate: Callable[[dict[str, Any]], str | None],
    dry_run: bool,
) -> str:
    """
    Load ``path`` (empty dict if absent), apply ``mutate``, back up the
    original once, write back. Returns a human-readable summary; ``mutate``
    may return a note to append to it. A file that ``mutate`` leaves as it
    was is not rewritten.
    Raises on unparseable existing content — never clobber a file we can't read.
    """
    data: dict[str, Any] = {}
    if path.is_file() and path.stat().st_size > 0:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")

    before = copy.deepcopy(data)
    note = mutate(data)
    suffix = f" ({note})" if note else ""

    from backend.utils.display_paths import display_path

    if data == before:
        return f"{ALREADY_CONFIGURED} in {display_path(path)}{suffix}"

    if dry_run:
        return f"would write {SERVER_NAME} entry to {display_path(path)}{suffix}"

    if path.is_file():
        backup = path.with_name(path.name + ".guaardvark-backup")
        if not backup.exists():
            shutil.copy2(path, backup)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"wrote {SERVER_NAME} entry to {display_path(path)}{suffix}"


def _set_mcp_servers_entry(data: dict[str, Any]) -> str | None:
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("existing 'mcpServers' key is not an object")
    entry = servers[SERVER_NAME] = _merged_entry(servers.get(SERVER_NAME), _stdio_entry())
    return _switched_off_note(entry)


# ─────────────────────── what a CLI-managed client already has ───────────────────────


def _claude_code_config_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"


def _codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def _grok_config_path() -> Path:
    return Path.home() / ".grok" / "config.toml"


def _antigravity_config_path() -> Path:
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def _load_config_file(path: Path) -> dict[str, Any] | None:
    """The parsed JSON or TOML document at ``path``; None when it is missing,
    empty, unparseable (JSON with comments, say) or not a table."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".toml":
            import tomllib
            data = tomllib.loads(text)
        else:
            data = json.loads(text)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _existing_entry(path: Path, table: str) -> tuple[bool, dict[str, Any] | None]:
    """``(readable, entry)`` for the ``guaardvark`` entry under ``table``.

    ``readable`` is False when the file could not be read, in which case no
    entry says nothing about whether the client has one."""
    data = _load_config_file(path)
    if data is None:
        return False, None
    servers = data.get(table)
    entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    return True, entry if isinstance(entry, dict) else None


def _same_launch(entry: dict[str, Any] | None, command: str, args: list[str]) -> bool:
    if entry is None or entry.get("command") != command:
        return False
    found = entry.get("args") or []
    return isinstance(found, list) and [str(a) for a in found] == args


def _env_flags(flag: str, entry: dict[str, Any] | None) -> list[str]:
    """``flag KEY=value`` pairs that carry the old entry's environment to the
    new one. A client's ``mcp add`` replaces the entry, and the environment is
    where the backend's API key lives."""
    env = entry.get("env") if entry else None
    if not isinstance(env, dict):
        return []
    flags: list[str] = []
    for key, value in env.items():
        flags += [flag, f"{key}={value}"]
    return flags


def _not_carried_note(entry: dict[str, Any] | None) -> str:
    """Names what a client's ``mcp add`` cannot take across from the old entry."""
    if not entry:
        return ""
    lost = sorted(k for k in entry if k not in ("type", "command", "args", "env"))
    return f" (not carried over from the old entry: {', '.join(lost)})" if lost else ""


# ─────────────────────── per-client installers ───────────────────────


def _run_cli(argv: list[str], dry_run: bool, masked: tuple[str, ...] = (),
             may_fail: bool = False) -> str:
    """Run a client's own CLI and describe what happened.

    ``masked`` are ``KEY=value`` arguments whose value is kept out of the
    description: it is printed, and the value may be an API key. With
    ``may_fail`` a non-zero exit is described instead of raised."""
    from backend.utils.display_paths import display_text

    shown = [f"{a.split('=', 1)[0]}=***" if a in masked else a for a in argv]
    pretty = display_text(" ".join(shlex.quote(a) for a in shown))
    if dry_run:
        return f"would run: {pretty}"
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if may_fail:
            return f"ran: {pretty} (exit {proc.returncode}: {detail})"
        raise RuntimeError(f"`{pretty}` failed (exit {proc.returncode}): {detail}")
    return f"ran: {pretty}"


def _install_cursor(dry_run: bool) -> str:
    return _merge_json_config(
        Path.home() / ".cursor/mcp.json", _set_mcp_servers_entry, dry_run)


def _install_claude_code(dry_run: bool) -> str:
    command, args = _shell_wrapper()
    readable, entry = _existing_entry(_claude_code_config_path(), "mcpServers")
    if _same_launch(entry, command, args):
        return f"{ALREADY_CONFIGURED} (user scope)"

    # `claude mcp add` refuses a name that already exists, so an old entry is
    # removed first. When the config could not be read the removal is tried
    # anyway; with nothing to remove it fails and that is fine.
    removed = ""
    if entry is not None or not readable:
        removed = _run_cli(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME],
                           dry_run, may_fail=True)
    env = _env_flags("-e", entry)
    add = ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, *env, "--", command, *args]
    try:
        added = _run_cli(add, dry_run, masked=tuple(env[1::2]))
    except RuntimeError as exc:
        raise RuntimeError(f"{exc} (before it: {removed})" if removed else str(exc)) from None
    return "; ".join(step for step in (removed, added) if step) + _not_carried_note(entry)


def _install_via_mcp_add(path: Path, table: str, env_flag: str,
                         argv: Callable[[list[str], str, list[str]], list[str]],
                         dry_run: bool) -> str:
    """For a client whose ``mcp add`` replaces an existing entry: skip the run
    when the entry already launches this checkout, otherwise run it with the
    old entry's environment. ``argv(env_flags, command, args)`` builds the line."""
    command, args = _shell_wrapper()
    _readable, entry = _existing_entry(path, table)
    if _same_launch(entry, command, args):
        return ALREADY_CONFIGURED
    env = _env_flags(env_flag, entry)
    ran = _run_cli(argv(env, command, args), dry_run, masked=tuple(env[1::2]))
    return ran + _not_carried_note(entry)


def _install_grok(dry_run: bool) -> str:
    return _install_via_mcp_add(
        _grok_config_path(), "mcp_servers", "-e",
        lambda env, command, args: ["grok", "mcp", "add", "--scope", "user", *env,
                                    SERVER_NAME, command, "--", *args],
        dry_run,
    )


def _install_codex(dry_run: bool) -> str:
    return _install_via_mcp_add(
        _codex_config_path(), "mcp_servers", "--env",
        lambda env, command, args: ["codex", "mcp", "add", SERVER_NAME, *env, "--", command, *args],
        dry_run,
    )


def _install_antigravity(dry_run: bool) -> str:
    # agy rejects a flag placed after the server name.
    return _install_via_mcp_add(
        _antigravity_config_path(), "mcpServers", "--env",
        lambda env, command, args: ["agy", "mcp", "add", *env, SERVER_NAME, "--", command, *args],
        dry_run,
    )


def _opencode_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "opencode" / "opencode.json"


def _install_opencode(dry_run: bool) -> str:
    # opencode's own `mcp add` is interactive, so its JSON config is merged here.
    def mutate(data: dict[str, Any]) -> str | None:
        servers = data.setdefault("mcp", {})
        if not isinstance(servers, dict):
            raise ValueError("existing 'mcp' key is not an object")
        command, args = _shell_wrapper()
        entry = _merged_entry(servers.get(SERVER_NAME),
                              {"type": "local", "command": [command, *args]})
        entry.setdefault("enabled", True)
        servers[SERVER_NAME] = entry
        data.setdefault("$schema", "https://opencode.ai/config.json")
        return _switched_off_note(entry)

    return _merge_json_config(_opencode_config_path(), mutate, dry_run)


def _install_claude_desktop(dry_run: bool) -> str:
    return _merge_json_config(
        _claude_desktop_config_path(), _set_mcp_servers_entry, dry_run)


def _install_zed(dry_run: bool) -> str:
    def mutate(data: dict[str, Any]) -> str | None:
        servers = data.setdefault("context_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("existing 'context_servers' key is not an object")
        command, args = _shell_wrapper()
        existing = servers.get(SERVER_NAME)
        if isinstance(existing, dict) and isinstance(existing.get("command"), str):
            # Zed's flat form: the executable, with args and env beside it.
            entry = _merged_entry(existing, {"command": command, "args": args})
        else:
            entry = _merged_entry(existing, {})
            launch = dict(entry["command"]) if isinstance(entry.get("command"), dict) else {}
            launch.update({"path": command, "args": args})
            launch.setdefault("env", {})
            entry["command"] = launch
        servers[SERVER_NAME] = entry
        return _switched_off_note(entry)

    # Zed's settings.json may contain comments (JSONC); json.loads will raise
    # and we report a paste-it-yourself failure rather than corrupt the file.
    return _merge_json_config(Path.home() / ".config/zed/settings.json", mutate, dry_run)


def _install_gemini(dry_run: bool) -> str:
    return _merge_json_config(
        Path.home() / ".gemini/settings.json", _set_mcp_servers_entry, dry_run)


_INSTALLERS: dict[str, Callable[[bool], str]] = {
    "cursor": _install_cursor,
    "claude-code": _install_claude_code,
    "codex": _install_codex,
    "grok": _install_grok,
    "antigravity": _install_antigravity,
    "opencode": _install_opencode,
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
    if detail.startswith(ALREADY_CONFIGURED):
        return InstallResult(client, "unchanged", detail)
    return InstallResult(client, "dry-run" if dry_run else "installed", detail)


# ---------------------------------------------------------------------------
# Agent skills (.agents/skills -> ~/.claude/skills and <root>/.claude/skills)
# ---------------------------------------------------------------------------

SKILLS_SOURCE = Path(".agents") / "skills"
# Skill folders carry bare job names (image, video, swarm, ...): inside the
# checkout and in the Claude Code plugin the namespace is the plugin. In the
# user's personal skills folder there is no namespace, so links get this prefix.
SKILL_PREFIX = "guaardvark-"


def _skills_source_dir() -> Path:
    return _project_root() / SKILLS_SOURCE


def _skill_targets() -> list[Path]:
    """Where Claude Code looks for skills: the user's personal folder (every
    project) and this checkout's project folder (``.claude/`` is gitignored
    here, so the link is per-clone)."""
    return [Path.home() / ".claude" / "skills", _project_root() / ".claude" / "skills"]


def _link_skill(src: Path, dest: Path, dry_run: bool) -> str:
    """Symlink ``dest`` -> ``src``; copy when symlinks are unavailable.

    An existing symlink is repointed. An existing real directory that is not
    ours is left alone and reported, never overwritten."""
    if dest.is_symlink():
        if dest.resolve() == src.resolve():
            return "already linked"
        if dry_run:
            return f"would repoint -> {src}"
        dest.unlink()
    elif dest.exists():
        return f"skipped: {dest} exists and is not a symlink"
    if dry_run:
        return f"would link -> {src}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.symlink_to(src, target_is_directory=True)
        return f"linked -> {src}"
    except (OSError, NotImplementedError):
        shutil.copytree(src, dest)
        return f"copied (symlinks unavailable) <- {src}"


def _antigravity_plugin_source() -> Path:
    return _project_root() / ".agents" / "plugins" / "guaardvark"


def _has_antigravity_plugin() -> bool:
    return (_antigravity_plugin_source() / "plugin.json").is_file()


def install_antigravity_plugin(dry_run: bool = False) -> InstallResult:
    """Install .agents/plugins/guaardvark with ``agy plugin install``. The
    plugin carries the skills, the rules and its own MCP server entry, so it
    takes the place of the bare server entry for Antigravity."""
    src = _antigravity_plugin_source()
    if dry_run:
        return InstallResult("plugin:antigravity", "dry-run", f"would run: agy plugin install {src}")
    try:
        proc = subprocess.run(["agy", "plugin", "install", str(src)],
                              capture_output=True, text=True, timeout=60)
    except Exception as exc:
        return InstallResult("plugin:antigravity", "failed", str(exc))
    if proc.returncode != 0:
        return InstallResult("plugin:antigravity", "failed", (proc.stderr or proc.stdout or "").strip())
    return InstallResult("plugin:antigravity", "installed", f"installed guaardvark plugin from {src}")


# The launcher in scripts/mcp_launcher.sh reads this when a plugin client
# cannot tell it where the checkout is (Grok Build, the plugin repository).
CHECKOUT_POINTER = Path(".config") / "guaardvark" / "root"


def _save_checkout_root(dry_run: bool) -> None:
    if dry_run:
        return
    pointer = Path.home() / CHECKOUT_POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(_project_root()) + "\n", encoding="utf-8")


def install_skills(dry_run: bool = False) -> list[InstallResult]:
    """Link every ``.agents/skills/guaardvark-*`` folder into each skills target.

    Returns one InstallResult per (target, skill) so ``run_install`` can print
    them in the same table as the client entries."""
    source = _skills_source_dir()
    skills = sorted(
        p for p in source.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "SKILL.md").is_file()
    )
    if not skills:
        return [InstallResult("skills", "failed", f"no <skill>/SKILL.md under {source}")]
    results: list[InstallResult] = []
    for target in _skill_targets():
        for skill in skills:
            link_name = skill.name if skill.name.startswith(SKILL_PREFIX) else SKILL_PREFIX + skill.name
            detail = _link_skill(skill, target / link_name, dry_run)
            status = ("skipped" if detail.startswith("skipped")
                      else "dry-run" if dry_run
                      else "installed")
            results.append(InstallResult(f"skill:{link_name}", status, f"{target}: {detail}"))
    return results


def run_install(clients: list[str] | None, dry_run: bool = False, skills: bool = False) -> int:
    """
    Install the guaardvark server entry. With no explicit ``clients``,
    auto-detect and configure everything present. With ``skills``, also link
    the agent skills. Returns a process exit code.
    """
    explicit = bool(clients)
    targets = clients or [c for c in CLIENTS if _detect(c)]
    if not targets and not skills:
        print("No supported MCP clients detected "
              f"(looked for: {', '.join(CLIENTS)}).", file=sys.stderr)
        return 1

    _save_checkout_root(dry_run)
    # With --skills, Antigravity gets the plugin (skills, rules and server)
    # instead of a bare server entry, so it does not run the server twice.
    agy_plugin = skills and "antigravity" in targets and _has_antigravity_plugin()
    results = [install_client(c, dry_run=dry_run, force=explicit)
               for c in targets if not (agy_plugin and c == "antigravity")]
    if skills:
        results.extend(install_skills(dry_run=dry_run))
    if agy_plugin:
        results.append(install_antigravity_plugin(dry_run=dry_run))
    if not results:
        return 1

    width = max(len(r.client) for r in results)
    failed = False
    for r in results:
        mark = {"installed": "ok", "unchanged": "ok", "dry-run": "dry", "skipped": "--",
                "failed": "FAIL"}[r.status]
        print(f"  [{mark:>4}] {r.client:<{width}}  {r.detail}")
        failed = failed or r.status == "failed"

    if not dry_run and any(r.status == "installed" for r in results):
        print("\nRestart the client (or reload its MCP servers) to pick up the change.")
        print("Verify with: python -m backend.mcp doctor")
    return 1 if failed else 0
