"""FastAPI entrypoint for Audio Foundry.

Single worker, sync endpoints (uvicorn runs them in its default thread pool —
same pattern as vision_pipeline). Skeleton phase: the three /generate/* routes
return 501 because no backends are registered yet. /health and /status work.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict

# Names only — this module keeps torch inside its methods, so importing it
# here does not pull the ML stack into service startup.
from backends.voice_gen_chatterbox import EMOTION_PRESETS
from service.bootstrap import bootstrap
from service.config_loader import load_config, resolve_backend_url
from service.dispatcher import BackendUnavailable, Dispatcher, Intent, NotWired
from service.jobs import JobManager
from service.orchestrator_client import OrchestratorClient
from service.registration import register_output

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


class MusicRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    lyrics: Optional[str] = None
    style_prompt: str = Field(..., min_length=1)
    # Optional steering-away tags. ACE-Step drifts toward its strongest training
    # prior when style tags are vague ("professional", "futuristic"); negative
    # tags push it off that prior. Caller can leave None to skip negative steering.
    negative_prompt: Optional[str] = None
    duration_s: float = Field(60.0, gt=0, le=240.0)
    instrumental_only: bool = False
    output_format: str = Field("wav", pattern="^(wav|mp3)$")
    seed: Optional[int] = None
    async_mode: bool = Field(False, alias="async")


# ---------- app setup --------------------------------------------------------

app = FastAPI(
    title="Audio Foundry",
    version="0.1.0",
    description="Audio generation plugin for Guaardvark (voiceover, SFX, music).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_config = load_config()

# GPU orchestrator client — talks to the main Guaardvark backend over HTTP
# so the dispatcher can request VRAM and trigger eviction of other models
# (Ollama, ComfyUI, ...) before loading an audio backend.
_gpu_cfg = _config.get("runtime", {}).get("gpu", {})
_reg_cfg = _config.get("runtime", {}).get("registration", {})
_orch_client = OrchestratorClient(
    backend_url=resolve_backend_url(_reg_cfg.get("backend_url")),
    enabled=_gpu_cfg.get("orchestrator_enabled", True),
)

_dispatcher = Dispatcher(orchestrator=_orch_client)
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


def _finalize(result) -> dict:
    """Register the output and build the response dict. Shared by the inline
    path and the async worker so the shape is identical."""
    reg_cfg = _config.get("runtime", {}).get("registration", {})
    doc = None
    registration_error = None
    if reg_cfg.get("enabled", True):
        backend_url = resolve_backend_url(reg_cfg.get("backend_url"))
        doc = register_output(
            result,
            backend_url=backend_url,
            folder=reg_cfg.get("folder", "Audio"),
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
    return _finalize(result)


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
    """Inline if short / async not requested; otherwise queue a job and 202."""
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
    est = _estimate_seconds(intent, params)
    if _ASYNC_ENABLED and want_async and est >= _ASYNC_THRESHOLD_S:
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

    Kokoro voices are listed inline (the IDs are stable per Kokoro release).
    Chatterbox voices come from reference clips at request-time, so the
    Chatterbox section just describes the contract — not a list.

    Frontend uses this to render the voice picker dropdown so we don't have
    to redeploy the UI when Kokoro adds voices upstream.
    """
    # Kokoro v1.0+ catalog. American and British English are the wired set;
    # voice_gen_kokoro.py routes lang_code from the voice prefix at runtime.
    return {
        "kokoro": {
            "default": "af_heart",
            "groups": [
                {"label": "American Female", "voices": [
                    {"id": "af_heart",   "label": "Heart (default)"},
                    {"id": "af_bella",   "label": "Bella"},
                    {"id": "af_nicole",  "label": "Nicole"},
                    {"id": "af_sarah",   "label": "Sarah"},
                    {"id": "af_sky",     "label": "Sky"},
                    {"id": "af_alloy",   "label": "Alloy"},
                    {"id": "af_aoede",   "label": "Aoede"},
                    {"id": "af_jessica", "label": "Jessica"},
                    {"id": "af_kore",    "label": "Kore"},
                    {"id": "af_nova",    "label": "Nova"},
                    {"id": "af_river",   "label": "River"},
                ]},
                {"label": "American Male", "voices": [
                    {"id": "am_adam",    "label": "Adam"},
                    {"id": "am_michael", "label": "Michael"},
                    {"id": "am_eric",    "label": "Eric"},
                    {"id": "am_echo",    "label": "Echo"},
                    {"id": "am_fenrir",  "label": "Fenrir"},
                    {"id": "am_liam",    "label": "Liam"},
                    {"id": "am_onyx",    "label": "Onyx"},
                    {"id": "am_puck",    "label": "Puck"},
                    {"id": "am_santa",   "label": "Santa"},
                ]},
                {"label": "British Female", "voices": [
                    {"id": "bf_emma",     "label": "Emma"},
                    {"id": "bf_isabella", "label": "Isabella"},
                    {"id": "bf_alice",    "label": "Alice"},
                    {"id": "bf_lily",     "label": "Lily"},
                ]},
                {"label": "British Male", "voices": [
                    {"id": "bm_george",  "label": "George"},
                    {"id": "bm_lewis",   "label": "Lewis"},
                    {"id": "bm_daniel",  "label": "Daniel"},
                    {"id": "bm_fable",   "label": "Fable"},
                ]},
                {"label": "Spanish Female", "voices": [
                    {"id": "ef_dora",    "label": "Dora"},
                ]},
                {"label": "Spanish Male", "voices": [
                    {"id": "em_alex",    "label": "Alex"},
                    {"id": "em_santa",   "label": "Santa"},
                ]},
            ],
        },
        "chatterbox": {
            "type": "reference_clip",
            "description": "Zero-shot voice cloning from a 5-10s reference clip. Pass `reference_clip_path` in the /generate/voice request.",
        },
    }


@app.post("/generate/fx")
def generate_fx(req: FxRequest) -> Any:
    return _dispatch(Intent.FX, req)


@app.post("/generate/voice")
def generate_voice(req: VoiceRequest) -> Any:
    return _dispatch(Intent.VOICE, req)


@app.post("/generate/voice/stream")
def generate_voice_stream(req: VoiceRequest) -> Any:
    """Streaming voice for first-chunk low latency (voice specialist audit rec).

    Yields WAV chunks as Kokoro synthesizes sentence-by-sentence. First chunk
    playable immediately (header included per chunk).
    """
    from starlette.responses import StreamingResponse
    params = req.model_dump(exclude_none=True)
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

    Translates NotWired to 501, real errors to 500. Registration of the output
    as a Document happens in _finalize (shared with the async worker) and is
    non-fatal — a failure there doesn't kill the response; the file is on disk.
    """
    # progress_cb/cancel_event are popped if a caller ever sent them by mistake.
    params.pop("progress_cb", None)
    params.pop("cancel_event", None)
    try:
        result = _dispatcher.generate(intent, **params)
    except NotWired as e:
        # Valid intent, no backend registered yet (skeleton for voice/music).
        raise HTTPException(status_code=501, detail=str(e))
    except BackendUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Generation failed for intent=%s", intent.value)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return _finalize(result)
