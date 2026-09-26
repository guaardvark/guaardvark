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

from backend.services.job_types import Job, JobKind, JobStatus, batch_failure, failure_text, map_status

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


def adapt_publish_task(row) -> Job:
    """Connection publish Task row → Job."""
    progress = float(row.progress) if row.progress is not None else None
    status = map_status(JobKind.PUBLISH, row.status)

    platform = None
    handler_config = getattr(row, "handler_config", None)
    if isinstance(handler_config, dict):
        platform = handler_config.get("platform")

    return Job(
        id=f"publish:{row.id}",
        kind=JobKind.PUBLISH,
        native_id=row.id,
        status=status,
        label=row.name or f"Publish #{row.id}",
        progress=progress,
        started_at=row.created_at,
        finished_at=row.updated_at if status.is_terminal else None,
        duration_s=_compute_duration(row.created_at, row.updated_at if status.is_terminal else None),
        cancellable=status.is_active,
        error_message=getattr(row, "error_message", None),
        metadata={
            "type": row.type,
            "task_id": row.id,
            "task_job_id": getattr(row, "job_id", None),
            "platform": platform,
        },
    )


def adapt_outreach_task(row) -> Job:
    """Social Outreach Task row → Job."""
    progress = float(row.progress) if row.progress is not None else None
    status = map_status(JobKind.OUTREACH, row.status)

    return Job(
        id=f"outreach:{row.id}",
        kind=JobKind.OUTREACH,
        native_id=row.id,
        status=status,
        label=row.name or f"Outreach task #{row.id}",
        progress=progress,
        started_at=row.created_at,
        finished_at=row.updated_at if status.is_terminal else None,
        duration_s=_compute_duration(row.created_at, row.updated_at if status.is_terminal else None),
        cancellable=status.is_active,
        parent_id=f"task:{row.parent_task_id}" if getattr(row, "parent_task_id", None) else None,
        error_message=getattr(row, "error_message", None),
        metadata={
            "type": row.type,
            "task_id": row.id,
            "task_job_id": getattr(row, "job_id", None),
            "workflow_config": _safe_json(getattr(row, "workflow_config", None)),
        },
    )


