"""Swarm orchestrator — list, run, status, logs."""

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
swarm_app = typer.Typer(help="Parallel agents in isolated worktrees", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


@swarm_app.command("list")
def swarm_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List swarm history / active runs."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/swarm/history"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        items = data.get("swarms", data.get("history", data)) if isinstance(data, dict) else data
        if not items:
            console.print("[llx.dim]No swarm runs.[/llx.dim]")
            return
        rows = []
        for s in items if isinstance(items, list) else []:
            rows.append({
                "id": s.get("id", s.get("swarm_id", "")),
                "status": s.get("status", ""),
                "tasks": s.get("task_count", s.get("tasks", "")),
            })
        output.print_table(rows, title="Swarm")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@swarm_app.command("run")
def swarm_run(
    prompt: str = typer.Argument(..., help="Task prompt for the swarm"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Launch a swarm from a prompt."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post("/api/swarm/launch", json={"prompt": prompt, "goal": prompt})
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
            return
        swarm_id = result.get("swarm_id", result.get("id", "")) if isinstance(result, dict) else ""
        output.print_success("Swarm launched")
        if swarm_id:
            console.print(f"[llx.dim]id: {swarm_id}  →  guaardvark swarm status {swarm_id}[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@swarm_app.command("status")
def swarm_status(
    swarm_id: str = typer.Argument(None, help="Swarm id (omit for overall status)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show swarm status."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        path = f"/api/swarm/status/{swarm_id}" if swarm_id else "/api/swarm/status"
        data = _unwrap(_client(server).get(path))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        if isinstance(data, dict):
            output.print_kv({k: v for k, v in data.items() if not isinstance(v, (dict, list))}, title=swarm_id or "Swarm")
        else:
            console.print(data)
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@swarm_app.command("logs")
def swarm_logs(
    swarm_id: str = typer.Argument(..., help="Swarm id"),
    task_id: str = typer.Argument(..., help="Task id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Fetch logs for one swarm task."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get(f"/api/swarm/{swarm_id}/logs/{task_id}"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        logs = data.get("logs", data) if isinstance(data, dict) else data
        console.print(logs)
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
