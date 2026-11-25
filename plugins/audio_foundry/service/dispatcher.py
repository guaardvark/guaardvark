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

On a machine with no CUDA device there is no VRAM to arbitrate: a backend that
runs on the CPU (Kokoro, Chatterbox) loads without the handshake, and nothing
is reserved, released or evicted from other services on its behalf.
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
# How long a cold load waits for memory it asked other services to give up:
# this many looks at the card, one a second. Ollama's runner and ComfyUI were
# seen to leave within 30 s of a request that failed for lack of them.
_ROOM_WAIT_LOOKS = 30
_ROOM_WAIT_POLL_S = 1.0
# How often a job waiting for the GPU asks again who holds it.
_GPU_WAIT_POLL_S = 5.0
# A call with no progress callback answers an open HTTP request, so it waits
# only this long; a background job waits up to Dispatcher.gpu_wait_s.
_GPU_WAIT_INLINE_S = 120.0


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _hand_back_vram() -> None:
    """Return this process's freed CUDA memory to the card after an unload.

    A backend's own empty_cache() runs while the model's reference cycles are
    still alive, and a cached block holding one of their tensors cannot be
    released. Measured 2026-11-23 (one effect, one Chatterbox line, four Kokoro
    lines, then both backends unloaded): the process still held 2192 MB with
    nothing loaded, and 296 MB after a collection and a second empty_cache().
    """
    import gc
    import sys

    gc.collect()
    # A backend that held CUDA memory imported torch itself; do not pull it in here.
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


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


def _is_oom(exc: BaseException) -> bool:
    """torch.cuda.OutOfMemoryError, the RuntimeError older torch raises, or a
    daemon's error text carrying either."""
    return type(exc).__name__ == "OutOfMemoryError" or "out of memory" in str(exc).lower()


class NotWired(NotImplementedError):
    """Raised when an intent is reachable but its backend hasn't been wired yet.

    The service layer maps this to HTTP 501. Distinct from NotImplementedError
    on an abstract method so we can tell 'feature pending' from 'programmer error'.
    """


class BackendUnavailable(RuntimeError):
    """Raised when a backend is wired but can't run on this machine
    (e.g. Stable Audio Open without a CUDA GPU). Service layer maps to HTTP 503.
    Checked before a job is accepted so callers get the reason up front."""


