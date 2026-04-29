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

    Thread-safe; allows concurrent requests across DIFFERENT intents.
    Requests for the SAME intent are serialized to avoid GPU context thrashing.
    """

    def __init__(self, orchestrator: Optional[OrchestratorClient] = None) -> None:
        self._backends: dict[Intent, AudioBackend | None] = {
            Intent.FX: None,
            Intent.VOICE: None,
            Intent.MUSIC: None,
        }
        # One lock for dispatcher state (registration, loading status)
        self._state_lock = threading.RLock()
        # One lock per intent to serialize generations on that specific backend
        self._intent_locks: dict[Intent, threading.Lock] = {
            Intent.FX: threading.Lock(),
            Intent.VOICE: threading.Lock(),
            Intent.MUSIC: threading.Lock(),
        }
        self._last_used: dict[Intent, float] = {}

        # If no client passed, we still construct a disabled one so the call
        # sites can stay branch-free. enabled=False means every method is a no-op.
        self._orch = orchestrator or OrchestratorClient(enabled=False)

    def register(self, intent: Intent, backend: AudioBackend) -> None:
        """Called from service bootstrap as each backend comes online."""
        with self._state_lock:
            self._backends[intent] = backend
            logger.info("Registered backend for %s: %s", intent.value, backend.name)

    def status(self) -> dict[str, dict[str, Any]]:
        """Snapshot of what's registered and what's loaded, for /status endpoint."""
        with self._state_lock:
            import time
            now = time.monotonic()
            return {
                intent.value: {
                    "backend": backend.name if backend else None,
                    "loaded": backend.is_loaded if backend else False,
                    "vram_mb_estimate": backend.vram_mb_estimate if backend else 0,
                    "last_vram_mb": backend.last_vram_mb if backend else 0,
                    "idle_seconds": round(now - self._last_used[intent], 1) if intent in self._last_used else None,
                }
                for intent, backend in self._backends.items()
            }

    def generate(self, intent: Intent, **params: Any) -> GenerationResult:
        """Run a generation request. Loads the backend if cold.

        Serializes requests for the same intent, but allows different intents
        to proceed in parallel (VRAM permitting, mediated by orchestrator).
        """
        # 1. Get the intent lock to serialize requests for this specific model
        with self._intent_locks[intent]:
            # 2. Check/Load inside the state lock
            with self._state_lock:
                backend = self._backends.get(intent)
                if backend is None:
                    raise NotWired(f"No backend registered for intent: {intent.value}")

                if not backend.is_loaded:
                    self._load_with_orchestrator(intent, backend)

            # 3. Release state_lock but KEEP intent_lock during the slow generate()
            import time
            self._last_used[intent] = time.monotonic()
            
            try:
                result = backend.generate(**params)
                # Signal success to orchestrator so eviction timer can reset
                self._orch.release(f"{_SLOT_PREFIX}:{intent.value}")
                
                # Update dynamic VRAM estimate if possible
                try:
                    import torch
                    if torch.cuda.is_available():
                        # max_memory_allocated() returns bytes; convert to MB
                        peak_mb = int(torch.cuda.max_memory_allocated() / 1024 / 1024)
                        if peak_mb > 0:
                            backend.last_vram_mb = peak_mb
                except Exception:
                    pass

                return result
            except Exception:
                # If generation fails, we still release the intent lock (via context manager)
                # and the orchestrator already knows we are LOADED.
                raise

    def unload(self, intent: Intent) -> bool:
        """Release VRAM for an intent. Returns True if unloaded, False if not registered/busy."""
        # Attempt to get the intent lock without blocking. If it's blocked,
        # the backend is currently generating and shouldn't be unloaded.
        if not self._intent_locks[intent].acquire(blocking=False):
            logger.warning("Cannot unload %s: backend is currently busy generating", intent.value)
            return False

        try:
            with self._state_lock:
                backend = self._backends.get(intent)
                if backend and backend.is_loaded:
                    # Capture final peak before clearing
                    try:
                        import torch
                        if torch.cuda.is_available():
                            peak_mb = int(torch.cuda.max_memory_allocated() / 1024 / 1024)
                            if peak_mb > 0:
                                backend.last_vram_mb = peak_mb
                    except Exception:
                        pass
                    
                    backend.unload()
                    slot_id = f"{_SLOT_PREFIX}:{intent.value}"
                    self._orch.evict(slot_id)
                    return True
                return False
        finally:
            self._intent_locks[intent].release()

    # ------------------------------------------------------------------

    def _load_with_orchestrator(self, intent: Intent, backend: AudioBackend) -> None:
        """Request VRAM, run load(), report load completion. Cleans up on failure."""
        slot_id = f"{_SLOT_PREFIX}:{intent.value}"
        # Use the higher of hardcoded estimate vs actually observed peak.
        vram_req = max(backend.vram_mb_estimate, backend.last_vram_mb)
        # Backends that need the whole GPU (ACE-Step at 10 GB) ask the
        # orchestrator to evict everything else, including Ollama which
        # otherwise sits at priority 90 and grace-period-immune after a recent
        # warm chat call.
        exclusive = bool(getattr(backend, "requires_exclusive_vram", False))

        logger.info(
            "Cold backend for %s — requesting %d MB via orchestrator (slot=%s, exclusive=%s)",
            intent.value, vram_req, slot_id, exclusive,
        )
        # Best-effort eviction. If the orchestrator is unreachable we still try
        # the load — it might just OOM, which the caller will see as a 500.
        self._orch.request_vram(slot_id, vram_req, _DEFAULT_PRIORITY, exclusive=exclusive)

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            backend.load()
        except Exception:
            # Don't leave a LOADING slot dangling in the orchestrator's registry.
            self._orch.evict(slot_id)
            raise

        self._orch.mark_loaded(slot_id)
        logger.info("Backend %s loaded; slot %s now LOADED", backend.name, slot_id)
