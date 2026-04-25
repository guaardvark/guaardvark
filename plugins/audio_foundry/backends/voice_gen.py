"""voice_gen facade — owns Chatterbox + Kokoro, picks the right one per request.

Behaves like a single AudioBackend to the dispatcher above it. Internally
routes per-call based on the requested backend and degrades to Kokoro if
Chatterbox runs out of VRAM (16 GB cards on a busy day).

Routing rules:
  backend="chatterbox" -> Chatterbox only; raises if Chatterbox fails
  backend="kokoro"     -> Kokoro only; raises if Kokoro fails
  backend="auto"       -> Try Chatterbox first; on any error, retry with Kokoro
                         (logs the original error so the cause isn't swallowed)

vram_mb_estimate is the *max* of the inner backends, not the sum — both are
never expected to be loaded simultaneously in steady state. The dispatcher's
orchestrator request uses this number; if both happen to be loaded briefly
during a fallback, the orchestrator's eviction logic will catch up next
request.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult
from backends.voice_gen_chatterbox import ChatterboxBackend
from backends.voice_gen_kokoro import KokoroBackend

logger = logging.getLogger(__name__)


class VoiceGenBackend(AudioBackend):
    name = "voice_gen"

    def __init__(
        self,
        output_root: Path,
        chatterbox_kwargs: dict[str, Any] | None = None,
        kokoro_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._chatterbox = ChatterboxBackend(output_root, **(chatterbox_kwargs or {}))
        self._kokoro = KokoroBackend(output_root, **(kokoro_kwargs or {}))

    # AudioBackend interface ------------------------------------------------

    @property
    def vram_mb_estimate(self) -> int:  # type: ignore[override]
        return max(self._chatterbox.vram_mb_estimate, self._kokoro.vram_mb_estimate)

    @property
    def is_loaded(self) -> bool:
        return self._chatterbox.is_loaded or self._kokoro.is_loaded

    def load(self) -> None:
        """Pre-load the primary (Chatterbox). Falls back to Kokoro on OOM/import-failure.

        Called once by the dispatcher when the voice intent is first hit cold.
        Per-call routing in generate() can lazily load the other backend on
        demand; this just gets *something* warm so the first call is fast.
        """
        try:
            self._chatterbox.load()
        except Exception as e:
            logger.warning(
                "Chatterbox load failed (%s) — falling back to Kokoro for cold-start", e,
            )
            self._kokoro.load()

    def unload(self) -> None:
        # Idempotent on each — base contract guarantees no-op if already unloaded.
        self._chatterbox.unload()
        self._kokoro.unload()

    def generate(self, **params: Any) -> GenerationResult:
        requested = (params.get("backend") or "auto").lower()

        if requested == "chatterbox":
            return self._gen_with(self._chatterbox, params)
        if requested == "kokoro":
            return self._gen_with(self._kokoro, params)

        # auto: prefer Chatterbox, fall back to Kokoro on any runtime error.
        try:
            return self._gen_with(self._chatterbox, params)
        except Exception as e:
            logger.warning(
                "Chatterbox generate failed (%s) — retrying with Kokoro fallback", e,
            )
            return self._gen_with(self._kokoro, params)

    # ----------------------------------------------------------------------

    def _gen_with(self, backend: AudioBackend, params: dict[str, Any]) -> GenerationResult:
        """Lazy-load the chosen inner backend if it's cold, then generate."""
        if not backend.is_loaded:
            backend.load()
        return backend.generate(**params)
