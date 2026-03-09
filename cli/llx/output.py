"""Rich output formatting — tables, panels, markdown, JSON, pipe detection."""

import json
import sys
from typing import Any

from rich.markdown import Markdown
from rich.syntax import Syntax

from llx.theme import make_console, make_table, make_panel, ICON_SUCCESS, ICON_ERROR, ICON_WARNING

_json_mode = False
_console = make_console(stderr=True)
_out = make_console()


def refresh_theme():
    """Rebuild console instances after a theme change."""
    global _console, _out
    _console = make_console(stderr=True)
    _out = make_console()


def set_json_mode(enabled: bool):
    global _json_mode
    _json_mode = enabled


def is_json_mode() -> bool:
    return _json_mode


def is_pipe() -> bool:
    """True if stdout is piped (not a terminal)."""
    return not sys.stdout.isatty()


def print_json(data: Any):
    """Pretty-print JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def print_table(rows: list[dict], columns: list[str] | None = None, title: str | None = None):
    """Render a Rich table, or JSON in json/pipe mode."""
    if _json_mode or is_pipe():
        print_json(rows)
        return

    if not rows:
        _out.print("[llx.dim]No results.[/llx.dim]")
        return

    cols = columns or list(rows[0].keys())
    table = make_table(title=title)
    for col in cols:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in cols])
    _out.print(table)


def print_panel(title: str, content: str, style: str = "llx.panel.border"):
    """Render a Rich panel, or JSON in json/pipe mode."""
    if _json_mode or is_pipe():
        print_json({"title": title, "content": content})
        return
    _out.print(make_panel(content, title=title))


def print_markdown(text: str):
    """Render markdown with syntax highlighting."""
    if _json_mode or is_pipe():
        print(text)
        return
    _out.print(Markdown(text))


def print_success(message: str):
    if _json_mode or is_pipe():
        print_json({"status": "success", "message": message})
        return
    _out.print(f"[llx.success]{ICON_SUCCESS} {message}[/llx.success]")


def print_error(message: str):
    """Print error to stderr."""
    _console.print(f"[llx.error]{ICON_ERROR} Error:[/llx.error] {message}")


def print_warning(message: str):
    _console.print(f"[llx.warning]{ICON_WARNING} Warning:[/llx.warning] {message}")


def print_kv(pairs: dict, title: str | None = None):
    """Print key-value pairs as a neat panel."""
    if _json_mode or is_pipe():
        print_json(pairs)
        return
    lines = []
    for k, v in pairs.items():
        lines.append(f"[llx.kv.key]{k}:[/llx.kv.key] {v}")
    content = "\n".join(lines)
    if title:
        _out.print(make_panel(content, title=title))
    else:
        _out.print(content)
