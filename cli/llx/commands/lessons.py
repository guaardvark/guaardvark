"""Lesson pearls — begin / end / list the active lesson."""

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
lessons_app = typer.Typer(help="Begin/end lesson pearls", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


@lessons_app.command("begin")
def lessons_begin(
    session: str = typer.Option(None, "--session", help="Chat session id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Start a lesson bracket."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    body = {}
    if session:
        body["session_id"] = session
    try:
        data = _unwrap(_client(server).post("/api/lessons/start", json=body))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        lesson_id = data.get("lesson_id", data.get("id", "")) if isinstance(data, dict) else ""
        output.print_success(f"Lesson started{': ' + str(lesson_id) if lesson_id else ''}")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@lessons_app.command("end")
def lessons_end(
    lesson_id: str = typer.Argument(..., help="Lesson id from /lessons begin"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
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
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@lessons_app.command("list")
def lessons_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show the active lesson, if any."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/lessons/active"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        if not data:
            console.print("[llx.dim]No active lesson. /lessons begin to start one.[/llx.dim]")
            return
        if isinstance(data, dict):
            output.print_kv({k: v for k, v in data.items() if not isinstance(v, (dict, list))}, title="Active lesson")
        else:
            console.print(data)
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
