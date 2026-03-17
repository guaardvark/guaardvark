"""Slash-command tab completion for the Guaardvark REPL.

Provides nested completion for ``/command subcommand`` patterns, wrapped in
a ``FuzzyCompleter`` so partial and out-of-order keystrokes still match.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.completion.fuzzy_completer import FuzzyCompleter
from prompt_toolkit.document import Document

# ---------------------------------------------------------------------------
# Command tree — top-level commands map to their available subcommands.
# An empty list means the command accepts no subcommands.
# ---------------------------------------------------------------------------

SLASH_TREE: dict[str, list[str]] = {
    # System / quick actions
    "status": [],
    "health": [],
    "doctor": [],
    "start": [],
    "stop": [],
    "search": [],
    "dashboard": [],
    # Resource management
    "files": ["list", "upload", "download", "delete", "mkdir"],
    "projects": ["list", "create", "info", "delete"],
    "rules": ["list", "create", "delete", "export", "import"],
    "agents": ["list", "info", "run", "update"],
    "generate": ["csv", "image"],
    "jobs": ["list", "status", "watch", "cancel"],
    "settings": ["list", "get", "set"],
    "models": ["list", "active", "set"],
    "index": ["document", "status", "entity", "all"],
    "backup": ["create", "list", "download", "restore", "delete"],
    "family": ["list", "status", "sync", "health"],
    "logs": ["tail", "search", "stats"],
    "rag": ["status", "query", "entities", "eval"],
    "clients": ["list", "create", "info", "delete"],
    "websites": ["list", "create", "info", "scrape", "delete"],
    "tasks": ["list", "create", "info", "start", "download", "delete"],
    "images": ["list", "generate", "status", "models", "delete"],
    "videos": [
        "list",
        "generate",
        "from-image",
        "status",
        "models",
        "delete",
        "download",
        "combine",
    ],
    # REPL-only
    "new": [],
    "clear": [],
    "history": [],
    "export": [],
    "config": ["server", "theme", "timeout", "api_key"],
    "theme": [],
    "help": [],
    "quit": [],
}

# ---------------------------------------------------------------------------
# Short descriptions shown beside each top-level command
# ---------------------------------------------------------------------------

_META: dict[str, str] = {
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
    "new": "New conversation",
    "clear": "Clear screen",
    "history": "Command history",
    "export": "Export conversation",
    "config": "REPL configuration",
    "theme": "Switch colour theme",
    "help": "Show help",
    "quit": "Exit the REPL",
}


def _get_meta(command: str) -> str:
    """Return a short description for a top-level command."""
    return _META.get(command, "")


# ---------------------------------------------------------------------------
# Completer
# ---------------------------------------------------------------------------


class SlashCompleter(Completer):
    """Tab-completion for ``/command [subcommand]`` input.

    Parameters
    ----------
    get_dynamic_completions:
        Optional callback ``(command, sub_text) -> list[str] | None``.
        When provided, the completer calls it after exhausting static
        subcommands.  If it returns a list, those strings are yielded as
        additional completions.
    """

    def __init__(
        self,
        get_dynamic_completions: Optional[
            Callable[[str, str], Optional[List[str]]]
        ] = None,
    ) -> None:
        self.get_dynamic_completions = get_dynamic_completions

    # ---- prompt_toolkit interface ----------------------------------------

    def get_completions(self, document: Document, complete_event):  # noqa: D401
        """Yield ``Completion`` objects for the current input."""
        text = document.text_before_cursor

        # Only activate when the line starts with "/"
        if not text.startswith("/"):
            return

        stripped = text[1:]  # drop the leading "/"

        if " " not in stripped:
            # Still typing the command name — complete top-level commands.
            prefix = stripped.lower()
            for cmd in sorted(SLASH_TREE):
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display_meta=_get_meta(cmd),
                    )
            return

        # A space exists — split into command + remainder.
        cmd, _, rest = stripped.partition(" ")
        cmd = cmd.lower()
        sub_prefix = rest.lstrip().lower()

        # Static subcommands
        if cmd in SLASH_TREE:
            for sub in SLASH_TREE[cmd]:
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(sub_prefix) if sub_prefix else 0,
                    )

        # Dynamic completions (plugin-provided, live data, etc.)
        if self.get_dynamic_completions is not None:
            dynamic = self.get_dynamic_completions(cmd, rest)
            if dynamic:
                for item in dynamic:
                    if item.lower().startswith(sub_prefix):
                        yield Completion(
                            item,
                            start_position=-len(sub_prefix) if sub_prefix else 0,
                        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_completer(
    get_dynamic: Optional[Callable[[str, str], Optional[List[str]]]] = None,
) -> FuzzyCompleter:
    """Return a ``FuzzyCompleter``-wrapped ``SlashCompleter``."""
    return FuzzyCompleter(SlashCompleter(get_dynamic), enable_fuzzy=True)
