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
import time
from dataclasses import dataclass
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


def reclaim_in_process_vram(needed_mb: int = 0) -> int:
    """Release CUDA memory this process is holding. Returns MB freed. Never raises.

    The third resident. ``evict_ollama_models`` and ``free_comfyui_vram`` both talk
    to *other* processes over HTTP; models this backend loaded into its own CUDA
    context — a resident diffusers pipeline, the retrieval cross-encoder — answer to
    neither. Before this existed the admission path could ask two of the three
    residents to leave and then refuse the job because of the third: a 1.1GB
    cross-encoder against ~1.4GB of slack refused image batches by ~130MB, and the
    orchestrator's own reclaim (which does know how to unload it) sits behind the
    fit check that had already raised.
    """
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator_if_created
        orch = get_orchestrator_if_created()
        if orch is not None:
            return int(orch.reclaim_auxiliary_models() or 0)
        # No orchestrator in this process. These models load themselves without
        # asking one, so they can be resident even here; release them directly
        # rather than starting a sync thread to ask about them.
        freed = 0
        for module_path in (
            "backend.utils.reranker",
            "backend.utils.docling_loader",
            "backend.utils.faster_whisper_utils",
        ):
            try:
                import importlib
                unload = getattr(importlib.import_module(module_path), "unload", None)
                if unload is None:
                    continue
                result = unload()
                if isinstance(result, dict):
                    freed += int(result.get("freed_mb") or 0)
            except ImportError:
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("in-process reclaim of %s failed: %s", module_path, e)
        return freed
    except Exception as e:  # noqa: BLE001
        log.warning("in-process GPU reclaim failed (non-fatal): %s", e)
        return 0


def reclaim_gpu(
    *,
    evict_ollama: bool = False,
    free_comfyui: bool = False,
    in_process: bool = False,
    needed_mb: int = 0,
) -> None:
    """Run the requested VRAM reclaims before a render uses the card. Best-effort."""
    if evict_ollama:
        evict_ollama_models()
    if free_comfyui:
        free_comfyui_vram()
    if in_process:
        reclaim_in_process_vram(needed_mb)


# --- Orchestrator budget hooks (opt-in) --------------------------------------

def _orchestrator_request(
    slot_id: str, vram_estimate_mb: int, *, hard_fit: bool = False, vram_reserve_mb: int = 0
) -> None:
    """Book ``vram_estimate_mb`` for ``slot_id`` in the orchestrator ledger.

    The session has already decided fit, so the booking is not a second fit
    check (``hard_fit`` defaults to False). An orchestrator refusal surfaces as
    ``GpuBusyError``; any other failure is non-fatal.
    """
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        get_orchestrator().request_model(
            slot_id, vram_estimate_mb, priority=95, exclusive=False, hard_fit=hard_fit,
            vram_reserve_mb=vram_reserve_mb,
        )
    except RuntimeError as e:
        from backend.services.job_operation_gate import GpuBusyError
        log.warning("orchestrator refused %s: %s", slot_id, e)
        raise GpuBusyError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("orchestrator request_model(%s) failed (non-fatal): %s", slot_id, e)


def _orchestrator_release(slot_id: str) -> None:
    """Release the booking; video slots are dropped outright so tracked VRAM
    falls at once (the weights behind them belong to the generator)."""
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        orch = get_orchestrator()
        orch.release_model(slot_id)
        if "video" in slot_id.lower():
            orch.drop_booking(slot_id)
    except Exception as e:  # noqa: BLE001
        log.warning("orchestrator release_model(%s) failed (non-fatal): %s", slot_id, e)


@dataclass(frozen=True)
class Fit:
    """Outcome of one VRAM fit check (see ``fit_verdict``).

    ``capacity`` marks a refusal no eviction can fix: the estimate, or the
    estimate plus headroom, exceeds the card. ``headroom`` is True when free
    VRAM minus the reserve already covers the estimate plus margin, so there is
    nothing to reclaim; it is False when the probe is unavailable. ``reason``
    holds the refusal text with a ``{slot}`` placeholder.
    """

    ok: bool
    capacity: bool
    free_mb: int
    total_mb: int
    need_mb: int
    headroom: bool = False
    reason: str = ""


