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
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
    except LlxError as e:
        raise output.fail(e)


@swarm_app.command("run")
def swarm_run(
    what: str = typer.Argument(..., help="What to do in plain words, a plan .md file, or a template name"),
    repo: str = typer.Option(None, "--repo", "-r", help="Git repository to work on (default: Guaardvark itself)"),
    max_agents: int = typer.Option(None, "--max-agents", "-n", help="Agents working at once"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and plan, start no agents"),
    allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Start even with uncommitted changes in Guaardvark's own checkout"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Launch a swarm: each task gets its own agent in its own git worktree.

    A sentence becomes a one-task plan. A markdown plan has one `## Task:` heading
    per agent (see `swarm templates`).
    """
    from pathlib import Path

    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    body: dict = {"dry_run": dry_run, "acknowledge_dirty_tree": allow_dirty}
    if repo:
        body["repo_path"] = str(Path(repo).expanduser().resolve())
    if max_agents:
        body["max_agents"] = max_agents
    try:
        client = _client(server)
        local = Path(what).expanduser()
        if what.endswith(".md") and local.is_file():
            body["plan_markdown"] = local.read_text(encoding="utf-8")
        elif what.endswith(".md") or " " not in what.strip():
            names = [t.get("filename", t) if isinstance(t, dict) else t
                     for t in (_unwrap(client.get("/api/swarm/templates")) or {}).get("templates", [])]
            name = what if what.endswith(".md") else f"{what}.md"
            if name in names:
                body["plan_path"] = f"plugins/swarm/templates/{name}"
            else:
                body["prompt"] = what
        else:
            body["prompt"] = what

        data = client.post("/api/swarm/launch", json=body)
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
            return
        swarm_id = result.get("swarm_id", result.get("id", "")) if isinstance(result, dict) else ""
        output.print_success("Swarm planned (dry run)" if dry_run else "Swarm launched")
        if swarm_id:
            console.print(f"[llx.dim]id: {swarm_id}  →  guaardvark swarm status {swarm_id}[/llx.dim]")
    except LlxConnectionError as e:
        raise output.fail(e)
    except LlxError as e:
        if e.status_code == 409:
            output.print_error(
                "Guaardvark's own checkout has uncommitted changes, and a swarm would build on top of "
                "them. Commit them first, or run again with --allow-dirty.", code="DIRTY_TREE")
        elif e.status_code == 503:
            output.print_error("The Swarm plugin is not running. Start it with: guaardvark plugins start swarm",
                               code="SWARM_OFFLINE")
        else:
            output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@swarm_app.command("templates")
def swarm_templates(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List the plan templates a swarm can start from."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/swarm/templates")) or {}
        items = data.get("templates", [])
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": items})
            return
        rows = [{"template": t.get("filename", ""), "title": t.get("title", t.get("name", "")),
                 "tasks": t.get("task_count", t.get("tasks", ""))} if isinstance(t, dict)
                else {"template": str(t), "title": "", "tasks": ""} for t in items]
        output.print_table(rows, title="Swarm templates")
    except LlxError as e:
        raise output.fail(e)

@swarm_app.command("status")
def swarm_status(
    swarm_id: str = typer.Argument(None, help="Swarm id (omit for overall status)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
    except LlxError as e:
        raise output.fail(e)


@swarm_app.command("logs")
def swarm_logs(
    swarm_id: str = typer.Argument(..., help="Swarm id"),
    task_id: str = typer.Argument(..., help="Task id"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
    except LlxError as e:
        raise output.fail(e)
