"""Video generation and management commands."""

import time
from pathlib import Path
from typing import Optional

import typer
from rich.live import Live
from rich.spinner import Spinner

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
videos_app = typer.Typer(help="Video generation and management", no_args_is_help=True)

TERMINAL_STATUSES = {"completed", "error", "cancelled"}




def _poll_batch(api_client, batch_id: str, json_out: bool):
    """Poll batch status until terminal state, showing progress."""
    try:
        with Live(
            Spinner("dots", text="[llx.dim]Starting video generation...[/llx.dim]"),
            console=console,
            transient=True,
        ) as live:
            while True:
                data = api_client.get(f"/api/batch-video/status/{batch_id}")
                result = data.get("data", data)
                status = result.get("status", "unknown")
                completed = result.get("completed_videos", 0)
                total = result.get("total_videos", 0)
                failed = result.get("failed_videos", 0)

                if status in TERMINAL_STATUSES:
                    break

                live.update(Spinner(
                    "dots",
                    text=f"[llx.accent]Generating: {completed}/{total} done, {failed} failed[/llx.accent]",
                ))
                time.sleep(3)
    except KeyboardInterrupt:
        console.print(f"\n[llx.dim]Detached. Generation continues on server.[/llx.dim]")
        console.print(f"  Track with: [llx.accent]guaardvark videos status {batch_id}[/llx.accent]")
        raise typer.Exit(0)

    # Print final results
    if json_out or output.is_pipe():
        output.print_json(result)
        return

    output.print_kv({
        "Batch ID": batch_id,
        "Status": status,
        "Completed": f"{completed}/{total}",
        "Failed": str(failed),
    }, title="Generation Complete")

    results = result.get("results", [])
    if results:
        rows = [{
            "item": r.get("item_id", "")[:12],
            "success": "yes" if r.get("success") else "no",
            "path": r.get("video_path") or r.get("error", ""),
        } for r in results]
        output.print_table(rows, columns=["item", "success", "path"], title="Results")


def _build_gen_params(
    model: Optional[str],
    duration: Optional[int],
    fps: Optional[int],
    width: Optional[int],
    height: Optional[int],
    steps: Optional[int],
    guidance: float,
    motion: float,
    seed: Optional[int],
    frames_only: bool,
) -> dict:
    """Build generation parameters. Omitted size/fps/steps let the API use the model native defaults."""
    params = {
        "guidance_scale": guidance,
        "motion_strength": motion,
        "generate_frames_only": frames_only,
    }
    if duration is not None:
        params["duration_frames"] = duration
    if fps is not None:
        params["fps"] = fps
    if width is not None:
        params["width"] = width
    if height is not None:
        params["height"] = height
    if steps is not None:
        params["num_inference_steps"] = steps
    if model:
        params["model"] = model
    if seed is not None:
        params["seed"] = seed
    return params


