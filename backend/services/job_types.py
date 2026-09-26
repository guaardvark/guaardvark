"""Canonical Job type for the Tasks/Jobs unification.

The system has 7 distinct state stores tracking "things in flight":
Task, TrainingJob, SelfImprovementRun, ExperimentRun, DemoStep,
in-memory UnifiedProgress, Celery/Redis broker, and the bare-SQL
batch_job_rows tables. This module defines a wire-format `Job`
dataclass that adapts each native row into a single canonical shape.

No new DB tables (job_history is added separately in Phase 5).
Existing models keep their internal fields untouched. Only the
shape of `/api/jobs/*` responses and `jobs:*` socket events
becomes uniform.

See plans/2026-04-29-tasks-jobs-progress-unification.md §4.1.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class JobKind(str, Enum):
    """The native source of a job. Used to dispatch cancel transports
    and route queries back to the correct underlying table."""

    TASK = "task"                       # Task (backend/models.py)
    TRAINING = "training"               # TrainingJob
    SELF_IMPROVEMENT = "self_improvement"
    EXPERIMENT = "experiment"
    DEMO = "demo"
    BATCH_CSV = "batch_csv"             # batch_job_rows (bare SQL)
    VIDEO_GEN = "video_gen"             # batch video generation (BatchVideoGenerator)
    MUSIC_VIDEO = "music_video"         # song-driven music video pipeline (MusicVideo row)
    VIDEO_RENDER = "video_render"       # editor renders (lands in Phase 7 of editor plan)
    PRODUCTION = "production"           # ViMax-style production pipeline parent
    LORA_TRAIN = "lora_train"           # Per-Subject LoRA training (child of PRODUCTION)
    OUTREACH = "outreach"               # Social outreach Task rows and progress events
    PUBLISH = "publish"                 # Publishing an asset to a social connection
    WEBSITE = "website"                 # Website Task rows (crawl/index/code, type=website_*)
    UNIFIED_PROGRESS = "unified"        # in-memory-only process


class JobStatus(str, Enum):
    """The canonical status set. Every native status set maps onto these
    six values via the per-kind adapter; consumers never see raw native
    status strings."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.PAUSED)


def _json_safe(value: Any) -> Any:
    """Recursively coerce values json.dumps can't encode (enums -> .value,
    datetimes -> isoformat) inside free-form structures like Job.metadata.
    Keeps one collector's stray enum from 500-ing the whole /api/jobs response."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@dataclass
class Job:
    """The canonical wire format every consumer sees.

    `id` is always a string composed as f"{kind}:{native_id}" so collisions
    between native id spaces (Task.id=5 vs TrainingJob.id=5) are impossible
    at the API surface.

    `metadata` is a free-form dict for kind-specific extras. The schema for
    each kind is documented in the adapter that produces it; consumers
    should treat unknown keys as opaque.
    """

    id: str                              # "{kind}:{native_id}"
    kind: JobKind
    native_id: int | str
    status: JobStatus
    label: str                           # user-facing
    progress: float | None = None        # 0-100, None if indeterminate
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    cancellable: bool = False
    parent_id: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Wire-format serialization. ISO datetimes; enum values, not enum repr.

        `metadata` is free-form, so defensively coerce any nested enum/datetime it
        carries — a single unserializable extra from one collector must not 500 the
        whole GET /api/jobs response (it has, twice: ProcessStatus then ProcessType).
        """
        d = _json_safe(asdict(self))
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        if self.started_at is not None:
            d["started_at"] = self.started_at.isoformat()
        if self.finished_at is not None:
            d["finished_at"] = self.finished_at.isoformat()
        return d


# ---------- Per-kind status mappings -----------------------------------------
#
# Native status strings → canonical JobStatus. Missing keys raise a KeyError
# so a new native value gets noticed instead of silently degrading. Tests
# should cover every entry in each kind's status enum.

_TASK_STATUS_MAP = {
    "pending": JobStatus.PENDING,
    "queued": JobStatus.PENDING,
    "in-progress": JobStatus.RUNNING,
    "running": JobStatus.RUNNING,        # tolerate alt casing seen in code
    "paused": JobStatus.PAUSED,
    "completed": JobStatus.COMPLETED,
    "complete": JobStatus.COMPLETED,
    "failed": JobStatus.FAILED,
    "error": JobStatus.FAILED,
    "cancelled": JobStatus.CANCELLED,
    "canceled": JobStatus.CANCELLED,
}

_TRAINING_STATUS_MAP = {
    "pending": JobStatus.PENDING,
    "running": JobStatus.RUNNING,
    "completed": JobStatus.COMPLETED,
    "failed": JobStatus.FAILED,
    "cancelled": JobStatus.CANCELLED,
}

_UNIFIED_PROGRESS_STATUS_MAP = {
    "start": JobStatus.RUNNING,
    "processing": JobStatus.RUNNING,
    "running": JobStatus.RUNNING,
    "complete": JobStatus.COMPLETED,
    "completed": JobStatus.COMPLETED,
    "end": JobStatus.COMPLETED,
    "error": JobStatus.FAILED,
    "failed": JobStatus.FAILED,
    "cancelled": JobStatus.CANCELLED,
    "canceled": JobStatus.CANCELLED,
}

