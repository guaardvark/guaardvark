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
_dispatcher = Dispatcher()
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
    """Thin wrapper around dispatcher — translates NotWired to 501, real errors to 500."""
    try:
        result = _dispatcher.generate(intent, **params)
    except NotWired as e:
        # Skeleton phase: valid intent, no backend yet.
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.exception("Generation failed for intent=%s", intent.value)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    return {
        "path": str(result.path),
        "duration_s": result.duration_s,
        "sample_rate": result.sample_rate,
        "meta": result.meta,
    }
