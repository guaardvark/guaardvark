"""Content generation commands — CSV, image."""

from pathlib import Path

import typer

from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
generate_app = typer.Typer(help="Content generation", no_args_is_help=True)


@generate_app.command("csv")
def generate_csv(
    prompt: str = typer.Argument(..., help="Generation prompt"),
    output_file: str = typer.Option("output.csv", "--output", "-o", help="Output filename"),
    client_name: str = typer.Option(None, "--client", "-c", help="Client name"),
    project_name: str = typer.Option(None, "--project", "-p", help="Project name"),
    word_count: int = typer.Option(500, "--words", "-w", help="Target word count"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
            output.print_json({"status": "success", "data": result})
        else:
            output.print_success(f"Generated: {result.get('output_file', output_file)}")
            stats = result.get("statistics", {})
            if stats:
                console.print(f"  [llx.dim]Items: {stats.get('generated_items', '?')} | Time: {stats.get('generation_time', '?')}s[/llx.dim]")

    except LlxError as e:
        raise output.fail(e)


@generate_app.command("image")
def generate_image(
    prompt: str = typer.Argument(..., help="Image description"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for the image, showing progress"),
    save: Path = typer.Option(None, "--save", help="With --wait: download the image to this file"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Generate an image from a prompt (short form of `images generate`)."""
    from llx.commands.images import images_generate

    images_generate(
        prompt=prompt, from_file=None, count=1, model=None, width=None, height=None, steps=None,
        seed=None, negative=None, wait=wait, save=save, server=server, json_out=json_out,
    )
