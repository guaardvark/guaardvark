"""Quality scorecard CLI (automation / KPI)."""

import typer

from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx import output

quality_app = typer.Typer(help="Quality gates and scorecard")


@quality_app.command("scorecard")
def quality_scorecard(
    server: str = typer.Option(None, "--server", "-s", help="Backend base URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Run the structured quality scorecard (POST /api/meta/quality-scorecard)."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        raw = client.post("/api/meta/quality-scorecard")
        data = raw.get("data", raw)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        output.print_json(data)
    except LlxError as e:
        raise output.fail(e)
