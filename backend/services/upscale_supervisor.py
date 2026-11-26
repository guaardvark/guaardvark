"""Upscale jobs take their turn on the GPU like every other render.

The upscaling service runs its jobs in its own process, out of reach of the
backend's job gate, cross-process lease and VRAM booking: an upscale started in
the middle of a render, and whichever allocated last ran out of memory. Every
upscale the backend submits now comes through here. Queued jobs (videos, image
batches) run one at a time, each inside a ``gpu_session`` held for as long as
the service works on it, with a progress record on Activity whose cancel reaches
the service. A single image or a preview takes a turn on the GPU around its
request instead of queueing.

``GUAARDVARK_UPSCALE_GPU_SESSION=0`` sends requests straight to the service, as
before.
"""
from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)

ENV = "GUAARDVARK_UPSCALE_GPU_SESSION"
GPU_WAIT_ENV = "GUAARDVARK_UPSCALE_GPU_WAIT_S"
UPSCALING_URL = "http://localhost:8202"

# A queued job waits for the GPU as long as a render can take; a single image
# or a preview has a person waiting on the page, so it gives up sooner and
# says who holds the card.
DEFAULT_GPU_WAIT_S = 3600.0
SINGLE_GPU_WAIT_S = 60.0
# Booked when the service cannot say what a model needs: the largest measured
# figure in its model registry (plugins/upscaling/service/model_manager.py).
FALLBACK_VRAM_MB = 2200

_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_HISTORY = 50


