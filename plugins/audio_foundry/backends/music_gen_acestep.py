"""ACE-Step music generation backend (StepFun, Apache 2.0).

Full-song generation with vocals + instrumental from a style prompt + lyrics.
~3.5B params, ~10 GB VRAM at fp16 — the heaviest backend in the plugin and
the one most dependent on the dispatcher's orchestrator-mediated eviction
of other GPU residents (Ollama, ComfyUI) before load.

Heavy imports live inside methods.

Install (handled at first start.sh run after the requirements.txt bump):
    pip install acestep

The exact import surface for ACE-Step varies a bit between releases; the
load() path raises a clear "package version mismatch" error if the import
shape changes, with the actual ImportError attached for diagnosis.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)


class ACEStepBackend(AudioBackend):
    """ACE-Step v1 3.5B — full songs with vocals."""

    name = "ace_step_v1_3.5b"
    vram_mb_estimate = 10000  # ~9-10 GB observed in fp16; pad for activations

    MODEL_ID = "ACE-Step/ACE-Step-v1-3.5B"

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 44100,
        max_duration_s: float = 240.0,
        steps: int = 60,
        guidance_scale: float = 7.5,
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._max_duration_s = float(max_duration_s)
        self._steps = int(steps)
        self._guidance_scale = float(guidance_scale)
        self._pipeline: Any = None

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def load(self) -> None:
        if self._pipeline is not None:
            return

        logger.info("Loading %s (first run downloads ~10 GB)...", self.MODEL_ID)
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available — ACE-Step backend requires a GPU")

        try:
            from acestep.pipeline_ace_step import ACEStepPipeline
        except ImportError as e:
            raise RuntimeError(
                "acestep package not installed. Run: pip install acestep. "
                f"Underlying ImportError: {e}"
            ) from e

        try:
            self._pipeline = ACEStepPipeline(
                checkpoint_path=self.MODEL_ID,
                device="cuda",
                torch_dtype=torch.float16,
            )
        except TypeError:
            # Older versions of ACE-Step may use a different constructor.
            # Try the most common alt-form before giving up.
            self._pipeline = ACEStepPipeline.from_pretrained(
                self.MODEL_ID,
                torch_dtype=torch.float16,
            ).to("cuda")

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
            raise RuntimeError("ACE-Step not loaded; dispatcher should have called load() first")

        style_prompt: str = params["style_prompt"]
        lyrics: str | None = params.get("lyrics")
        instrumental_only: bool = bool(params.get("instrumental_only", False))
        duration_s = min(float(params.get("duration_s", 60.0)), self._max_duration_s)
        seed = params.get("seed")

        import torch
        import soundfile as sf

        generator = None
        if seed is not None:
            generator = torch.Generator("cuda").manual_seed(int(seed))

        # Empty lyrics or instrumental_only => instrumental track. ACE-Step
        # treats empty lyrics as "no vocal" rather than a separate flag.
        effective_lyrics = "" if instrumental_only or not lyrics else lyrics

        logger.info(
            "ACE-Step generate: style=%r duration=%.1fs lyrics=%d-chars instrumental=%s seed=%s",
            style_prompt[:80], duration_s, len(effective_lyrics), instrumental_only, seed,
        )
        t0 = time.monotonic()
        result = self._pipeline(
            prompt=style_prompt,
            lyrics=effective_lyrics,
            audio_duration=duration_s,
            num_inference_steps=self._steps,
            guidance_scale=self._guidance_scale,
            generator=generator,
        )
        gen_seconds = time.monotonic() - t0

        # ACE-Step returns either a tensor [batch, channels, samples] or an
        # object with an `.audios` attr like diffusers pipelines. Handle both.
        if hasattr(result, "audios"):
            audio_tensor = result.audios[0]
        else:
            audio_tensor = result[0] if isinstance(result, (list, tuple)) else result

        if hasattr(audio_tensor, "cpu"):
            audio_np = audio_tensor.float().cpu().numpy()
        else:
            import numpy as np
            audio_np = np.asarray(audio_tensor)

        # Normalize shape to [samples, channels] for soundfile.
        if audio_np.ndim == 3:
            audio_np = audio_np.squeeze(0)
        if audio_np.ndim == 2 and audio_np.shape[0] in (1, 2):
            # [channels, samples] -> [samples, channels]
            audio_np = audio_np.T
        if audio_np.ndim == 1:
            # mono
            audio_np = audio_np.reshape(-1, 1)

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        out_path = self._output_root / f"{asset_id}.wav"
        sf.write(str(out_path), audio_np, self._sample_rate)

        actual_duration = audio_np.shape[0] / self._sample_rate
        logger.info(
            "ACE-Step wrote %s — %.2fs audio in %.1fs wall",
            out_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=out_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "model": self.MODEL_ID,
                "style_prompt": style_prompt,
                "lyrics": effective_lyrics,
                "instrumental_only": instrumental_only,
                "requested_duration_s": duration_s,
                "steps": self._steps,
                "guidance_scale": self._guidance_scale,
                "seed": seed,
                "generation_seconds": round(gen_seconds, 2),
            },
        )