def adapt_website_task(row) -> Job:
    """Website Task row (type=website_*) → Job."""
    progress = float(row.progress) if row.progress is not None else None
    status = map_status(JobKind.WEBSITE, row.status)

    return Job(
        id=f"website:{row.id}",
        kind=JobKind.WEBSITE,
        native_id=row.id,
        status=status,
        label=row.name or f"Website task #{row.id}",
        progress=progress,
        started_at=row.created_at,
        finished_at=row.updated_at if status.is_terminal else None,
        duration_s=_compute_duration(row.created_at, row.updated_at if status.is_terminal else None),
        cancellable=status.is_active,
        parent_id=f"task:{row.parent_task_id}" if getattr(row, "parent_task_id", None) else None,
        error_message=getattr(row, "error_message", None),
        metadata={
            "type": row.type,
            "task_id": row.id,
            "website_id": getattr(row, "website_id", None),
            "target_website": getattr(row, "target_website", None),
            "task_job_id": getattr(row, "job_id", None),
            "workflow_config": _safe_json(getattr(row, "workflow_config", None)),
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
    # process_type can arrive as a ProcessType enum (from unified_progress_system).
    # Coerce to its string value so the "outreach" comparison below actually matches
    # AND it stays JSON-serializable in label/metadata — a raw enum here was 500-ing
    # the whole GET /api/jobs response ("ProcessType is not JSON serializable").
    process_type = getattr(process_type, "value", process_type)
    kind = JobKind.OUTREACH if process_type == "outreach" else JobKind.UNIFIED_PROGRESS
    native_id = process_id
    if kind == JobKind.OUTREACH and str(process_id).startswith("task_"):
        native_id = str(process_id).split("_", 1)[1]
    status = map_status(kind, event.get("status"))
    progress = event.get("progress")
    if progress is not None:
        try:
            progress = float(progress)
        except (TypeError, ValueError):
            progress = None

    return Job(
        id=f"{kind.value}:{native_id}",
        kind=kind,
        native_id=native_id,
        status=status,
        label=event.get("message") or process_type,
        progress=progress,
        started_at=_parse_ts(event.get("timestamp")),
        finished_at=_parse_ts(event.get("timestamp")) if status.is_terminal else None,
        cancellable=status.is_active,
        metadata={
            "process_type": process_type,
            "process_id": process_id,
            "additional_data": event.get("additional_data") or {},
        },
    )


_MUSIC_VIDEO_STAGE_STATUS = {
    "draft": JobStatus.PENDING,
    "analyzing": JobStatus.RUNNING,
    "awaiting_approval": JobStatus.PAUSED,
    "generating": JobStatus.RUNNING,
    "assembling": JobStatus.RUNNING,
    "complete": JobStatus.COMPLETED,
    "cancelled": JobStatus.CANCELLED,
    "failed": JobStatus.FAILED,
}


def _music_video_job_status(mv) -> JobStatus:
    st = (getattr(mv, "status", None) or "").lower()
    if st.startswith("cancelled") or getattr(mv, "current_stage", None) == "cancelled":
        return JobStatus.CANCELLED
    if st.startswith("failed") or getattr(mv, "current_stage", None) == "failed":
        return JobStatus.FAILED
    return _MUSIC_VIDEO_STAGE_STATUS.get(
        getattr(mv, "current_stage", None) or "draft",
        JobStatus.PENDING,
    )


def adapt_music_video(mv) -> Job:
    """MusicVideo → Job for the Jobs / Activity pages."""
    clips = mv.clips or []
    clip_count = len(clips)
    clips_done = sum(1 for c in clips if c.get("status") == "done")
    status = _music_video_job_status(mv)
    progress = None
    if clip_count > 0 and status.is_active:
        progress = round(clips_done / clip_count * 100, 1)

    label = f"MusicVideo: {mv.name or f'#{mv.id}'}"
    if mv.current_stage == "generating" and clip_count:
        label = f"MusicVideo: {mv.name} ({clips_done}/{clip_count} clips)"

    return Job(
        id=f"music_video:{mv.id}",
        kind=JobKind.MUSIC_VIDEO,
        native_id=mv.id,
        status=status,
        label=label,
        progress=progress,
        started_at=getattr(mv, "created_at", None),
        finished_at=getattr(mv, "updated_at", None) if status.is_terminal else None,
        duration_s=_compute_duration(
            getattr(mv, "created_at", None),
            getattr(mv, "updated_at", None) if status.is_terminal else None,
        ),
        cancellable=mv.current_stage in ("generating", "assembling", "awaiting_approval"),
        error_message=(mv.error_blob or {}).get("message") if isinstance(mv.error_blob, dict) else None,
        metadata={
            "music_video_id": mv.id,
            "current_stage": mv.current_stage,
            "status": mv.status,
            "clip_count": clip_count,
            "clips_done": clips_done,
            "output_document_id": mv.output_document_id,
        },
    )


def _as_status_object(data: dict):
    """A batch status dict (batch_metadata.json) read the way batch_failure reads
    a BatchVideoStatus."""
    from types import SimpleNamespace

    results = [SimpleNamespace(success=bool(r.get("success")), error=r.get("error"), error_kind=r.get("error_kind"))
               for r in data.get("results") or [] if isinstance(r, dict)]
    return SimpleNamespace(status=data.get("status"), error=data.get("error"),
                           error_kind=data.get("error_kind"), results=results)


def adapt_video_gen(status) -> Job:
    """BatchVideoStatus (or dict) → Job for the Jobs page."""
    if isinstance(status, dict):
        batch_id = status.get("batch_id") or status.get("id") or "unknown"
        native_status = status.get("status")
        total = int(status.get("total_videos") or 0)
        completed = int(status.get("completed_videos") or 0)
        failed = int(status.get("failed_videos") or 0)
        metadata = status.get("metadata") or {}
        error = status.get("error")
        start_time = _parse_ts(status.get("start_time"))
        end_time = _parse_ts(status.get("end_time"))
        is_running = bool(status.get("is_running"))
    else:
        batch_id = status.batch_id
        native_status = status.status
        total = status.total_videos
        completed = status.completed_videos
        failed = status.failed_videos
        metadata = status.metadata or {}
        error = status.error
        start_time = status.start_time
        end_time = status.end_time
        is_running = native_status == "running"

    status_enum = map_status(JobKind.VIDEO_GEN, native_status)
    failure = None
    if status_enum in (JobStatus.FAILED, JobStatus.CANCELLED):
        failure = batch_failure(_as_status_object(status) if isinstance(status, dict) else status)
        if failure:
            error = failure_text(failure["kind"], failure["message"])
    display = metadata.get("display_name") or batch_id
    stage = None
    progress_pct = None
    if isinstance(status, dict):
        stage = status.get("stage")
        progress_pct = status.get("progress_pct")
    else:
        stage = getattr(status, "stage", None)
        progress_pct = getattr(status, "progress_pct", None)
    label = f"VideoGen: {display}"
    if stage and stage not in ("queued", "done"):
        label = f"VideoGen [{stage}]: {display}"
    progress = None
    if progress_pct is not None:
        try:
            progress = float(progress_pct)
        except (TypeError, ValueError):
            progress = None
    if progress is None and total > 0:
        progress = round((completed + failed) / total * 100, 1)

    return Job(
        id=f"video_gen:{batch_id}",
        kind=JobKind.VIDEO_GEN,
        native_id=batch_id,
        status=status_enum,
        label=label,
        progress=progress,
        started_at=start_time,
        finished_at=end_time if status_enum.is_terminal else None,
        duration_s=_compute_duration(start_time, end_time if status_enum.is_terminal else None),
        cancellable=status_enum.is_active,
        error_message=error,
        metadata={
            "batch_id": batch_id,
            "total_videos": total,
            "completed_videos": completed,
            "failed_videos": failed,
            "display_name": metadata.get("display_name"),
            "model": metadata.get("model"),
            "is_running": is_running,
            "queue_position": metadata.get("queue_position"),
            "stage": stage,
            "failure": failure,
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


def _load_video_gen(batch_id):
    from backend.services.batch_video_generator import get_batch_video_generator
    return get_batch_video_generator().get_batch_status(str(batch_id))


def _load_music_video(native_id):
    from backend.models import MusicVideo, db
    return db.session.get(MusicVideo, int(native_id))


# Per-kind (loader, adapter) pairs. Add a new kind here + a single adapter
# function and the rest of the system (API resource, socket emitter,
# Tasks/Jobs page) picks it up automatically.
REGISTRY: dict[JobKind, tuple[LoaderFn, AdapterFn]] = {
    JobKind.TASK: (_load_task, adapt_task),
    JobKind.OUTREACH: (_load_task, adapt_outreach_task),
    JobKind.PUBLISH: (_load_task, adapt_publish_task),
    JobKind.WEBSITE: (_load_task, adapt_website_task),
    JobKind.TRAINING: (_load_training, adapt_training_job),
    JobKind.SELF_IMPROVEMENT: (_load_self_improvement, adapt_self_improvement),
    JobKind.EXPERIMENT: (_load_experiment, adapt_experiment),
    JobKind.DEMO: (_load_demo_step, adapt_demo_step),
    JobKind.UNIFIED_PROGRESS: (_load_unified_progress, adapt_unified_progress),
    JobKind.VIDEO_GEN: (_load_video_gen, adapt_video_gen),
    JobKind.MUSIC_VIDEO: (_load_music_video, adapt_music_video),
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