def enabled() -> bool:
    return os.environ.get(ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def _gpu_wait_s() -> float:
    try:
        return max(0.0, float(os.environ.get(GPU_WAIT_ENV, DEFAULT_GPU_WAIT_S)))
    except ValueError:
        return DEFAULT_GPU_WAIT_S


class UpscaleGpuBusy(Exception):
    """The GPU did not come free for a single image or preview in time."""


def _auth_headers() -> dict:
    from backend.utils.sidecar_guard import read_internal_token

    token = read_internal_token("upscaling")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _service_get(path: str, timeout: float = 10.0):
    resp = requests.get(f"{UPSCALING_URL}{path}", headers=_auth_headers(), timeout=timeout)
    return resp.json(), resp.status_code


def _service_up() -> bool:
    try:
        return requests.get(f"{UPSCALING_URL}/health", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def _ensure_service() -> Optional[str]:
    """Start the service if it is down. Returns why it is not up, or None."""
    from backend.services.plugin_bridge import start_for_job

    ok, why = start_for_job("upscale", "generating", is_up=_service_up)
    return None if ok else (why or "the upscaling service is not running")


@dataclass
class UpscaleJob:
    id: str
    kind: str                 # "video" or "image_batch"
    path: str                 # the service route that runs it
    payload: Dict[str, Any]
    model: str
    vram_mb: int
    label: str
    status: str = "pending"
    message: str = "Queued"
    error: Optional[str] = None
    service_job_id: Optional[str] = None
    service_job: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)
    on_complete: Optional[Callable[["UpscaleJob"], None]] = None

    def to_dict(self) -> Dict[str, Any]:
        """The shape the Upscaling page reads (the service's job dict), keyed by this job's id."""
        base = {
            "kind": self.kind,
            "item_count": len(self.payload.get("inputs") or []) or 1,
            "outputs": [],
            "input_path": self.payload.get("input_path") or (self.payload.get("inputs") or [None])[0],
            "output_path": self.payload.get("output_path") or self.payload.get("output_dir"),
            "model": self.model,
            "progress": 0.0,
            "fps": 0.0,
            "eta_seconds": None,
            "frames_done": 0,
            "frames_total": 0,
            "created_at": self.created_at,
            "started_at": None,
            "completed_at": None,
        }
        base.update(self.service_job or {})
        base.update({
            "job_id": self.id,
            "status": self.status if self.status != "waiting" else "pending",
            "message": self.message,
            "error": self.error or (self.service_job or {}).get("error"),
        })
        return base


class UpscaleSupervisor:
    def __init__(self) -> None:
        self._jobs: "OrderedDict[str, UpscaleJob]" = OrderedDict()
        self._queue: "queue.Queue[UpscaleJob]" = queue.Queue()
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # ---- what a job needs ----------------------------------------------------

    def _models(self) -> Dict[str, Any]:
        data, status = _service_get("/models")
        return data if status == 200 and isinstance(data, dict) else {}

    def resolve(self, model: Optional[str]) -> tuple[Optional[str], int, Optional[str]]:
        """(model the service will use, VRAM to book, refusal) for a request.

        A request that names no model gets the service's ``request_default``.
        The refusal names a model that is not installed, as the service would.
        """
        try:
            models = self._models()
        except (requests.RequestException, ValueError):
            models = {}
        downloaded = {m.get("name"): m for m in models.get("downloaded") or []}
        name = model
        if not name:
            default = models.get("default_model")
            # The second rule is the service's own, for one that does not report its pick.
            name = models.get("request_default") or (
                default if default in downloaded else next(iter(downloaded), default))
        if models and name not in downloaded:
            return name, FALLBACK_VRAM_MB, (
                f"The upscaling model {name} is not installed. Install it on the Upscaling page."
            )
        entry = downloaded.get(name) or {}
        return name, int(entry.get("vram_mb") or FALLBACK_VRAM_MB), None

    # ---- queued jobs -----------------------------------------------------------

    def submit(self, kind: str, path: str, payload: Dict[str, Any], label: str,
               on_complete: Optional[Callable[[UpscaleJob], None]] = None) -> tuple[Optional[dict], Optional[str], int]:
        """Queue a job. Returns (job dict, None, 200) or (None, refusal, status).

        ``on_complete`` runs on the worker thread once the job has completed.
        """
        down = _ensure_service()
        if down:
            return None, f"Upscaling is not available: {down}", 503
        model, vram_mb, refusal = self.resolve(payload.get("model"))
        if refusal:
            return None, refusal, 409
        inputs = payload.get("inputs") or [payload.get("input_path")]
        missing = [p for p in inputs if not p or not os.path.isfile(p)]
        if missing:
            return None, f"Input file not found: {missing[0]}", 400

        job = UpscaleJob(
            id=f"up_{uuid.uuid4().hex[:12]}", kind=kind, path=path,
            payload={**payload, "model": model}, model=model, vram_mb=vram_mb, label=label,
            on_complete=on_complete,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._trim_locked()
        self._progress_create(job)
        self._queue.put(job)
        self._start_worker()
        return job.to_dict(), None, 200

    def _start_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._loop, daemon=True, name="upscale-supervisor")
            self._worker.start()

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._run(job)
            except Exception as e:  # noqa: BLE001 — one job must not end the queue
                logger.exception("upscale job %s crashed", job.id)
                self._finish(job, "failed", str(e))

    def _cancelled(self, job: UpscaleJob) -> bool:
        """Cancelled here, or on Activity (which only marks the progress record).

        Reads the record's whole history: a progress update racing the cancel
        replaces the current status, but the cancelled event stays in it.
        """
        if job.cancel.is_set():
            return True
        try:
            from backend.utils.unified_progress_system import ProcessStatus, get_unified_progress

            history = get_unified_progress().get_process_history(job.id)
            if any(event.status == ProcessStatus.CANCELLED for event in history):
                job.cancel.set()
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _run(self, job: UpscaleJob) -> None:
        from backend.services.gpu_resource_policy import GpuWaitStopped, gpu_session_when_free
        from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError, gpu_holder_label
        from backend.services.job_types import JobKind

        if self._cancelled(job):
            self._finish(job, "cancelled", None)
            return

        def on_wait(reason: str) -> None:
            holder = gpu_holder_label(GpuBusyError(reason))
            self._set(job, "waiting", f"Waiting for the GPU ({holder})" if holder else "Waiting for the GPU")

        try:
            with gpu_session_when_free(
                JobKind.VIDEO_RENDER,
                f"upscale:{job.id}",
                wait_s=_gpu_wait_s(),
                on_wait=on_wait,
                should_stop=lambda: self._cancelled(job),
                evict_ollama=True,
                free_comfyui=True,
                cross_process=True,
                require_fit=True,
                vram_estimate_mb=job.vram_mb,
                slot_id=f"upscale:{job.id}",
                upscale_model=job.model,
            ):
                self._run_on_service(job)
        except GpuWaitStopped:
            self._finish(job, "cancelled", None)
        except GpuCapacityError as e:
            self._finish(job, "failed", str(e))
        except GpuBusyError as e:
            self._finish(job, "failed", f"The GPU did not come free within {_gpu_wait_s():.0f} s: {e}")

    def _run_on_service(self, job: UpscaleJob) -> None:
        down = _ensure_service()
        if down:
            self._finish(job, "failed", f"Upscaling is not available: {down}")
            return
        self._set(job, "running", f"Upscaling {job.label}")
        resp = requests.post(f"{UPSCALING_URL}{job.path}", json=job.payload, headers=_auth_headers(), timeout=30)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code not in (200, 202) or not data.get("job_id"):
            detail = data.get("detail") or data.get("error") or resp.text[:200]
            self._finish(job, "failed", f"The upscaling service refused the job: {detail}")
            return
        job.service_job_id = data["job_id"]
        job.service_job = data

        cancel_sent = False
        while True:
            if self._cancelled(job) and not cancel_sent:
                try:
                    requests.delete(f"{UPSCALING_URL}/jobs/{job.service_job_id}", headers=_auth_headers(), timeout=10)
                    cancel_sent = True
                except requests.RequestException as e:
                    # Not delivered: ask again on the next pass.
                    logger.warning("upscale cancel of %s failed: %s", job.service_job_id, e)
            try:
                state, status = _service_get(f"/jobs/{job.service_job_id}")
            except (requests.RequestException, ValueError) as e:
                if not _service_up():
                    self._finish(job, "failed", f"The upscaling service stopped during the job ({e})")
                    return
                time.sleep(1.0)
                continue
            if status == 200 and isinstance(state, dict):
                job.service_job = state
                if state.get("status") in _TERMINAL:
                    self._finish(job, state["status"], state.get("error"))
                    return
                done, total = state.get("frames_done") or 0, state.get("frames_total") or 0
                self._set(job, "running", f"Upscaling {job.label}" + (f": {done}/{total}" if total else ""))
            elif status == 404:
                self._finish(job, "failed", "The upscaling service lost the job (was it restarted?)")
                return
            time.sleep(1.0)

    # ---- single images and previews -------------------------------------------

    @contextlib.contextmanager
    def single_gpu_turn(self, model: Optional[str]) -> Iterator[None]:
        """Hold the GPU around one synchronous upscale request.

        Raises UpscaleGpuBusy, naming the holder, when the card does not come
        free within SINGLE_GPU_WAIT_S.
        """
        from backend.services.gpu_resource_policy import gpu_session_when_free
        from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError, gpu_holder_label
        from backend.services.job_types import JobKind

        name, vram_mb, _refusal = self.resolve(model)  # the service refuses a missing model itself
        op = f"upscale:single_{uuid.uuid4().hex[:8]}"
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(gpu_session_when_free(
                JobKind.VIDEO_RENDER, op, wait_s=SINGLE_GPU_WAIT_S,
                evict_ollama=True, free_comfyui=True, cross_process=True, require_fit=True,
                vram_estimate_mb=vram_mb, slot_id=op, upscale_model=name,
            ))
        except GpuCapacityError as e:
            raise UpscaleGpuBusy(str(e)) from e
        except GpuBusyError as e:
            holder = gpu_holder_label(e)
            fit = getattr(e, "fit", None)
            if holder is None and fit is not None:
                # No job holds the card: memory is the problem, and waiting
                # for "it" to finish will not help.
                raise UpscaleGpuBusy(
                    f"Not enough free video memory for this upscale: it needs about "
                    f"{fit.need_mb / 1024:.1f} GB and {fit.usable_mb / 1024:.1f} GB is free, "
                    "after unloading the chat model and asking ComfyUI to release its models. "
                    "Something else is still holding the card, such as another app or a "
                    "ComfyUI that kept its models loaded. Free it, then try again."
                ) from e
            raise UpscaleGpuBusy(
                f"The GPU is busy{f' with {holder}' if holder else ''}; try again when it finishes, "
                "or upload several images at once, which waits its turn."
            ) from e
        with stack:
            yield

    # ---- the page's view ---------------------------------------------------------

    def owns(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def merge_job_list(self, service_jobs: List[dict]) -> List[dict]:
        """This queue's jobs, then the service's own (watch folder) that are not ours."""
        with self._lock:
            mine = [j.to_dict() for j in self._jobs.values()]
            ours = {j.service_job_id for j in self._jobs.values() if j.service_job_id}
        return mine + [j for j in service_jobs or [] if j.get("job_id") not in ours]

    def cancel(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status not in _TERMINAL:
            job.cancel.set()
            job.message = "Cancelling"
        return {"status": "cancelling" if job.status not in _TERMINAL else job.status, "job_id": job_id}

    def clear_finished(self) -> int:
        with self._lock:
            done = [k for k, j in self._jobs.items() if j.status in _TERMINAL]
            for k in done:
                del self._jobs[k]
        return len(done)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status not in _TERMINAL)

    def stop_blocked_reason(self) -> Optional[str]:
        n = self.active_count()
        return f"{n} upscale job(s) queued or running" if n else None

    def _trim_locked(self) -> None:
        finished = [k for k, j in self._jobs.items() if j.status in _TERMINAL]
        for k in finished[: max(0, len(self._jobs) - _HISTORY)]:
            del self._jobs[k]

    # ---- progress record (Activity) ----------------------------------------------

    def _progress_create(self, job: UpscaleJob) -> None:
        try:
            from backend.utils.unified_progress_system import ProcessType, get_unified_progress

            ups = get_unified_progress()
            ups.create_process(
                ProcessType.UPSCALE, job.label, process_id=job.id,
                additional_data={"kind": job.kind, "model": job.model, "vram_mb": job.vram_mb},
            )
            # Activity's Cancel reaches the job even while it waits on a long step
            # (service start, freeing memory) and its record is cleaned up meanwhile.
            ups.on_cancel(job.id, job.cancel.set)
        except Exception as e:  # noqa: BLE001
            logger.warning("upscale progress record for %s not created: %s", job.id, e)

    def _set(self, job: UpscaleJob, status: str, message: str) -> None:
        job.status, job.message = status, message
        if self._cancelled(job):
            return  # the record already says cancelled; an update would hide it
        try:
            from backend.utils.unified_progress_system import get_unified_progress

            pct = int(round(100 * float((job.service_job or {}).get("progress") or 0.0)))
            get_unified_progress().update_process(job.id, pct, message)
        except Exception:  # noqa: BLE001
            pass

    def _finish(self, job: UpscaleJob, status: str, error: Optional[str]) -> None:
        if status == "completed" and job.on_complete is not None:
            try:
                job.on_complete(job)
            except Exception as e:  # noqa: BLE001
                logger.error("upscale %s finished but its result was not filed: %s", job.id, e)
                status, error = "failed", f"Upscaled, but the result was not filed: {e}"
        job.status, job.error = status, error
        job.message = {"completed": f"Upscaled {job.label}", "cancelled": "Cancelled"}.get(status, error or "Failed")
        try:
            from backend.utils.unified_progress_system import get_unified_progress

            ups = get_unified_progress()
            if status == "completed":
                ups.complete_process(job.id, job.message)
            elif status == "cancelled":
                ups.cancel_process(job.id, job.message)
            else:
                ups.error_process(job.id, job.message)
        except Exception:  # noqa: BLE001
            pass
        logger.info("upscale %s %s%s", job.id, status, f": {error}" if error else "")


_SUPERVISOR: Optional[UpscaleSupervisor] = None
_SUPERVISOR_LOCK = threading.Lock()


def get_supervisor() -> UpscaleSupervisor:
    global _SUPERVISOR
    with _SUPERVISOR_LOCK:
        if _SUPERVISOR is None:
            _SUPERVISOR = UpscaleSupervisor()
        return _SUPERVISOR


def stop_blocked_reason() -> Optional[str]:
    """Why the upscaling service must not be stopped now, or None."""
    return _SUPERVISOR.stop_blocked_reason() if _SUPERVISOR is not None else None
