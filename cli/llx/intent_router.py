"""Resolve REPL input to CLI commands before falling through to chat LLM.

Extended with many natural-language shortcuts for local agentic actions so the
REPL feels like Claude Code / Cline / Cursor / Grok when doing software work.
"""

from __future__ import annotations

import re
import shlex

from llx.command_catalog import COMMAND_TREE

# (pattern, command name, fixed sub-args or None to use parsed tail)
_NL_INTENT_RULES: list[tuple[re.Pattern[str], str, list[str] | None]] = [
    (re.compile(r"^(?:list|show|what are)\s+(?:the\s+)?agents?\s*$", re.I), "agents", ["list"]),
    (re.compile(r"^agents?\s+list\s*$", re.I), "agents", ["list"]),
    (re.compile(r"^(?:system\s+)?status\s*$", re.I), "status", []),
    (re.compile(r"^health(?:\s+check)?\s*$", re.I), "health", []),
    (
        re.compile(
            r"^(?:outreach\s+)?(?:comment|scout|draft).*(?:youtube|reddit|discord|comfyui|offline\s*ai|local\s*(?:llm|ai)).+$",
            re.I,
        ),
        "outreach",
        None,
    ),
    (re.compile(r"^outreach(?:\s+(.+))?$", re.I), "outreach", None),
    (re.compile(r"^(?:run|execute)\s+agent\s+(.+)$", re.I), "agents", None),

    # Image / video generation (must precede generic COMMAND_TREE "generate" fallback)
    (
        re.compile(
            r"^(?:generate|create|make|draw)\s+(?:an?\s+)?(?:image|picture|photo)\s+(?:of\s+)?(.+)$",
            re.I,
        ),
        "imagine",
        None,
    ),
    (
        re.compile(r"^(?:generate|create|make)\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)$", re.I),
        "video",
        None,
    ),

    (re.compile(r"^(?:list|show)\s+plugins?\s*$", re.I), "plugins", ["list"]),
    (re.compile(r"^what(?:'s| is) using the gpu\s*$", re.I), "gpu", ["status"]),
    (re.compile(r"^gpu(?:\s+status)?\s*$", re.I), "gpu", ["status"]),
    (re.compile(r"^start\s+(comfyui|ollama|upscaling)\s*$", re.I), "plugins", None),
    (re.compile(r"^play\s+(?:that\s+)?(?:voice|audio|clip)\s+(.+)$", re.I), "audio", None),

    # Local coding actions (new in this improvement)
    (re.compile(r"^(?:ls|list(?:\s+files?)?|dir)\s*(.*)$", re.I), "ls", None),
    (re.compile(r"^(?:cd|chdir)\s+(.+)$", re.I), "cd", None),
    (re.compile(r"^pwd\s*$", re.I), "pwd", []),
    (re.compile(r"^(?:cat|read|show|view)\s+(?:file\s+)?(.+)$", re.I), "read", None),
    (re.compile(r"^(?:grep|search|find)\s+(.+)$", re.I), "grep", None),
    (re.compile(r"^(?:edit|fix|update|implement|change)\s+(.+)$", re.I), "edit", None),
    (re.compile(r"^(?:run|exec|execute|sh)\s+(.+)$", re.I), "run", None),
    (re.compile(r"^(?:test|pytest|check)\s*(.*)$", re.I), "test", None),
    (re.compile(r"^(?:todo|task|tasks?)\s*(.*)$", re.I), "todo", None),
    (re.compile(r"^(?:context|status|state|where am i)\s*$", re.I), "context", []),
    (re.compile(r"^(?:suggest|what tools?|recommend tools?)\s*$", re.I), "suggest", []),
    (re.compile(r"^(?:init|initialize|setup project|create guaardvark)\s*$", re.I), "init", []),
    (re.compile(r"^(?:analyze|review|inspect)\s+(?:the\s+)?(?:site|project|folder|css|styling|build)\s*(.*)$", re.I), "analyze", None),
    (re.compile(r"^(?:suggest|improve)\s+(?:css|styling|style)\s*(.*)$", re.I), "analyze", None),

    # Load skill / dynamic instructions (like loading this session's skill files)
    (re.compile(r"^(?:load|use|apply)\s+(?:the\s+)?skill\s+(.+?)(?:\s+(?:file|md|instructions?))?\s*$", re.I), "load", None),
    (re.compile(r"^(?:load|read)\s+(?:skill|instructions?)\s+(.+)$", re.I), "load", None),
]


def resolve_repl_line(line: str) -> tuple[str, list[str]] | None:
    """Return (command, args) for SlashRouter, or None to use chat."""
    raw = line.strip()
    if not raw or raw.startswith("/"):
        return None

    if raw.lower().startswith("guaardvark "):
        raw = raw[len("guaardvark ") :].strip()
    if not raw:
        return None

    for pattern, cmd, fixed_args in _NL_INTENT_RULES:
        match = pattern.match(raw)
        if not match:
            continue
        if fixed_args is not None:
            return cmd, list(fixed_args)
        if cmd == "outreach":
            # Prefer capture group when present; else full raw line is the NL intent.
            try:
                captured = (match.group(1) or "").strip()
            except IndexError:
                captured = ""
            payload = captured or raw
            if payload.lower().startswith("outreach "):
                payload = payload[9:].strip()
            return cmd, [payload] if payload else []
        tail = (match.group(1) or "").strip()
        if cmd in ("ls", "read", "grep", "edit", "run", "test", "cd", "todo"):
            # pass the whole tail as single arg string; slash handler will shlex if needed
            return cmd, [tail] if tail else []
        if cmd == "analyze":
            return "analyze", [tail] if tail else []
        if cmd == "load":
            return "load", [tail] if tail else []
        if cmd in ("imagine", "video"):
            return cmd, [tail] if tail else []
        if cmd == "plugins":
            captured = (match.group(1) or "").strip() if match.lastindex else ""
            return cmd, ["start", captured] if captured else ["list"]
        if cmd == "audio":
            return cmd, ["play", tail] if tail else ["voices"]
        return cmd, ["run", tail] if tail else []

    try:
        parts = shlex.split(raw)
    except ValueError:
        return None

    if not parts:
        return None

    cmd = parts[0].lower()
    if cmd in COMMAND_TREE:
        return cmd, parts[1:]

    return None
