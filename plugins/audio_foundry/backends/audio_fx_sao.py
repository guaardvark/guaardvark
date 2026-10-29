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

from backends.base import AudioBackend, GenerationResult, chosen_seed

# The files Install leaves out: stable-audio-tools' own checkpoints and the card
# image. Keep in step with the stable-audio-open entry in the backend's catalog.
INSTALL_SKIPS = ["model.ckpt", "model.safetensors", "vae_model.ckpt", "*.png"]

logger = logging.getLogger(__name__)


def _make_generator(seed: int | None, device: str, torch_module: Any | None = None):
    """Build a seeded generator when the backend can safely use one.

    Stable Audio's MPS path has a torch/numpy recursion bug when an explicit
    MPS generator is threaded through the pipeline. On MPS, seed the global RNG
    instead and let diffusers draw from that state.
    """
    if seed is None:
        return None

    torch = torch_module
    if torch is None:
        import torch as torch  # type: ignore[no-redef]

    seed_int = int(seed)
    if device == "mps":
        torch.manual_seed(seed_int)
        return None

    return torch.Generator(device).manual_seed(seed_int)


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
        self._device: str = "cpu"
        self._availability: tuple[bool, str | None] | None = None

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    # Apple Silicon: Stable Audio Open on Metal is untested by us, so it is opt-in.
    MPS_OPT_IN_ENV = "AUDIO_FOUNDRY_SAO_MPS"

    @classmethod
    def _mps_opted_in(cls) -> bool:
        import os

        return os.environ.get(cls.MPS_OPT_IN_ENV, "").strip().lower() in ("1", "true", "yes", "on")

    @classmethod
    def select_device(cls, torch_module: Any) -> tuple[str | None, str | None]:
        """(device, reason): "cuda", "mps" when opted in, else None with the reason."""
        if torch_module.cuda.is_available():
            return "cuda", None
        mps = getattr(torch_module.backends, "mps", None)
        if mps is not None and mps.is_available():
            if cls._mps_opted_in():
                return "mps", None
            return None, (
                "Sound FX generation (Stable Audio Open) runs on NVIDIA CUDA. Apple Silicon "
                f"(Metal) is experimental: set {cls.MPS_OPT_IN_ENV}=1 for the audio service to try it."
            )
        return None, (
            "Sound FX generation (Stable Audio Open) requires an NVIDIA GPU with CUDA — "
            "no CUDA device was detected on this machine."
        )

    def availability(self) -> tuple[bool, str | None]:
        # Cached: the first probe pays the torch import, after that it's free.
        if self._availability is None:
            try:
                import torch
            except Exception as e:
                self._availability = (False, f"PyTorch unavailable: {e}")
            else:
                device, reason = self.select_device(torch)
                self._availability = (device is not None, reason)
        return self._availability

    def load(self) -> None:
        if self._pipeline is not None:
            return

        logger.info("Loading %s from local cache...", self.MODEL_ID)
        import torch
        from diffusers import StableAudioPipeline

        device, reason = self.select_device(torch)
        if device is None:
            raise RuntimeError(reason or "FX Lab (Stable Audio) needs an NVIDIA GPU (CUDA).")
        # fp16 on Metal is where diffusers audio pipelines misbehave; fp32 costs memory, not correctness.
        dtype = torch.float16 if device == "cuda" else torch.float32
        if device == "mps":
            logger.warning("%s on Apple Silicon (MPS) is experimental; please report results", self.MODEL_ID)

        from backends.hub_weights import INSTALL_HINT, WeightsNotInstalled, require_hub_files

        require_hub_files(self.MODEL_ID, ["model_index.json"], "Sound FX")
        try:
            # Install fetches the diffusers layout and skips the repo's original
            # checkpoints (backend/services/audio_foundry_models.py). Loaded by
            # repo id, huggingface_hub compares the cache with the repo's whole
            # file list and refuses the snapshot as incomplete, so resolve the
            # folder with the same skips and load from there.
            from huggingface_hub import snapshot_download

            local_dir = snapshot_download(
                self.MODEL_ID,
                local_files_only=True,
                ignore_patterns=INSTALL_SKIPS,
            )
            pipe = StableAudioPipeline.from_pretrained(local_dir, torch_dtype=dtype)
        except WeightsNotInstalled:
            raise
        except OSError as e:
            raise WeightsNotInstalled(
                f"Sound FX: weights for '{self.MODEL_ID}' are not on this machine. "
                f"{INSTALL_HINT}"
            ) from e
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

        # The model's default scheduler (CosineDPMSolverMultistep) drives an SDE
        # solver via `torchsde`, whose Brownian-motion path recurses infinitely on
        # Apple MPS (numpy seterr/geterr). Swap to a non-SDE multistep scheduler so
        # FX generation works on MPS; CUDA keeps the model's release scheduler.
        if device == "mps":
            try:
                from diffusers import EDMDPMSolverMultistepScheduler
                pipe.scheduler = EDMDPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
                logger.info("%s scheduler -> EDMDPMSolverMultistep (avoids torchsde on MPS)", self.MODEL_ID)
            except Exception as e:
                logger.warning("Could not swap SAO scheduler (%s); keeping default", e)

        pipe.to(device)
        self._pipeline = pipe
        self._device = device
        logger.info("%s loaded (%s, %s)", self.MODEL_ID, "fp16" if dtype == torch.float16 else "fp32", device)

    def unload(self) -> None:
        if self._pipeline is None:
            return
        import torch

        del self._pipeline
        self._pipeline = None
        if self._device == "cuda":
            torch.cuda.empty_cache()
        elif self._device == "mps":
            torch.mps.empty_cache()
        logger.info("%s unloaded", self.MODEL_ID)

    def generate(self, **params: Any) -> GenerationResult:
        if self._pipeline is None:
            raise RuntimeError("SAO backend not loaded; dispatcher should have called load() first")

        prompt: str = params["prompt"]
        duration_s = min(float(params.get("duration_s", 10.0)), self._max_duration_s)
        requested_seed = params.get("seed")
        seed = chosen_seed(requested_seed)
        # output_format is accepted but ignored for MVP — we always write WAV.
        # MP3 conversion is a separate bolt-on (ffmpeg/pydub) once real use surfaces.
        requested_format = params.get("output_format", "wav")

        import torch
        import soundfile as sf

        generator = _make_generator(seed, self._device, torch)

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
                "requested_seed": requested_seed,
                "generation_seconds": round(gen_seconds, 2),
            },
        )