def fit_verdict(estimate_mb: int, *, reserve_mb: int = 0, margin_mb: int = 1024) -> Fit:
    """Decide whether ``estimate_mb`` fits the card right now — the one fit check.

    Probes physical free VRAM through the coordinator, which already counts
    ComfyUI and sidecar allocations the in-process registry cannot see. A
    failed or absent probe admits (advisory; never blocks). ``reserve_mb`` is
    treated as not free. A mostly idle card (at least 85% free) admits an
    estimate that fits the card minus the reserve even when the margin alone
    spills past the total — near-full-card video models depend on this.
    """
    estimate_mb = int(estimate_mb)
    margin_mb = int(margin_mb)
    reserve_mb = max(0, int(reserve_mb))
    need = estimate_mb + margin_mb
    try:
        from backend.services.gpu_resource_coordinator import get_gpu_coordinator
        info = get_gpu_coordinator().get_available_vram()
    except Exception as e:  # noqa: BLE001
        log.warning("VRAM fit-check probe failed (%s); admitting (advisory)", e)
        return Fit(ok=True, capacity=False, free_mb=0, total_mb=0, need_mb=need)
    if not info.get("success"):
        return Fit(ok=True, capacity=False, free_mb=0, total_mb=0, need_mb=need)
    free = int(info.get("available_mb") or 0)
    total = int(info.get("total_mb") or 0)
    free_eff = max(0, free - reserve_mb)
    # mostly_free is judged on raw free: it detects an idle card, which the
    # reserve cannot change.
    mostly_free = total > 0 and free >= int(total * 0.85)
    if free_eff >= need:
        return Fit(
            ok=True, capacity=False, free_mb=free, total_mb=total, need_mb=need,
            headroom=True,
        )
    capacity_reason = (
        f"Not enough free VRAM for {{slot}}: estimate exceeds GPU capacity "
        f"(~{need}MB needed = est {estimate_mb} + {margin_mb} headroom, "
        f"card total ~{total}MB, free ~{free}MB). Pick a lighter model "
        f"(e.g. Wan 2.2 5B on 16GB cards) or lower the estimate."
    )
    if total > 0 and estimate_mb > total:
        return Fit(
            ok=False, capacity=True, free_mb=free, total_mb=total, need_mb=need,
            reason=capacity_reason,
        )
    if mostly_free and estimate_mb <= total - reserve_mb:
        log.info(
            "VRAM fit-check: admitting est %s (fits card total %s minus %s "
            "reserve; need %s with margin; free %s mostly idle)",
            estimate_mb, total, reserve_mb, need, free,
        )
        return Fit(ok=True, capacity=False, free_mb=free, total_mb=total, need_mb=need)
    # NOT capacity. Reaching here means the estimate itself fits the card, the
    # card is not mostly idle, and only estimate+margin overflows — which is a
    # statement about who is resident right now, not about the card. Once they
    # leave, `mostly_free` above admits the identical job. Marking it capacity
    # made it terminal, and callers discard their whole retry deadline on a
    # capacity refusal: a 16000MB video model on a 16376MB card hit this on every
    # attempt and was told to "pick a lighter model" while merely waiting would
    # have worked. `_reclaim_needed` already draws the line in the right place
    # (only estimate-alone overflow is final); this is the other call site
    # agreeing with it.
    _reserve_note = f" − {reserve_mb} compositor reserve" if reserve_mb else ""
    resident_reason = (
        f"Not enough free VRAM for {{slot}}: need ~{need}MB (est {estimate_mb} + "
        f"{margin_mb} headroom), only {free_eff}MB usable ({free}MB free"
        f"{_reserve_note}) after eviction — another model/render may be "
        f"resident. Try again shortly."
    )
    return Fit(
        ok=False, capacity=False, free_mb=free, total_mb=total, need_mb=need,
        reason=resident_reason,
    )


def _raise_unless_fits(fit: Fit, slot: str) -> None:
    """Raise the typed refusal for ``fit``: ``GpuCapacityError`` when no eviction
    can help, ``GpuBusyError`` when another resident may leave."""
    if fit.ok:
        return
    from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
    text = fit.reason.format(slot=slot)
    raise (GpuCapacityError if fit.capacity else GpuBusyError)(text)


def _ensure_fits_or_busy(
    estimate_mb: int, slot: str, *, margin_mb: int = 1024, reserve_mb: int = 0
) -> None:
    """Raise unless ``estimate_mb`` fits the card (``fit_verdict`` + ``_raise_unless_fits``)."""
    _raise_unless_fits(fit_verdict(estimate_mb, reserve_mb=reserve_mb, margin_mb=margin_mb), slot)


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


