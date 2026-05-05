"""Chatterbox neural TTS backend (Resemble AI, MIT).

High-quality voice synthesis with reference-clip-based voice cloning. Primary
backend for the voice_gen intent. ~500 MB weights, ~2 GB VRAM at fp16.

Heavy imports (torch, chatterbox) live inside methods so `from service.app
import app` stays cheap for tests.

Install (handled at first start.sh run after the requirements.txt bump):
    pip install chatterbox-tts
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)


class ChatterboxBackend(AudioBackend):
    """Chatterbox TTS — 24 kHz mono, optional reference-clip cloning."""

    name = "chatterbox"
    vram_mb_estimate = 2000  # ~1.7-2 GB observed in fp16 on consumer cards

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 24000,
        chunk_chars: int = 220,
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._chunk_chars = int(chunk_chars)
        self._model: Any = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return

        logger.info("Loading Chatterbox TTS (first run downloads ~500 MB)...")
        import torch

        try:
            from chatterbox.tts import ChatterboxTTS
        except ImportError as e:
            raise RuntimeError(
                "chatterbox-tts package not installed. "
                "Run: pip install chatterbox-tts"
            ) from e

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            logger.warning("CUDA not available — Chatterbox on CPU will be slow")

        self._model = ChatterboxTTS.from_pretrained(device=device)
        logger.info("Chatterbox loaded on %s", device)

    def unload(self) -> None:
        if self._model is None:
            return
        import torch

        del self._model
        self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Chatterbox unloaded")

    def generate(self, **params: Any) -> GenerationResult:
        if self._model is None:
            raise RuntimeError("Chatterbox not loaded; call load() first")

        text: str = params["text"]
        reference_clip = params.get("reference_clip_path")
        emotion = params.get("emotion")  # not all Chatterbox builds support this
        seed = params.get("seed")
        requested_format = params.get("output_format", "wav")

        import torch
        import soundfile as sf

        # Long-text chunking — single inference can OOM on very long inputs and
        # quality degrades past a few hundred characters. Naive splitter on the
        # last sentence end inside the chunk window.
        chunks = self._split_for_synthesis(text)

        gen_kwargs: dict[str, Any] = {}
        if reference_clip:
            gen_kwargs["audio_prompt_path"] = str(reference_clip)
        if emotion:
            gen_kwargs["emotion"] = emotion
        if seed is not None:
            torch.manual_seed(int(seed))

        logger.info(
            "Chatterbox generate: chars=%d chunks=%d ref=%s emotion=%s",
            len(text), len(chunks), bool(reference_clip), emotion,
        )

        t0 = time.monotonic()
        wav_segments = []
        for chunk in chunks:
            wav = self._model.generate(text=chunk, **gen_kwargs)
            # Chatterbox returns a 1-D or 2-D torch tensor [samples] or [1, samples]
            if hasattr(wav, "cpu"):
                wav = wav.cpu().numpy()
            if wav.ndim == 2:
                wav = wav.squeeze(0)
            wav_segments.append(wav)
        gen_seconds = time.monotonic() - t0

        # Concatenate segments. Numpy import is local — no extra top-level cost.
        import numpy as np
        audio = np.concatenate(wav_segments)

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        out_path = self._output_root / f"{asset_id}.wav"
        sf.write(str(out_path), audio, self._sample_rate)

        # Post-process: normalization + format conversion (MP3).
        # If post_process raises (ffmpeg missing, format invalid, OOM), the raw
        # WAV at out_path used to leak forever. Reap it on failure; on success
        # post_process either returns a new file (and we delete the raw WAV) or
        # returns out_path itself (and we keep it).
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
            "Chatterbox wrote %s — %.2fs audio in %.1fs wall",
            final_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "text": text,
                "chunks": len(chunks),
                "reference_clip_path": str(reference_clip) if reference_clip else None,
                "emotion": emotion,
                "seed": seed,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "generation_seconds": round(gen_seconds, 2),
            },
        )

    def _split_for_synthesis(self, text: str) -> list[str]:
        """Split text on sentence boundaries near self._chunk_chars."""
        text = text.strip()
        if len(text) <= self._chunk_chars:
            return [text]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > self._chunk_chars:
            window = remaining[: self._chunk_chars]
            # Prefer the last sentence end inside the window
            split_at = max(
                window.rfind(". "),
                window.rfind("! "),
                window.rfind("? "),
            )
            if split_at == -1:
                # No sentence end — fall back to the last space
                split_at = window.rfind(" ")
            if split_at == -1:
                # Single long token — hard cut
                split_at = self._chunk_chars
            chunks.append(remaining[: split_at + 1].strip())
            remaining = remaining[split_at + 1 :].strip()
        if remaining:
            chunks.append(remaining)
        return chunks
