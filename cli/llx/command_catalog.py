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


# Commands that end the session, wipe its state, start/stop the services, or
# write a file named by the next word (export).
# Typed without a slash they run only when the whole line is the bare word:
# "new ideas for a party", "exit strategy for my startup", "stop being so
# verbose" and "export my notes" are chat. With a slash they take arguments as
# usual ("/undo path/to/file.py"). "start comfyui" has its own plugin rule.
BARE_ONLY_COMMANDS: frozenset[str] = frozenset(
    {"new", "clear", "abort", "undo", "apply", "quit", "exit", "start", "stop", "export"}
)

# Typed without a slash these run bare or with one number: "history 3"
# resumes session 3, "history of rome" is chat.
BARE_OR_NUMBER_COMMANDS: frozenset[str] = frozenset({"history"})

# Commands that read no words after their name, only options. Typed without a
# slash they run bare or with options ("doctor --repair"): "doctor who
# episodes", "tools for woodworking", "suggest a name for my dog" and "status
# of my order" are chat.
OPTIONS_ONLY_COMMANDS: frozenset[str] = frozenset(
    {"status", "health", "doctor", "dashboard", "tools", "context", "suggest", "skills"}
)

# Typed without a slash these run bare or with one more word: a command name
# for help ("help agents"), a page for web ("web images"), a theme name.
# "help me write a cover letter" and "web design trends" are chat.
ONE_WORD_COMMANDS: frozenset[str] = frozenset({"help", "web", "theme"})


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
        ("mcp", ["config", "install", "doctor", "list-tools", "client"]),
        ("audio", ["tts", "play", "music", "sfx", "voices"]),
        ("swarm", ["list", "run", "templates", "status", "logs"]),
        ("lessons", ["begin", "end", "list"]),
        # Multi-modal / REPL-only
        ("imagine", []),
        ("video", []),
        ("voice", []),
        ("ingest", []),
        ("agent", ["on", "off", "shot"]),
        ("plan", ["on", "off"]),
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
    "analyze": "Analyze project/site (explore build.py, CSS, create todos; writes GUAARDVARK.md if missing)",
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
    "mcp": "Guaardvark as an MCP server; `mcp client` for the servers it uses",
    "audio": "Audio Foundry — TTS, music, SFX",
    "swarm": "Parallel agents in isolated worktrees",
    "lessons": "Begin/end lesson pearls",
    "imagine": "Generate an image from a text prompt",
    "video": "Generate a video from a text prompt",
    "voice": "Text-to-speech",
    "ingest": "Index files or a directory for RAG",
    "agent": "Toggle autonomous screen-agent mode (on/off/shot)",
    "plan": "Plan mode: look into it first, act only on a plan you approve (on/off, or Shift+Tab)",
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