# Catch-all fallback for less-trafficked kinds. Unknown values map to FAILED
# rather than silently to a "looks healthy" status.
_GENERIC_STATUS_MAP = {
    **_TASK_STATUS_MAP,
    **_UNIFIED_PROGRESS_STATUS_MAP,
}


def map_status(kind: JobKind, native_status: str | None) -> JobStatus:
    """Translate a native status string into canonical JobStatus.

    Returns JobStatus.PENDING if `native_status` is None/empty (a job that
    just got created and hasn't reported yet). Returns JobStatus.FAILED if
    the value is non-empty but unrecognized — that way a backend regression
    that introduces a new status word is visible (red row in UI) rather
    than hidden (silently maps to "running forever").
    """
    if not native_status:
        return JobStatus.PENDING

    # native_status may be a str OR an enum — the unified progress system passes a
    # ProcessStatus enum whose .value is the lowercase status string ("complete",
    # "error", …). Coerce to a string before normalizing so .lower() never throws
    # and crashes the /api/jobs collector (which then corrupts the streamed response).
    if not isinstance(native_status, str):
        native_status = getattr(native_status, "value", None) or str(native_status)

    table = {
        JobKind.TASK: _TASK_STATUS_MAP,
        JobKind.OUTREACH: _TASK_STATUS_MAP,
        JobKind.PUBLISH: _TASK_STATUS_MAP,
        JobKind.WEBSITE: _TASK_STATUS_MAP,
        JobKind.TRAINING: _TRAINING_STATUS_MAP,
        JobKind.UNIFIED_PROGRESS: _UNIFIED_PROGRESS_STATUS_MAP,
        JobKind.VIDEO_GEN: _GENERIC_STATUS_MAP,
        JobKind.MUSIC_VIDEO: _GENERIC_STATUS_MAP,
        JobKind.VIDEO_RENDER: _UNIFIED_PROGRESS_STATUS_MAP,
        JobKind.LORA_TRAIN: _TRAINING_STATUS_MAP,
    }.get(kind, _GENERIC_STATUS_MAP)

    return table.get(native_status.lower(), JobStatus.FAILED)


# ---------- Render failures ---------------------------------------------------
#
# Every way a render can fail, named once where it is detected and read by the
# batch status JSON, the MCP/chat tools, the Studio and the pipelines.

class RenderErrorKind(str, Enum):
    INVALID_REQUEST = "invalid_request"        # the request asks for something this model cannot do
    MODEL_NOT_INSTALLED = "model_not_installed"
    COMPANION_MISSING = "companion_missing"    # the model is installed, a file it needs is not
    COMFYUI_DOWN = "comfyui_down"              # unreachable, refused to start, or died mid-render
    NODE_MISSING = "node_missing"              # a custom node the graph uses is not loaded
    CARD_TOO_SMALL = "card_too_small"          # the model can never fit this card
    VRAM_BUSY = "vram_busy"                    # the card did not free up before the wait ran out
    OOM = "oom"                                # out of memory while rendering
    NODE_ERROR = "node_error"                  # a ComfyUI node raised or rejected its inputs
    RENDER_TIMEOUT = "render_timeout"          # ComfyUI stopped reporting the prompt
    BLANK_OUTPUT = "blank_output"              # a black, blank or NaN-decoded video
    OUTPUT_MISSING = "output_missing"          # finished, but no video file came back
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# What each kind tells the person, and whether a retry can help.
#   retryable       the same request can succeed later without anything changing
#   retry_after_s   the wait before each retry; its length is the attempt budget
#   action          the next step, shown after the message
# The retry waits are starting values, not measurements: ComfyUI restarts in
# about 5 s on a warm box (video_model_registry COMFYUI_AUTOSTART_WAIT_S), and a
# batch holding the card usually ends within minutes.
RENDER_ERROR_POLICY: dict[RenderErrorKind, dict[str, Any]] = {
    RenderErrorKind.INVALID_REQUEST: {
        "label": "Request not supported", "retryable": False, "retry_after_s": [],
        "action": "Change the request as the message says."},
    RenderErrorKind.MODEL_NOT_INSTALLED: {
        "label": "Model not installed", "retryable": False, "retry_after_s": [],
        "action": "Open Manage Video Models and install it."},
    RenderErrorKind.COMPANION_MISSING: {
        "label": "Companion file missing", "retryable": False, "retry_after_s": [],
        "action": "Open Manage Video Models and install the model again; companions come with it."},
    RenderErrorKind.COMFYUI_DOWN: {
        "label": "ComfyUI not running", "retryable": True, "retry_after_s": [30],
        "action": "Start the ComfyUI plugin; if it does not stay up, read logs/comfyui.log."},
    RenderErrorKind.NODE_MISSING: {
        "label": "ComfyUI node missing", "retryable": False, "retry_after_s": [],
        "action": "Run plugins/comfyui/scripts/install_deps.sh, then restart the ComfyUI plugin."},
    RenderErrorKind.CARD_TOO_SMALL: {
        "label": "Card too small", "retryable": False, "retry_after_s": [],
        "action": "Pick a lighter model or a smaller size."},
    RenderErrorKind.VRAM_BUSY: {
        "label": "GPU busy", "retryable": True, "retry_after_s": [60, 180],
        "action": "Another job holds the GPU; retry when it finishes."},
    RenderErrorKind.OOM: {
        "label": "Out of GPU memory", "retryable": False, "retry_after_s": [],
        "action": "Lower the size, length or steps, or turn off upscale and LoRAs."},
    RenderErrorKind.NODE_ERROR: {
        "label": "ComfyUI node failed", "retryable": False, "retry_after_s": [],
        "action": "The message names the node; logs/comfyui.log has the traceback."},
    RenderErrorKind.RENDER_TIMEOUT: {
        "label": "Render stalled", "retryable": True, "retry_after_s": [60],
        "action": "ComfyUI stopped reporting progress; check logs/comfyui.log."},
    RenderErrorKind.BLANK_OUTPUT: {
        "label": "Blank video", "retryable": False, "retry_after_s": [],
        "action": "Usually a model file is incomplete; reinstall it from Manage Video Models."},
    RenderErrorKind.OUTPUT_MISSING: {
        "label": "No video written", "retryable": True, "retry_after_s": [30],
        "action": "ComfyUI finished without a video file; check its output folder and logs/comfyui.log."},
    RenderErrorKind.CANCELLED: {
        "label": "Cancelled", "retryable": False, "retry_after_s": [], "action": ""},
    RenderErrorKind.UNKNOWN: {
        "label": "Render failed", "retryable": False, "retry_after_s": [],
        "action": "logs/backend.log has the details."},
}


