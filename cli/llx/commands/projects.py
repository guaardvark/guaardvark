"""Project management commands."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

projects_app = typer.Typer(help="Project management")


@projects_app.command("list")
def projects_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all projects."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/projects")
        projects = data if isinstance(data, list) else data.get("data", [])

        if json_out or output.is_pipe():
            output.print_json(projects)
            return

        rows = [{
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "client": (p.get("client") or {}).get("name", "—"),
            "docs": p.get("document_count", 0),
            "tasks": p.get("task_count", 0),
        } for p in projects]
        output.print_table(rows, columns=["id", "name", "client", "docs", "tasks"], title=f"Projects ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("create")
def projects_create(
    name: str = typer.Argument(..., help="Project name"),
    client_id: int = typer.Option(None, "--client", "-c", help="Client ID"),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new project."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        body = {"name": name, "description": description}
        if client_id:
            body["client_id"] = client_id
        data = client.post("/api/projects", json=body)
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Created project: {name} (id: {result.get('id', '?')})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("info")
def projects_info(
    project_id: int = typer.Argument(..., help="Project ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show project details."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/projects/{project_id}")
        project = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(project)
        else:
            output.print_kv({
                "ID": project.get("id", ""),
                "Name": project.get("name", ""),
                "Client": (project.get("client") or {}).get("name", "—"),
                "Description": project.get("description", "—"),
                "Documents": project.get("document_count", 0),
                "Tasks": project.get("task_count", 0),
            }, title="Project Details")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("delete")
def projects_delete(
    project_id: int = typer.Argument(..., help="Project ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a project."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete project {project_id}?", abort=True)
    try:
        client = get_client(server)
        client.delete(f"/api/projects/{project_id}")
        output.print_success(f"Deleted project {project_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
