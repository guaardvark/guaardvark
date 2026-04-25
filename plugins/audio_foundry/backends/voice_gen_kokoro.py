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
    """Kokoro-82M TTS — 24 kHz mono, built-in voices."""

    name = "kokoro"
    vram_mb_estimate = 600  # ~500 MB observed; pad for activations

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 24000,
        default_voice: str = "af_heart",
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._default_voice = default_voice
        self._pipeline: Any = None

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def load(self) -> None:
        if self._pipeline is not None:
            return

        logger.info("Loading Kokoro-82M (first run downloads ~80 MB)...")
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise RuntimeError(
                "kokoro package not installed. Run: pip install kokoro"
            ) from e

        # KPipeline takes a language code; default to American English ("a")
        self._pipeline = KPipeline(lang_code="a")
        logger.info("Kokoro loaded")

    def unload(self) -> None:
        if self._pipeline is None:
            return
        import torch

        del self._pipeline
        self._pipeline = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Kokoro unloaded")

    def generate(self, **params: Any) -> GenerationResult:
        if self._pipeline is None:
            raise RuntimeError("Kokoro not loaded; call load() first")

        text: str = params["text"]
        voice = params.get("voice_id") or self._default_voice
        # Kokoro has no reference-clip cloning — silently ignore those args.

        import numpy as np
        import soundfile as sf

        logger.info("Kokoro generate: chars=%d voice=%s", len(text), voice)
        t0 = time.monotonic()

        # KPipeline streams tuples: (graphemes, phonemes, audio_tensor).
        # We concatenate all audio chunks; each is a 1-D float tensor at 24 kHz.
        segments = []
        for _, _, audio_tensor in self._pipeline(text, voice=voice):
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

        actual_duration = audio.shape[0] / self._sample_rate
        logger.info(
            "Kokoro wrote %s — %.2fs audio in %.1fs wall",
            out_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=out_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "text": text,
                "voice": voice,
                "generation_seconds": round(gen_seconds, 2),
            },
        )