# ComfyUI's /free is asynchronous; the fit probe waits this long after a reclaim
# so the driver reports the freed VRAM.
_RECLAIM_SETTLE_S = 3.0


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


def _default_lease_seconds(kind) -> int:
    """Lease length by job kind; the heartbeat renews it, so this only bounds a
    holder that dies without releasing."""
    name = str(getattr(kind, "value", kind)).lower()
    if "train" in name:
        return 4 * 3600
    if "video" in name:
        return 3600
    return 900


def _start_lease_heartbeat(slot: str, lease_seconds: int) -> "threading.Event":
    """Renew the cross-process lease every lease/3 until the returned event is set."""
    import threading
    stop = threading.Event()
    interval = max(60.0, lease_seconds / 3.0)

    def _beat():
        try:
            from backend.services.gpu_resource_coordinator import get_gpu_coordinator
            coord = get_gpu_coordinator()
        except Exception:  # noqa: BLE001
            return
        while not stop.wait(interval):
            try:
                if not coord.renew_generic(slot, lease_seconds=lease_seconds):
                    log.warning("gpu lease heartbeat for %s: lock no longer ours; stopping", slot)
                    return
            except Exception as e:  # noqa: BLE001
                log.warning("gpu lease heartbeat for %s failed: %s", slot, e)

    threading.Thread(target=_beat, name=f"gpu-lease-heartbeat:{slot}", daemon=True).start()
    return stop


