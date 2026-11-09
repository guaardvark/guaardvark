"""Celery stages of a video style analysis: probing → shots → measure → audio → distill.

Each stage loads the job and the analysis document from the job folder, runs
one analyzer stage, saves, advances the job and tail-calls the next task, so
a long measure stage never blocks the worker on the whole run and a cancel
lands between stages. One footer entry follows the run from start to finish.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from celery import Celery

from backend.services.video_style import analyze, jobs
from backend.utils.progress_emitter import emit_progress_event

log = logging.getLogger(__name__)

# job stage name -> (analyzer stage, next job stage)
PIPELINE = {
    "probing": ("probe", "shots"),
    "shots": ("shots", "measure"),
    "measure": ("measure", "audio"),
    "audio": ("audio", "distill"),
    "distill": ("distill", "complete"),
}
STAGE_SHARE = {"probing": (0, 5), "shots": (5, 20), "measure": (20, 85), "audio": (85, 95), "distill": (95, 100)}
PROCESS_TYPE = "analysis"


def _process_id(job_id: str) -> str:
    return f"video_style_{job_id}"


def _emit(job: Dict[str, Any], progress: int, message: str, status: str) -> None:
    emit_progress_event(_process_id(job["id"]), progress, f"Style analysis: {job['name']}: {message}", status,
                        PROCESS_TYPE, {"job_id": job["id"], "stage": job.get("stage")})


def run_stage(job_id: str, stage: str) -> Optional[Dict[str, Any]]:
    """Run one stage for a job. Returns the next stage to dispatch, or None."""
    job = jobs.load_job(job_id)
    if not job or job.get("stage") != stage or job.get("status") in ("failed", "cancelled", "complete"):
        return None
    if jobs.cancel_requested(job_id):
        jobs.update(job_id, status="cancelled", message="cancelled")
        _emit(job, job.get("progress", 0), "cancelled", "cancelled")
        return None
    analyzer_stage, next_stage = PIPELINE[stage]
    lo, hi = STAGE_SHARE[stage]
    if stage == "probing":
        analysis = analyze.new_analysis(job["video_path"], job["dir"], settings=job.get("settings"), name=job["name"])
        _emit(job, 0, "starting", "start")
    else:
        analysis = analyze.load_analysis(job["dir"])
        if analysis is None:
            jobs.update(job_id, status="failed", error="analysis document missing", message="failed")
            _emit(job, lo, "analysis document missing", "error")
            return None

    def progress(_stage: str, pct: int, message: str) -> None:
        overall = int(lo + (hi - lo) * max(0, min(100, pct)) / 100)
        jobs.update(job_id, status="running", progress=overall, message=message)
        _emit(job, overall, message, "processing")

    jobs.update(job_id, status="running", progress=lo, message=f"{stage} started")
    _emit(job, lo, f"{stage}", "processing")
    try:
        analyze.run_stage(analysis, analyzer_stage, progress=progress)
    except Exception as e:  # noqa: BLE001 - recorded on the job; Celery must not retry
        log.exception("video style %s: stage %s failed", job_id, stage)
        jobs.update(job_id, status="failed", error=str(e), message=f"{stage} failed")
        _emit(job, lo, f"{stage} failed: {e}", "error")
        return None
    if next_stage == "complete":
        shots = len(analysis.get("shots") or [])
        jobs.update(job_id, status="complete", stage="complete", progress=100, message=f"{shots} shots measured")
        _emit(job, 100, f"{shots} shots measured", "complete")
        return None
    jobs.update(job_id, stage=next_stage, progress=hi, message=f"{stage} done")
    return next_stage


def dispatch(job_id: str, stage: str = "probing") -> None:
    """Queue one stage. Raises TaskNotStarted (via GuaardvarkCelery) when no worker can take it."""
    from backend.celery_app import celery

    celery.send_task(f"video_style.run_{stage}", args=[job_id])


def create_video_style_tasks(celery_app: Celery):
    from flask import current_app

    tasks = {}
    for stage in PIPELINE:
        def make(stage_name: str):
            @celery_app.task(name=f"video_style.run_{stage_name}")
            def task(job_id: str):
                with current_app.app_context():
                    nxt = run_stage(job_id, stage_name)
                if nxt:
                    dispatch(job_id, nxt)
            return task

        tasks[stage] = make(stage)
    return tasks
