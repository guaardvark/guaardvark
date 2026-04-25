"""Intent dispatcher.

Routes FX / voice / music requests to the right backend, handling lazy load,
GPU memory arbitration, and idle-unload. The dispatcher is the ONLY thing that
instantiates backends. The service layer talks to the dispatcher; the dispatcher
talks to the backends. Backends never talk to each other.

GPU arbitration goes through the main backend's gpu_memory_orchestrator over
HTTP (see service/orchestrator_client.py). The pre-load handshake is:
  1. orchestrator.request_vram(slot_id, vram_mb)  — may evict other models
  2. backend.load()                                — actually pulls weights to VRAM
  3. orchestrator.mark_loaded(slot_id)             — registry transitions LOADING -> LOADED
On load() failure we call orchestrator.evict(slot_id) so the LOADING slot
doesn't dangle in the registry.
"""
from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Any, Optional

from backends.base import AudioBackend, GenerationResult
from service.orchestrator_client import OrchestratorClient

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    FX = "fx"
    VOICE = "voice"
    MUSIC = "music"


# Slot-id prefix when this plugin talks to the orchestrator. Naming follows
# the convention seen in ROUTE_MODEL_MAP: "ollama:llm", "sd:pipeline", etc.
_SLOT_PREFIX = "audio_foundry"
# Default priority for audio backends — between embeddings (60) and chat-LLM (90).
_DEFAULT_PRIORITY = 70


class NotWired(NotImplementedError):
    """Raised when an intent is reachable but its backend hasn't been wired yet.

    The service layer maps this to HTTP 501. Distinct from NotImplementedError
    on an abstract method so we can tell 'feature pending' from 'programmer error'.
    """


class Dispatcher:
    """Single source of truth for which backend is loaded and which isn't.

    Thread-safe; the FastAPI service runs in uvicorn's default thread pool so
    concurrent requests can race for a load. One lock per dispatcher is fine —
    backend load is the slow path, and we'd rather serialize than double-load.
    """

    def __init__(self, orchestrator: Optional[OrchestratorClient] = None) -> None:
        self._backends: dict[Intent, AudioBackend | None] = {
            Intent.FX: None,
            Intent.VOICE: None,
            Intent.MUSIC: None,
        }
        self._lock = threading.RLock()
        # If no client passed, we still construct a disabled one so the call
        # sites can stay branch-free. enabled=False means every method is a no-op.
        self._orch = orchestrator or OrchestratorClient(enabled=False)

    def register(self, intent: Intent, backend: AudioBackend) -> None:
        """Called from service bootstrap as each backend comes online."""
        with self._lock:
            self._backends[intent] = backend
            logger.info("Registered backend for %s: %s", intent.value, backend.name)

    def status(self) -> dict[str, dict[str, Any]]:
        """Snapshot of what's registered and what's loaded, for /status endpoint."""
        with self._lock:
            return {
                intent.value: {
                    "backend": backend.name if backend else None,
                    "loaded": backend.is_loaded if backend else False,
                    "vram_mb_estimate": backend.vram_mb_estimate if backend else 0,
                }
                for intent, backend in self._backends.items()
            }

    def generate(self, intent: Intent, **params: Any) -> GenerationResult:
        """Run a generation request. Loads the backend if cold.

        Raises NotWired if the intent is valid but no backend is registered yet
        (skeleton phase). Raises whatever the backend raises on real errors.
        """
        with self._lock:
            backend = self._backends.get(intent)
            if backend is None:
                raise NotWired(f"No backend registered for intent: {intent.value}")

            if not backend.is_loaded:
                self._load_with_orchestrator(intent, backend)

            return backend.generate(**params)

    # ------------------------------------------------------------------

    def _load_with_orchestrator(self, intent: Intent, backend: AudioBackend) -> None:
        """Request VRAM, run load(), report load completion. Cleans up on failure."""
        slot_id = f"{_SLOT_PREFIX}:{intent.value}"
        logger.info(
            "Cold backend for %s — requesting %d MB via orchestrator (slot=%s)",
            intent.value, backend.vram_mb_estimate, slot_id,
        )
        # Best-effort eviction. If the orchestrator is unreachable we still try
        # the load — it might just OOM, which the caller will see as a 500.
        self._orch.request_vram(slot_id, backend.vram_mb_estimate, _DEFAULT_PRIORITY)

        try:
            backend.load()
        except Exception:
            # Don't leave a LOADING slot dangling in the orchestrator's registry.
            self._orch.evict(slot_id)
            raise

        self._orch.mark_loaded(slot_id)
        logger.info("Backend %s loaded; slot %s now LOADED", backend.name, slot_id)
