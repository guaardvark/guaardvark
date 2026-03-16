"""RAG inspection — index stats, entity exploration, evaluation, and debug tracing."""

import typer
from rich.table import Table
from rich.tree import Tree

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console, make_panel, ICON_SUCCESS, ICON_WARNING
from llx import output

console = make_console()
rag_app = typer.Typer(help="RAG index inspection and evaluation", no_args_is_help=True)


@rag_app.command("status")
def rag_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show RAG index statistics — document counts, index health, storage size."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/indexing/status")
        status_data = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(status_data)
            return

        total = status_data.get("total_documents", status_data.get("document_count", "?"))
        indexed = status_data.get("indexed_documents", status_data.get("indexed_count", "?"))
        pending = status_data.get("pending_documents", status_data.get("pending_count", 0))
        embedding = status_data.get("embedding_model", "?")

        lines = []
        lines.append(f"[llx.kv.key]Total Documents:[/llx.kv.key]  {total}")
        lines.append(f"[llx.kv.key]Indexed:[/llx.kv.key]          {indexed}")
        if pending:
            lines.append(f"[llx.warning]{ICON_WARNING} Pending: {pending}[/llx.warning]")
        else:
            lines.append(f"[llx.kv.key]Pending:[/llx.kv.key]          0")
        lines.append(f"[llx.kv.key]Embedding Model:[/llx.kv.key]  [llx.accent]{embedding}[/llx.accent]")

        # Try to get storage info
        storage = status_data.get("storage", {})
        if storage:
            store_type = storage.get("type", "?")
            lines.append(f"[llx.kv.key]Store Type:[/llx.kv.key]      {store_type}")

        console.print(make_panel("\n".join(lines), title="RAG Index"))

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@rag_app.command("query")
def rag_query(
    query: str = typer.Argument(help="Search query to test against the RAG index"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results to retrieve"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Test a RAG query and see retrieved chunks with relevance scores."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post("/api/search", json={
            "query": query,
            "top_k": top_k,
        })
        results = data.get("results", data.get("data", {}).get("results", []))

        if json_out or output.is_pipe():
            output.print_json(results)
            return

        if not results:
            console.print(f"[llx.dim]No results for: {query}[/llx.dim]")
            return

        console.print(f"[llx.accent]Results for:[/llx.accent] [bold]{query}[/bold]\n")

        for i, r in enumerate(results, 1):
            score = r.get("score", r.get("relevance", 0))
            source = r.get("source", r.get("filename", r.get("document_name", "?")))
            text = r.get("text", r.get("content", ""))[:200]

            score_color = "llx.success" if score > 0.7 else ("llx.warning" if score > 0.4 else "llx.error")
            console.print(f"  [bold]{i}.[/bold] [{score_color}]{score:.3f}[/{score_color}]  [llx.accent]{source}[/llx.accent]")
            console.print(f"     [llx.dim]{text}{'...' if len(r.get('text', '')) > 200 else ''}[/llx.dim]")
            console.print()

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@rag_app.command("entities")
def rag_entities(
    limit: int = typer.Option(25, "--limit", "-l", help="Max entities to show"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List extracted entities from the RAG knowledge graph."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/indexing/entities", limit=limit)
        entities = data.get("entities", data.get("data", {}).get("entities", []))

        if json_out or output.is_pipe():
            output.print_json(entities)
            return

        if not entities:
            console.print("[llx.dim]No entities extracted yet. Index some documents first.[/llx.dim]")
            return

        table = Table(title=f"Knowledge Graph Entities ({len(entities)})", border_style="llx.panel.border")
        table.add_column("Entity", style="llx.accent")
        table.add_column("Type")
        table.add_column("Mentions", justify="right")
        table.add_column("Related To")

        for e in entities:
            name = e.get("name", e.get("entity", "?"))
            etype = e.get("type", e.get("entity_type", "?"))
            mentions = str(e.get("mention_count", e.get("count", "?")))
            related = ", ".join(e.get("related_entities", e.get("relationships", []))[:3])
            if not related:
                related = "[llx.dim]-[/llx.dim]"
            table.add_row(name, etype, mentions, related)

        console.print(table)

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@rag_app.command("eval")
def rag_eval(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show RAG autoresearch evaluation results and optimization status."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/autoresearch/status")
        status_data = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(status_data)
            return

        enabled = status_data.get("enabled", False)
        last_run = status_data.get("last_run", "Never")
        experiments = status_data.get("completed_experiments", 0)
        best_score = status_data.get("best_score")
        current_score = status_data.get("current_score")

        lines = []
        e_style = "llx.success" if enabled else "llx.dim"
        lines.append(f"[{e_style}]{ICON_SUCCESS if enabled else '  '} Autoresearch {'Enabled' if enabled else 'Disabled'}[/{e_style}]")
        lines.append(f"[llx.kv.key]Last Run:[/llx.kv.key]       {last_run}")
        lines.append(f"[llx.kv.key]Experiments:[/llx.kv.key]    {experiments}")

        if current_score is not None:
            score_color = "llx.success" if current_score > 0.7 else ("llx.warning" if current_score > 0.4 else "llx.error")
            lines.append(f"[llx.kv.key]Current Score:[/llx.kv.key]  [{score_color}]{current_score:.3f}[/{score_color}]")
        if best_score is not None:
            lines.append(f"[llx.kv.key]Best Score:[/llx.kv.key]     {best_score:.3f}")

        console.print(make_panel("\n".join(lines), title="RAG Autoresearch"))

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
