"""Slash-command tab completion for the Guaardvark REPL.

Completes catalog commands with or without a leading ``/``. Nested
subcommands, local paths, and a dynamic callback (live tool names, themes)
are supported. Wrapped in ``FuzzyCompleter`` so partial and out-of-order
keystrokes still match.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterator, List, Optional

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.completion.fuzzy_completer import FuzzyCompleter
from prompt_toolkit.document import Document

from llx.command_catalog import COMMAND_META, COMMAND_TREE


def _get_meta(command: str) -> str:
    """Return a short description for a top-level command."""
    return COMMAND_META.get(command, "")


def _path_completions(sub_prefix: str, cwd: Path | None = None) -> Iterator[Completion]:
    """Yield filename completions relative to cwd."""
    try:
        base = cwd or Path.cwd()
        prefix_path = Path(sub_prefix or ".")
        search_dir = (base / prefix_path).parent if not (base / prefix_path).is_dir() else (base / prefix_path)
        search_dir = search_dir.resolve()
        if not search_dir.exists() or not search_dir.is_dir():
            return
        leaf = sub_prefix.rsplit("/", 1)[-1] if "/" in sub_prefix else sub_prefix
        for entry in sorted(search_dir.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") and not sub_prefix.startswith("."):
                continue
            name = entry.name + ("/" if entry.is_dir() else "")
            if name.lower().startswith(leaf.lower()):
                yield Completion(
                    name,
                    start_position=-len(leaf),
                    display_meta="dir" if entry.is_dir() else "file",
                )
    except Exception:
        return


class SlashCompleter(Completer):
    """Tab-completion for ``/command [subcommand]`` and bare ``command``.

    Parameters
    ----------
    get_dynamic_completions:
        Optional callback ``(command, sub_text) -> list[str] | None``.
        When provided, the completer calls it after exhausting static
        subcommands.  If it returns a list, those strings are yielded as
        additional completions.
    """

    PATH_COMMANDS = frozenset({"ls", "read", "grep", "edit", "cd", "diff", "run", "ingest", "load"})

    def __init__(
        self,
        get_dynamic_completions: Optional[
            Callable[[str, str], Optional[List[str]]]
        ] = None,
    ) -> None:
        self.get_dynamic_completions = get_dynamic_completions

    def get_completions(self, document: Document, complete_event):  # noqa: D401
        """Yield ``Completion`` objects for the current input."""
        text = document.text_before_cursor
        has_slash = text.startswith("/")
        stripped = text[1:] if has_slash else text

        last = text.rsplit(None, 1)[-1] if text.strip() else ""
        if last.startswith("@"):
            mention = last[1:]
            yield from _path_completions(mention)
            if not has_slash:
                return

        # Bare empty line is chat — do not dump every command.
        if not has_slash and not stripped:
            return

        if " " not in stripped:
            prefix = stripped.lower()
            for cmd in COMMAND_TREE:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display_meta=_get_meta(cmd),
                    )
            return

        cmd, _, rest = stripped.partition(" ")
        cmd = cmd.lower()
        sub_prefix = rest.lstrip().lower()

        if cmd in COMMAND_TREE:
            for sub in COMMAND_TREE[cmd]:
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(sub_prefix) if sub_prefix else 0,
                    )

        if self.get_dynamic_completions is not None:
            dynamic = self.get_dynamic_completions(cmd, rest)
            if dynamic:
                for item in dynamic:
                    if item.lower().startswith(sub_prefix):
                        yield Completion(
                            item,
                            start_position=-len(sub_prefix) if sub_prefix else 0,
                        )

        if cmd in self.PATH_COMMANDS:
            yield from _path_completions(rest.lstrip())

        if cmd in {"tool", "tools"}:
            common_tools = [
                "read_code",
                "edit_code",
                "search_code",
                "list_files",
                "execute_python",
                "grep_search",
                "save_memory",
                "search_memory",
                "list_code_files",
                "get_repository_map",
                "verify_change",
                "agent_task_execute",
            ]
            for name in common_tools:
                if name.lower().startswith(sub_prefix):
                    yield Completion(
                        name,
                        start_position=-len(sub_prefix) if sub_prefix else 0,
                    )


def make_completer(
    get_dynamic: Optional[Callable[[str, str], Optional[List[str]]]] = None,
) -> FuzzyCompleter:
    """Return a ``FuzzyCompleter``-wrapped ``SlashCompleter``."""
    return FuzzyCompleter(SlashCompleter(get_dynamic), enable_fuzzy=True)