def _reclaim_needed(estimate_mb: int, *, reserve_mb: int = 0, margin_mb: int = 1024) -> bool:
    """Decide whether evicting residents can help before doing it.

    Returns False when the estimate already fits with headroom (nothing to
    reclaim) and raises ``GpuCapacityError`` when the estimate alone exceeds
    the card — in both cases the resident chat model survives. An unavailable
    probe returns True.

    Note what this does NOT cover: a job admitted by ``fit_verdict``'s mostly-idle
    escape hatch still reclaims, because there `free_eff < need` and freeing the
    remaining residents is what keeps it from OOMing. `headroom` is deliberately
    not set on that path. For any model whose estimate+margin exceeds the card
    (the 14000/16000MB video models) `headroom` is arithmetically unreachable, so
    those callers always reclaim — that is intended, not an oversight.
    """
    fit = fit_verdict(estimate_mb, reserve_mb=reserve_mb, margin_mb=margin_mb)
    # Only the estimate-alone overflow is final before eviction; a margin
    # overflow may still clear once residents leave and the card is mostly idle.
    if fit.capacity and fit.total_mb > 0 and int(estimate_mb) > fit.total_mb:
        _raise_unless_fits(fit, "preflight")
    return not fit.headroom


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
    fail-fast ``GpuBusyError`` — and additionally, once the slot is actually held:
      * acquires the cross-process lease (opt-in), then
      * runs ``reclaim_gpu(evict_ollama, free_comfyui)`` (evict only after we win), then
      * refuses with a typed ``GpuBusyError``/``GpuCapacityError`` when ``require_fit``
        and the estimate still does not fit, then
      * admits against system load and books the GPUMemoryOrchestrator budget when
        ``vram_estimate_mb`` is given, releasing everything on exit.

    A refusal raised after the claim releases the gate without its post-release
    cooldown: nothing touched the card. Teardown runs in reverse order and before
    the gate release, so ComfyUI's /free and the lease release precede it.

    With all defaults this is a pure pass-through to the gate (no eviction, no budget),
    so adopting it in one caller never changes another's behavior. Yields the gate's
    acquired bool (False only in the degraded ``on_busy='register'`` path).
    """
    # Reentrancy: a same-thread nested gpu_session is a pass-through — the outer call owns
    # the gate, the cross-process lease, the eviction and the cooldown. Prevents self-
    # deadlock if enforcement ever lives inside a generator a wrapped caller also wraps.
    if getattr(_session_tls, "held", False):
        log.debug("gpu_session(%s) reentrant pass-through", op_id)
        yield True
        return

    from backend.services.job_operation_gate import GpuBusyError, get_gate

    gate = get_gate()
    _slot = slot_id or f"{getattr(kind, 'value', kind)}:{op_id}"
    acquired = False
    lease_held = False
    heartbeat_stop = None
    load_weight = None
    booked = False

    def _teardown() -> None:
        # Reverse of the acquisition order, with two deviations that exist because
        # a BaseException — Ctrl-C, SystemExit, a Celery revoke, gevent's
        # GreenletExit — can arrive between any two statements here, and every
        # inner guard below only catches Exception.
        #
        #   1. The heartbeat is stopped FIRST. It renews the cross-process lease
        #      every lease/3 seconds, so a heartbeat that outlives its teardown
        #      does not merely delay the release, it holds the lease for the life
        #      of the process: acquire_generic's stale-PID and expired-lease
        #      sweeps both decline while the PID is alive and the lease keeps
        #      moving. Nothing recovers it.
        #   2. The lease release is in a finally. It was last, behind an
        #      orchestrator release and a ComfyUI /free with a 15s timeout — a
        #      wide window in which an interrupt would strand the on-disk lock for
        #      its full term (3600s video, 14400s training), refusing every other
        #      process with "GPU is held by another process".
        nonlocal load_weight, heartbeat_stop, lease_held, booked
        if heartbeat_stop is not None:
            heartbeat_stop.set()
            heartbeat_stop = None
        try:
            _load_release(load_weight)
            load_weight = None
            if booked:
                _orchestrator_release(_slot)
                if "video" in _slot.lower():
                    try:
                        free_comfyui_vram()
                    except Exception:  # noqa: BLE001
                        pass
                booked = False
        finally:
            if lease_held:
                _release_cross_process_lease(_slot)
                lease_held = False

    with gate.gpu_exclusive(
        kind, op_id, on_busy=on_busy, wait_timeout=wait_timeout
    ) as acq:
        acquired = acq
        if acquired:
            _session_tls.held = True
            try:
                # Cross-process lease: after the in-PID gate (lock ordering) and
                # before any eviction — only evict once both locks are ours.
                if cross_process:
                    lease_len = int(lease_seconds or _default_lease_seconds(kind))
                    lease_held = _acquire_cross_process_lease(
                        _slot, lease_seconds=lease_len
                    )
                    if lease_held:
                        heartbeat_stop = _start_lease_heartbeat(_slot, lease_len)
                # Evict residents only when the estimate does not already fit and
                # the card could hold it at all; otherwise the refusal would have
                # cost the user their chat model for nothing.
                # In-process reclaim is NOT gated on the evict_ollama/free_comfyui
                # flags: those name other processes, and a caller that asked for
                # neither still cannot afford to be refused over memory this very
                # process is sitting on. It runs only behind _reclaim_needed, so a
                # job that already fits never costs anyone their resident model.
                if require_fit and vram_estimate_mb:
                    if _reclaim_needed(vram_estimate_mb, reserve_mb=vram_reserve_mb):
                        reclaim_gpu(
                            evict_ollama=evict_ollama,
                            free_comfyui=free_comfyui,
                            in_process=True,
                            needed_mb=vram_estimate_mb,
                        )
                        if free_comfyui:
                            time.sleep(_RECLAIM_SETTLE_S)
                    else:
                        log.info("gpu_session(%s): %d MB already fits; skipping eviction", op_id, vram_estimate_mb)
                elif evict_ollama or free_comfyui:
                    reclaim_gpu(evict_ollama=evict_ollama, free_comfyui=free_comfyui)
                if require_fit and vram_estimate_mb:
                    _raise_unless_fits(
                        fit_verdict(vram_estimate_mb, reserve_mb=vram_reserve_mb), _slot
                    )
                # RAM/swap/loadavg admission for heavy/budgeted jobs only, so
                # estimate-less callers stay a pure gate pass-through.
                admit_ram_gb = ram_estimate_gb if ram_estimate_gb is not None else (
                    2.0 if vram_estimate_mb else None
                )
                if admit_ram_gb is not None:
                    load_weight = _load_admit_or_busy(_slot, ram_gb=admit_ram_gb)
                if vram_estimate_mb:
                    _orchestrator_request(
                        _slot, vram_estimate_mb, vram_reserve_mb=vram_reserve_mb
                    )
                    booked = True
            except GpuBusyError:
                _session_tls.held = False
                _teardown()
                gate.release_gpu_exclusive(kind, op_id, cooldown=False)
                raise
            except BaseException:
                _session_tls.held = False
                _teardown()
                raise
        try:
            yield acquired
            # Clean exit: LOADING -> LOADED so the orchestrator's tracked VRAM
            # and eviction scoring stay accurate.
            if acquired and vram_estimate_mb:
                try:
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().mark_model_loaded(_slot)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if acquired:
                _session_tls.held = False
                _teardown()