@videos_app.command("list")
def videos_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List generated video batches."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get("/api/batch-video/list")
        batches = data.get("data", data)
        if isinstance(batches, dict):
            batches = batches.get("batches", [])
        if not isinstance(batches, list):
            batches = []

        if json_out or output.is_pipe():
            output.print_json(batches)
            return

        rows = [{
            "id": b.get("batch_id", b.get("id", "")),
            "name": b.get("display_name", b.get("batch_id", "")),
            "videos": b.get("video_count", b.get("total_videos", 0)),
            "status": b.get("status", ""),
        } for b in batches]
        output.print_table(rows, columns=["id", "name", "videos", "status"], title=f"Video Batches ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("generate")
def videos_generate(
    prompt: str = typer.Argument(..., help="Video description prompt"),
    count: int = typer.Option(1, "--count", "-n", help="Number of videos to generate"),
    model: str = typer.Option(None, "--model", "-m", help="Registry model id (default: active video model)"),
    duration: int = typer.Option(None, "--duration", "-d", help="Duration in frames (default: model native)"),
    fps: int = typer.Option(None, "--fps", help="Output frame rate (default: model native)"),
    width: int = typer.Option(None, "--width", "-W", help="Video width (default: model native)"),
    height: int = typer.Option(None, "--height", "-H", help="Video height (default: model native)"),
    steps: int = typer.Option(None, "--steps", help="Inference steps (default: model floor)"),
    guidance: float = typer.Option(7.5, "--guidance", help="Guidance scale"),
    motion: float = typer.Option(1.0, "--motion", help="Motion strength"),
    seed: int = typer.Option(None, "--seed", help="Random seed for reproducibility"),
    frames_only: bool = typer.Option(False, "--frames-only", help="Generate frames without combining"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for completion with progress"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate videos from a text prompt."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {"prompts": [prompt] * count}
        body.update(_build_gen_params(model, duration, fps, width, height, steps, guidance, motion, seed, frames_only))

        data = api_client.post("/api/batch-video/generate/text", json=body)
        result = data.get("data", data)
        batch_id = result.get("batch_id", "")

        if wait and batch_id:
            _poll_batch(api_client, batch_id, json_out)
            return

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Video generation started ({count} video{'s' if count > 1 else ''})")
            if batch_id:
                console.print(f"  Track with: [llx.accent]guaardvark videos status {batch_id}[/llx.accent]")
                console.print(f"  Or wait:    [llx.accent]guaardvark videos generate \"{prompt[:40]}\" --wait[/llx.accent]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("from-image")
def videos_from_image(
    image_path: str = typer.Argument(..., help="Path to source image"),
    count: int = typer.Option(1, "--count", "-n", help="Number of videos to generate"),
    model: str = typer.Option(None, "--model", "-m", help="Registry model id (default: active I2V model)"),
    duration: int = typer.Option(None, "--duration", "-d", help="Duration in frames (default: model native)"),
    fps: int = typer.Option(None, "--fps", help="Output frame rate (default: model native)"),
    width: int = typer.Option(None, "--width", "-W", help="Video width (default: model native)"),
    height: int = typer.Option(None, "--height", "-H", help="Video height (default: model native)"),
    steps: int = typer.Option(None, "--steps", help="Inference steps (default: model floor)"),
    guidance: float = typer.Option(7.5, "--guidance", help="Guidance scale"),
    motion: float = typer.Option(1.0, "--motion", help="Motion strength"),
    seed: int = typer.Option(None, "--seed", help="Random seed for reproducibility"),
    frames_only: bool = typer.Option(False, "--frames-only", help="Generate frames without combining"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for completion with progress"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate videos from a source image."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    # Validate image exists
    img = Path(image_path)
    if not img.exists():
        output.print_error(f"Image not found: {image_path}")
        raise typer.Exit(1)

    try:
        api_client = get_client(server)
        body = {"image_paths": [str(img.resolve())] * count}
        body.update(_build_gen_params(model, duration, fps, width, height, steps, guidance, motion, seed, frames_only))

        data = api_client.post("/api/batch-video/generate/image", json=body)
        result = data.get("data", data)
        batch_id = result.get("batch_id", "")

        if wait and batch_id:
            _poll_batch(api_client, batch_id, json_out)
            return

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Image-to-video generation started ({count} video{'s' if count > 1 else ''})")
            if batch_id:
                console.print(f"  Track with: [llx.accent]guaardvark videos status {batch_id}[/llx.accent]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("status")
def videos_status(
    batch_id: str = typer.Argument(..., help="Batch ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check generation status of a video batch."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get(f"/api/batch-video/status/{batch_id}")
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
            return

        output.print_kv({
            "Batch ID": batch_id,
            "Status": result.get("status", ""),
            "Videos": f"{result.get('completed_videos', 0)}/{result.get('total_videos', 0)}",
            "Failed": str(result.get("failed_videos", 0)),
            "Started": result.get("start_time") or "-",
            "Ended": result.get("end_time") or "-",
        }, title="Batch Status")

        results = result.get("results", [])
        if results:
            rows = [{
                "item": r.get("item_id", "")[:12],
                "success": "yes" if r.get("success") else "no",
                "path": r.get("video_path") or r.get("error", ""),
            } for r in results]
            output.print_table(rows, columns=["item", "success", "path"], title="Results")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("models")
def videos_models(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List video models from the registry (installed status + active default)."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get("/api/batch-video/models")
        payload = data.get("data", data)
        models = payload.get("models", payload) if isinstance(payload, dict) else payload
        if not isinstance(models, list):
            models = []
        generation = [m for m in models if m.get("capabilities")]
        if json_out or output.is_pipe():
            output.print_json(payload if isinstance(payload, dict) else generation)
            return
        rows = [{
            "id": m.get("id", ""),
            "name": m.get("name", ""),
            "ready": "yes" if m.get("is_ready") else "no",
            "active": "yes" if m.get("active") else "",
            "vram": m.get("vram_mb") or "",
        } for m in generation]
        output.print_table(rows, columns=["id", "name", "ready", "active", "vram"], title="Video Models")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("delete")
def videos_delete(
    batch_id: str = typer.Argument(..., help="Batch ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a video batch."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete video batch {batch_id}?", abort=True)
    try:
        api_client = get_client(server)
        api_client.delete(f"/api/batch-video/delete/{batch_id}")
        output.print_success(f"Deleted batch {batch_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("download")
def videos_download(
    batch_id: str = typer.Argument(..., help="Batch ID to download"),
    video_name: str = typer.Argument(None, help="Specific video filename (omit for full batch ZIP)"),
    output_dir: str = typer.Option(".", "--output", "-o", help="Output directory"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Download videos from a batch."""
    server = server or get_global_server()
    try:
        api_client = get_client(server)
        out_path = Path(output_dir)

        if video_name:
            dest = out_path / video_name
            api_client.download(f"/api/batch-video/video/{batch_id}/{video_name}", dest)
            output.print_success(f"Downloaded to {dest}")
        else:
            dest = out_path / f"{batch_id}.zip"
            api_client.download(f"/api/batch-video/download/{batch_id}", dest)
            output.print_success(f"Downloaded batch to {dest}")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@videos_app.command("combine")
def videos_combine(
    batch_id: str = typer.Argument(..., help="Batch ID with generated frames"),
    fps: int = typer.Option(7, "--fps", help="Output frame rate"),
    item_id: str = typer.Option(None, "--item", help="Specific item ID (omit for all)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Combine generated frames into a video."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {"fps": fps}
        if item_id:
            body["item_id"] = item_id

        data = api_client.post(f"/api/batch-video/combine-frames/{batch_id}", json=body)
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            video_path = result.get("video_path", "")
            output.print_success(f"Frames combined into video: {video_path}")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
