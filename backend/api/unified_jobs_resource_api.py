"""Unified /api/jobs resource.

The new wire-format endpoints introduced by Phase 3 of the Tasks/Jobs
unification plan. Reads through the adapter registry in
`backend.services.job_registry`; never touches native tables directly
beyond the adapter loader functions.

Existing /api/tasks/* and /api/meta/active_jobs endpoints stay live —
this file is additive. Phase 8 (deprecation sweep) will inventory their
callers, migrate where safe, and alias the rest. Until then both shapes
coexist; consumers can adopt /api/jobs/* on their own schedule.

Endpoints:
    GET  /api/jobs              List with filters: kind, status, limit, since
    GET  /api/jobs/<id>         Detail for one job (id="kind:native_id")
    GET  /api/jobs/active       Currently in-flight (terminal jobs excluded)
    GET  /api/jobs/summary      Counts per (kind, status) for dashboard chips

Cancel-by-id (POST /api/jobs/<id>/cancel) lands in Phase 7. History
(GET /api/jobs/history) lands in Phase 5 with the job_history table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from flask import Blueprint, jsonify, request

from backend.services.job_registry import (
    REGISTRY,
    adapt_task,
    adapt_training_job,
    adapt_self_improvement,
    adapt_experiment,
    adapt_demo_step,
    adapt_unified_progress,
    get_job,
    parse_job_id,
)
from backend.services.job_types import Job, JobKind, JobStatus

logger = logging.getLogger(__name__)

unified_jobs_resource_bp = Blueprint(
    "unified_jobs_resource", __name__, url_prefix="/api/jobs"
)

# Hard cap on list endpoint to prevent runaway queries from the dashboard.
# Pagination beyond this needs explicit user action (or a kind filter).
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


# ---------- list collectors --------------------------------------------------

def _collect_tasks(*, since: datetime | None, limit: int) -> Iterable[Job]:
    from backend.models import Task as DBTask, db
    q = db.session.query(DBTask).order_by(DBTask.updated_at.desc())
    if since:
        q = q.filter(DBTask.updated_at >= since)
    for row in q.limit(limit).all():
        yield adapt_task(row)


def _collect_training(*, since: datetime | None, limit: int) -> Iterable[Job]:
    from backend.models import TrainingJob, db
    q = db.session.query(TrainingJob).order_by(TrainingJob.id.desc())
    if since:
        q = q.filter(
            (TrainingJob.completed_at >= since) |
            (TrainingJob.started_at >= since) |
            (TrainingJob.created_at >= since)
        )
    for row in q.limit(limit).all():
        yield adapt_training_job(row)


def _collect_self_improvement(*, since, limit) -> Iterable[Job]:
    try:
        from backend.models import SelfImprovementRun, db
    except ImportError:
        return
    q = db.session.query(SelfImprovementRun).order_by(SelfImprovementRun.id.desc())
    if since and hasattr(SelfImprovementRun, "started_at"):
        q = q.filter(SelfImprovementRun.started_at >= since)
    for row in q.limit(limit).all():
        yield adapt_self_improvement(row)


def _collect_experiments(*, since, limit) -> Iterable[Job]:
    try:
        from backend.models import ExperimentRun, db
    except ImportError:
        return
    q = db.session.query(ExperimentRun).order_by(ExperimentRun.id.desc())
    if since and hasattr(ExperimentRun, "started_at"):
        q = q.filter(ExperimentRun.started_at >= since)
    for row in q.limit(limit).all():
        yield adapt_experiment(row)


def _collect_demo_steps(*, since, limit) -> Iterable[Job]:
    try:
        from backend.models import DemoStep, db
    except ImportError:
        return
    q = db.session.query(DemoStep).order_by(DemoStep.id.desc())
    if since and hasattr(DemoStep, "started_at"):
        q = q.filter(DemoStep.started_at >= since)
    for row in q.limit(limit).all():
        yield adapt_demo_step(row)


def _collect_unified_progress(*, since, limit) -> Iterable[Job]:
    """In-memory ProgressEvents from the singleton broadcaster.

    `since` doesn't apply here — these are by definition recent. Limit
    applies, but the in-memory map is typically small.
    """
    try:
        from backend.utils.unified_progress_system import get_unified_progress
    except ImportError:
        return
    ups = get_unified_progress()
    if not hasattr(ups, "get_active_processes"):
        return
    snapshot = ups.get_active_processes() or {}
    count = 0
    for process_id, event in snapshot.items():
        if count >= limit:
            break
        # Adapter expects a dict; some implementations return objects with
        # __dict__. Normalize.
        payload = event if isinstance(event, dict) else getattr(event, "__dict__", {})
        if not payload.get("process_id"):
            payload = {**payload, "process_id": process_id}
        yield adapt_unified_progress(payload)
        count += 1


# Per-kind collector dispatch. Adding a new kind = add adapter + loader to
# job_registry.py + add a collector here. The list endpoint picks it up.
_COLLECTORS = {
    JobKind.TASK: _collect_tasks,
    JobKind.TRAINING: _collect_training,
    JobKind.SELF_IMPROVEMENT: _collect_self_improvement,
    JobKind.EXPERIMENT: _collect_experiments,
    JobKind.DEMO: _collect_demo_steps,
    JobKind.UNIFIED_PROGRESS: _collect_unified_progress,
}


def _parse_kinds(raw: str | None) -> set[JobKind]:
    """Parse ?kind=task,training into a set of JobKind. Empty/None = all kinds."""
    if not raw:
        return set(_COLLECTORS.keys())
    selected = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            selected.add(JobKind(piece))
        except ValueError:
            logger.warning("/api/jobs: ignoring unknown kind %r", piece)
    return selected or set(_COLLECTORS.keys())


def _parse_statuses(raw: str | None) -> set[JobStatus] | None:
    """Parse ?status=running,failed. None = no filter."""
    if not raw:
        return None
    selected = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            selected.add(JobStatus(piece))
        except ValueError:
            logger.warning("/api/jobs: ignoring unknown status %r", piece)
    return selected or None


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("/api/jobs: bad since %r — ignoring", raw)
        return None


# ---------- routes -----------------------------------------------------------

@unified_jobs_resource_bp.route("", methods=["GET"])
@unified_jobs_resource_bp.route("/", methods=["GET"])
def list_jobs():
    """List jobs across every kind, with optional filters.

    Query params:
        kind     comma-separated JobKind values (default = all)
        status   comma-separated JobStatus values (default = no filter)
        since    ISO 8601 timestamp; only jobs updated/created after (default = none)
        limit    cap per-kind row pull (default 100, max 500)

    Returns a dict with `jobs` (list of Job dicts) and `total` (length).
    Sort order: most-recently-updated first across all kinds.
    """
    kinds = _parse_kinds(request.args.get("kind"))
    status_filter = _parse_statuses(request.args.get("status"))
    since = _parse_since(request.args.get("since"))

    try:
        limit = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    jobs: list[Job] = []
    for kind in kinds:
        collector = _COLLECTORS.get(kind)
        if collector is None:
            continue
        try:
            for job in collector(since=since, limit=limit):
                if status_filter is None or job.status in status_filter:
                    jobs.append(job)
        except Exception as e:
            logger.exception("/api/jobs: collector for %s failed: %s", kind.value, e)

    # Sort by most-recent activity. Use finished_at if terminal else started_at,
    # falling back to a fake epoch so missing timestamps land at the bottom.
    def _sort_key(j: Job) -> datetime:
        ts = j.finished_at or j.started_at
        if ts is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        # Strip tz for stable comparison — naive vs aware mix would otherwise raise.
        return ts.replace(tzinfo=None) if ts.tzinfo else ts

    jobs.sort(key=_sort_key, reverse=True)
    jobs = jobs[:limit]

    return jsonify({
        "jobs": [j.to_dict() for j in jobs],
        "total": len(jobs),
        "applied_filters": {
            "kind": sorted(k.value for k in kinds),
            "status": sorted(s.value for s in status_filter) if status_filter else None,
            "since": since.isoformat() if since else None,
            "limit": limit,
        },
    }), 200


@unified_jobs_resource_bp.route("/active", methods=["GET"])
def active_jobs():
    """Currently in-flight jobs (status in pending/running/paused).

    Convenience wrapper over list_jobs with a fixed status filter — saves
    the dashboard from constructing the query string and saves a sort.
    """
    kinds = _parse_kinds(request.args.get("kind"))
    try:
        limit = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    active = []
    for kind in kinds:
        collector = _COLLECTORS.get(kind)
        if collector is None:
            continue
        try:
            for job in collector(since=None, limit=limit):
                if job.status.is_active:
                    active.append(job)
        except Exception as e:
            logger.exception("/api/jobs/active: collector %s failed: %s", kind.value, e)

    active.sort(
        key=lambda j: j.started_at or datetime.min.replace(tzinfo=None),
        reverse=True,
    )
    active = active[:limit]

    return jsonify({
        "jobs": [j.to_dict() for j in active],
        "total": len(active),
    }), 200


@unified_jobs_resource_bp.route("/summary", methods=["GET"])
def jobs_summary():
    """Counts per (kind, status) for the Tasks/Jobs page header chips.

    Cheap pull — runs the active collector under the hood and tallies. Not
    suitable for big-history queries; that's what /api/jobs/history will
    serve once Phase 5 lands.
    """
    counts: dict[str, dict[str, int]] = {}
    total_active = 0
    total_terminal_24h = 0
    cutoff = datetime.utcnow().replace(tzinfo=None)

    for kind, collector in _COLLECTORS.items():
        try:
            for job in collector(since=None, limit=_MAX_LIMIT):
                bucket = counts.setdefault(kind.value, {})
                bucket[job.status.value] = bucket.get(job.status.value, 0) + 1
                if job.status.is_active:
                    total_active += 1
                elif job.finished_at and (cutoff - job.finished_at.replace(tzinfo=None)).total_seconds() < 86400:
                    total_terminal_24h += 1
        except Exception as e:
            logger.exception("/api/jobs/summary: collector %s failed: %s", kind.value, e)

    return jsonify({
        "by_kind": counts,
        "total_active": total_active,
        "total_terminal_24h": total_terminal_24h,
    }), 200


@unified_jobs_resource_bp.route("/history", methods=["GET"])
def job_history():
    """Paginated terminal-job history from the job_history table.

    Persists across backend restart (unlike the in-memory active list).
    Default retention is keep-forever per the data retention plan; opt-in
    pruners are wired separately under a Settings → Data Retention surface.

    Query params:
        kind     filter to one kind (single value, not multi)
        status   filter to one terminal status (completed/failed/cancelled)
        limit    page size (default 100, max 500)
        offset   pagination offset
    """
    from backend.services.job_history_service import list_history

    kind = request.args.get("kind")
    status = request.args.get("status")
    try:
        limit = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)

    rows = list_history(kind=kind, status=status, limit=limit, offset=offset)
    return jsonify({
        "history": rows,
        "total": len(rows),
        "applied_filters": {
            "kind": kind,
            "status": status,
            "limit": limit,
            "offset": offset,
        },
    }), 200


@unified_jobs_resource_bp.route("/<path:job_id>/cancel", methods=["POST"])
def cancel_job_route(job_id: str):
    """Cancel a job by id, dispatched per-kind.

    Returns 200 with `{cancelled: true, id}` on success, 200 with
    `{cancelled: false, id, reason}` if the kind doesn't support cancel
    or the underlying transport refused. 4xx only on malformed input.
    """
    try:
        kind, native_id = parse_job_id(job_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    from backend.services.job_cancel import cancel_job
    ok = cancel_job(kind, native_id)
    return jsonify({
        "id": job_id,
        "cancelled": ok,
        "reason": None if ok else "Cancel transport refused or kind not cancellable",
    }), 200


@unified_jobs_resource_bp.route("/<path:job_id>", methods=["GET"])
def job_detail(job_id: str):
    """Fetch one job by wire-format id ('kind:native_id').

    `<path:job_id>` rather than `<string:>` because some kinds (UNIFIED_PROGRESS)
    use process_ids that legitimately contain slashes.
    """
    try:
        kind, native_id = parse_job_id(job_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    job = get_job(kind, native_id)
    if job is None:
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    return jsonify(job.to_dict()), 200
