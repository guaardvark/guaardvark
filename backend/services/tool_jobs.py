"""Background runs of tool calls that an MCP client polls instead of waiting on.

An MCP client stops waiting for a call after the server's timeout (120 s by
default), and some tools take longer: a Qwen-Image-Edit runs about three
minutes on a 16 GB card. For those tools the MCP server hands the call to the
backend (``run_tool_in_backend``); the backend checks the inputs, starts the
work here on a thread, and answers at once with a job id that
``get_generation_status`` reads (in process, or over GET /api/tools/jobs/<id>).
The work then no longer depends on the client: closing the MCP session does not
stop it.

Chat, the Studio and the Tools page run the same tools in the backend and wait
for them inline; they never come through here.

Jobs live in this process's memory. Every job id carries a token of the backend
process that started it, so after a restart a reader can say that the job
belonged to the previous run instead of claiming it never existed.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

JOB_ID_PREFIX = "tooljob_"

# Limits, with what each one is sized against.
# How long wait_for_result=true (and GET /api/tools/jobs/<id>?wait_s=) may hold
# a call before answering with the job id: 60 s, or half the MCP call timeout
# when that is shorter (the bound map_codebase uses), so the answer reaches an
# MCP client before it gives up on the call.
MAX_WAIT_S = 60.0
# How long a finished job stays readable. A client polls every few seconds and
# reads the result minutes after it lands; six hours also covers one that comes
# back after a long queue. The output file stays in data/outputs either way.
FINISHED_TTL_S = 6 * 3600
# Most jobs remembered; the oldest finished ones go first, a running one never.
MAX_JOBS = 200
# Most jobs queued or running at once. Queued jobs wait up to
# GUAARDVARK_IMAGE_VRAM_WAIT_S (600 s) for the GPU, and one card finishes about
# three 3-minute edits in that time, so more than this would mostly fail waiting.
MAX_ACTIVE_JOBS = 8
# A job waiting for the GPU is retried at least every 15 s
# (gpu_session_when_free's longest backoff); a wait note older than this means
# the job got the card and is running.
GPU_WAIT_NOTE_S = 30.0

# This backend process, as it appears in the job ids it hands out.
_PROCESS_TOKEN = uuid.uuid4().hex[:6]
_JOB_ID_SHAPE = re.compile(re.escape(JOB_ID_PREFIX) + r"([0-9a-f]{6})_[0-9a-f]{12}")


class ToolJobsBusy(RuntimeError):
    """MAX_ACTIVE_JOBS jobs are already queued or running."""


@dataclass
class _Job:
    job_id: str
    tool: str
    # What the progress footer calls this job, and the footer type it shows under.
    title: str = ""
    footer_type: str = "image_generation"
    created: float = field(default_factory=time.time)
    created_mono: float = field(default_factory=time.monotonic)
    status: str = "queued"  # queued | running | done | failed
    finished_mono: Optional[float] = None
    gpu_wait_note: Optional[str] = None
    gpu_wait_mono: Optional[float] = None
    # ToolResult.to_dict() of the finished run.
    result: Optional[Dict[str, Any]] = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        status, note = self.status, None
        if (status == "running" and self.gpu_wait_mono is not None
                and now - self.gpu_wait_mono < GPU_WAIT_NOTE_S):
            status, note = "queued", self.gpu_wait_note
        end = self.finished_mono if self.finished_mono is not None else now
        return {
            "job_id": self.job_id,
            "tool": self.tool,
            "status": status,
            "note": note,
            "created": self.created,
            "elapsed_s": round(end - self.created_mono, 1),
            "result": dict(self.result) if self.result is not None else None,
        }


_jobs: "OrderedDict[str, _Job]" = OrderedDict()
_lock = threading.Lock()
_current = threading.local()


def is_job_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(JOB_ID_PREFIX)


def wait_seconds() -> float:
    """MAX_WAIT_S, or half the MCP call timeout when that is shorter."""
    try:
        from backend.mcp.config import load_config
        return min(MAX_WAIT_S, max(1.0, load_config().timeout_seconds / 2))
    except Exception:  # noqa: BLE001 - an unreadable config keeps the default bound
        return MAX_WAIT_S


def in_job() -> bool:
    """True on a job's own thread."""
    return getattr(_current, "job", None) is not None


def note_gpu_wait(reason: str) -> None:
    """From inside a job: it is waiting for the GPU (shown as 'queued')."""
    job = getattr(_current, "job", None)
    if job is None:
        return
    with _lock:
        job.gpu_wait_note = f"Waiting for the GPU: {reason}"[:300]
        job.gpu_wait_mono = time.monotonic()
    from backend.services import footer_jobs
    entry = footer_jobs.current()
    if entry is not None:
        entry.update(f"{job.title or job.tool}: waiting for the GPU. {reason}")


def _prune(now: float) -> None:
    """Drop expired finished jobs, then the oldest finished ones over MAX_JOBS. Call under _lock."""
    for job_id, job in list(_jobs.items()):
        if job.finished_mono is not None and now - job.finished_mono > FINISHED_TTL_S:
            del _jobs[job_id]
    for job_id, job in list(_jobs.items()):
        if len(_jobs) <= MAX_JOBS:
            break
        if job.finished_mono is not None:
            del _jobs[job_id]


