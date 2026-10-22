"""music_gen facade: ACE-Step v1 (the default) and the optional ACE-Step 1.5.

Behaves like a single AudioBackend to the dispatcher. A request's ``model`` picks
the engine:

  omitted or "ace-step" -> ACE-Step v1
  "ace-step-1.5"         -> ACE-Step 1.5 (refused with an Install hint until installed)

Both engines need the whole GPU, so only one is loaded at a time. The dispatcher
calls ``select_for_request`` before deciding whether to load; when that unloads the
other engine it also releases the orchestrator slot, and the normal cold-load
handshake then runs for the engine the request named. ``name`` and
``vram_mb_estimate`` are the selected engine's, so a request that names no model
reports v1's name and asks for v1's VRAM.
"""
from __future__ import annotations

from typing import Any

from backends.base import AudioBackend, GenerationResult

MODEL_V1 = "ace-step"
MODEL_V15 = "ace-step-1.5"
MODELS = (MODEL_V1, MODEL_V15)


class UnknownMusicModel(ValueError):
    """A request named a music model this service does not have."""


def model_of(params: dict[str, Any]) -> str:
    """The engine id a request names; MODEL_V1 when it names none."""
    model = str(params.get("model") or MODEL_V1).strip().lower()
    if model not in MODELS:
        raise UnknownMusicModel(f"Unknown music model {model!r}; choose one of {', '.join(MODELS)}")
    return model


class MusicGenBackend(AudioBackend):
    requires_exclusive_vram = True

    def __init__(self, v1: AudioBackend, v15: AudioBackend) -> None:
        self._engines: dict[str, AudioBackend] = {MODEL_V1: v1, MODEL_V15: v15}
        self._active = MODEL_V1

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._engines[self._active].name

    @property
    def vram_mb_estimate(self) -> int:  # type: ignore[override]
        return self._engines[self._active].vram_mb_estimate

    @property
    def is_loaded(self) -> bool:
        return self._engines[self._active].is_loaded

    def select_for_request(self, params: dict[str, Any]) -> bool:
        """Make the engine ``params`` names the active one.

        Returns True when another engine had to be unloaded for it, so the caller
        can release the GPU slot that engine held.
        """
        wanted = model_of(params)
        if wanted == self._active:
            return False
        previous = self._engines[self._active]
        unloaded = previous.is_loaded
        if unloaded:
            previous.unload()
        self._active = wanted
        return unloaded

    def load(self) -> None:
        self._engines[self._active].load()

    def unload(self) -> None:
        for engine in self._engines.values():
            engine.unload()

    def prepare_plan(self, params: dict[str, Any]) -> bool:
        """Aim a plan at ACE-Step 1.5. The dispatcher releases the GPU slot when this unloads v1."""
        params["model"] = MODEL_V15
        return self.select_for_request(params)

    def plan(self, **params: Any) -> dict[str, Any]:
        """Arrange with ACE-Step 1.5's planner. v1 has no equivalent pass."""
        self.prepare_plan(params)
        params.pop("model", None)
        engine = self._engines[MODEL_V15]
        if not engine.is_loaded:
            engine.load()
        plan = getattr(engine, "plan", None)
        if plan is None:
            raise RuntimeError("ACE-Step 1.5 cannot arrange a song")
        return plan(**params)

    def generate(self, **params: Any) -> GenerationResult:
        self.select_for_request(params)
        params.pop("model", None)
        engine = self._engines[self._active]
        if not engine.is_loaded:
            engine.load()
        return engine.generate(**params)