class RenderFailure(str):
    """A failure message that knows its kind. It is a str, so every place that
    passes an error string along keeps working and the kind rides with it."""

    kind: RenderErrorKind

    def __new__(cls, kind: "RenderErrorKind | str", message: str):
        obj = super().__new__(cls, message)
        obj.kind = RenderErrorKind(kind)
        return obj

    def __reduce__(self):
        return (RenderFailure, (self.kind.value, str(self)))


def failure_kind(message: Any, default: "RenderErrorKind | None" = None) -> "RenderErrorKind | None":
    """The kind a message carries, else ``default``."""
    kind = getattr(message, "kind", None)
    return RenderErrorKind(kind) if kind else default


def describe_failure(kind: "RenderErrorKind | str | None", message: str | None) -> dict[str, Any]:
    """The record status JSON carries for a failure."""
    k = RenderErrorKind(kind) if kind else RenderErrorKind.UNKNOWN
    policy = RENDER_ERROR_POLICY[k]
    return {
        "kind": k.value,
        "label": policy["label"],
        "message": str(message or ""),
        "action": policy["action"],
        "retryable": policy["retryable"],
        "retry_after_s": list(policy["retry_after_s"]),
    }


def render_failed(what: str, kind: "RenderErrorKind | str | None", message: str | None) -> RenderFailure:
    """The failure a pipeline raises for a render it needed ("Wan 2.2 I2V
    failed — Out of GPU memory: … Next: …"), keeping the kind:
    ``raise RuntimeError(render_failed(...))``."""
    k = RenderErrorKind(kind) if kind else RenderErrorKind.UNKNOWN
    return RenderFailure(k, f"{what} failed — {failure_text(k, message or 'no video produced')}")


def retry_waits(kind: "RenderErrorKind | str | None") -> list[float]:
    """Seconds to wait before each retry of a failure of this kind; [] when a
    retry cannot help."""
    if not kind:
        return []
    policy = RENDER_ERROR_POLICY[RenderErrorKind(kind)]
    return [float(s) for s in policy["retry_after_s"]] if policy["retryable"] else []


def failure_text(kind: "RenderErrorKind | str | None", message: str | None) -> str:
    """One line for chat and MCP: label, message, next step."""
    d = describe_failure(kind, message)
    text = f"{d['label']}: {d['message']}" if d["message"] else d["label"]
    if d["action"]:
        text += f" Next: {d['action']}"
    if d["retryable"]:
        text += " (Retrying later can succeed.)"
    return text


def batch_failure(status: Any) -> dict[str, Any] | None:
    """The failure record a batch reports: its own error, else its first
    failed clip's, else None when nothing failed. Reads any status object
    with status/error/error_kind/results (BatchVideoStatus)."""
    error = getattr(status, "error", None)
    if getattr(status, "status", None) == "cancelled":
        return describe_failure(RenderErrorKind.CANCELLED, error or "Cancelled")
    if error:
        return describe_failure(getattr(status, "error_kind", None), error)
    failed = next((r for r in getattr(status, "results", None) or [] if not r.success), None)
    if failed is not None:
        return describe_failure(getattr(failed, "error_kind", None), failed.error)
    return None
