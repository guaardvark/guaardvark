"""Music and speech through Audio Foundry, for MCP clients.

Both tools call the backend's Audio Foundry routes, the same ones the Studio's
Audio page uses, so a song or a voice line made from an agent lands in the
library like any other. Voice cloning is not offered here: it needs a consent
record for the reference clip, which only the Audio Studio's consent step
writes, after the person confirms they have the right to clone that voice.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.tool_jobs import MAX_WAIT_S as _SPEECH_MAX_WAIT_S

logger = logging.getLogger(__name__)

STUDIO_URL = "/audio"
MAX_SPEECH_CHARS = 3000

# generate_speech runs as an Audio Foundry job and waits for its file at most
# tool_jobs.wait_seconds() (60 s, or half the MCP per-call timeout when that
# is shorter), then answers with the job id to poll, so the answer reaches an
# MCP client before its call times out. That bound comes from the timeout,
# not from a timed render. What makes the job necessary is the plugin's own
# estimate (config.yaml async.chars_per_sec_est: 30): about 100 s of synthesis
# for a 3000-character read, before Chatterbox's 3.3 GB load.
SPEECH_POLL_S = 2.0
# The submit itself answers as soon as the job is queued. An Audio Foundry
# started before the `queue` field existed still renders short text inline
# on this request, which must fit: 110 s stays under the MCP server's
# default 120 s per-call timeout (backend/mcp/config.py).
SPEECH_SUBMIT_TIMEOUT_S = 110

# The older wording; the tools now give plugin_bridge.service_down_message.
AUDIO_FOUNDRY_NOT_RUNNING = (
    "Audio Foundry is not running. Start it from the Studio's Plugins page "
    "(or POST /api/plugins/audio_foundry/start), then try again."
)


def _kokoro_voice_ids() -> list[str]:
    from backend.services.audio_foundry_models import kokoro_voice_ids
    return kokoro_voice_ids()


def plugin_stopped(error) -> bool:
    """True when the backend answered that the Audio Foundry service itself
    is not running (its proxy's 503), as opposed to a 503 the running plugin
    sent (a model that cannot run on this machine), which carries FastAPI's
    ``detail`` and is reported in its own words."""
    if getattr(error, "kind", None) != "plugin_offline":
        return False
    body = error.body if isinstance(getattr(error, "body", None), dict) else {}
    return body.get("plugin_running") is False or "detail" not in body


def _backend_error_text(error) -> str:
    """What a failed backend call says, as a job_types.RenderFailure when the
    failure has a kind (a stopped Audio Foundry is SERVICE_DOWN; a refusal
    whose body names its ``error_kind`` keeps it)."""
    from backend.services.job_types import RenderErrorKind, RenderFailure

    body = error.body if isinstance(getattr(error, "body", None), dict) else {}
    if plugin_stopped(error):
        # The backend's own sentence (plugin_bridge.service_down_message): it
        # knows whether automatic start is on there. Older backends lack it.
        from backend.services.plugin_bridge import service_down_message
        return RenderFailure(RenderErrorKind.SERVICE_DOWN, str(
            body.get("message") or service_down_message("audio_foundry", context="audio")))
    if error.kind in ("plugin_offline", "auth", "http") and body.get("detail"):
        # The plugin's own words; backend_http's hints are about the backend.
        text = str(body["detail"])
    else:
        text = str(error)
    try:
        kind = RenderErrorKind(body["error_kind"]) if body.get("error_kind") else None
    except ValueError:
        kind = None
    return RenderFailure(kind, text) if kind else text


def _refused(err) -> ToolResult:
    """A failed call, with ``metadata.error_kind`` when the failure has one."""
    from backend.services.job_types import failure_kind
    kind = failure_kind(err)
    return ToolResult(success=False, error=str(err),
                      metadata={"error_kind": kind.value} if kind else {})


def _call(method: str, path: str, payload: Optional[dict] = None,
          read_timeout: float = 30) -> tuple[Optional[dict], Optional[str]]:
    from backend.utils.backend_http import BackendError, request_json
    try:
        reply = request_json(method, path, payload=payload, read_timeout=read_timeout)
    except BackendError as e:
        return None, _backend_error_text(e)
    body = reply.body if isinstance(reply.body, dict) else {}
    return body, None


def _post(path: str, payload: dict, read_timeout: float) -> tuple[Optional[dict], Optional[str]]:
    return _call("POST", path, payload, read_timeout)


def _speech_wait_s() -> float:
    from backend.services.tool_jobs import wait_seconds
    return wait_seconds()


def _file_entry(result: dict) -> dict:
    """What a caller needs to find a finished audio file: its name, library
    document and a download link on the backend."""
    from backend.utils.backend_http import backend_base_url

    entry: dict[str, Any] = {"file": Path(str(result.get("path") or "audio")).name}
    if result.get("duration_s") is not None:
        entry["duration_s"] = round(float(result["duration_s"]), 1)
    doc_id = result.get("document_id")
    if doc_id:
        entry["document_id"] = doc_id
        entry["url"] = f"/api/files/document/{doc_id}/download"
        entry["download"] = f"{backend_base_url()}{entry['url']}"
    elif result.get("registration_error"):
        entry["note"] = "Saved, but not yet in the library: " + str(result["registration_error"])
    return entry


MUSIC_MODELS = ("ace-step", "ace-step-1.5")


class GenerateMusicTool(BaseTool):
    """Queue a song (ACE-Step v1, or ACE-Step 1.5 when installed) in Audio Foundry."""

    name = "generate_music"
    read_only = False
    destructive = False
    description = (
        "Make a song on this machine with ACE-Step in Guaardvark's Audio Foundry: a style prompt "
        "(genre, instruments, mood, tempo, voice) and optional lyrics, up to 240 seconds, sung or "
        "instrumental. Returns at once with a job_id; a 30-second song usually takes one to two "
        "minutes. Poll get_generation_status with that job_id; when it reports complete it gives the "
        "file name, its library document id and a download link. The song is also listed on the "
        "Studio's Audio page. Needs the Audio Foundry plugin running (it answers that it is not "
        "running otherwise). model 'ace-step-1.5' uses ACE-Step 1.5 instead of v1 when it has "
        "been installed in Audio Studio → Manage models; it is refused otherwise, never "
        "downloaded. ACE-Step 1.5 expands a plain-language style into a caption, lyrics, "
        "tempo, key and meter with the planner loaded beside it, and keeps a bpm or key "
        "written in the style. For a spoken line use generate_speech; for a music video "
        "set to a song, generate_music_video."
    )
    parameters = {
        "style": ToolParameter(
            name="style", type="string", required=True,
            description="The sound in plain tags, e.g. 'upbeat synthwave, analog bass, female vocals, 110 bpm'. "
                        "Concrete genre, instrument and tempo words work better than 'professional' or 'epic'.",
        ),
        "lyrics": ToolParameter(
            name="lyrics", type="string", required=False,
            description="Words to sing. Section tags like [verse] and [chorus] on their own lines help. "
                        "Omit for an instrumental, or set instrumental=true.",
        ),
        "seconds": ToolParameter(
            name="seconds", type="float", required=False, default=30.0, minimum=5, maximum=240,
            description="Length in seconds, 5-240 (default 30).",
        ),
        "instrumental": ToolParameter(
            name="instrumental", type="bool", required=False, default=False,
            description="True for no vocals; lyrics are then ignored.",
        ),
        "seed": ToolParameter(
            name="seed", type="int", required=False,
            description="Repeat a take exactly with the same seed and inputs. Omit for a new one.",
        ),
        "model": ToolParameter(
            name="model", type="string", required=False, default="ace-step",
            enum=list(MUSIC_MODELS),
            description="'ace-step' (default): ACE-Step v1, tag-style prompts. 'ace-step-1.5': ACE-Step 1.5, "
                        "available once installed in Audio Studio → Manage models. 1.5 expands a sentence "
                        "into the caption it renders and keeps a stated bpm or key.",
        ),
    }

    def execute(self, style: str = "", lyrics: str = None, seconds: float = None,
                instrumental: bool = None, seed: int = None, model: str = None,
                **kwargs) -> ToolResult:
        style = (style or "").strip()
        if not style:
            return ToolResult(success=False, error="style is required: describe the sound, e.g. 'lo-fi hip hop, soft piano, 80 bpm'")
        try:
            seconds = float(seconds if seconds is not None else 30.0)
        except (TypeError, ValueError):
            return ToolResult(success=False, error="seconds must be a number from 5 to 240")
        if not 5 <= seconds <= 240:
            return ToolResult(success=False, error="seconds must be from 5 to 240")
        model = (model or MUSIC_MODELS[0]).strip().lower()
        if model not in MUSIC_MODELS:
            return ToolResult(success=False, error=f"model must be one of {', '.join(MUSIC_MODELS)}")

        # No lyrics means an instrumental, as the lyrics parameter promises. Sent as
        # instrumental_only so ACE-Step gets its "[instrumental]" marker; empty lyrics
        # alone let it sing made-up words.
        lyrics = (lyrics or "").strip()
        instrumental = bool(instrumental) or not lyrics
        payload: dict[str, Any] = {"style_prompt": style, "duration_s": seconds,
                                   "instrumental_only": instrumental, "async": True}
        if not instrumental:
            payload["lyrics"] = lyrics
        if seed is not None:
            payload["seed"] = int(seed)
        if model != MUSIC_MODELS[0]:
            payload["model"] = model

        body, err = _post("/api/audio-foundry/generate/music", payload, read_timeout=60)
        if err:
            return _refused(err)
        job_id = body.get("job_id")
        if job_id:
            return ToolResult(
                success=True,
                output={
                    "job_id": job_id,
                    "status": body.get("status", "queued"),
                    "estimate_s": body.get("estimate_s"),
                    "studio_url": STUDIO_URL,
                    "next": "Poll get_generation_status with this job_id; the song is ready when it reports complete.",
                },
                metadata={"style": style, "seconds": seconds, "model": model},
            )
        if body.get("path"):
            # Short requests can finish inline instead of queueing.
            return ToolResult(success=True, output={"status": "complete", **_file_entry(body)})
        return ToolResult(success=False, error=body.get("error") or "Audio Foundry did not start the song")


class GenerateSpeechTool(BaseTool):
    """Speak a line of text with a stock voice (Kokoro or Chatterbox)."""

    name = "generate_speech"
    read_only = False
    destructive = False
    description = (
        "Turn text into speech on this machine with Guaardvark's Audio Foundry. Unlike the other "
        "generate tools, which over MCP answer at once with a job or batch id to poll, this waits "
        "for the file: up to "
        f"{_SPEECH_MAX_WAIT_S:.0f} s, or half the MCP per-call timeout when that is shorter "
        "(usually it takes seconds), and returns its name, library document id, length and a "
        "download link. A longer read, such as a script near the 3000-character limit or a "
        "cold start, answers with a job_id instead: poll get_generation_status with it, and do not "
        "call again, since the file lands in the library either way. Up to 3000 characters per "
        "call; split longer scripts. Voices: naming a voice (a Kokoro id "
        "such as 'af_heart' or 'bm_george') always speaks with Kokoro; with no voice, engine 'auto' "
        "uses Chatterbox's single stock voice when Chatterbox is installed and Kokoro's default "
        "voice otherwise. Chatterbox has no voice ids, so engine 'chatterbox' with a voice is "
        "refused. A model or voice pack that is not installed is refused with a pointer to Audio "
        "Studio → Manage models; nothing is downloaded. Needs the Audio Foundry plugin running. "
        "Cloning a real person's voice (a Chatterbox reference clip) is not available here; it "
        "needs the person to confirm in Audio Studio that they have the right to clone that "
        "voice. For a song use generate_music."
    )
    parameters = {
        "text": ToolParameter(
            name="text", type="string", required=True,
            description="What to say, as it should be spoken (spell out numbers and names the way they sound).",
        ),
        "voice": ToolParameter(
            name="voice", type="string", required=False,
            description="A Kokoro voice id: accent and gender prefix plus a name, e.g. 'af_heart' "
                        "(American female, the default), 'am_michael', 'bf_emma', 'bm_george', "
                        "'ef_dora' (Spanish). Naming one selects Kokoro. An unknown id is refused "
                        "with the full list. Omit for the engine's default voice.",
        ),
        "engine": ToolParameter(
            name="engine", type="string", required=False, default="auto",
            enum=["auto", "kokoro", "chatterbox"],
            description="'auto' (default): Kokoro when voice is set; otherwise Chatterbox when it is "
                        "installed, falling back to Kokoro. 'kokoro': the named voice or af_heart. "
                        "'chatterbox': its one stock voice; do not combine with voice.",
        ),
    }

    def execute(self, text: str = "", voice: str = None, engine: str = None, **kwargs) -> ToolResult:
        text = (text or "").strip()
        if not text:
            return ToolResult(success=False, error="text is required")
        if len(text) > MAX_SPEECH_CHARS:
            return ToolResult(success=False, error=(
                f"text is {len(text)} characters; the limit is {MAX_SPEECH_CHARS} per call. "
                "Split it at sentence ends and call once per part."))
        engine = (engine or "auto").strip().lower()
        if engine not in ("auto", "kokoro", "chatterbox"):
            return ToolResult(success=False, error="engine must be auto, kokoro or chatterbox")
        voice = (voice or "").strip()
        if voice:
            # Validated here as well as in the plugin: Kokoro reads a value
            # ending in .pt as a file path and a comma as a blend of voices.
            valid = _kokoro_voice_ids()
            if voice not in valid:
                return ToolResult(success=False, error=(
                    f"Unknown voice {voice!r}. Kokoro voices: {', '.join(valid)}. "
                    "Omit voice for the default."))
            if engine == "chatterbox":
                return ToolResult(success=False, error=(
                    f"Chatterbox has no built-in voices, so voice {voice!r} cannot be used with "
                    "engine 'chatterbox'. Use engine 'kokoro' (or 'auto') for that voice, or omit "
                    "voice to hear Chatterbox's stock voice."))
            # A named voice is Kokoro's (Chatterbox has none); Audio Foundry's
            # auto mode routes it there too, but naming the engine keeps the
            # request unambiguous.
            engine = "kokoro"
        # Always an Audio Foundry job (``queue``), so a long or cold read that
        # outlasts the wait still has an id to poll instead of an error while
        # the file is made anyway.
        payload: dict[str, Any] = {"text": text, "backend": engine, "async": True, "queue": True}
        if voice:
            payload["voice_id"] = voice

        started = time.monotonic()
        body, err = _post("/api/audio-foundry/generate/voice", payload, read_timeout=SPEECH_SUBMIT_TIMEOUT_S)
        if err:
            return _refused(err)
        if body.get("path"):
            return self._finished(body)
        job_id = body.get("job_id")
        if not job_id:
            return ToolResult(success=False, error=body.get("error") or body.get("detail") or "No audio came back")

        deadline = started + _speech_wait_s()
        job: dict = {"status": body.get("status") or "queued"}
        while True:
            now = time.monotonic()
            if now + SPEECH_POLL_S > deadline:
                break
            time.sleep(SPEECH_POLL_S)
            polled, poll_err = _call("GET", f"/api/audio-foundry/jobs/{job_id}", read_timeout=10)
            if poll_err:
                # The job was accepted; the client can poll once the reader recovers.
                logger.info("generate_speech: reading job %s failed (%s)", job_id, poll_err)
                break
            job = polled
            status = job.get("status")
            if status == "done":
                return self._finished(job.get("result") or {}, job_id=job_id)
            if status in ("error", "cancelled"):
                kind = job.get("error_kind") or ("cancelled" if status == "cancelled" else None)
                return ToolResult(success=False, error=(
                    f"Speech job {job_id} {'failed' if status == 'error' else 'was cancelled'}: "
                    f"{job.get('error') or 'no reason given'}"),
                    metadata={"job_id": job_id, **({"error_kind": kind} if kind else {})})

        progress = job.get("progress") or {}
        output: dict[str, Any] = {
            "status": job.get("status") or "queued",
            "job_id": job_id,
            "estimate_s": body.get("estimate_s"),
            "studio_url": STUDIO_URL,
            "next": ("Poll get_generation_status with this job_id (wait_seconds lets it wait); "
                     "the file is saved to the library when it finishes. Do not call "
                     "generate_speech again for the same text."),
        }
        if progress.get("total"):
            output["progress"] = f"{progress.get('current') or 0}/{progress['total']} parts"
        return ToolResult(success=True, output=output, metadata={"job_id": job_id})

    @staticmethod
    def _finished(result: dict, job_id: Optional[str] = None) -> ToolResult:
        if not result.get("path"):
            return ToolResult(success=False, error="Audio Foundry finished without a file",
                              metadata={"error_kind": "output_missing"})
        meta = result.get("meta") or {}
        output = {"status": "complete", **_file_entry(result),
                  "engine": meta.get("backend") or meta.get("engine"),
                  "voice": meta.get("voice") or meta.get("voice_id")}
        if job_id:
            output["job_id"] = job_id
        return ToolResult(success=True, output=output)
