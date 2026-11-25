"""Lesson pearls — begin / end / list the active lesson."""

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
lessons_app = typer.Typer(help="Begin/end lesson pearls", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


def _session(session: str | None) -> str:
    """The chat a lesson belongs to: the one named, else this CLI's latest chat."""
    from llx.config import get_last_session_id

    sid = session or get_last_session_id()
    if not sid:
        output.print_error("A lesson is attached to a conversation. Chat first (guaardvark chat or the REPL), "
                           "or pass --session <id>.", code="NO_SESSION")
        raise typer.Exit(1)
    return sid


@lessons_app.command("begin")
def lessons_begin(
    session: str = typer.Option(None, "--session", help="Chat session id (default: your latest chat)"),
    title: str = typer.Option(None, "--title", "-t", help="A name for the lesson"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Start a lesson bracket."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    body = {"session_id": _session(session)}
    if title:
        body["title"] = title
    try:
        data = _unwrap(_client(server).post("/api/lessons/start", json=body))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        lesson_id = data.get("lesson_id", data.get("id", "")) if isinstance(data, dict) else ""
        output.print_success(f"Lesson started{': ' + str(lesson_id) if lesson_id else ''}")
    except LlxError as e:
        raise output.fail(e)


@lessons_app.command("end")
def lessons_end(
    lesson_id: str = typer.Argument(..., help="Lesson id from /lessons begin"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """End a lesson and distill it into memory."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).post(f"/api/lessons/{lesson_id}/end"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        output.print_success("Lesson ended and distilled")
    except LlxError as e:
        raise output.fail(e)


@lessons_app.command("list")
def lessons_list(
    session: str = typer.Option(None, "--session", help="Chat session id (default: your latest chat)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show the active lesson of a chat session: your latest chat in this CLI, or --session.

    Needs a chat session; without one it says so and exits 1."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/lessons/active", session_id=_session(session)))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        if not data or (isinstance(data, dict) and not data.get("active")):
            console.print("[llx.dim]No active lesson. /lessons begin to start one.[/llx.dim]")
            return
        if isinstance(data, dict):
            output.print_kv({k: v for k, v in data.items()
                             if k not in ("success", "active") and not isinstance(v, (dict, list))},
                            title="Active lesson")
        else:
            console.print(data)
    except LlxError as e:
        raise output.fail(e)
