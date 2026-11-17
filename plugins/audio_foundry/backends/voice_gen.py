"""voice_gen facade — owns Chatterbox + Kokoro, picks the right one per request.

Behaves like a single AudioBackend to the dispatcher above it. Internally
routes per-call based on the requested backend and degrades to Kokoro if
Chatterbox runs out of VRAM (16 GB cards on a busy day).

Routing rules (``route``), the same for every caller (Audio Studio, the
backend's voice API, the Film Crew Editor, MCP's generate_speech):

  backend="chatterbox" -> Chatterbox only; raises if Chatterbox fails
  backend="kokoro"     -> Kokoro only; raises if Kokoro fails
  backend="auto"       -> decided by what the request names:
      a reference clip  -> Chatterbox cloning that clip; no fallback, since
                           Kokoro cannot speak in that voice
      a voice_id        -> Kokoro speaking that built-in voice; no fallback,
                           since Chatterbox has no built-in voices
      neither           -> Chatterbox's stock voice, falling back to Kokoro's
                           default voice on any error; the result's
                           meta["fallback"] records it with the reason

A request that names nothing therefore sounds exactly as it always did; a
named voice or clip is either honoured or refused, never swapped for another
voice.

vram_mb_estimate is the *max* of the inner backends, not the sum — both are
never expected to be loaded simultaneously in steady state. If both happen to
be loaded briefly during a fallback, the orchestrator's eviction logic will
catch up next request.

A cold load brings up the engine the request routes to: a request for a
built-in voice loads Kokoro alone, so narration on a machine with no GPU
never loads Chatterbox first. The orchestrator request stays the max above,
since Chatterbox may still load into the same slot on a later request.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult, GenerationCancelled
from backends.voice_gen_chatterbox import ChatterboxBackend
from backends.voice_gen_kokoro import KokoroBackend

logger = logging.getLogger(__name__)

AUTO = "auto"


def route(params: dict[str, Any]) -> str:
    """The engine this request asks for: 'chatterbox', 'kokoro', or AUTO
    (Chatterbox's stock voice with the Kokoro fallback)."""
    requested = str(params.get("backend") or AUTO).lower()
    if requested in ("chatterbox", "kokoro"):
        return requested
    if params.get("reference_clip_path"):
        return "chatterbox"
    if params.get("voice_id"):
        return "kokoro"
    return AUTO


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
        # route() of the request the dispatcher is about to load for.
        self._requested = AUTO

    # AudioBackend interface ------------------------------------------------

    @property
    def vram_mb_estimate(self) -> int:  # type: ignore[override]
        return max(self._chatterbox.vram_mb_estimate, self._kokoro.vram_mb_estimate)

    @property
    def is_loaded(self) -> bool:
        return self._chatterbox.is_loaded or self._kokoro.is_loaded

    def select_for_request(self, params: dict[str, Any]) -> bool:
        """Note the engine this request speaks with, for load(). Nothing is
        unloaded, so the slot stays as recorded."""
        self._requested = route(params)
        return False

    def load(self) -> None:
        """Load the engine the request routes to: Kokoro for a built-in voice,
        otherwise Chatterbox, falling back to Kokoro on OOM/import-failure.

        Called once by the dispatcher when the voice intent is first hit cold.
        Per-call routing in generate() can lazily load the other backend on
        demand; this just gets *something* warm so the first call is fast.
        """
        if self._requested == "kokoro":
            self._kokoro.load()
            return
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
        engine = route(params)
        if engine == "chatterbox":
            return self._gen_with(self._chatterbox, params)
        if engine == "kokoro":
            return self._gen_with(self._kokoro, params)

        # Nothing named: prefer Chatterbox, fall back to Kokoro on any runtime
        # error — but NOT on a user cancel. The result says it fell back and
        # why, so a caller can show that this line is in another voice.
        try:
            return self._gen_with(self._chatterbox, params)
        except GenerationCancelled:
            raise
        except Exception as e:
            logger.warning(
                "Chatterbox generate failed (%s) — retrying with Kokoro fallback", e,
            )
            result = self._gen_with(self._kokoro, params)
            result.meta = {
                **(result.meta or {}),
                "fallback": {"from": "chatterbox", "to": "kokoro", "reason": str(e)[:300]},
            }
            return result

    # ----------------------------------------------------------------------

    def _gen_with(self, backend: AudioBackend, params: dict[str, Any]) -> GenerationResult:
        """Lazy-load the chosen inner backend if it's cold, then generate."""
        if not backend.is_loaded:
            backend.load()
        return backend.generate(**params)
