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
    VIDEO_RENDER = "video_render"       # editor renders (lands in Phase 7 of editor plan)
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
        """Wire-format serialization. ISO datetimes; enum values, not enum repr."""
        d = asdict(self)
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

    table = {
        JobKind.TASK: _TASK_STATUS_MAP,
        JobKind.TRAINING: _TRAINING_STATUS_MAP,
        JobKind.UNIFIED_PROGRESS: _UNIFIED_PROGRESS_STATUS_MAP,
        JobKind.VIDEO_RENDER: _UNIFIED_PROGRESS_STATUS_MAP,
    }.get(kind, _GENERIC_STATUS_MAP)

    return table.get(native_status.lower(), JobStatus.FAILED)
