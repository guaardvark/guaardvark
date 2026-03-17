"""Semantic search command."""

import typer

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()


def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Semantic search over indexed documents."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post("/api/search/semantic", json={"query": query})

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        answer = data.get("answer", "")
        sources = data.get("sources", [])[:limit]

        if answer:
            output.print_markdown(answer)

        if sources:
            console.print(f"\n[llx.dim]Sources ({len(sources)}):[/llx.dim]")
            rows = [
                {
                    "source": s.get("source_document", "?"),
                    "score": f"{s.get('score', 0):.3f}",
                }
                for s in sources
            ]
            output.print_table(rows, columns=["source", "score"])

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
