"""Wire concrete backends into the dispatcher at service startup.

Lives separately from app.py so it can be skipped (set
AUDIO_FOUNDRY_DISABLE_BACKENDS=all) in tests without mocking the whole app.
Supported env var values:
    AUDIO_FOUNDRY_DISABLE_BACKENDS=all          # skip all backends
    AUDIO_FOUNDRY_DISABLE_BACKENDS=fx,music     # skip a comma-separated list
    unset / empty                               # register every backend configured in config.yaml
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from service.dispatcher import Dispatcher, Intent

logger = logging.getLogger(__name__)

# Plugin root -> project root: plugins/audio_foundry/ -> /
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def bootstrap(dispatcher: Dispatcher, config: dict[str, Any]) -> None:
    """Register each backend that (a) has config and (b) isn't disabled."""
    disabled = _parse_disabled(os.environ.get("AUDIO_FOUNDRY_DISABLE_BACKENDS", ""))
    if "all" in disabled:
        logger.info("All backends disabled via AUDIO_FOUNDRY_DISABLE_BACKENDS=all")
        return

    runtime = config.get("runtime", {})
    backends_cfg = runtime.get("backends", {})
    output_dir = _PROJECT_ROOT / runtime.get("output", {}).get("dir", "data/outputs/audio")

    if "fx" not in disabled:
        _try_register_fx(dispatcher, backends_cfg.get("audio_fx", {}), output_dir)

    if "voice" not in disabled:
        _try_register_voice(dispatcher, backends_cfg.get("voice_gen", {}), output_dir)

    if "music" not in disabled:
        _try_register_music(dispatcher, backends_cfg.get("music_gen", {}), output_dir)


def _try_register_fx(
    dispatcher: Dispatcher,
    cfg: dict[str, Any],
    output_dir: Path,
) -> None:
    try:
        from backends.audio_fx_sao import StableAudioOpenBackend
        backend = StableAudioOpenBackend(
            output_root=output_dir,
            steps=int(cfg.get("steps", 100)),
            sample_rate=int(cfg.get("sample_rate", 44100)),
            max_duration_s=float(cfg.get("max_duration_s", 47.0)),
        )
        dispatcher.register(Intent.FX, backend)
    except Exception as e:
        # Registration failure shouldn't kill the service — log and leave fx unwired.
        # /generate/fx will return 501 via NotWired, which is honest.
        logger.error("Failed to register audio_fx backend: %s", e, exc_info=True)


def _try_register_voice(
    dispatcher: Dispatcher,
    cfg: dict[str, Any],
    output_dir: Path,
) -> None:
    """Register the voice_gen facade (Chatterbox primary + Kokoro fallback)."""
    try:
        from backends.voice_gen import VoiceGenBackend
        chat_cfg = cfg.get("chatterbox", {}) or {}
        koko_cfg = cfg.get("kokoro", {}) or {}
        backend = VoiceGenBackend(
            output_root=output_dir,
            chatterbox_kwargs={
                "sample_rate": int(chat_cfg.get("sample_rate", 24000)),
                "chunk_chars": int(chat_cfg.get("chunk_chars", 220)),
            },
            kokoro_kwargs={
                "sample_rate": int(koko_cfg.get("sample_rate", 24000)),
                "default_voice": str(koko_cfg.get("default_voice", "af_heart")),
            },
        )
        dispatcher.register(Intent.VOICE, backend)
    except Exception as e:
        logger.error("Failed to register voice_gen backend: %s", e, exc_info=True)


def _try_register_music(
    dispatcher: Dispatcher,
    cfg: dict[str, Any],
    output_dir: Path,
) -> None:
    """Register the music_gen backend (ACE-Step v1 3.5B)."""
    try:
        from backends.music_gen_acestep import ACEStepBackend
        backend = ACEStepBackend(
            output_root=output_dir,
            sample_rate=int(cfg.get("sample_rate", 44100)),
            max_duration_s=float(cfg.get("max_duration_s", 240.0)),
            steps=int(cfg.get("steps", 60)),
            guidance_scale=float(cfg.get("guidance_scale", 7.5)),
        )
        dispatcher.register(Intent.MUSIC, backend)
    except Exception as e:
        logger.error("Failed to register music_gen backend: %s", e, exc_info=True)


def _parse_disabled(val: str) -> set[str]:
    if not val:
        return set()
    return {x.strip() for x in val.split(",") if x.strip()}
