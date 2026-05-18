"""Backend contract.

Every audio backend (SAO, Chatterbox, Kokoro, ACE-Step) implements this
interface. The dispatcher owns lifecycle — it calls load() before the first
generate() and unload() when evicting for VRAM. Backends don't self-manage.

Keep this file tiny. Implementation detail belongs in the concrete backend files.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GenerationResult:
    """What every backend returns.

    Path is absolute and points to a file the service just wrote.
    duration_s is wall-clock audio length (not generation time).
    meta is a free-form dict the backend can fill with model-specific params
    (seed, steps, actual guidance scale used, etc.) — gets stored alongside
    the Document row for reproducibility.
    """
    path: Path
    duration_s: float
    sample_rate: int
    meta: dict[str, Any]


class AudioBackend(ABC):
    """Abstract backend. Dispatcher owns lifecycle; backends own the model."""

    # Human-readable identifier used in logs / status endpoints.
    name: str = "base"

    # Max VRAM this backend holds once loaded. Dispatcher uses this to talk
    # to gpu_memory_orchestrator *before* calling load().
    vram_mb_estimate: int = 0

    # Actual observed peak VRAM usage from the last load/run.
    last_vram_mb: int = 0

    # If True, this backend can't share the GPU with anything heavy — the
    # dispatcher will ask the orchestrator to evict ALL other models (incl.
    # Ollama via keep_alive=0) before loading. ACE-Step at 10 GB on a 16 GB
    # card is the canonical case; voice/FX backends at ~3 GB can coexist.
    requires_exclusive_vram: bool = False

    @abstractmethod
    def load(self) -> None:
        """Pull weights into VRAM. Idempotent — calling twice is a no-op."""

    @abstractmethod
    def unload(self) -> None:
        """Release VRAM. Idempotent — calling on an already-unloaded backend is a no-op."""

    @abstractmethod
    def generate(self, **params: Any) -> GenerationResult:
        """Produce audio. Caller guarantees load() has been called first."""

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """True iff weights are currently in VRAM."""

    def post_process(
        self,
        input_path: Path,
        output_format: str = "wav",
        peak_dbfs: float | None = -1.0,
    ) -> Path:
        """Peak-normalize and (optionally) convert format.

        The previous version of this method tried to hit a target *average*
        loudness of 0 dBFS, which is the digital ceiling — speech with normal
        dynamics ended up amplified ~25–30 dB past clipping and sounded like
        Commodore-era buzz. We now peak-normalize: amplify so the loudest
        sample lands at `peak_dbfs` (default -1 dBFS, i.e. ~0.89 of full scale).
        That gives ~1 dB of headroom for downstream processing and zero
        clipping risk.

        Pass `peak_dbfs=None` to skip normalization entirely and emit the
        backend's native levels.

        Returns a Path to the final file. May overwrite input_path or create
        a new one. Requires pydub + ffmpeg for MP3 conversion only.
        """
        try:
            from pydub import AudioSegment
        except ImportError:
            logger.warning("pydub not installed; skipping post-processing")
            return input_path

        try:
            seg = AudioSegment.from_file(str(input_path))

            # Peak-normalize. seg.max_dBFS is the loudest sample; if we want
            # that to land at peak_dbfs, we apply (peak_dbfs - max_dBFS) gain.
            # Skip if the file is silent (max_dBFS is -inf) or peak_dbfs is None.
            if peak_dbfs is not None and seg.max_dBFS != float("-inf"):
                gain_db = peak_dbfs - seg.max_dBFS
                seg = seg.apply_gain(gain_db)

            if output_format == "mp3":
                out_path = input_path.with_suffix(".mp3")
                seg.export(str(out_path), format="mp3", bitrate="192k")
                if out_path != input_path and input_path.exists():
                    input_path.unlink()
                return out_path

            seg.export(str(input_path), format="wav")
            return input_path

        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Post-processing failed: %s", e)
            return input_path
