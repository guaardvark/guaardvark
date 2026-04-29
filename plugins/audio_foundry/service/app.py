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
from pydantic import BaseModel, Field

from service.bootstrap import bootstrap
from service.config_loader import load_config
from service.dispatcher import Dispatcher, Intent, NotWired
from service.orchestrator_client import OrchestratorClient
from service.registration import register_output

logger = logging.getLogger(__name__)

# ---------- request models ---------------------------------------------------
# Kept lenient at skeleton phase. Each backend tightens its own fields when wired.

class FxRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    duration_s: float = Field(10.0, gt=0, le=47.0)
    output_format: str = Field("wav", pattern="^(wav|mp3)$")
    seed: Optional[int] = None


class VoiceRequest(BaseModel):
    text: str = Field(..., min_length=1)
    backend: str = Field("auto", pattern="^(auto|chatterbox|kokoro)$")
    reference_clip_path: Optional[str] = None
    voice_id: Optional[str] = None
    emotion: Optional[str] = None
    output_format: str = Field("wav", pattern="^(wav|mp3)$")


class MusicRequest(BaseModel):
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
    backend_url=_reg_cfg.get("backend_url", "http://localhost:5002"),
    enabled=_gpu_cfg.get("orchestrator_enabled", True),
)

_dispatcher = Dispatcher(orchestrator=_orch_client)
bootstrap(_dispatcher, _config)


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
            ],
        },
        "chatterbox": {
            "type": "reference_clip",
            "description": "Zero-shot voice cloning from a 5-10s reference clip. Pass `reference_clip_path` in the /generate/voice request.",
        },
    }


@app.post("/generate/fx")
def generate_fx(req: FxRequest) -> dict[str, Any]:
    return _run(Intent.FX, req.model_dump(exclude_none=True))


@app.post("/generate/voice")
def generate_voice(req: VoiceRequest) -> dict[str, Any]:
    return _run(Intent.VOICE, req.model_dump(exclude_none=True))


@app.post("/generate/music")
def generate_music(req: MusicRequest) -> dict[str, Any]:
    return _run(Intent.MUSIC, req.model_dump(exclude_none=True))


# ---------- helpers ----------------------------------------------------------

def _run(intent: Intent, params: dict[str, Any]) -> dict[str, Any]:
    """Thin wrapper around dispatcher — translates NotWired to 501, real errors to 500.

    Also registers the resulting file with the main Guaardvark backend so it shows
    up in DocumentsPage. Registration is non-fatal — a failure there doesn't kill
    the generate response; the file is on disk either way.
    """
    try:
        result = _dispatcher.generate(intent, **params)
    except NotWired as e:
        # Valid intent, no backend registered yet (skeleton for voice/music).
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.exception("Generation failed for intent=%s", intent.value)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    reg_cfg = _config.get("runtime", {}).get("registration", {})
    doc = None
    if reg_cfg.get("enabled", True):
        doc = register_output(
            result,
            backend_url=reg_cfg.get("backend_url", "http://localhost:5002"),
            folder=reg_cfg.get("folder", "Audio"),
        )

    return {
        "path": str(result.path),
        "duration_s": result.duration_s,
        "sample_rate": result.sample_rate,
        "meta": result.meta,
        "document_id": doc.get("id") if doc else None,
    }
