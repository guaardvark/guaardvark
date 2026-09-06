"""Music-video pipeline — create a plan, inspect, cancel. Does not approve a render."""

from pathlib import Path

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
music_video_app = typer.Typer(help="Music video plans (approve/render stays in Studio)", no_args_is_help=True)

_AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


def _song_document_id(api_client, song: str) -> int:
    ref = (song or "").strip()
    if not ref:
        raise LlxError("song is required (document id or path to an audio file)")
    if ref.isdigit():
        return int(ref)
    path = Path(ref).expanduser()
    if not path.is_file():
        raise LlxError(f"song file not found: {ref}")
    if path.suffix.lower() not in _AUDIO_EXT:
        raise LlxError(f"song must be audio ({', '.join(sorted(_AUDIO_EXT))})")
    data = api_client.upload(
        "/api/files/upload",
        path,
        folder_path="",
        tags="music-video-song",
        auto_index="false",
    )
    result = _unwrap(data)
    doc_id = result.get("id")
    if not doc_id:
        raise LlxError("upload did not return a document id")
    return int(doc_id)


@music_video_app.command("list")
def music_video_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List music-video projects."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/music-video"))
        rows = data.get("music_videos", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            rows = []
        if json_out or output.is_pipe():
            output.print_json(rows)
            return
        table = [{
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "stage": r.get("current_stage", ""),
            "status": r.get("status", ""),
            "clips": f"{r.get('clips_done', 0)}/{r.get('clip_count', 0)}",
        } for r in rows]
        output.print_table(table, columns=["id", "name", "stage", "status", "clips"],
                           title=f"Music Videos ({len(table)})")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@music_video_app.command("create")
def music_video_create(
    song: str = typer.Option(..., "--song", help="Document id or path to an audio file"),
    style: str = typer.Option(..., "--style", help="Visual style for the Director"),
    name: str = typer.Option(None, "--name", "-n", help="Project name (default: song stem)"),
    model: str = typer.Option(None, "--model", "-m", help="I2V model id (default: active video model)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a music-video project and start analysis. Does not render clips."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = _client(server)
        song_id = _song_document_id(api_client, song)
        title = (name or "").strip() or Path(song).expanduser().stem or "Music video"
        body = {
            "name": title,
            "song_document_id": song_id,
            "style_prompt": style.strip(),
        }
        if (model or "").strip():
            body["settings"] = {"i2v_model": model.strip()}
        data = api_client.post("/api/music-video", json=body)
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json(result)
            return
        mv_id = result.get("id", "")
        stage = result.get("current_stage", "")
        output.print_success(f"Music video '{result.get('name', title)}' created (id {mv_id}, stage: {stage})")
        console.print("[llx.dim]Analysis is running. Approve the cut plan in Studio before any clip renders.[/llx.dim]")
        console.print("[llx.dim]Open: /music-video    Status: guaardvark music-video status " + str(mv_id) + "[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@music_video_app.command("status")
def music_video_status(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show one music-video project."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get(f"/api/music-video/{mv_id}"))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        output.print_kv({
            "ID": data.get("id", mv_id),
            "Name": data.get("name", ""),
            "Stage": data.get("current_stage", ""),
            "Status": data.get("status", ""),
            "I2V model": data.get("i2v_model", ""),
            "Cuts": data.get("cut_count", 0),
            "Clips": f"{data.get('clips_done', 0)}/{data.get('clip_count', 0)}",
        }, title="Music Video")
        if data.get("current_stage") == "awaiting_approval":
            console.print("[llx.dim]Approve the cut plan in Studio to start clip renders.[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@music_video_app.command("cancel")
def music_video_cancel(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Cancel analysis or generation. Does not delete the row."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).post(f"/api/music-video/{mv_id}/cancel"))
        if json_out or output.is_pipe():
            output.print_json(data)
            return
        output.print_success(f"Cancelled music video {mv_id} (stage: {data.get('current_stage', 'cancelled')})")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@music_video_app.command("delete")
def music_video_delete(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a music-video project row. Song and output files stay on disk."""
    if not force:
        typer.confirm(f"Delete music video {mv_id}?", abort=True)
    try:
        _client(server).delete(f"/api/music-video/{mv_id}")
        output.print_success(f"Deleted music video {mv_id}")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
