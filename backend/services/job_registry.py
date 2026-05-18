"""Job adapter registry.

Translates each native row type into the canonical `Job` shape from
`job_types.py`. Read-only — these adapters never write back to the
native tables. Mutations go through the per-kind code paths that
already exist (Task CRUD, TrainingJob lifecycle, etc.).

Each adapter is pure: it takes the native row plus optional in-memory
context and returns a Job dataclass. Consumers (the new `/api/jobs`
resource, the `jobs:*` socket emitter) call the registry with a kind
+ native id; the registry loads the row and adapts it.

See plans/2026-04-29-tasks-jobs-progress-unification.md §4.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from backend.services.job_types import Job, JobKind, JobStatus, map_status

logger = logging.getLogger(__name__)


# ---------- adapters ---------------------------------------------------------

def adapt_task(row, *, started_at: datetime | None = None) -> Job:
    """Task → Job. The Task row already exposes most fields directly."""
    progress = float(row.progress) if row.progress is not None else None
    status = map_status(JobKind.TASK, row.status)

    return Job(
        id=f"task:{row.id}",
        kind=JobKind.TASK,
        native_id=row.id,
        status=status,
        label=row.name or f"Task #{row.id}",
        progress=progress,
        started_at=started_at or row.created_at,
        finished_at=row.updated_at if status.is_terminal else None,
        duration_s=_compute_duration(started_at or row.created_at,
                                     row.updated_at if status.is_terminal else None),
        cancellable=status.is_active,
        parent_id=f"task:{row.parent_task_id}" if getattr(row, "parent_task_id", None) else None,
        error_message=getattr(row, "error_message", None),
        metadata={
            "type": row.type,
            "priority": row.priority,
            "task_handler": getattr(row, "task_handler", None),
            "schedule_type": getattr(row, "schedule_type", None),
            "next_run_at": _iso(getattr(row, "next_run_at", None)),
            "celery_task_id": getattr(row, "job_id", None),  # Task.job_id is the Celery task UUID
        },
    )


def adapt_training_job(row) -> Job:
    """TrainingJob → Job. Pulls progress, current_step/total_steps,
    pipeline_stage, and the live PID from the dedicated training schema."""
    status = map_status(JobKind.TRAINING, row.status)
    progress = float(row.progress) if row.progress is not None else None

    label_parts = [row.name or row.output_model_name or f"Training #{row.id}"]
    if row.pipeline_stage:
        label_parts.append(f"({row.pipeline_stage})")
    label = " ".join(label_parts)

    return Job(
        id=f"training:{row.id}",
        kind=JobKind.TRAINING,
        native_id=row.id,
        status=status,
        label=label,
        progress=progress,
        started_at=row.started_at or row.created_at,
        finished_at=row.completed_at if status.is_terminal else None,
        duration_s=_compute_duration(row.started_at or row.created_at,
                                     row.completed_at if status.is_terminal else None),
        cancellable=status.is_active,
        error_message=row.error_message,
        metadata={
            "job_id": row.job_id,
            "base_model": row.base_model,
            "output_model_name": row.output_model_name,
            "pipeline_stage": row.pipeline_stage,
            "current_step": row.current_step,
            "total_steps": row.total_steps,
            "celery_task_id": row.celery_task_id,
            "pid": getattr(row, "pid", None),
            "is_resumable": bool(getattr(row, "is_resumable", False)),
            "metrics": _safe_json(row.metrics_json),
        },
    )


def adapt_self_improvement(row) -> Job:
    """SelfImprovementRun → Job. Status fields + duration + JSON results."""
    status = map_status(JobKind.SELF_IMPROVEMENT, getattr(row, "status", None))
    return Job(
        id=f"self_improvement:{row.id}",
        kind=JobKind.SELF_IMPROVEMENT,
        native_id=row.id,
        status=status,
        label=getattr(row, "name", None) or f"Self-improvement #{row.id}",
        progress=None,
        started_at=getattr(row, "started_at", None) or getattr(row, "created_at", None),
        finished_at=getattr(row, "completed_at", None) if status.is_terminal else None,
        duration_s=getattr(row, "duration_s", None),
        cancellable=status.is_active,
        error_message=getattr(row, "error_message", None),
        metadata={
            "results_json": _safe_json(getattr(row, "results_json", None)),
        },
    )


def adapt_experiment(row) -> Job:
    status = map_status(JobKind.EXPERIMENT, getattr(row, "status", None))
    return Job(
        id=f"experiment:{row.id}",
        kind=JobKind.EXPERIMENT,
        native_id=row.id,
        status=status,
        label=getattr(row, "name", None) or f"Experiment #{row.id}",
        started_at=getattr(row, "started_at", None) or getattr(row, "created_at", None),
        finished_at=getattr(row, "completed_at", None) if status.is_terminal else None,
        cancellable=status.is_active,
        error_message=getattr(row, "error_message", None),
        metadata={"experiment_type": getattr(row, "experiment_type", None)},
    )


def adapt_demo_step(row) -> Job:
    status = map_status(JobKind.DEMO, getattr(row, "status", None))
    return Job(
        id=f"demo:{row.id}",
        kind=JobKind.DEMO,
        native_id=row.id,
        status=status,
        label=getattr(row, "step_name", None) or f"Demo step #{row.id}",
        progress=None,
        started_at=getattr(row, "started_at", None),
        finished_at=getattr(row, "completed_at", None) if status.is_terminal else None,
        cancellable=False,  # demo steps aren't user-cancellable
        parent_id=(
            f"demo:{row.demonstration_id}"
            if getattr(row, "demonstration_id", None) else None
        ),
    )


def adapt_unified_progress(event: dict[str, Any]) -> Job:
    """Adapt a UnifiedProgress in-memory ProgressEvent dict.

    These don't have a row-style native_id — the process_id string serves
    that purpose. The dict shape matches `unified_progress_system.ProgressEvent`
    plus whatever additional_data the producer attached.
    """
    process_id = event.get("process_id") or event.get("job_id") or "unknown"
    process_type = event.get("process_type") or event.get("processType") or "processing"
    status = map_status(JobKind.UNIFIED_PROGRESS, event.get("status"))
    progress = event.get("progress")
    if progress is not None:
        try:
            progress = float(progress)
        except (TypeError, ValueError):
            progress = None

    return Job(
        id=f"unified:{process_id}",
        kind=JobKind.UNIFIED_PROGRESS,
        native_id=process_id,
        status=status,
        label=event.get("message") or process_type,
        progress=progress,
        started_at=_parse_ts(event.get("timestamp")),
        finished_at=_parse_ts(event.get("timestamp")) if status.is_terminal else None,
        cancellable=status.is_active,
        metadata={
            "process_type": process_type,
            "additional_data": event.get("additional_data") or {},
        },
    )


def adapt_batch_csv(row: dict[str, Any]) -> Job:
    """batch_job_rows → Job. The bare-SQL table has no SQLAlchemy model,
    so callers pass a dict from the row's column read."""
    status = map_status(JobKind.BATCH_CSV, row.get("status"))
    return Job(
        id=f"batch_csv:{row.get('id')}",
        kind=JobKind.BATCH_CSV,
        native_id=row.get("id"),
        status=status,
        label=row.get("name") or f"CSV batch #{row.get('id')}",
        progress=row.get("progress"),
        started_at=_parse_ts(row.get("started_at")),
        finished_at=_parse_ts(row.get("completed_at")) if status.is_terminal else None,
        cancellable=status.is_active,
        error_message=row.get("error_message"),
        metadata={
            "row_count": row.get("row_count"),
            "completed_rows": row.get("completed_rows"),
        },
    )


