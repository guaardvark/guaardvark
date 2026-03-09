"""Content generation commands — CSV, image."""

import typer

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
generate_app = typer.Typer(help="Content generation")


@generate_app.command("csv")
def generate_csv(
    prompt: str = typer.Argument(..., help="Generation prompt"),
    output_file: str = typer.Option("output.csv", "--output", "-o", help="Output filename"),
    client_name: str = typer.Option(None, "--client", "-c", help="Client name"),
    project_name: str = typer.Option(None, "--project", "-p", help="Project name"),
    word_count: int = typer.Option(500, "--words", "-w", help="Target word count"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate CSV content from a prompt."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {
            "type": "single",
            "output_filename": output_file,
            "prompt": prompt,
            "target_word_count": word_count,
        }
        if client_name:
            body["client"] = client_name
        if project_name:
            body["project"] = project_name

        data = api_client.post("/api/generate/csv", json=body)
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Generated: {result.get('output_file', output_file)}")
            stats = result.get("statistics", {})
            if stats:
                console.print(f"  [llx.dim]Items: {stats.get('generated_items', '?')} | Time: {stats.get('generation_time', '?')}s[/llx.dim]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@generate_app.command("image")
def generate_image(
    prompt: str = typer.Argument(..., help="Image description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate an image from a prompt."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.post("/api/batch-image-generation", json={
            "prompts": [prompt],
            "batch_size": 1,
        })
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            job_id = result.get("job_id", "")
            output.print_success(f"Image generation started (job: {job_id})")
            console.print(f"  Track with: [llx.accent]llx jobs watch {job_id}[/llx.accent]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
