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
