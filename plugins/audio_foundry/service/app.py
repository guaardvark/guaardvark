"""FastAPI entrypoint for Audio Foundry.

Single worker, sync endpoints (uvicorn runs them in its default thread pool —
same pattern as vision_pipeline). Skeleton phase: the three /generate/* routes
return 501 because no backends are registered yet. /health and /status work.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict

# Names only — this module keeps torch inside its methods, so importing it
# here does not pull the ML stack into service startup.
from backends.voice_gen_chatterbox import EMOTION_PRESETS
from backends.kokoro_voices import UnknownVoice, check_voice_id
from backends.voice_consent import ConsentRequired, require_consent
from service.bootstrap import bootstrap
from service.config_loader import load_config, resolve_backend_url
from service.dispatcher import BackendUnavailable, Dispatcher, Intent, NotWired
from service.jobs import JobManager
from service.orchestrator_client import OrchestratorClient
from service.registration import register_output, spoken_display_name

logger = logging.getLogger(__name__)

_EMOTION_PATTERN = "^(" + "|".join(sorted(EMOTION_PRESETS)) + ")$"

# ---------- request models ---------------------------------------------------
# Kept lenient at skeleton phase. Each backend tightens its own fields when wired.

class FxRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    prompt: str = Field(..., min_length=1)
    duration_s: float = Field(10.0, gt=0, le=47.0)
    output_format: str = Field("wav", pattern="^(wav|mp3)$")
    seed: Optional[int] = None
    async_mode: bool = Field(False, alias="async")


class VoiceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    text: str = Field(..., min_length=1)
    backend: str = Field("auto", pattern="^(auto|chatterbox|kokoro)$")
    reference_clip_path: Optional[str] = None
    voice_id: Optional[str] = None
    # Chatterbox only: a named preset over the sampling controls below.
    # Validated here rather than in the backend because the dispatcher's 'auto'
    # mode falls back to Kokoro on any Chatterbox error — a typo would
    # otherwise return a completely different voice instead of an error.
    emotion: Optional[str] = Field(None, pattern=_EMOTION_PATTERN)
    # Chatterbox only; each overrides the preset when set.
    exaggeration: Optional[float] = Field(None, ge=0.0, le=2.0)
    cfg_weight: Optional[float] = Field(None, ge=0.0, le=1.0)
    temperature: Optional[float] = Field(None, gt=0.0, le=2.0)
    # Reproduces a take exactly. The backend has always honoured this; it was
    # missing here, so callers could not reach it.
    seed: Optional[int] = None
    output_format: str = Field("wav", pattern="^(wav|mp3)$")
    async_mode: bool = Field(False, alias="async")
    # With async: queue a job however short the text, so the caller always
    # gets a job id to poll (MCP's generate_speech, which must answer before
    # its client's call timeout even on a cold model load).
    queue: bool = False


class MusicRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # Omitted or "ace-step": ACE-Step v1. "ace-step-1.5": the optional ACE-Step 1.5
    # (backends/music_gen.py), refused until it is installed.
    model: Optional[str] = Field(None, pattern=r"^(ace-step|ace-step-1\.5)$")
    lyrics: Optional[str] = None
    style_prompt: str = Field(..., min_length=1)
    # Accepted for API compatibility but never applied: neither ACE-Step v1 nor 1.5
    # has negative conditioning. The result reports negative_prompt_applied: false.
    negative_prompt: Optional[str] = None
    duration_s: float = Field(60.0, gt=0, le=240.0)
    instrumental_only: bool = False
    output_format: str = Field("wav", pattern="^(wav|mp3)$")
    seed: Optional[int] = None
    async_mode: bool = Field(False, alias="async")
    # ACE-Step 1.5. A bpm, key, meter or language the person stated or edited.
    # Empty means the planner may choose. arrangement_accepted means the
    # caption is already the one to render, and must not be planned again.
    bpm: Optional[int] = Field(None, ge=30, le=300)
    keyscale: Optional[str] = Field(None, max_length=32)
    timesignature: Optional[str] = Field(None, max_length=8)
    vocal_language: Optional[str] = Field(None, max_length=16)
    arrangement_accepted: bool = False


# ---------- app setup --------------------------------------------------------

_GUARD_MODULE = "guaardvark_sidecar_guard"


def _load_guard():
    """backend/utils/sidecar_guard.py, loaded by path: this service runs in
    its own venv, outside the backend package."""
    loaded = sys.modules.get(_GUARD_MODULE)
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().parents[3] / "backend" / "utils" / "sidecar_guard.py"
    spec = importlib.util.spec_from_file_location(_GUARD_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[_GUARD_MODULE] = module
    return module


# No CORS middleware: browsers never call this service. The Studio goes
# through the backend's /api/audio-foundry proxy, and every other caller is a
# process on this machine (scripts/start.sh binds 127.0.0.1). A page whose
# name was re-pointed at 127.0.0.1 is still a browser on this machine, so the
# Host check refuses any request addressed to a name that is not this one's.
app = FastAPI(
    title="Audio Foundry",
    version="0.1.0",
    description="Audio generation plugin for Guaardvark (voiceover, SFX, music).",
)
app.add_middleware(_load_guard().HostCheckASGIMiddleware)

_config = load_config()

# GPU orchestrator client — talks to the main Guaardvark backend over HTTP
# so the dispatcher can request VRAM and trigger eviction of other models
# (Ollama, ComfyUI, ...) before loading an audio backend.
_gpu_cfg = _config.get("runtime", {}).get("gpu", {})
_reg_cfg = _config.get("runtime", {}).get("registration", {})
_orch_client = OrchestratorClient(
    backend_url=_reg_cfg.get("backend_url"),
    enabled=_gpu_cfg.get("orchestrator_enabled", True),
)

_dispatcher = Dispatcher(orchestrator=_orch_client)
_dispatcher.start_idle_unloader(int(_gpu_cfg.get("idle_unload_s", 0) or 0))
bootstrap(_dispatcher, _config)


# ---- async job plumbing -----------------------------------------------------
from pathlib import Path as _Path

_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent.parent
_out_dir = _PROJECT_ROOT / _config.get("runtime", {}).get("output", {}).get("dir", "data/outputs/audio")
_async_cfg = _config.get("runtime", {}).get("async", {}) or {}
# Back-compat: honor the old celery.async_threshold_s if async.threshold_s is absent.
_ASYNC_THRESHOLD_S = float(
    _async_cfg.get("threshold_s",
                   _config.get("runtime", {}).get("celery", {}).get("async_threshold_s", 5))
)
_CHARS_PER_SEC_EST = float(_async_cfg.get("chars_per_sec_est", 30))
_MUSIC_RT_FACTOR = float(_async_cfg.get("music_realtime_factor", 1.5))
_FX_RT_FACTOR = float(_async_cfg.get("fx_realtime_factor", 1.2))
_ASYNC_ENABLED = bool(_async_cfg.get("enabled", True))


def _finalize(result, intent: Intent | None = None, params: dict | None = None) -> dict:
    """Register the output and build the response dict. Shared by the inline
    path and the async worker so the shape is identical.

    Spoken clips are filed in their own subfolder under a name taken from their
    text, so chat replies and narration don't bury the music and effects."""
    reg_cfg = _config.get("runtime", {}).get("registration", {})
    doc = None
    registration_error = None
    if reg_cfg.get("enabled", True):
        backend_url = resolve_backend_url(reg_cfg.get("backend_url"))
        subfolder = filename = None
        if intent == Intent.VOICE:
            subfolder = _config.get("runtime", {}).get("output", {}).get("voice_subdir", "Voice")
            filename = spoken_display_name((params or {}).get("text"), Path(result.path).suffix)
        doc = register_output(
            result,
            backend_url=backend_url,
            folder=reg_cfg.get("folder", "Audio"),
            subfolder=subfolder,
            filename=filename,
        )
        if doc is None:
            registration_error = (
                f"Audio generated and saved to {result.path}, but registering it "
                f"with the Guaardvark backend at {backend_url} failed — it won't "
                f"appear in the media library until the backend is reachable "
                f"(check FLASK_PORT in .env matches the running backend)."
            )
    response = {
        "path": str(result.path),
        "duration_s": result.duration_s,
        "sample_rate": result.sample_rate,
        "meta": result.meta,
        "document_id": doc.get("id") if doc else None,
    }
    if registration_error:
        response["registration_error"] = registration_error
    return response


