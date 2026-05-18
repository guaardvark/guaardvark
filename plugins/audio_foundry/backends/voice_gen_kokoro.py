"""Kokoro TTS backend (hexgrad/Kokoro-82M, Apache 2.0).

Lightweight fallback TTS — ~80M params, sub-1 GB VRAM, fast. Several
built-in voices but no reference-clip cloning. Used when Chatterbox fails
to load (OOM) or when the caller explicitly asks for backend="kokoro".

Heavy imports live inside methods.

Install (handled at first start.sh run after the requirements.txt bump):
    pip install kokoro
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)


class KokoroBackend(AudioBackend):
    """Kokoro-82M TTS — 24 kHz mono, built-in voices.

    Voice IDs are prefixed with the accent: af_*/am_* are American English,
    bf_*/bm_* are British English, ef_*/em_* are Spanish. The phonemizer
    (which is what the lang_code controls) needs to match the voice's accent
    or pronunciation gets weird — we keep one KPipeline per lang_code and
    route at generate-time.
    """

    name = "kokoro"
    vram_mb_estimate = 600  # ~500 MB observed; pad for activations

    # Recognized accent prefixes -> Kokoro lang_code.
    # Voice IDs that don't start with these fall back to American.
    # Spanish ("e") needs the misaki[es] extra in the venv (see requirements.txt)
    # and espeak-ng on the host. Extend with f/h/i/j/p/z when wiring more langs.
    _ACCENT_LANG_CODES = {"a": "a", "b": "b", "e": "e"}

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 24000,
        default_voice: str = "af_heart",
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._default_voice = default_voice
        # One pipeline per lang_code, lazy-loaded. The "default" one (American)
        # is created at load() time; British is created on first British voice.
        self._pipelines: dict[str, Any] = {}

    @property
    def is_loaded(self) -> bool:
        return bool(self._pipelines)

    @classmethod
    def _lang_code_for(cls, voice_id: str) -> str:
        """Return the Kokoro lang_code matching this voice's accent prefix."""
        if not voice_id:
            return "a"
        prefix = voice_id[0].lower()
        return cls._ACCENT_LANG_CODES.get(prefix, "a")

    def _get_or_load_pipeline(self, lang_code: str) -> Any:
        """Return the KPipeline for this lang_code, loading it if needed."""
        if lang_code in self._pipelines:
            return self._pipelines[lang_code]

        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise RuntimeError(
                "kokoro package not installed. Run: pip install kokoro"
            ) from e

        logger.info("Loading Kokoro pipeline for lang_code=%r", lang_code)
        pipeline = KPipeline(lang_code=lang_code)
        self._pipelines[lang_code] = pipeline
        return pipeline

    def load(self) -> None:
        # Pre-warm only the default voice's lang_code. Other accents lazy-load
        # on first request — keeps cold-start fast and VRAM low.
        if self._pipelines:
            return
        logger.info("Loading Kokoro-82M (first run downloads ~80 MB)...")
        self._get_or_load_pipeline(self._lang_code_for(self._default_voice))
        logger.info("Kokoro loaded")

    def unload(self) -> None:
        if not self._pipelines:
            return
        import torch

        self._pipelines.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Kokoro unloaded")

    def generate(self, **params: Any) -> GenerationResult:
        if not self._pipelines:
            raise RuntimeError("Kokoro not loaded; call load() first")

        text: str = params["text"]
        voice = params.get("voice_id") or self._default_voice
        requested_format = params.get("output_format", "wav")
        # Kokoro has no reference-clip cloning — silently ignore those args.

        # Route to the right phonemizer for this voice's accent. American voices
        # speak Kokoro's American pipeline; British speak its British pipeline.
        lang_code = self._lang_code_for(voice)
        pipeline = self._get_or_load_pipeline(lang_code)

        import numpy as np
        import soundfile as sf

        logger.info("Kokoro generate: chars=%d voice=%s lang=%s", len(text), voice, lang_code)
        t0 = time.monotonic()

        # KPipeline streams tuples: (graphemes, phonemes, audio_tensor).
        # We concatenate all audio chunks; each is a 1-D float tensor at 24 kHz.
        segments = []
        for _, _, audio_tensor in pipeline(text, voice=voice):
            arr = audio_tensor.cpu().numpy() if hasattr(audio_tensor, "cpu") else np.asarray(audio_tensor)
            segments.append(arr)
        gen_seconds = time.monotonic() - t0

        if not segments:
            raise RuntimeError("Kokoro produced no audio segments — input may be empty")

        audio = np.concatenate(segments)

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        out_path = self._output_root / f"{asset_id}.wav"
        sf.write(str(out_path), audio, self._sample_rate)

        # Reap the raw WAV if post_process fails or replaces it (see chatterbox).
        try:
            final_path = self.post_process(out_path, output_format=requested_format)
        except Exception:
            out_path.unlink(missing_ok=True)
            raise
        if final_path != out_path:
            out_path.unlink(missing_ok=True)
        actual_format = final_path.suffix.lstrip(".").lower()

        actual_duration = audio.shape[0] / self._sample_rate
        logger.info(
            "Kokoro wrote %s — %.2fs audio in %.1fs wall",
            final_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "text": text,
                "voice": voice,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "generation_seconds": round(gen_seconds, 2),
            },
        )
