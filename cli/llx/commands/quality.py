"""Quality scorecard CLI (automation / KPI)."""

import typer

from llx.client import get_client, LlxConnectionError, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx import output

quality_app = typer.Typer(help="Quality gates and scorecard")


@quality_app.command("scorecard")
def quality_scorecard(
    server: str = typer.Option(None, "--server", "-s", help="Backend base URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Fetch structured quality scorecard from GET /api/meta/quality-scorecard."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        raw = client.get("/api/meta/quality-scorecard")
        data = raw.get("data", raw)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        output.print_json(data)
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)
