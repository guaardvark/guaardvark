"""
GPU holds for agents that run a local Ollama model.

An agent that runs ``ollama/<model>`` (Guaardvark's own code agent, or cline)
keeps that model on the GPU for as long as it works, but it talks to Ollama
directly, so the backend's GPU memory orchestrator only knows the model as one
more Ollama resident: free to evict when something else asks for room, after
which the agent's next call loads it again beside whatever took its place.

While such an agent runs, the swarm holds the model's orchestrator slot
(``ollama:<model>``) over the backend's /api/gpu/memory endpoints, the way the
Audio Foundry sidecar holds its models: preload (reserve room, or find the
model already resident), mark-loaded, then begin-use, which pins it. When the
agent exits: end-use, then release, which starts the normal idle timer.

Every call is best effort. With the backend unreachable, or the GPU unable to
fit the model, the agent still runs, without a hold, and the log says so.
SWARM_DISABLE_GPU_HOLD=1 turns the calls off (the plugin's tests set it).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from .backend_url import API_KEY_HEADER, backend_api_key

logger = logging.getLogger("swarm.gpu_hold")

_ENV_DISABLE = "SWARM_DISABLE_GPU_HOLD"
_OLLAMA_PREFIX = "ollama/"

# What the orchestrator gives an Ollama chat model it discovers on its own,
# so a released hold ranks like any other resident model.
_HOLD_PRIORITY = 70


def ollama_model_of(model: str | None) -> str | None:
    """The Ollama model a backend's ``model`` setting runs, or None.

    ``ollama/gemma4:e4b`` -> ``gemma4:e4b``. A name without a tag gets
    ``:latest``, the name Ollama lists it under in /api/ps.
    """
    if not model:
        return None
    text = str(model).strip()
    if not text.lower().startswith(_OLLAMA_PREFIX):
        return None
    name = text[len(_OLLAMA_PREFIX):].strip()
    if not name:
        return None
    return name if ":" in name else f"{name}:latest"


class GpuHoldClient:
    """Holds and releases Ollama model slots on the backend's GPU orchestrator.

    ``api_url`` is the backend API base, e.g. ``http://localhost:5000/api``.
    """

    def __init__(self, api_url: str, timeout_s: float = 5.0) -> None:
        self._url = (api_url or "").rstrip("/")
        self._timeout = float(timeout_s)
        self._enabled = bool(self._url) and os.environ.get(_ENV_DISABLE) != "1"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def hold(self, model: str) -> str | None:
        """Reserve, mark loaded and pin ``ollama:<model>``. The slot id, or None."""
        if not self._enabled:
            return None
        slot_id = f"ollama:{model}"
        if not self._post("/gpu/memory/preload", {"slot_id": slot_id, "priority": _HOLD_PRIORITY}):
            logger.warning(f"No GPU hold for {slot_id}; the agent runs without one")
            return None
        self._post("/gpu/memory/mark-loaded", {"slot_id": slot_id})
        if not self._post("/gpu/memory/begin-use", {"slot_id": slot_id}):
            logger.warning(f"{slot_id} could not be pinned; the agent runs without a hold")
            return None
        logger.info(f"GPU hold on {slot_id}")
        return slot_id

    def release(self, slot_id: str) -> None:
        """Unpin ``slot_id`` and start its idle timer."""
        if not self._enabled or not slot_id:
            return
        self._post("/gpu/memory/end-use", {"slot_id": slot_id})
        self._post("/gpu/memory/release", {"slot_id": slot_id})
        logger.info(f"GPU hold on {slot_id} released")

    def _post(self, path: str, payload: dict[str, Any]) -> bool:
        try:
            key = backend_api_key()
            response = requests.post(
                f"{self._url}{path}",
                json=payload,
                headers={API_KEY_HEADER: key} if key else None,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            logger.warning(f"GPU orchestrator {path} failed: {e}")
            return False
        if response.status_code >= 400:
            detail = ""
            try:
                detail = (response.json() or {}).get("error") or ""
            except ValueError:
                pass
            logger.warning(f"GPU orchestrator {path} answered {response.status_code} {detail}".rstrip())
            return False
        return True
