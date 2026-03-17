"""Website management commands — list, create, info, delete, scrape."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

websites_app = typer.Typer(help="Website management", no_args_is_help=True)


@websites_app.command("list")
def websites_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all websites."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get("/api/websites/")
        sites = (
            data
            if isinstance(data, list)
            else data.get("data", data.get("websites", []))
        )
        if not isinstance(sites, list):
            sites = []

        if json_out or output.is_pipe():
            output.print_json(sites)
            return

        rows = [
            {
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "pages": s.get("page_count", 0),
            }
            for s in sites
        ]
        output.print_table(
            rows,
            columns=["id", "name", "url", "pages"],
            title=f"Websites ({len(rows)})",
        )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@websites_app.command("create")
def websites_create(
    name: str = typer.Argument(..., help="Website name"),
    url: str = typer.Argument(..., help="Website URL"),
    client_id: int = typer.Option(None, "--client", "-c", help="Client ID"),
    project_id: int = typer.Option(None, "--project", "-p", help="Project ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new website entry."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {"name": name, "url": url}
        if client_id:
            body["client_id"] = client_id
        if project_id:
            body["project_id"] = project_id
        data = api_client.post("/api/websites/", json=body)
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(
                f"Created website: {name} (id: {result.get('id', '?')})"
            )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@websites_app.command("info")
def websites_info(
    website_id: int = typer.Argument(..., help="Website ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show website details."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get(f"/api/websites/{website_id}")
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_kv(
                {
                    "ID": result.get("id", ""),
                    "Name": result.get("name", ""),
                    "URL": result.get("url", ""),
                    "Pages": result.get("page_count", 0),
                    "Client": (result.get("client") or {}).get("name", "—"),
                },
                title="Website Details",
            )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@websites_app.command("scrape")
def websites_scrape(
    website_id: int = typer.Argument(..., help="Website ID to scrape"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Trigger a scrape of the website."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.post(f"/api/websites/{website_id}/scrape")
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            msg = result.get("message", f"Scrape started for website {website_id}")
            output.print_success(msg)

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@websites_app.command("delete")
def websites_delete(
    website_id: int = typer.Argument(..., help="Website ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a website."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete website {website_id}?", abort=True)
    try:
        api_client = get_client(server)
        api_client.delete(f"/api/websites/{website_id}")
        output.print_success(f"Deleted website {website_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
