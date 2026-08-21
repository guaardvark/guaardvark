"""GpuResourcePolicy — one front door composing the existing GPU-coordination layers.

Design: docs/local-workspace-only/GPU_RESOURCE_POLICY_DESIGN.md

The repo grew FOUR decoupled GPU-coordination layers — JobOperationGate (exclusivity),
GPUMemoryOrchestrator (VRAM-MB budget), GPUResourceCoordinator (cross-process file lock),
GlobalLoadGate (RAM admission) — plus scattered ad-hoc VRAM-reclaim hacks (ComfyUI /free,
4 copies of Ollama keep_alive=0 eviction). Because the gate does no VRAM math and the
orchestrator isn't called by jobs, a gate holder eats ~14GB the orchestrator never debits
and both can believe they own the 16GB card.

This module does NOT replace those layers — it COMPOSES them so exclusivity and VRAM
reclaim/budget become ONE operation. It is strictly ADDITIVE: existing
``gate.gpu_exclusive`` callers keep working untouched; new/critical paths opt into
``gpu_session(...)``. The VRAM-budget debit is an OPT-IN param (off by default) so adopting
this in one caller never changes another's behavior.

Invariants preserved (see design doc): the gate's 8s release cooldown + fail-fast
``GpuBusyError`` (we delegate straight to ``gpu_exclusive``), the lock-ordering rule, and
the ``plugin_runner`` CUDA-fork sidecar (this module spawns no processes). Reclaim runs
only AFTER the slot is claimed — we never evict on behalf of a job that lost the slot.
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Iterator, Optional

log = logging.getLogger(__name__)

try:
    from backend.config import COMFYUI_URL
except Exception:
    COMFYUI_URL = "http://127.0.0.1:8188"


def compositor_vram_reserve_mb() -> int:
    """VRAM held back for the desktop compositor on fit checks (opt-in per caller).

    2026-08-04 client box 2048² incident: gnome-shell/Xwayland hold ~600-800MB of the
    card; admitting jobs against raw totals starves the compositor and kills the
    Wayland session. OPT-IN by call site (default reserve stays 0) because the
    video path legitimately admits near-full-card estimates (LTX/Cog ~16000MB on
    16376MB) and ComfyUI already self-guards via --reserve-vram.
    """
    try:
        return max(0, int(os.environ.get("GUAARDVARK_COMPOSITOR_VRAM_RESERVE_MB", "800")))
    except ValueError:
        return 800


# --- Canonical VRAM reclaim (consolidates the scattered ad-hoc hacks) ---------

def free_comfyui_vram(*, timeout: float = 15.0) -> bool:
    """Unload ComfyUI's resident models (POST /free). Best-effort; never raises.

    Canonical home for the FLUX→i2v eviction the i2v custom nodes need — they move
    models onto CUDA without asking ComfyUI to evict first, so a ~10GB FLUX stays
    resident and the animator OOMs. Was inlined in music_video_tasks; centralized so
    every image→video handoff can reuse the one implementation. Returns True on a
    successful POST, False if ComfyUI was unreachable (non-fatal either way).
    """
    import requests
    try:
        requests.post(
            f"{COMFYUI_URL}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=timeout,
        )
        log.info("comfyui VRAM freed")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("comfyui /free failed (non-fatal): %s", e)
        return False


def evict_ollama_models() -> bool:
    """Evict Ollama's resident models so a render gets the card. Best-effort.

    Delegates to the proven ``GPUResourceCoordinator.unload_ollama_models`` (keep_alive=0
    with num_ctx=1 to avoid a large KV-cache alloc during unload) — one canonical call
    meant to converge the 4 ad-hoc copies (bark / unified_chat_engine / coordinator /
    orchestrator). Never raises.
    """
    try:
        from backend.services.gpu_resource_coordinator import unload_ollama_models as _unload
        _unload()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ollama eviction failed (non-fatal): %s", e)
        return False


def reclaim_gpu(*, evict_ollama: bool = False, free_comfyui: bool = False) -> None:
    """Run the requested VRAM reclaims before a render uses the card. Best-effort."""
    if evict_ollama:
        evict_ollama_models()
    if free_comfyui:
        free_comfyui_vram()


# --- Orchestrator budget hooks (opt-in) --------------------------------------

def _orchestrator_request(
    slot_id: str, vram_estimate_mb: int, *, hard_fit: bool = True, vram_reserve_mb: int = 0
) -> None:
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        get_orchestrator().request_model(
            slot_id, vram_estimate_mb, priority=95, exclusive=False, hard_fit=hard_fit,
            vram_reserve_mb=vram_reserve_mb,
        )
    except RuntimeError as e:
        # Hard-fit refuse → surface as GpuBusyError so callers can retry cleanly
        from backend.services.job_operation_gate import GpuBusyError
        log.warning("orchestrator refused %s: %s", slot_id, e)
        raise GpuBusyError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("orchestrator request_model(%s) failed (non-fatal): %s", slot_id, e)


def _orchestrator_release(slot_id: str) -> None:
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        get_orchestrator().release_model(slot_id)
    except Exception as e:  # noqa: BLE001
        log.warning("orchestrator release_model(%s) failed (non-fatal): %s", slot_id, e)


def _ensure_fits_or_busy(
    estimate_mb: int, slot: str, *, margin_mb: int = 1024, reserve_mb: int = 0
) -> None:
    """After eviction, re-probe PHYSICAL free VRAM and fail fast if it still won't fit.

    The physical probe (pynvml/nvidia-smi via the coordinator) already includes ComfyUI +
    plugin-sidecar allocations that the in-process registry can't see — so admitting
    against it inherently accounts for every consumer. If estimate + headroom won't fit,
    raise GpuBusyError so the caller gets a clean 'busy, retry' instead of a CUDA OOM or a
    hung allocation. Probe-unavailable (CPU-only host / no driver) admits — never blocks.

    Message distinguishes two cases:
      * estimate alone > total card VRAM → estimate exceeds GPU capacity
      * card has free space but still short → another consumer may be resident

    Margin must not invent capacity overflow: when the estimate itself fits the card
    (`estimate_mb <= total`) but `estimate + margin` spills over total, and the GPU is
    mostly free (≥85%), admit. That unblocks near-full-card models (LTX/Cog @ ~16GB
    estimate on a ~16376MB card) that already render successfully via ComfyUI.
    """
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator
        info = get_gpu_coordinator().get_available_vram()
    except Exception as e:  # noqa: BLE001
        log.warning("VRAM fit-check probe failed (%s); admitting (advisory)", e)
        return
    if not info.get("success"):
        return  # no usable GPU probe — do not block
    free = int(info.get("available_mb") or 0)
    total = int(info.get("total_mb") or 0)
    estimate_mb = int(estimate_mb)
    margin_mb = int(margin_mb)
    # Compositor reserve (opt-in, 2026-08-04): treat reserved MB as not free —
    # the desktop's share of the card must survive the job. mostly_free stays
    # computed on RAW free (it detects "card is idle", which reserve can't change).
    reserve_mb = max(0, int(reserve_mb))
    free_eff = max(0, free - reserve_mb)
    need = estimate_mb + margin_mb
    mostly_free = total > 0 and free >= int(total * 0.85)
    if free_eff >= need:
        return
    # Honest capacity refuse: the estimate itself does not fit the card.
    if total > 0 and estimate_mb > total:
        from backend.services.job_operation_gate import GpuBusyError
        raise GpuBusyError(
            f"Not enough free VRAM for {slot}: estimate exceeds GPU capacity "
            f"(~{need}MB needed = est {estimate_mb} + {margin_mb} headroom, "
            f"card total ~{total}MB, free ~{free}MB). Pick a lighter model "
            f"(e.g. Wan 2.2 5B on 16GB cards) or lower the estimate."
        )
    # Margin / headroom must not invent a refuse when the estimate fits the card
    # and the GPU is mostly free (≥85%). Covers:
    #   * estimate+margin > total (false "capacity overflow" on ~16GB cards)
    #   * estimate+margin <= total but free is a few GB short (ComfyUI base ~2GB
    #     resident) — proven renderable via the direct generator path.
    if mostly_free and estimate_mb <= total - reserve_mb:
        log.info(
            "VRAM fit-check: admitting %s (est %s fits card total %s minus "
            "%s reserve; need %s with margin; free %s mostly idle — not "
            "inventing a refuse)",
            slot, estimate_mb, total, reserve_mb, need, free,
        )
        return
    from backend.services.job_operation_gate import GpuBusyError
    if total > 0 and need > total:
        raise GpuBusyError(
            f"Not enough free VRAM for {slot}: estimate exceeds GPU capacity "
            f"(~{need}MB needed = est {estimate_mb} + {margin_mb} headroom, "
            f"card total ~{total}MB, free ~{free}MB). Pick a lighter model "
            f"(e.g. Wan 2.2 5B on 16GB cards) or lower the estimate."
        )
    # Free short and card is NOT mostly idle → something else is resident.
    _reserve_note = f" − {reserve_mb} compositor reserve" if reserve_mb else ""
    raise GpuBusyError(
        f"Not enough free VRAM for {slot}: need ~{need}MB (est {estimate_mb} + "
        f"{margin_mb} headroom), only {free_eff}MB usable ({free}MB free"
        f"{_reserve_note}) after eviction — another model/render may be "
        f"resident. Try again shortly."
    )


def is_capacity_overflow_error(exc: BaseException) -> bool:
    """True when GpuBusyError means the estimate can never fit this card."""
    msg = str(exc) or ""
    return "estimate exceeds GPU capacity" in msg


def vram_probe_snapshot(*, margin_mb: int = 1024, reserve_mb: int = 0) -> dict:
    """Best-effort free/need snapshot for UI gpu_wait messaging. Never raises."""
    out = {"free_mb": None, "total_mb": None, "util_pct": None, "success": False}
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator
        info = get_gpu_coordinator().get_available_vram()
        if not info.get("success"):
            return out
        free = int(info.get("available_mb") or 0)
        total = int(info.get("total_mb") or 0)
        reserve_mb = max(0, int(reserve_mb))
        out.update({
            "success": True,
            "free_mb": free,
            "free_eff_mb": max(0, free - reserve_mb),
            "total_mb": total,
            "util_pct": float(info.get("utilization_percent") or 0),
            "margin_mb": int(margin_mb),
            "reserve_mb": reserve_mb,
        })
    except Exception as e:  # noqa: BLE001
        log.debug("vram_probe_snapshot failed: %s", e)
    return out


def reclaim_and_settle(*, evict_ollama: bool = True, free_comfyui: bool = True, settle_s: float = 3.0) -> dict:
    """Unload residents, then sleep so the driver reports freed VRAM before re-admit."""
    reclaim_gpu(evict_ollama=evict_ollama, free_comfyui=free_comfyui)
    settle = max(0.0, float(settle_s))
    if settle:
        import time as _t
        _t.sleep(settle)
    return vram_probe_snapshot()


import threading as _threading

# Per-thread reentrancy flag: set while THIS thread holds a gpu_session slot, so a nested
# gpu_session on the same thread becomes a pass-through instead of dead-locking on the
# (process-wide) in-PID gate or double-acquiring the cross-process lease.
_session_tls = _threading.local()


@contextlib.contextmanager
def adopt_gpu_session() -> Iterator[None]:
    """Mark THIS thread as covered by a gpu_session held by ANOTHER thread.

    The reentrancy flag above is thread-local, so when a holder fans work out to
    worker threads (e.g. BatchImageGenerator's batch-level session + ThreadPool
    workers), any nested gpu_session in the worker would try to claim the gate
    the batch already holds and fail with GpuBusyError. Wrap the worker's unit of
    work in this to make nested sessions pass through, exactly as they would on
    the holder's own thread. Only use when the owning session provably outlives
    the wrapped work (the batch body runs inside the owning ``with`` block).
    """
    prev = getattr(_session_tls, "held", False)
    _session_tls.held = True
    try:
        yield
    finally:
        _session_tls.held = prev


def _acquire_cross_process_lease(slot: str, *, lease_seconds: Optional[int] = None) -> bool:
    """Acquire the cross-process GPU file lock AFTER the in-PID gate (lock ordering) and
    BEFORE eviction. Raise GpuBusyError if ANOTHER process holds it (the in-PID gate has
    already serialized same-process work). Returns True if acquired; False if the
    coordinator is unavailable (degrade to in-process-only rather than block)."""
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator
        coord = get_gpu_coordinator()
    except Exception as e:  # noqa: BLE001
        log.warning("cross-process GPU lease unavailable (%s); proceeding in-process only", e)
        return False
    res = coord.acquire_generic(slot, lease_seconds=lease_seconds)
    if res.get("success"):
        return True
    from backend.services.job_operation_gate import GpuBusyError
    raise GpuBusyError(f"GPU is held by another process ({res.get('error', 'busy')}).")


def _release_cross_process_lease(slot: str) -> None:
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator
        get_gpu_coordinator().release_generic(slot)
    except Exception as e:  # noqa: BLE001
        log.warning("cross-process GPU lease release failed (%s)", e)


def _load_admit_or_busy(slot: str, *, ram_gb: float = 2.0):
    """RAM/swap/loadavg admission via the (built-but-previously-unwired) GlobalLoadGate.
    The in-PID GPU slot is already held — lock order (gate FIRST, then this), per the
    gate's own docstring. Single FAIL-FAST check (timeout=0): refuse heavy work with a
    clean GpuBusyError when the box has no system-RAM/swap headroom, rather than pile on
    and drive it into swap-death. VRAM is the gate + _ensure_fits's job, so vram_gb=0 here
    — this guards system load only. Fail-OPEN (return None, proceed) if the gate/probe is
    unavailable. Returns the reserved JobWeight (release it on exit) or None."""
    try:
        from backend.services.system_load_gate import get_load_gate, JobWeight, LoadGateTimeout
    except Exception:  # gate module / psutil unavailable -> fail open
        return None
    weight = JobWeight(ram_gb=float(ram_gb), vram_gb=0.0, cpu_cores=1.0)
    try:
        get_load_gate().admit(weight, timeout=0.0)
        return weight
    except LoadGateTimeout as e:
        from backend.services.job_operation_gate import GpuBusyError
        raise GpuBusyError(f"System under heavy load — {e}")
    except Exception as e:  # noqa: BLE001 - probe unavailable -> fail open, never block gen
        log.warning("GlobalLoadGate admit unavailable (%s); proceeding", e)
        return None


def _load_release(weight) -> None:
    if weight is None:
        return
    try:
        from backend.services.system_load_gate import get_load_gate
        get_load_gate().release(weight)
    except Exception as e:  # noqa: BLE001
        log.warning("GlobalLoadGate release failed (%s)", e)


# --- The front door -----------------------------------------------------------

@contextlib.contextmanager
def gpu_session(
    kind,
    op_id: str,
    *,
    on_busy: str = "raise",
    wait_timeout: float = 120.0,
    evict_ollama: bool = False,
    free_comfyui: bool = False,
    vram_estimate_mb: Optional[int] = None,
    ram_estimate_gb: Optional[float] = None,
    require_fit: bool = False,
    cross_process: bool = False,
    slot_id: Optional[str] = None,
    lease_seconds: Optional[int] = None,
    vram_reserve_mb: int = 0,
) -> Iterator[bool]:
    """Claim the GPU for a unit of work — exclusivity + VRAM reclaim/budget in one place.

    Wraps ``JobOperationGate.gpu_exclusive(kind, op_id, on_busy)`` — preserving its
    fail-fast ``GpuBusyError`` and 8s post-release cooldown EXACTLY — and additionally,
    once the slot is actually held:
      * runs ``reclaim_gpu(evict_ollama, free_comfyui)`` (evict only after we win), and
      * optionally debits the GPUMemoryOrchestrator budget when ``vram_estimate_mb`` is
        given (makes 'exclusive' and 'VRAM-budgeted' the same fact), releasing on exit.

    With all defaults this is a pure pass-through to the gate (no eviction, no budget),
    so adopting it in one caller never changes another's behavior. Yields the gate's
    acquired bool (False only in the degraded ``on_busy='register'`` path).
    """
    # Reentrancy: a same-thread nested gpu_session is a pass-through — the outer call owns
    # the gate, the cross-process lease, the eviction and the 8s cooldown. Prevents self-
    # deadlock if enforcement ever lives inside a generator a wrapped caller also wraps.
    if getattr(_session_tls, "held", False):
        log.debug("gpu_session(%s) reentrant pass-through", op_id)
        yield True
        return

    from backend.services.job_operation_gate import get_gate

    gate = get_gate()
    _slot = slot_id or f"{getattr(kind, 'value', kind)}:{op_id}"
    acquired = False
    lease_held = False
    load_weight = None
    try:
        with gate.gpu_exclusive(
            kind, op_id, on_busy=on_busy, wait_timeout=wait_timeout
        ) as acq:
            acquired = acq
            if acquired:
                _session_tls.held = True
                # Cross-process lease (opt-in): acquire AFTER the in-PID gate (lock
                # ordering), BEFORE eviction — only evict once we own both locks.
                if cross_process:
                    lease_held = _acquire_cross_process_lease(
                        _slot, lease_seconds=lease_seconds
                    )
                reclaim_gpu(evict_ollama=evict_ollama, free_comfyui=free_comfyui)
                # Strict admission (opt-in): after eviction, refuse with a clean "busy" if
                # the estimate still won't physically fit — turns a CUDA OOM/hang into retry.
                if require_fit and vram_estimate_mb:
                    _ensure_fits_or_busy(
                        vram_estimate_mb, _slot, reserve_mb=vram_reserve_mb
                    )
                admit_ram_gb = ram_estimate_gb if ram_estimate_gb is not None else (
                    2.0 if vram_estimate_mb else None
                )
                if admit_ram_gb is not None:
                    # RAM/swap/loadavg admission (GlobalLoadGate) — heavy/budgeted jobs
                    # only, so default (estimate-less) callers stay a pure gate pass-
                    # through. Fail-fast (won't hang), fail-open (won't block on a probe
                    # error). Serialize-don't-thrash WITHOUT touching output quality.
                    load_weight = _load_admit_or_busy(_slot, ram_gb=admit_ram_gb)
                if vram_estimate_mb:
                    _orchestrator_request(
                        _slot, vram_estimate_mb, vram_reserve_mb=vram_reserve_mb
                    )
            yield acquired
            # Success path for the unit of work: transition LOADING -> LOADED so
            # the orchestrator's tracked_vram and eviction scoring are accurate.
            # Particularly important for high-estimate VIDEO_RENDER slots used by
            # music-video / film-crew (the main ~14GB consumers). Without this,
            # slots linger as LOADING and inflate tracked / prevent proper idle
            # eviction (vram specialist rec).
            if acquired and vram_estimate_mb:
                try:
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().mark_model_loaded(_slot)
                except Exception:
                    pass  # best-effort; release below will still run
    finally:
        if acquired:
            _session_tls.held = False
        _load_release(load_weight)
        if lease_held:
            _release_cross_process_lease(_slot)
        if acquired and vram_estimate_mb:
            _orchestrator_release(_slot)

            # Video-slot release: ask ComfyUI to drop resident models and forget the
            # session's booking so tracked_vram falls immediately. Only the booking
            # is dropped — unloading in-process pipelines is the generator's job
            # (force_evict would route a keep_pipeline image pipeline through the
            # video teardown path).
            slot_lower = _slot.lower()
            if "video" in slot_lower or "video_render" in slot_lower:
                try:
                    free_comfyui_vram()
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().drop_booking(_slot)
                    log.info(f"Released video slot {_slot}: ComfyUI /free sent, booking dropped")
                except Exception:
                    pass