# ---------- registry ---------------------------------------------------------

# Loader is a callable that takes a native_id and returns either a row/dict
# (whatever the adapter expects) or None. Keeps the registry decoupled from
# DB session management — callers wire concrete loaders at consumer time.
LoaderFn = Callable[[Any], Any]
AdapterFn = Callable[[Any], Job]


def _load_task(native_id):
    from backend.models import Task as DBTask, db
    return db.session.get(DBTask, int(native_id))


def _load_training(native_id):
    from backend.models import TrainingJob, db
    return db.session.get(TrainingJob, int(native_id))


def _load_self_improvement(native_id):
    from backend.models import SelfImprovementRun, db
    return db.session.get(SelfImprovementRun, int(native_id))


def _load_experiment(native_id):
    from backend.models import ExperimentRun, db
    return db.session.get(ExperimentRun, int(native_id))


def _load_demo_step(native_id):
    from backend.models import DemoStep, db
    return db.session.get(DemoStep, int(native_id))


def _load_unified_progress(process_id):
    """Fetch the live ProgressEvent dict for `process_id`. None if unknown."""
    from backend.utils.unified_progress_system import get_unified_progress
    ups = get_unified_progress()
    snapshot = ups.get_active_processes() if hasattr(ups, "get_active_processes") else {}
    return snapshot.get(process_id)


# Per-kind (loader, adapter) pairs. Add a new kind here + a single adapter
# function and the rest of the system (API resource, socket emitter,
# Tasks/Jobs page) picks it up automatically.
REGISTRY: dict[JobKind, tuple[LoaderFn, AdapterFn]] = {
    JobKind.TASK: (_load_task, adapt_task),
    JobKind.TRAINING: (_load_training, adapt_training_job),
    JobKind.SELF_IMPROVEMENT: (_load_self_improvement, adapt_self_improvement),
    JobKind.EXPERIMENT: (_load_experiment, adapt_experiment),
    JobKind.DEMO: (_load_demo_step, adapt_demo_step),
    JobKind.UNIFIED_PROGRESS: (_load_unified_progress, adapt_unified_progress),
}


def get_job(kind: JobKind, native_id: Any) -> Optional[Job]:
    """Resolve a single Job by (kind, native_id). Returns None if not found."""
    pair = REGISTRY.get(kind)
    if pair is None:
        logger.warning("get_job: unsupported kind %s", kind)
        return None
    loader, adapter = pair
    row = loader(native_id)
    if row is None:
        return None
    try:
        return adapter(row)
    except Exception as e:
        logger.exception("get_job: adapter failed for %s:%s — %s", kind.value, native_id, e)
        return None


def parse_job_id(job_id: str) -> tuple[JobKind, str]:
    """Split a wire-format id ("{kind}:{native_id}") into a kind+native_id pair.

    Raises ValueError on malformed input. Native id is returned as a string;
    callers cast to int for kinds whose primary key is integer.
    """
    if ":" not in job_id:
        raise ValueError(f"Bad job id {job_id!r}: missing kind prefix")
    kind_str, native_id = job_id.split(":", 1)
    try:
        kind = JobKind(kind_str)
    except ValueError as e:
        raise ValueError(f"Bad job id {job_id!r}: unknown kind {kind_str!r}") from e
    if not native_id:
        raise ValueError(f"Bad job id {job_id!r}: empty native id")
    return kind, native_id


# ---------- helpers ----------------------------------------------------------

def _compute_duration(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    try:
        return (end - start).total_seconds()
    except Exception:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value if value < 1e12 else value / 1000)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