class GpuBusy(BackendUnavailable):
    """Another job (a video render, training) held the GPU for longer than this
    request may wait. Maps to 503 like any unavailable backend."""


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
        # Intents whose slot this process reserved with the orchestrator.
        self._claimed: set[Intent] = set()

        # If no client passed, we still construct a disabled one so the call
        # sites can stay branch-free. enabled=False means every method is a no-op.
        self._orch = orchestrator or OrchestratorClient(enabled=False)
        # Longest a background job waits for another job to give the GPU back
        # (runtime.gpu.wait_for_gpu_s).
        self.gpu_wait_s: float = 3600.0

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
            # Outside the state lock: an eviction from the backend calls unload(),
            # which takes it, and the wait can be long.
            self.wait_for_gpu(intent, progress_cb=progress_cb, cancel_event=cancel_event)
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
                self._release(intent)
                
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
            except Exception as e:
                # If generation fails, we still release the intent lock (via context manager)
                # and the orchestrator already knows we are LOADED. Out of memory
                # leaves the card in an unknown state: drop the model so its
                # memory goes back to the job that needed it.
                if _is_oom(e):
                    self._drop_after_oom(intent, backend)
                raise

    def plan(self, intent: Intent, **params: Any) -> dict[str, Any]:
        """Load the backend that can arrange, then ask it. No audio.

        Music planning selects ACE-Step 1.5. The same slot handshake as
        generate applies, so a plan does not run beside another engine.
        """
        params = dict(params)
        with self._intent_locks[intent]:
            self.wait_for_gpu(intent)
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
                self._release(intent)
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
            self.wait_for_gpu(intent)
            with self._state_lock:
                backend = self._backends.get(intent)
                if backend is None:
                    raise NotWired(f"No backend registered for intent: {intent.value}")

                available, reason = backend.availability()
                if not available:
                    raise BackendUnavailable(
                        reason or f"Backend {backend.name} is unavailable on this machine"
                    )

                select = getattr(backend, "select_for_request", None)
                if select is not None and select(params):
                    self._orch.evict(f"{_SLOT_PREFIX}:{intent.value}")

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
            self._release(intent)
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
                    _hand_back_vram()
                    slot_id = f"{_SLOT_PREFIX}:{intent.value}"
                    self._claimed.discard(intent)
                    self._orch.evict(slot_id)
                    return True
                return False
        finally:
            self._intent_locks[intent].release()

    # ------------------------------------------------------------------

    def _release(self, intent: Intent) -> None:
        """Tell the orchestrator a generation finished, for a slot it holds."""
        if intent in self._claimed:
            self._orch.release(f"{_SLOT_PREFIX}:{intent.value}")

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
                logger.info(
                    "Unloaded %s to make room for %s (%d MB free, %d MB wanted)",
                    other.value, intent.value, free, vram_req,
                )

    @staticmethod
    def _wait_for_room(intent: Intent, vram_req: int) -> None:
        """Give the evictions asked for just now time to hand their memory back.

        A model another service was told to unload leaves the card a few
        seconds later (Ollama's runner, ComfyUI after a render). Loading in
        that gap fails with CUDA out of memory, where the same request a
        moment later works. The wait is a fixed number of looks, so it ends
        whatever the clock does; if the card is still short the load goes
        ahead as before and reports its own error.
        """
        import time

        for look in range(_ROOM_WAIT_LOOKS):
            free = _free_vram_mb()
            if free is None or free >= vram_req + _HEADROOM_MB:
                if look:
                    logger.info("%s has its room after %d s (%d MB free)", intent.value, look, free or 0)
                return
            if look == 0:
                logger.info(
                    "%s waits for evicted models to leave the card (%d MB free, %d MB wanted)",
                    intent.value, free, vram_req,
                )
            time.sleep(_ROOM_WAIT_POLL_S)
        logger.warning("%s loads with the card still short of %d MB", intent.value, vram_req)

    def _claim_card_again(self, intent: Intent, backend: AudioBackend) -> None:
        """For a loaded backend that needs the whole card (ACE-Step): ask for it
        again before running. A chat model loaded between two songs would
        otherwise leave this one out of memory. Voice and effects share."""
        if not getattr(backend, "requires_exclusive_vram", False) or not _cuda_available():
            return
        slot_id = f"{_SLOT_PREFIX}:{intent.value}"
        vram_req = max(backend.vram_mb_estimate, backend.last_vram_mb)
        self._claimed.add(intent)
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

    def wait_for_gpu(
        self,
        intent: Intent,
        *,
        progress_cb: Optional[Any] = None,
        cancel_event: Optional[Any] = None,
    ) -> None:
        """Wait while another job holds the GPU and this request would need it.

        A video render or a training run claims the card through the backend's
        job gate and lease; a model loaded beside it runs out of memory and
        stalls the render. Music (which needs the whole card) waits whenever
        another job holds it. Voice and effects wait only when they are cold
        and the card is too short for them; a loaded voice model keeps working.

        Raises GpuBusy after gpu_wait_s (a background job) or
        _GPU_WAIT_INLINE_S (an inline request), GenerationCancelled on cancel.
        Returns at once without CUDA, with the orchestrator off, or when the
        backend cannot say who holds the card.
        """
        import time

        with self._state_lock:
            backend = self._backends.get(intent)
        if backend is None or not _cuda_available() or not self._orch.enabled:
            return
        exclusive = bool(getattr(backend, "requires_exclusive_vram", False))
        if not exclusive and backend.is_loaded:
            return

        limit = self.gpu_wait_s if progress_cb is not None else min(self.gpu_wait_s, _GPU_WAIT_INLINE_S)
        started = time.monotonic()
        announced = False
        while True:
            holder = self._orch.gpu_holder()
            if holder is None:
                break
            if not exclusive:
                need = max(backend.vram_mb_estimate, backend.last_vram_mb) + _HEADROOM_MB
                free = _free_vram_mb()
                if free is not None and free >= need:
                    break
            if cancel_event is not None and cancel_event.is_set():
                from backends.base import GenerationCancelled
                raise GenerationCancelled(f"Cancelled while waiting for the GPU ({holder})")
            waited = time.monotonic() - started
            if waited >= limit:
                raise GpuBusy(
                    f"The GPU is held by {holder}; waited {int(waited)} s for it. "
                    "Try again when that job finishes."
                )
            if not announced:
                logger.info("%s waits for the GPU, held by %s", intent.value, holder)
                if progress_cb is not None:
                    try:
                        progress_cb(0, 0, "waiting_for_gpu")
                    except Exception:  # noqa: BLE001 - progress is best effort
                        pass
                announced = True
            if cancel_event is not None:
                cancel_event.wait(_GPU_WAIT_POLL_S)
            else:
                time.sleep(_GPU_WAIT_POLL_S)
        if announced:
            logger.info("%s got the GPU after %.0f s", intent.value, time.monotonic() - started)

    @staticmethod
    def _free_backend(backend: AudioBackend) -> None:
        """Best-effort: unload a backend and hand its cached blocks back."""
        try:
            backend.unload()
        except Exception as e:  # noqa: BLE001
            logger.warning("Unload of %s after out-of-memory failed: %s", backend.name, e)
        _hand_back_vram()

    def _drop_after_oom(self, intent: Intent, backend: AudioBackend) -> None:
        """Unload a backend whose generation ran out of memory and forget its slot.
        Runs with the intent lock held, so it does not go through unload()."""
        logger.warning("%s ran out of GPU memory; unloading it to free the card", intent.value)
        with self._state_lock:
            self._free_backend(backend)
            self._claimed.discard(intent)
            self._orch.evict(f"{_SLOT_PREFIX}:{intent.value}")

    def _load_with_orchestrator(self, intent: Intent, backend: AudioBackend) -> None:
        """Request VRAM, run load(), report load completion. Cleans up on failure."""
        slot_id = f"{_SLOT_PREFIX}:{intent.value}"
        if not _cuda_available():
            logger.info(
                "Cold backend for %s — no CUDA device, loading on the CPU with no VRAM request",
                intent.value,
            )
            backend.load()
            return
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
        self._claimed.add(intent)
        self._orch.request_vram(slot_id, vram_req, _DEFAULT_PRIORITY, exclusive=exclusive)
        self._make_room(intent, vram_req)
        self._wait_for_room(intent, vram_req)

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            backend.load()
        except Exception as e:
            # Don't leave a LOADING slot dangling in the orchestrator's registry.
            self._claimed.discard(intent)
            self._orch.evict(slot_id)
            if _is_oom(e):
                # A half-loaded model still holds what it allocated.
                self._free_backend(backend)
            raise

        self._orch.mark_loaded(slot_id)
        logger.info("Backend %s loaded; slot %s now LOADED", backend.name, slot_id)
