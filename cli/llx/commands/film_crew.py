"""Film Crew pipeline — create a production, inspect, delete. Does not render shots."""

from pathlib import Path

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
film_crew_app = typer.Typer(help="Film Crew productions (render stays in Studio)", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


def _script_text(script: str | None, file: str | None) -> str:
    if file:
        path = Path(file).expanduser()
        if not path.is_file():
            raise LlxError(f"script file not found: {file}")
        return path.read_text(encoding="utf-8")
    text = (script or "").strip()
    if not text:
        raise LlxError("provide --script text or --file path")
    return text


@film_crew_app.command("list")
def film_crew_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List Film Crew productions."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/production"))
        rows = data.get("productions", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            rows = []
        if json_out or output.is_pipe():
            output.print_json(rows)
            return
        table = [{
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "stage": r.get("current_stage", ""),
            "status": r.get("status", ""),
        } for r in rows]
        output.print_table(table, columns=["id", "name", "stage", "status"],
                           title=f"Film Crew ({len(table)})")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@film_crew_app.command("create")
def film_crew_create(
    script: str = typer.Option(None, "--script", help="Screenplay text"),
    file: str = typer.Option(None, "--file", help="Path to a screenplay file"),
    name: str = typer.Option(None, "--name", "-n", help="Production name (default: first line)"),
    model: str = typer.Option(None, "--model", "-m", help="Scene / I2V model id (default: active video model)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a Film Crew production and start the screenwriter. Does not render shots."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        script_text = _script_text(script, file)
        first = next((ln.strip() for ln in script_text.splitlines() if ln.strip()), "Film Crew")
        title = (name or "").strip() or first[:80]
        body = {"name": title, "script_text": script_text, "project_id": None}
        if (model or "").strip():
            body["settings"] = {"video_model": model.strip()}
        data = _client(server).post("/api/production", json=body)
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json(result)
            return
        prod_id = result.get("id", "")
        stage = result.get("current_stage", "")
        output.print_success(f"Film Crew '{result.get('name', title)}' created (id {prod_id}, stage: {stage})")
        console.print("[llx.dim]The screenwriter is running. Casting, storyboards and renders wait in Studio.[/llx.dim]")
        console.print("[llx.dim]Open: /film-crew    Status: guaardvark film-crew status " + str(prod_id) + "[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@film_crew_app.command("status")
def film_crew_status(
    prod_id: int = typer.Argument(..., help="Production id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show one Film Crew production."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get(f"/api/production/{prod_id}"))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        shots = data.get("shots") or []
        output.print_kv({
            "ID": data.get("id", prod_id),
            "Name": data.get("name", ""),
            "Stage": data.get("current_stage", ""),
            "Status": data.get("status", ""),
            "Shots": len(shots),
        }, title="Film Crew")
        settings = data.get("settings_json") or {}
        if settings.get("video_model"):
            console.print(f"[llx.dim]video_model: {settings['video_model']}[/llx.dim]")
        if data.get("current_stage") not in ("complete", "cancelled"):
            console.print("[llx.dim]Casting, storyboards and renders wait in Studio.[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@film_crew_app.command("delete")
def film_crew_delete(
    prod_id: int = typer.Argument(..., help="Production id"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a production and its shots. Subjects are kept."""
    if not force:
        typer.confirm(f"Delete Film Crew production {prod_id}?", abort=True)
    try:
        _client(server).delete(f"/api/production/{prod_id}")
        output.print_success(f"Deleted production {prod_id}")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
