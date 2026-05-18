"""Stable Audio Open backend.

Text → short instrumental audio clip (SFX, foley, ambience, music beds).
Up to ~47 s per call (the training-time ceiling); requests beyond that are clamped.

Model is gated on Hugging Face; first load needs HF_TOKEN in the environment
and the user must have accepted terms at hf.co/stabilityai/stable-audio-open-1.0.
After the first download the pipeline runs fully offline.

Heavy imports (torch, diffusers) live inside methods so `from service.app
import app` stays fast for tests; torch is obviously loaded once the service
actually boots, so this costs nothing at runtime.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)


class StableAudioOpenBackend(AudioBackend):
    """Stability AI's SAO v1.0 via diffusers.StableAudioPipeline."""

    name = "stable_audio_open_1.0"
    vram_mb_estimate = 6000  # measured: ~5.5 GB fp16 loaded, ~6 GB during inference

    MODEL_ID = "stabilityai/stable-audio-open-1.0"

    def __init__(
        self,
        output_root: Path,
        steps: int = 100,
        sample_rate: int = 44100,
        max_duration_s: float = 47.0,
    ) -> None:
        self._output_root = Path(output_root)
        self._steps = int(steps)
        self._sample_rate = int(sample_rate)
        self._max_duration_s = float(max_duration_s)
        self._pipeline: Any = None

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def load(self) -> None:
        if self._pipeline is not None:
            return

        logger.info("Loading %s (first run downloads ~1.5 GB)...", self.MODEL_ID)
        import torch
        from diffusers import StableAudioPipeline

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available — SAO backend requires a GPU")

        try:
            pipe = StableAudioPipeline.from_pretrained(
                self.MODEL_ID,
                torch_dtype=torch.float16,
            )
        except Exception as e:  # gated-access / auth failures come through here
            msg = str(e).lower()
            if "401" in msg or "gated" in msg or "access" in msg or "token" in msg:
                raise RuntimeError(
                    f"Stable Audio Open is gated on Hugging Face. "
                    f"1) Accept terms at https://hf.co/{self.MODEL_ID}. "
                    f"2) Set HF_TOKEN in the environment. "
                    f"Underlying error: {e}"
                ) from e
            raise

        pipe.to("cuda")
        self._pipeline = pipe
        logger.info("%s loaded (fp16, cuda)", self.MODEL_ID)

    def unload(self) -> None:
        if self._pipeline is None:
            return
        import torch

        del self._pipeline
        self._pipeline = None
        torch.cuda.empty_cache()
        logger.info("%s unloaded", self.MODEL_ID)

    def generate(self, **params: Any) -> GenerationResult:
        if self._pipeline is None:
            raise RuntimeError("SAO backend not loaded; dispatcher should have called load() first")

        prompt: str = params["prompt"]
        duration_s = min(float(params.get("duration_s", 10.0)), self._max_duration_s)
        seed = params.get("seed")
        # output_format is accepted but ignored for MVP — we always write WAV.
        # MP3 conversion is a separate bolt-on (ffmpeg/pydub) once real use surfaces.
        requested_format = params.get("output_format", "wav")

        import torch
        import soundfile as sf

        generator = None
        if seed is not None:
            generator = torch.Generator("cuda").manual_seed(int(seed))

        logger.info(
            "SAO generate: prompt=%r duration=%.1fs steps=%d seed=%s",
            prompt[:80], duration_s, self._steps, seed,
        )
        t0 = time.monotonic()
        result = self._pipeline(
            prompt,
            num_inference_steps=self._steps,
            audio_end_in_s=duration_s,
            generator=generator,
        )
        gen_seconds = time.monotonic() - t0

        # diffusers returns audios as tensor [batch, channels, samples]
        audio = result.audios[0].float().cpu().numpy().T  # -> [samples, channels]

        # Flat layout: everything lives directly under Audio/ so DocumentsPage
        # shows one tidy folder instead of a date-tree that's 99% empty on day one.
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
            "SAO wrote %s — %.2fs audio in %.1fs wall",
            final_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "model": self.MODEL_ID,
                "prompt": prompt,
                "steps": self._steps,
                "requested_duration_s": duration_s,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "seed": seed,
                "generation_seconds": round(gen_seconds, 2),
            },
        )
