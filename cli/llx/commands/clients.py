"""Client management commands — list, create, info, delete."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

clients_app = typer.Typer(help="Client management", no_args_is_help=True)


@clients_app.command("list")
def clients_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all clients."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/clients")
        clients = (
            data
            if isinstance(data, list)
            else data.get("data", data.get("clients", []))
        )
        if not isinstance(clients, list):
            clients = []

        if json_out or output.is_pipe():
            output.print_json(clients)
            return

        rows = [
            {
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "projects": c.get("project_count", len(c.get("projects", []))),
            }
            for c in clients
        ]
        output.print_table(
            rows, columns=["id", "name", "projects"], title=f"Clients ({len(rows)})"
        )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@clients_app.command("create")
def clients_create(
    name: str = typer.Argument(..., help="Client name"),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new client."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {"name": name}
        if description:
            body["description"] = description
        data = api_client.post("/api/clients/", json=body)
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(
                f"Created client: {name} (id: {result.get('id', '?')})"
            )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@clients_app.command("info")
def clients_info(
    client_id: int = typer.Argument(..., help="Client ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show client details."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get(f"/api/clients/{client_id}")
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_kv(
                {
                    "ID": result.get("id", ""),
                    "Name": result.get("name", ""),
                    "Description": result.get("description", "—"),
                    "Projects": result.get(
                        "project_count", len(result.get("projects", []))
                    ),
                },
                title="Client Details",
            )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@clients_app.command("delete")
def clients_delete(
    client_id: int = typer.Argument(..., help="Client ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a client."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete client {client_id}?", abort=True)
    try:
        api_client = get_client(server)
        api_client.delete(f"/api/clients/{client_id}")
        output.print_success(f"Deleted client {client_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
