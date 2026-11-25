"""Image management commands — list, generate, delete."""

from pathlib import Path

import typer
from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
images_app = typer.Typer(help="Image generation and management", no_args_is_help=True)


@images_app.command("list")
def images_list(
    limit: int = typer.Option(None, "--limit", "-n", help="How many batches, newest first (default: 20 in a table, all in JSON; 0 = all)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List generated image batches."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get("/api/batch-image/list")
        batches = data.get("data", data)
        if isinstance(batches, dict):
            batches = batches.get("batches", [])
        if not isinstance(batches, list):
            batches = []

        total = len(batches)
        if json_out or output.is_pipe():
            if limit:
                batches = batches[:limit]
            output.print_json({"status": "success", "data": {"batches": batches}})
            return
        shown = 20 if limit is None else limit
        if shown:
            batches = batches[:shown]

        rows = [{
            "id": b.get("batch_id", b.get("id", "")),
            "name": b.get("display_name", b.get("batch_id", "")),
            "images": b.get("completed_images", b.get("total_images", 0)),
            "status": b.get("status", ""),
        } for b in batches]
        output.print_listing(rows, ["id", "name", "images", "status"], f"Image Batches ({total})", total=total)

    except LlxError as e:
        raise output.fail(e)


def _save_images(api_client, files: list[str], save: Path) -> list[Path]:
    """Download a finished batch's images. One image goes to `save` as a file
    (unless it is a folder); several go into `save` as a folder."""
    as_dir = len(files) > 1 or save.is_dir()
    saved = []
    for url in files:
        name = url.rsplit("/", 1)[-1]
        dest = save / name if as_dir else save
        saved.append(api_client.download(url, dest, console=output.progress_console()))
    return saved


@images_app.command("generate")
def images_generate(
    prompt: str = typer.Argument(None, help="Image description prompt"),
    from_file: Path = typer.Option(None, "--from-file", "-f", exists=True, dir_okay=False,
                                   help="Text file with one prompt per line; all go in one batch"),
    count: int = typer.Option(1, "--count", "-n", help="Number of images"),
    model: str = typer.Option(None, "--model", "-m", help="Registry model id (default: active image model)"),
    width: int = typer.Option(None, "--width", "-W", help="Image width (default: model native)"),
    height: int = typer.Option(None, "--height", "-H", help="Image height (default: model native)"),
    steps: int = typer.Option(None, "--steps", help="Inference steps (default: model native)"),
    seed: int = typer.Option(None, "--seed", help="Seed for the first image; each next image uses the next number"),
    negative: str = typer.Option(None, "--negative", help="What to keep out of the image"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for the images, showing progress"),
    save: Path = typer.Option(None, "--save", help="With --wait: download the result here (a file for one image, a folder for several)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Generate images from a prompt."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    if save and not wait:
        output.print_error("--save needs --wait (there is nothing to download until the images finish)", code="USAGE")
        raise typer.Exit(output.EXIT_USAGE)
    try:
        api_client = get_client(server)
        if from_file:
            prompts = [ln.strip() for ln in from_file.read_text(encoding="utf-8").splitlines()
                       if ln.strip() and not ln.lstrip().startswith("#")]
        elif prompt:
            prompts = [prompt] * count
        else:
            output.print_error("Give a prompt, or --from-file with one prompt per line", code="USAGE")
            raise typer.Exit(output.EXIT_USAGE)
        count = len(prompts)
        body = {
            "prompts": prompts,
            "batch_size": count,
        }
        if model:
            body["model"] = model
        if width is not None:
            body["width"] = width
        if height is not None:
            body["height"] = height
        if steps is not None:
            # A typed step count is a deliberate choice; the server keeps it
            # instead of substituting the model's default.
            body["steps"] = steps
            body["steps_explicit"] = True
        if seed is not None:
            body["seed"] = seed
        if negative:
            body["negative_prompt"] = negative

        data = api_client.post("/api/batch-image/generate/prompts", json=body)
        result = data.get("data", data)
        job_id = result.get("job_id", result.get("batch_id", ""))
        quiet = json_out or output.is_pipe()

        if not wait or not job_id:
            if quiet:
                output.print_json({"status": "success", "data": result})
            else:
                output.print_success(f"Image generation started ({count} image{'s' if count > 1 else ''})")
                if job_id:
                    output.print_hint(f"Track with: [llx.accent]guaardvark jobs watch {job_id}[/llx.accent]")
            return

        from llx.commands.jobs import job_failure_json, watch_job

        try:
            info = watch_job(api_client, job_id, quiet=quiet or output.progress_console() is None)
        except KeyboardInterrupt:
            console.print("\n[llx.dim]Detached. Generation continues on the server.[/llx.dim]")
            console.print(f"  Track with: [llx.accent]guaardvark jobs watch {job_id}[/llx.accent]")
            raise typer.Exit(0)

        if info["status"] != "completed":
            if quiet:
                output.print_json(job_failure_json(info))
            else:
                output.print_error(str(info.get("error") or f"Generation {info['status']}"), code="JOB_FAILED")
            raise typer.Exit(output.EXIT_JOB_FAILED)

        files = info.get("files") or []
        saved = [str(p) for p in _save_images(api_client, files, save)] if save and files else []
        if quiet:
            output.print_json({"status": "success", "data": {"job_id": job_id, "files": files, "saved": saved}})
        elif saved:
            output.print_success(f"Saved {', '.join(saved)}")
        else:
            output.print_success(f"{len(files)} image{'s' if len(files) != 1 else ''} ready")
            output.print_hint(f"Fetch with: [llx.accent]guaardvark images download {job_id}[/llx.accent]")

    except LlxError as e:
        raise output.fail(e)


@images_app.command("download")
def images_download(
    batch_id: str = typer.Argument(..., help="Batch ID to download"),
    image_name: str = typer.Argument(None, help="One image's filename (omit for the whole batch as a ZIP)"),
    output_dir: Path = typer.Option(Path("."), "--output", "-o", help="Folder to save into"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Download a batch's images: one file, or the whole batch as a ZIP."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        if image_name:
            dest = output_dir / image_name
            api_client.download(f"/api/batch-image/image/{batch_id}/{image_name}", dest,
                                console=output.progress_console())
        else:
            dest = output_dir / f"{batch_id}.zip"
            api_client.download(f"/api/batch-image/download/{batch_id}", dest,
                                console=output.progress_console())
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"path": str(dest)}})
        else:
            output.print_success(f"Downloaded to {dest}")

    except LlxError as e:
        raise output.fail(e)


@images_app.command("status")
def images_status(
    batch_id: str = typer.Argument(..., help="Batch ID"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
            output.print_json({"status": "success", "data": result})
        else:
            output.print_kv({
                "Batch ID": batch_id,
                "Status": result.get("status", ""),
                "Progress": f"{result.get('progress', 0)}%",
                "Images": result.get("image_count", result.get("completed", 0)),
            }, title="Batch Status")

    except LlxError as e:
        raise output.fail(e)


@images_app.command("models")
def images_models(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
            output.print_json({"status": "success", "data": {"models": models}})
            return

        if isinstance(models, list):
            rows = [{"name": m.get("name", m) if isinstance(m, dict) else str(m)} for m in models]
            output.print_table(rows, columns=["name"], title="Image Models")
        else:
            output.print_json({"status": "success", "data": {"models": models}})

    except LlxError as e:
        raise output.fail(e)


@images_app.command("delete")
def images_delete(
    batch_id: str = typer.Argument(..., help="Batch ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Delete an image batch."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    if not force and not (json_out or output.is_pipe()):
        typer.confirm(f"Delete batch {batch_id}?", abort=True)
    try:
        api_client = get_client(server)
        api_client.delete(f"/api/batch-image/delete/{batch_id}")
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"batch_id": batch_id, "deleted": True}})
        else:
            output.print_success(f"Deleted batch {batch_id}")
    except LlxError as e:
        raise output.fail(e)
