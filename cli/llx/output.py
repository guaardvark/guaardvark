"""Rich output formatting — tables, panels, markdown, JSON, pipe detection."""

import json
import sys
from typing import Any

import typer
from rich.markdown import Markdown
from rich.syntax import Syntax

from llx.global_opts import get_global_quiet
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


def progress_console():
    """The console for a progress bar, or None when one would corrupt the output."""
    return None if (_json_mode or is_pipe() or get_global_quiet()) else _console


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


def print_listing(rows: list[dict], columns: list[str], title: str, total: int | None = None):
    """A table whose first column (an id to copy) is never cut and whose second
    (a free-text name) is shortened to fit instead of wrapping onto more lines."""
    if not rows:
        _out.print("[llx.dim]No results.[/llx.dim]")
        return
    def width(col: str) -> int:
        return max([len(col)] + [len(str(r.get(col, ""))) for r in rows])

    # Borders and padding take three cells per column plus one.
    room = _out.width - (3 * len(columns) + 1) - sum(width(c) for i, c in enumerate(columns) if i != 1)
    room = max(room, 12)
    table = make_table(title=title)
    for col in columns:
        table.add_column(col, no_wrap=True)
    for row in rows:
        cells = [str(row.get(c, "")) for c in columns]
        if len(columns) > 1 and len(cells[1]) > room:
            cells[1] = cells[1][: room - 1].rstrip() + "…"
        table.add_row(*cells)
    _out.print(table)
    if total and total > len(rows):
        _out.print(f"[llx.dim]Newest {len(rows)} of {total}. --limit 0 lists them all.[/llx.dim]")


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
    if get_global_quiet():
        return
    _out.print(f"[llx.success]{ICON_SUCCESS} {message}[/llx.success]")


def print_hint(message: str):
    """A next-step tip for a person at a terminal; never part of scripted output."""
    if _json_mode or is_pipe() or get_global_quiet():
        return
    _out.print(f"  {message}")


def print_error(message: str, code: str = "ERROR"):
    """Report an error without putting it in the data stream.

    --json keeps the structured error on stdout, where a caller that asked for
    JSON parses it. A plain pipe (`guaardvark images list | jq`) gets it on
    stderr, so the next program never reads an error as a result.
    """
    payload = {"status": "error", "error": {"code": code, "message": message}}
    if _json_mode:
        print_json(payload)
        return
    if is_pipe() and not sys.stderr.isatty():
        print(json.dumps(payload, indent=2, default=str), file=sys.stderr)
        return
    _console.print(f"[llx.error]{ICON_ERROR} Error:[/llx.error] {message}")


# Exit codes a script can branch on. 1 stays the catch-all and 2 is usage,
# as Click already uses it.
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
EXIT_NOT_FOUND = 4
EXIT_DENIED = 5
EXIT_JOB_FAILED = 6
EXIT_TIMEOUT = 7


def fail(exc: Exception) -> typer.Exit:
    """Print a client error and return the exit to raise for it.

    Usage: `except LlxError as e: raise output.fail(e)`.
    """
    from llx.client import LlxConnectionError

    status = getattr(exc, "status_code", 0)
    if isinstance(exc, LlxConnectionError):
        code, exit_code = "CONNECTION_ERROR", EXIT_UNREACHABLE
    elif status == 404:
        code, exit_code = "NOT_FOUND", EXIT_NOT_FOUND
    elif status in (401, 403):
        code, exit_code = "NOT_AUTHORIZED", EXIT_DENIED
    elif status == 408:
        code, exit_code = "TIMEOUT", EXIT_TIMEOUT
    else:
        code, exit_code = "API_ERROR", EXIT_ERROR
    print_error(str(exc), code=code)
    return typer.Exit(exit_code)


def print_warning(message: str):
    _console.print(f"[llx.warning]{ICON_WARNING} Warning:[/llx.warning] {message}")


def print_link(label: str, url: str):
    """Print an OSC-8 hyperlink when the terminal supports it (Rich markup)."""
    if _json_mode or is_pipe():
        print_json({"label": label, "url": url})
        return
    _out.print(f"[link={url}]{label}[/link]")


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
