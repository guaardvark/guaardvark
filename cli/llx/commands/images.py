"""Image management commands — list, generate, delete."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
images_app = typer.Typer(help="Image generation and management", no_args_is_help=True)


@images_app.command("list")
def images_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List generated image batches."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get("/api/batch-image/list")
        batches = data.get("batches", data.get("data", []))
        if not isinstance(batches, list):
            batches = []

        if json_out or output.is_pipe():
            output.print_json(batches)
            return

        rows = [{
            "id": b.get("batch_id", b.get("id", "")),
            "name": b.get("name", b.get("batch_id", "")),
            "images": b.get("image_count", 0),
            "status": b.get("status", ""),
        } for b in batches]
        output.print_table(rows, columns=["id", "name", "images", "status"], title=f"Image Batches ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@images_app.command("generate")
def images_generate(
    prompt: str = typer.Argument(..., help="Image description prompt"),
    count: int = typer.Option(1, "--count", "-n", help="Number of images"),
    model: str = typer.Option(None, "--model", "-m", help="Model override"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate images from a prompt."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {
            "prompts": [prompt] * count,
            "batch_size": count,
        }
        if model:
            body["model"] = model

        data = api_client.post("/api/batch-image/generate/prompts", json=body)
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            job_id = result.get("job_id", result.get("batch_id", ""))
            output.print_success(f"Image generation started ({count} image{'s' if count > 1 else ''})")
            if job_id:
                console.print(f"  Track with: [llx.accent]llx jobs watch {job_id}[/llx.accent]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@images_app.command("status")
def images_status(
    batch_id: str = typer.Argument(..., help="Batch ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check generation status of a batch."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get(f"/api/batch-image/status/{batch_id}")
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_kv({
                "Batch ID": batch_id,
                "Status": result.get("status", ""),
                "Progress": f"{result.get('progress', 0)}%",
                "Images": result.get("image_count", result.get("completed", 0)),
            }, title="Batch Status")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@images_app.command("models")
def images_models(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List available image generation models."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get("/api/batch-image/models")
        models = data.get("models", data.get("data", []))

        if json_out or output.is_pipe():
            output.print_json(models)
            return

        if isinstance(models, list):
            rows = [{"name": m.get("name", m) if isinstance(m, dict) else str(m)} for m in models]
            output.print_table(rows, columns=["name"], title="Image Models")
        else:
            output.print_json(models)

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@images_app.command("delete")
def images_delete(
    batch_id: str = typer.Argument(..., help="Batch ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete an image batch."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete batch {batch_id}?", abort=True)
    try:
        api_client = get_client(server)
        api_client.delete(f"/api/batch-image/delete/{batch_id}")
        output.print_success(f"Deleted batch {batch_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