def _flask_app():
    try:
        from flask import current_app, has_app_context
        return current_app._get_current_object() if has_app_context() else None
    except Exception:  # noqa: BLE001 - no Flask here means no app context to carry
        return None


# How the progress footer names a tool's job when the caller gives no title.
TOOL_TITLES = {
    "generate_identity": "New scene, same face",
    "edit_image": "Image edit",
    "inpaint_image": "Image edit",
    "outpaint_image": "Expand image",
    "remove_background": "Remove background",
    "generate_animation": "Animation",
}


def submit(tool: str, run: Callable[[], Any], *, for_mcp: bool = True,
           title: Optional[str] = None, footer_type: str = "image_generation") -> Dict[str, Any]:
    """Start ``run`` (returns a ToolResult) on its own thread; return the job's snapshot.

    The thread gets the caller's Flask app context and, with ``for_mcp``, the
    MCP-client mark (``calls_for_mcp_client``), so the work runs under the same
    rules as the request that started it. The job shows in the progress footer
    from start to finish under ``title``. Raises ToolJobsBusy at MAX_ACTIVE_JOBS.
    """
    job = _Job(job_id=f"{JOB_ID_PREFIX}{_PROCESS_TOKEN}_{uuid.uuid4().hex[:12]}", tool=tool,
               title=title or TOOL_TITLES.get(tool, tool.replace("_", " ").capitalize()),
               footer_type=footer_type)
    with _lock:
        _prune(time.monotonic())
        active = sum(1 for j in _jobs.values() if j.finished_mono is None)
        if active >= MAX_ACTIVE_JOBS:
            raise ToolJobsBusy(
                f"{active} tool jobs are already queued or running, the most this server takes at once. "
                "Poll them with get_generation_status and start this one when one finishes."
            )
        _jobs[job.job_id] = job
    app = _flask_app()
    threading.Thread(
        target=_run, args=(job, run, app, for_mcp), name=f"tooljob-{tool}", daemon=True,
    ).start()
    logger.info("tool job %s started for %s", job.job_id, tool)
    return job.snapshot()


def _run(job: _Job, run: Callable[[], Any], app: Any, for_mcp: bool) -> None:
    from backend.services import footer_jobs
    from backend.utils.backend_http import calls_for_mcp_client

    payload: Dict[str, Any]
    _current.job = job
    try:
        with _lock:
            job.status = "running"
        with contextlib.ExitStack() as stack:
            if app is not None:
                stack.enter_context(app.app_context())
            stack.enter_context(calls_for_mcp_client(for_mcp))
            entry = stack.enter_context(footer_jobs.footer_job(job.footer_type, job.title))
            result = run()
            if hasattr(result, "success") and not result.success:
                entry.fail(f"{job.title}: {getattr(result, 'error', None) or 'failed'}")
        if hasattr(result, "to_dict"):
            payload = result.to_dict()
        else:
            payload = {"success": False, "output": None, "metadata": {},
                       "error": f"{job.tool} returned no result."}
    except Exception as e:  # noqa: BLE001 - the job carries the failure
        logger.exception("tool job %s (%s) failed", job.job_id, job.tool)
        from backend.services.job_operation_gate import classify_render_exception
        kind = classify_render_exception(e)
        payload = {"success": False, "output": None,
                   "metadata": {} if kind.value == "unknown" else {"error_kind": kind.value},
                   "error": f"{job.tool} failed: {e}"}
    finally:
        _current.job = None
    with _lock:
        job.result = payload
        job.status = "done" if payload.get("success") else "failed"
        job.finished_mono = time.monotonic()
        job.gpu_wait_mono = None
    job.done.set()
    logger.info("tool job %s (%s) %s", job.job_id, job.tool, job.status)


def get(job_id: str) -> Optional[Dict[str, Any]]:
    """The job's snapshot, or None when this process does not know it."""
    with _lock:
        _prune(time.monotonic())
        job = _jobs.get(job_id)
        return job.snapshot() if job is not None else None


def wait(job_id: str, timeout: float) -> Optional[Dict[str, Any]]:
    """Like ``get``, after waiting up to ``timeout`` (capped at MAX_WAIT_S) for the job to finish."""
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return None
    job.done.wait(max(0.0, min(float(timeout or 0), MAX_WAIT_S)))
    with _lock:
        return job.snapshot()


def missing(job_id: str) -> Tuple[str, str]:
    """(reason, message) for a job id ``get`` does not know.

    ``restarted``: the id comes from an earlier run of the backend, whose jobs
    went with it. ``unknown``: this run never had it, or it finished longer
    ago than FINISHED_TTL_S / MAX_JOBS keep.
    """
    match = _JOB_ID_SHAPE.fullmatch(job_id or "")
    if match and match.group(1) != _PROCESS_TOKEN:
        return "restarted", (
            f"Tool job {job_id} was started before the Guaardvark backend last restarted. Tool jobs "
            "are kept in the backend's memory, so its state is gone. If it finished before the "
            "restart, its file is in data/outputs (resources/list shows generated files); if it "
            "was still running, it stopped with the backend and needs to be started again."
        )
    return "unknown", (
        f"No tool job {job_id} in this backend. A finished job is kept for "
        f"{FINISHED_TTL_S // 3600} hours (the newest {MAX_JOBS}); after that its file stays in "
        "data/outputs (resources/list shows generated files) but the job record is gone."
    )