def _job_runner(intent_value: str, params: dict, progress_cb, cancel_event) -> dict:
    result = _dispatcher.generate(
        Intent(intent_value), progress_cb=progress_cb, cancel_event=cancel_event, **params,
    )
    return _finalize(result, Intent(intent_value), params)


_jobs = JobManager(
    runner=_job_runner,
    jobs_dir=_out_dir / ".jobs",
    retention=int(_async_cfg.get("job_retention", 50)),
)


def _estimate_seconds(intent: Intent, params: dict) -> float:
    if intent == Intent.VOICE:
        return len(params.get("text", "")) / max(_CHARS_PER_SEC_EST, 1.0)
    if intent == Intent.MUSIC:
        return float(params.get("duration_s", 60.0)) * _MUSIC_RT_FACTOR
    if intent == Intent.FX:
        return float(params.get("duration_s", 10.0)) * _FX_RT_FACTOR
    return 0.0


def _dispatch(intent: Intent, req) -> Any:
    """Inline if short / async not requested; otherwise queue a job and 202.
    ``queue`` (voice) queues whatever the estimate."""
    # Fail fast before accepting a job: a backend that can't run on this
    # machine (e.g. SAO without CUDA) must 503 here, not crash the job later.
    try:
        _dispatcher.check_available(intent)
    except NotWired as e:
        raise HTTPException(status_code=501, detail=str(e))
    except BackendUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    params = req.model_dump(exclude_none=True)
    want_async = bool(params.pop("async_mode", False))
    always_queue = bool(params.pop("queue", False))
    est = _estimate_seconds(intent, params)
    if _ASYNC_ENABLED and want_async and (always_queue or est >= _ASYNC_THRESHOLD_S):
        job_id = _jobs.submit(intent.value, params)
        return JSONResponse(status_code=202, content={
            "mode": "async",
            "job_id": job_id,
            "status": "queued",
            "estimate_s": round(est, 1),
            "poll_url": f"/jobs/{job_id}",
        })
    return _run(intent, params)


