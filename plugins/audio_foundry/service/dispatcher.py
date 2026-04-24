"""Intent dispatcher.

Routes FX / voice / music requests to the right backend, handling lazy load,
idle-unload, and VRAM arbitration. Skeleton phase: intents are registered but
backends are not wired — calling generate() raises NotWired so the API returns
501 cleanly.

The dispatcher is the ONLY thing that instantiates backends. The service layer
talks to the dispatcher; the dispatcher talks to the backends. Backends never
talk to each other.
"""
from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Any

from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    FX = "fx"
    VOICE = "voice"
    MUSIC = "music"


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

    def __init__(self) -> None:
        self._backends: dict[Intent, AudioBackend | None] = {
            Intent.FX: None,
            Intent.VOICE: None,
            Intent.MUSIC: None,
        }
        self._lock = threading.RLock()

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
                logger.info("Cold backend for %s — loading %s", intent.value, backend.name)
                # TODO(post-skeleton): request VRAM via gpu_memory_orchestrator here
                # before load(); evict others if needed; translate shortage to HTTP 503.
                backend.load()

            return backend.generate(**params)
