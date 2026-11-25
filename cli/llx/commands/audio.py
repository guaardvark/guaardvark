"""Audio Foundry — TTS, music, SFX, voice list."""

import typer

from llx import output
from llx.client import LlxError, get_client
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
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
    except LlxError as e:
        raise output.fail(e)


@audio_app.command("tts")
def audio_tts(
    text: str = typer.Argument(..., help="Text to speak"),
    no_play: bool = typer.Option(False, "--no-play", help="Make the file without playing it"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
        output.print_success(f"Audio generated: {str(filename).rsplit('/', 1)[-1]}")
        if path and not str(path).startswith("http"):
            player = play_audio(path, no_play=no_play)
            if player:
                console.print(f"[llx.dim]Playing with {player}[/llx.dim]")
        elif path:
            console.print(f"[llx.dim]{path}[/llx.dim]")
    except LlxError as e:
        raise output.fail(e)


@audio_app.command("music")
def audio_music(
    style: str = typer.Argument(..., help="The sound you want: genre, instruments, mood, tempo"),
    lyrics: str = typer.Option(None, "--lyrics", "-l", help="Words to sing, or a path to a text file of them"),
    seconds: float = typer.Option(30.0, "--seconds", "-d", help="Length in seconds (up to 240)"),
    instrumental: bool = typer.Option(False, "--instrumental", help="No vocals"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for the song, then play it"),
    no_play: bool = typer.Option(False, "--no-play", help="With --wait: do not play it"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Generate a song (ACE-Step in Audio Foundry)."""
    import time
    from pathlib import Path

    from llx.job_status import TERMINAL, read_job

    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    if lyrics and len(lyrics) < 4096 and Path(lyrics).expanduser().is_file():
        lyrics = Path(lyrics).expanduser().read_text(encoding="utf-8")
    body = {"style_prompt": style, "duration_s": seconds, "instrumental_only": instrumental, "async": True}
    if lyrics and not instrumental:
        body["lyrics"] = lyrics
    try:
        client = _client(server)
        result = _unwrap(client.post("/api/audio-foundry/generate/music", json=body))
        job_id = result.get("job_id") if isinstance(result, dict) else None
        if not wait or not job_id:
            if json_out or output.is_pipe():
                output.print_json({"status": "success", "data": result})
                return
            if job_id:
                output.print_success("Music generation started")
                console.print(f"[llx.dim]Job: {job_id}  →  guaardvark jobs watch {job_id}[/llx.dim]")
            else:
                name = Path(str((result or {}).get("path", "song"))).name
                output.print_success(f"Song ready: {name}")
            return

        with console.status("[llx.brand]Writing the song…[/llx.brand]", spinner="dots"):
            info = read_job(client, job_id)
            while info["status"] not in TERMINAL:
                time.sleep(2)
                info = read_job(client, job_id)
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {k: v for k, v in info.items() if k != "raw"}})
            return
        if info["status"] != "completed" or not info.get("files"):
            output.print_error(str(info.get("error") or f"Song {info['status']}"), code="JOB_FAILED")
            raise typer.Exit(1)
        path = info["files"][0]
        output.print_success(f"Song ready: {Path(path).name}")
        player = play_audio(path, no_play=no_play)
        if player:
            console.print(f"[llx.dim]Playing with {player}[/llx.dim]")
    except LlxError as e:
        raise output.fail(e)


@audio_app.command("sfx")
def audio_sfx(
    prompt: str = typer.Argument(..., help="SFX / ambience description"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
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
    except LlxError as e:
        raise output.fail(e)


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
