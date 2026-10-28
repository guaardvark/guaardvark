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
# Room left above a backend's estimate before this plugin unloads its own others.
_HEADROOM_MB = 512


def _free_vram_mb() -> Optional[int]:
    """Free memory on the card in MB, or None when it cannot be read."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return int(free / 1024 / 1024)
    except Exception:
        return None


class NotWired(NotImplementedError):
    """Raised when an intent is reachable but its backend hasn't been wired yet.

    The service layer maps this to HTTP 501. Distinct from NotImplementedError
    on an abstract method so we can tell 'feature pending' from 'programmer error'.
    """


class BackendUnavailable(RuntimeError):
    """Raised when a backend is wired but can't run on this machine
    (e.g. Stable Audio Open without a CUDA GPU). Service layer maps to HTTP 503.
    Checked before a job is accepted so callers get the reason up front."""


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

    def forget_slots_from_before(self) -> None:
        """Drop the slots an earlier run of this service left in the orchestrator.

        A new process holds no models, but the backend's registry outlives a
        service restart. A slot left there is counted as in use, and evicting it
        is counted as freed although nothing leaves the card. Best-effort: the
        backend may not be up yet."""
        for intent in self._backends:
            self._orch.evict(f"{_SLOT_PREFIX}:{intent.value}")

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
            snapshot: dict[str, dict[str, Any]] = {}
            for intent, backend in self._backends.items():
                available, reason = backend.availability() if backend else (False, None)
                snapshot[intent.value] = {
                    "backend": backend.name if backend else None,
                    "loaded": backend.is_loaded if backend else False,
                    "available": available,
                    "unavailable_reason": reason,
                    "vram_mb_estimate": backend.vram_mb_estimate if backend else 0,
                    "last_vram_mb": backend.last_vram_mb if backend else 0,
                    "idle_seconds": round(now - self._last_used[intent], 1) if intent in self._last_used else None,
                }
            return snapshot

    def check_available(self, intent: Intent) -> None:
        """Raise NotWired / BackendUnavailable if the intent can't run here.

        Cheap (no model load) — the service layer calls this before accepting
        or queueing a job."""
        with self._state_lock:
            backend = self._backends.get(intent)
        if backend is None:
            raise NotWired(f"No backend registered for intent: {intent.value}")
        available, reason = backend.availability()
        if not available:
            raise BackendUnavailable(
                reason or f"Backend {backend.name} is unavailable on this machine"
            )

    def generate(
        self,
        intent: Intent,
        *,
        progress_cb: Optional[Any] = None,
        cancel_event: Optional[Any] = None,
        **params: Any,
    ) -> GenerationResult:
        """Run a generation request. Loads the backend if cold.

        Serializes requests for the same intent, but allows different intents
        to proceed in parallel (VRAM permitting, mediated by orchestrator).

        progress_cb/cancel_event are forwarded to the backend for async jobs;
        both default to None, so the inline path is byte-for-byte unchanged.
        """
        # 1. Get the intent lock to serialize requests for this specific model
        with self._intent_locks[intent]:
            # 2. Check/Load inside the state lock
            with self._state_lock:
                backend = self._backends.get(intent)
                if backend is None:
                    raise NotWired(f"No backend registered for intent: {intent.value}")

                available, reason = backend.availability()
                if not available:
                    raise BackendUnavailable(
                        reason or f"Backend {backend.name} is unavailable on this machine"
                    )

                # A backend holding several engines (music: ACE-Step v1 and 1.5)
                # picks the one this request names; if it unloaded another for it,
                # the slot no longer holds what the orchestrator recorded.
                select = getattr(backend, "select_for_request", None)
                if select is not None and select(params):
                    self._orch.evict(f"{_SLOT_PREFIX}:{intent.value}")

                if not backend.is_loaded:
                    self._load_with_orchestrator(intent, backend)
                else:
                    self._claim_card_again(intent, backend)

            # 3. Release state_lock but KEEP intent_lock during the slow generate()
            import time
            self._last_used[intent] = time.monotonic()
            
            try:
                result = backend.generate(
                    progress_cb=progress_cb, cancel_event=cancel_event, **params,
                )
                self._last_used[intent] = time.monotonic()
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

    def plan(self, intent: Intent, **params: Any) -> dict[str, Any]:
        """Load the backend that can arrange, then ask it. No audio.

        Music planning selects ACE-Step 1.5. The same slot handshake as
        generate applies, so a plan does not run beside another engine.
        """
        params = dict(params)
        with self._intent_locks[intent]:
            with self._state_lock:
                backend = self._backends.get(intent)
                if backend is None:
                    raise NotWired(f"No backend registered for intent: {intent.value}")
                available, reason = backend.availability()
                if not available:
                    raise BackendUnavailable(
                        reason or f"Backend {backend.name} is unavailable on this machine"
                    )
                prepare = getattr(backend, "prepare_plan", None)
                if prepare is None:
                    raise NotWired(f"No arrangement planner for intent: {intent.value}")
                if prepare(params):
                    self._orch.evict(f"{_SLOT_PREFIX}:{intent.value}")
                if not backend.is_loaded:
                    self._load_with_orchestrator(intent, backend)
                else:
                    self._claim_card_again(intent, backend)

            import time
            self._last_used[intent] = time.monotonic()
            try:
                result = backend.plan(**params)
                self._last_used[intent] = time.monotonic()
                self._orch.release(f"{_SLOT_PREFIX}:{intent.value}")
                return result
            except Exception:
                raise

    def stream(self, intent: Intent, **params: Any):
        """Streaming generation for lower perceived latency (voice specialist rec).

        For VOICE intent with Kokoro, yields (chunk_bytes, is_first) from the backend's
        stream() method. Falls back to full generation wrapped as single chunk if the
        backend doesn't support stream.

        Loading and locking same as generate().
        """
        with self._intent_locks[intent]:
            with self._state_lock:
                backend = self._backends.get(intent)
                if backend is None:
                    raise NotWired(f"No backend registered for intent: {intent.value}")

                available, reason = backend.availability()
                if not available:
                    raise BackendUnavailable(
                        reason or f"Backend {backend.name} is unavailable on this machine"
                    )

                if not backend.is_loaded:
                    self._load_with_orchestrator(intent, backend)

            import time
            self._last_used[intent] = time.monotonic()

            if hasattr(backend, 'stream'):
                try:
                    return backend.stream(**params)
                except Exception:
                    # fall back
                    pass

            # Fallback: full generate, yield the whole file as one "chunk"
            result = backend.generate(**params)
            self._orch.release(f"{_SLOT_PREFIX}:{intent.value}")
            def _one_chunk():
                with open(result.path, "rb") as f:
                    yield f.read(), True
            return _one_chunk()

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

    def _make_room(self, intent: Intent, vram_req: int) -> None:
        """Unload this plugin's other idle backends while the card is short.

        The orchestrator frees other services (Ollama, image pipelines) but for
        this plugin's slots it only stops tracking them: the models stay in this
        process and in the music daemon. Voice, then music, then an effect would
        otherwise stack them until a load runs out of memory.
        """
        for other, loaded in self._backends.items():
            if other is intent or loaded is None or not loaded.is_loaded:
                continue
            free = _free_vram_mb()
            if free is None or free >= vram_req + _HEADROOM_MB:
                break
            if self.unload(other):
                # Hand the freed blocks back now, so the next reading sees them
                # and the loop stops at the first unload that was enough.
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
                logger.info(
                    "Unloaded %s to make room for %s (%d MB free, %d MB wanted)",
                    other.value, intent.value, free, vram_req,
                )

    def _claim_card_again(self, intent: Intent, backend: AudioBackend) -> None:
        """For a loaded backend that needs the whole card (ACE-Step): ask for it
        again before running. A chat model loaded between two songs would
        otherwise leave this one out of memory. Voice and effects share."""
        if not getattr(backend, "requires_exclusive_vram", False):
            return
        slot_id = f"{_SLOT_PREFIX}:{intent.value}"
        vram_req = max(backend.vram_mb_estimate, backend.last_vram_mb)
        self._orch.request_vram(slot_id, vram_req, _DEFAULT_PRIORITY, exclusive=True)
        # The orchestrator may have forgotten an idle slot; a re-registered one
        # starts as loading, and the model is already in memory.
        self._orch.mark_loaded(slot_id)

    def start_idle_unloader(self, idle_s: int) -> None:
        """Unload a backend idle for ``idle_s`` seconds (runtime.gpu.idle_unload_s),
        so its memory returns to other work. 0 keeps backends loaded."""
        if idle_s <= 0:
            return

        def loop() -> None:
            import time
            while True:
                time.sleep(max(5, min(30, idle_s // 4)))
                now = time.monotonic()
                for intent in list(self._backends):
                    backend = self._backends.get(intent)
                    last = self._last_used.get(intent)
                    if backend is None or not backend.is_loaded or last is None:
                        continue
                    if now - last >= idle_s and self.unload(intent):
                        logger.info("Unloaded %s after %ds idle", intent.value, idle_s)

        threading.Thread(target=loop, name="audio-idle-unload", daemon=True).start()

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
        self._make_room(intent, vram_req)

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
