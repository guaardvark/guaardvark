"""Shared slash-command metadata for help and completion.

COMMAND_TREE is the source of truth for top-level REPL commands and their
subcommands. SlashRouter, the completer, /help, and the catalog contract
test must all agree with this map.
"""

from __future__ import annotations

import difflib
from collections import OrderedDict


# Alias → canonical command. Aliases are listed in COMMAND_TREE so they
# complete, but they dispatch to the canonical handler.
COMMAND_ALIASES: dict[str, str] = {
    "exit": "quit",
}


COMMAND_TREE: dict[str, list[str]] = OrderedDict(
    [
        ("status", []),
        ("health", []),
        ("doctor", []),
        ("start", []),
        ("stop", []),
        ("search", []),
        ("dashboard", []),
        ("files", ["list", "upload", "download", "delete", "mkdir"]),
        ("projects", ["list", "create", "info", "delete"]),
        ("rules", ["list", "create", "delete", "export", "import"]),
        ("agents", ["list", "info", "run", "update"]),
        ("generate", ["csv", "image"]),
        ("jobs", ["list", "status", "watch", "cancel"]),
        ("outreach", ["status", "queue", "approve"]),
        ("settings", ["list", "get", "set"]),
        ("models", ["list", "active", "set"]),
        ("index", ["document", "status", "entity", "all"]),
        ("backup", ["create", "list", "download", "restore", "delete"]),
        ("family", ["list", "status", "sync", "health"]),
        ("logs", ["tail", "search", "stats"]),
        ("rag", ["status", "query", "entities", "eval"]),
        ("clients", ["list", "create", "info", "delete"]),
        ("websites", ["list", "create", "info", "scrape", "delete"]),
        ("tasks", ["list", "create", "info", "start", "download", "delete"]),
        ("images", ["list", "generate", "status", "models", "delete"]),
        (
            "videos",
            ["list", "generate", "from-image", "status", "models", "delete", "download", "combine"],
        ),
        ("music-video", ["list", "create", "status", "cancel", "delete"]),
        ("film-crew", ["list", "create", "status", "delete"]),
        ("remember", []),
        ("memory", ["list", "search", "delete", "clear"]),
        # Local agentic coding surface
        ("ls", []),
        ("cd", []),
        ("pwd", []),
        ("read", []),
        ("grep", []),
        ("edit", []),
        ("run", []),
        ("test", []),
        ("todo", ["list", "add", "done", "clear"]),
        ("diff", []),
        ("apply", []),
        ("undo", []),
        ("tools", []),
        ("tool", []),
        ("context", []),
        ("suggest", []),
        ("analyze", []),
        ("init", []),
        ("load", []),
        ("skills", []),
        ("new", []),
        ("abort", []),
        ("clear", []),
        ("history", []),
        ("export", []),
        ("config", ["server", "theme", "timeout", "api_key"]),
        ("theme", []),
        ("quality", ["scorecard"]),
        ("recipes", ["list", "show", "validate"]),
        ("plugins", ["list", "start", "stop", "enable", "disable", "logs", "status"]),
        ("gpu", ["status", "release"]),
        ("mcp", ["config", "install", "doctor", "list-tools"]),
        ("audio", ["tts", "play", "music", "sfx", "voices"]),
        ("swarm", ["list", "run", "status", "logs"]),
        ("lessons", ["begin", "end", "list"]),
        # Multi-modal / REPL-only
        ("imagine", []),
        ("video", []),
        ("voice", []),
        ("ingest", []),
        ("agent", ["on", "off", "shot"]),
        ("web", []),
        ("help", []),
        ("quit", []),
        ("exit", []),
    ]
)


COMMAND_META: dict[str, str] = {
    "status": "System status overview",
    "health": "Service health checks",
    "doctor": "Diagnose common issues",
    "start": "Start Guaardvark services",
    "stop": "Stop Guaardvark services",
    "search": "Search across content",
    "dashboard": "System dashboard",
    "files": "File management",
    "projects": "Project management",
    "rules": "System prompt rules",
    "agents": "Agent management",
    "generate": "Generate content",
    "jobs": "Background job control",
    "outreach": "Social outreach status / queue / NL intent",
    "settings": "View/change settings",
    "models": "LLM model management",
    "index": "RAG indexing operations",
    "backup": "Backup & restore",
    "family": "Multi-instance family",
    "logs": "Log viewing & search",
    "rag": "RAG pipeline tools",
    "clients": "Client management",
    "websites": "Website management",
    "tasks": "Task management",
    "images": "Image generation",
    "videos": "Video generation",
    "music-video": "Beat-synced music video (plan; approve in Studio)",
    "film-crew": "Five-role Film Crew production (plan; render in Studio)",
    "remember": "Save to memory",
    "memory": "Manage saved memories",
    "ls": "List files (local)",
    "cd": "Change working directory",
    "pwd": "Print working directory",
    "read": "Read file contents (local)",
    "grep": "Search files (local)",
    "edit": "Search/replace edit with backup + diff (local)",
    "run": "Run shell command (local)",
    "test": "Run tests (local)",
    "todo": "Task list (add/list/done)",
    "diff": "Show recent or git diff",
    "apply": "Apply last proposal",
    "undo": "Restore last backup",
    "tools": "List backend agent tools (the real powerful set)",
    "tool": "Directly invoke a backend tool by name",
    "context": "Show current working context (files, todos, git, tools)",
    "suggest": "Suggest relevant tools for current context (active file, todos)",
    "analyze": "Analyze project/site (explore build.py, CSS, GUAARDVARK.md, create todos)",
    "init": "Recursively scan current project root and auto-create/update GUAARDVARK.md with agent findings",
    "load": "Load a skill / .md instructions file (e.g. load skill path/to/SKILL.md) and inject for session context",
    "skills": "List SKILL.md files in the project and ~/.guaardvark/skills",
    "new": "New conversation",
    "abort": "Hard-abort a stuck in-flight chat for this session",
    "clear": "Clear screen",
    "history": "Command history",
    "export": "Export conversation",
    "config": "REPL configuration",
    "theme": "Switch colour theme",
    "quality": "Quality scorecard and gates",
    "recipes": "Inspect and validate agent recipes",
    "plugins": "Start/stop GPU and service plugins",
    "gpu": "GPU status and owner lock",
    "mcp": "Install Guaardvark as an MCP server in agent clients",
    "audio": "Audio Foundry — TTS, music, SFX",
    "swarm": "Parallel agents in isolated worktrees",
    "lessons": "Begin/end lesson pearls",
    "imagine": "Generate an image from a text prompt",
    "video": "Generate a video from a text prompt",
    "voice": "Text-to-speech",
    "ingest": "Index files or a directory for RAG",
    "agent": "Toggle autonomous screen-agent mode (on/off/shot)",
    "web": "Open the Guaardvark web UI",
    "help": "Show help",
    "quit": "Exit the REPL",
    "exit": "Exit the REPL",
}


def suggest_command(name: str, n: int = 3, cutoff: float = 0.55) -> list[str]:
    """Return close command-name matches for an unknown token."""
    if not name:
        return []
    keys = [k for k in COMMAND_TREE if k != name]
    return difflib.get_close_matches(name.lower(), keys, n=n, cutoff=cutoff)