# ---------- endpoints --------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    """Liveness only. Does not load any backend. start.sh polls this."""
    return {"status": "ok", "service": "audio_foundry"}


@app.get("/status")
def status() -> dict[str, Any]:
    """Full service snapshot — what's registered, what's loaded, what's idle."""
    return {
        "service": "audio_foundry",
        "version": _config["manifest"].get("version", "0.0.0"),
        "port": _config["manifest"].get("port"),
        "backends": _dispatcher.status(),
    }


@app.get("/config")
def get_config() -> dict[str, Any]:
    """Return the merged manifest+runtime config, with secrets stripped (none yet)."""
    return _config


@app.post("/config/reload")
def reload_config() -> dict[str, Any]:
    """Hot-reload config from disk. Some changes (ports, models) still require restart."""
    global _config
    _config = load_config()
    return {"status": "reloaded", "config": _config}


@app.post("/evict/{intent}")
def evict_backend(intent: str) -> dict[str, Any]:
    """Force-unload a backend to free VRAM. Called by main backend or orchestrator."""
    try:
        it = Intent(intent)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid intent: {intent}")

    unloaded = _dispatcher.unload(it)
    return {"intent": intent, "unloaded": unloaded}


@app.get("/voices")
def list_voices() -> dict[str, Any]:
    """Return the available voice catalog grouped by backend.

    Kokoro voices come from backends/kokoro_voices.json; each carries
    ``installed``, true when its voice pack is in the local Hugging Face cache
    (a voice that is not installed is refused at generation with an Install
    hint rather than downloaded). Chatterbox voices come from reference clips
    at request-time, so the Chatterbox section just describes the contract.

    Frontend uses this to render the voice picker dropdown so we don't have
    to redeploy the UI when the catalog changes.
    """
    from backends import kokoro_voices
    from backends.hub_weights import cached_hub_file

    catalog = kokoro_voices.load_catalog()
    repo = kokoro_voices.hf_repo()
    groups = [
        {
            "label": group["label"],
            "voices": [
                {**voice,
                 "installed": cached_hub_file(repo, kokoro_voices.voice_file(voice["id"])) is not None}
                for voice in group["voices"]
            ],
        }
        for group in catalog["groups"]
    ]
    return {
        "kokoro": {"default": catalog["default"], "groups": groups},
        "chatterbox": {
            "type": "reference_clip",
            "description": (
                "Zero-shot voice cloning from a 5-10s reference clip imported in Audio Studio "
                "with consent recorded. Pass its path as `reference_clip_path` in the "
                "/generate/voice request; a clip without a consent record is refused (403)."
            ),
        },
    }


@app.post("/generate/fx")
def generate_fx(req: FxRequest) -> Any:
    return _dispatch(Intent.FX, req)


def _checked_voice_request(req: VoiceRequest) -> VoiceRequest:
    """Refuse, before any job is queued or model loaded, a request that could
    only be answered with a different voice than it names (routing rules in
    backends/voice_gen.py):

    * a reference clip without a consent record (403); ChatterboxBackend
      checks again when it clones. The clip travels on as the real path that
      was checked, and decides the voice, so voice_id is then not used;
    * a voice_id with backend 'chatterbox', which has no built-in voices, or
      one that is not a catalog Kokoro voice (400).
    """
    if req.reference_clip_path:
        try:
            clip = require_consent(req.reference_clip_path)
        except ConsentRequired as e:
            raise HTTPException(status_code=403, detail=str(e))
        return req.model_copy(update={"reference_clip_path": str(clip)})
    if req.voice_id:
        if req.backend == "chatterbox":
            raise HTTPException(status_code=400, detail=(
                f"Chatterbox has no built-in voices, so voice_id '{req.voice_id}' cannot be "
                "used with backend 'chatterbox'. Use backend 'kokoro' or 'auto' for a "
                "built-in voice, or a reference clip to clone one."))
        try:
            check_voice_id(req.voice_id)
        except UnknownVoice as e:
            raise HTTPException(status_code=400, detail=str(e))
    return req


