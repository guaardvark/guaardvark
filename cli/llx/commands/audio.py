"""Audio Foundry — TTS, music, SFX, voice list."""

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.media_preview import extract_media_path, play_audio
from llx.theme import make_console

console = make_console()
audio_app = typer.Typer(help="Audio Foundry (TTS, music, SFX)", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


@audio_app.command("voices")
def audio_voices(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List available TTS voices."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/audio-foundry/voices"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        voices = data.get("voices", data) if isinstance(data, dict) else data
        if isinstance(voices, list):
            rows = []
            for v in voices:
                if isinstance(v, dict):
                    rows.append({"id": v.get("id", v.get("name", "")), "name": v.get("name", ""), "engine": v.get("engine", "")})
                else:
                    rows.append({"id": str(v), "name": str(v), "engine": ""})
            output.print_table(rows, title="Voices")
        else:
            output.print_kv(data if isinstance(data, dict) else {"voices": voices})
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@audio_app.command("tts")
def audio_tts(
    text: str = typer.Argument(..., help="Text to speak"),
    no_play: bool = typer.Option(False, "--no-play"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate speech from text (Audio Foundry, falls back to /api/voice)."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    client = _client(server)
    try:
        try:
            data = client.post("/api/audio-foundry/generate/voice", json={"text": text})
        except LlxError:
            data = client.post("/api/voice/text-to-speech", json={"text": text})
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
            return
        path = extract_media_path(result if isinstance(result, dict) else {}, client.server_url)
        filename = (result.get("filename") if isinstance(result, dict) else None) or path or "audio"
        output.print_success(f"Audio generated: {filename}")
        if path and not str(path).startswith("http"):
            player = play_audio(path, no_play=no_play)
            if player:
                console.print(f"[llx.dim]Playing with {player}[/llx.dim]")
        elif path:
            console.print(f"[llx.dim]{path}[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@audio_app.command("music")
def audio_music(
    prompt: str = typer.Argument(..., help="Music description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate music from a prompt."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post("/api/audio-foundry/generate/music", json={"prompt": prompt})
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
            return
        job_id = result.get("job_id") if isinstance(result, dict) else None
        output.print_success("Music generation started")
        if job_id:
            console.print(f"[llx.dim]Job: {job_id}  →  guaardvark jobs watch {job_id}[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@audio_app.command("sfx")
def audio_sfx(
    prompt: str = typer.Argument(..., help="SFX / ambience description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate a sound effect."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post("/api/audio-foundry/generate/fx", json={"prompt": prompt})
        result = _unwrap(data)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": result})
            return
        output.print_success("SFX generation started")
        if isinstance(result, dict) and result.get("job_id"):
            console.print(f"[llx.dim]Job: {result['job_id']}[/llx.dim]")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@audio_app.command("play")
def audio_play(
    path: str = typer.Argument(..., help="Local wav/mp3 path"),
):
    """Play a local audio file through the first available player."""
    player = play_audio(path)
    if player:
        output.print_success(f"Playing with {player}")
    else:
        output.print_error("No audio player found (ffplay, paplay, afplay, aplay, mpv)")
        raise typer.Exit(1)
