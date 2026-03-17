"""Indexing / RAG commands — document and entity indexing."""

import typer

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

index_app = typer.Typer(
    help="Document and entity indexing for RAG", no_args_is_help=True
)


@index_app.command("document")
def index_document(
    doc_id: int = typer.Argument(..., help="Document ID to index"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Trigger indexing for a document."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post(f"/api/index/{doc_id}")

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            output.print_success(f"Indexing triggered for document {doc_id}")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@index_app.command("status")
def index_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show entity indexing status."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/entity-indexing/status")
        counts = data.get("entity_counts", {})

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            output.print_kv(counts, title="Entity Indexing Status")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@index_app.command("entity")
def index_entity(
    entity_type: str = typer.Option(
        ..., "--type", "-t", help="Entity type: client, project, website, task"
    ),
    entity_id: int = typer.Option(..., "--id", help="Entity ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Index a single entity by type and ID."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    if entity_type not in ("client", "project", "website", "task"):
        output.print_error("--type must be one of: client, project, website, task")
        raise typer.Exit(1)
    try:
        client = get_client(server)
        data = client.post(
            "/api/entity-indexing/index-entity",
            json={
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
        )

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            if data.get("success"):
                output.print_success(
                    data.get("message", f"Indexed {entity_type} {entity_id}")
                )
            else:
                output.print_error(data.get("error", "Indexing failed"))
                raise typer.Exit(1)

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@index_app.command("all")
def index_all(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Index all entities in the database."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post("/api/entity-indexing/index-all")

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            if data.get("success"):
                output.print_success(data.get("message", "Entity indexing completed"))
            else:
                output.print_error(data.get("error", "Indexing failed"))
                raise typer.Exit(1)

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