@app.post("/generate/voice")
def generate_voice(req: VoiceRequest) -> Any:
    return _dispatch(Intent.VOICE, _checked_voice_request(req))


@app.post("/generate/voice/stream")
def generate_voice_stream(req: VoiceRequest) -> Any:
    """Streaming voice for first-chunk low latency (voice specialist audit rec).

    Yields WAV chunks as Kokoro synthesizes sentence-by-sentence. First chunk
    playable immediately (header included per chunk).
    """
    from starlette.responses import StreamingResponse
    params = _checked_voice_request(req).model_dump(exclude_none=True)
    # The status is sent with the first byte, so every error that can happen
    # before audio exists maps to the same codes as /generate/voice.
    with _http_errors(Intent.VOICE):
        # Force inline load for stream path (chat texts are short)
        with _dispatcher._intent_locks[Intent.VOICE]:
            with _dispatcher._state_lock:
                backend = _dispatcher._backends.get(Intent.VOICE)
                if backend is None:
                    raise NotWired("No voice backend registered")
                if not backend.is_loaded:
                    _dispatcher._load_with_orchestrator(Intent.VOICE, backend)
        _dispatcher._last_used[Intent.VOICE] = __import__("time").monotonic()
        backend = _dispatcher._backends[Intent.VOICE]
        if hasattr(backend, "stream"):
            raw_gen = backend.stream(**params)
        else:
            # fallback: full file as one chunk
            res = backend.generate(**params)
            def _one():
                with open(res.path, "rb") as f:
                    yield f.read()
            raw_gen = _one()
    def byte_stream():
        for item in raw_gen:
            if isinstance(item, (tuple, list)):
                yield item[0]
            else:
                yield item
    return StreamingResponse(byte_stream(), media_type="audio/wav")


@app.post("/plan/music")
def plan_music(req: MusicRequest) -> Any:
    """ACE-Step 1.5 only: expand a description into a caption and metadata. No audio."""
    params = req.model_dump(exclude_none=True)
    for key in ("async_mode", "output_format", "negative_prompt", "seed", "arrangement_accepted"):
        params.pop(key, None)
    if params.get("model") not in (None, "ace-step-1.5"):
        raise HTTPException(status_code=400, detail="Arrange is for ACE-Step 1.5")
    params["model"] = "ace-step-1.5"
    try:
        arrangement = _dispatcher.plan(Intent.MUSIC, **params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except NotWired as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except BackendUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Music arrangement failed")
        detail = str(e).split("Traceback (most recent call last)")[0]
        raise HTTPException(status_code=500, detail=" ".join(detail.split())[:1000]) from e
    return {"arrangement": arrangement}


@app.post("/generate/music")
def generate_music(req: MusicRequest) -> Any:
    return _dispatch(Intent.MUSIC, req)


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


@app.get("/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": _jobs.list()}


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    if not _jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="Unknown or already-finished job")
    return {"job_id": job_id, "status": "cancelling"}


@app.delete("/jobs")
def clear_jobs() -> dict[str, Any]:
    """Forget finished jobs (records on disk included). Active jobs untouched."""
    return _jobs.clear_finished()


# ---------- helpers ----------------------------------------------------------

def _run(intent: Intent, params: dict[str, Any]) -> dict[str, Any]:
    """Synchronous generate (short inputs / async not requested).

    Translates NotWired to 501, an unknown Kokoro voice id to 400, a clip
    without consent to 403, real errors to 500. Registration of the output as
    a Document happens in _finalize (shared with the async worker) and is
    non-fatal — a failure there doesn't kill the response; the file is on disk.
    """
    # progress_cb/cancel_event are popped if a caller ever sent them by mistake.
    params.pop("progress_cb", None)
    params.pop("cancel_event", None)
    with _http_errors(intent):
        result = _dispatcher.generate(intent, **params)
    return _finalize(result, intent, params)


@contextmanager
def _http_errors(intent: Intent):
    """A generation error as the HTTP status the caller acts on."""
    try:
        yield
    except HTTPException:
        raise
    except NotWired as e:
        # Valid intent, no backend registered yet (skeleton for voice/music).
        raise HTTPException(status_code=501, detail=str(e))
    except BackendUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except UnknownVoice as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConsentRequired as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("Generation failed for intent=%s", intent.value)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
