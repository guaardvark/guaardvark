"""On-disk record of an analysis run: data/outputs/video_style/<job_id>/job.json.

A run has no database row. The folder holds the job record, the analysis
document and the keyframes, so a finished analysis survives restarts and
MCP's outputs resources can serve its frames. A cancel is a flag file the
next stage reads before it starts.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

FOLDER = "video_style"
STAGES = ("probing", "shots", "measure", "audio", "distill", "complete")
STATUSES = ("pending", "running", "complete", "failed", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jobs_root() -> Path:
    from backend.config import OUTPUT_DIR

    return Path(OUTPUT_DIR) / FOLDER


def job_dir(job_id: str) -> Path:
    if not job_id or "/" in job_id or ".." in job_id:
        raise ValueError("bad job id")
    return jobs_root() / job_id


def new_job(video_path: str | os.PathLike, *, name: Optional[str] = None, settings: Optional[Dict[str, Any]] = None,
            document_id: Optional[int] = None) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)
    job = {
        "id": job_id,
        "name": name or Path(video_path).stem,
        "video_path": str(video_path),
        "document_id": document_id,
        "settings": settings or {},
        "status": "pending",
        "stage": "probing",
        "progress": 0,
        "message": "queued",
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
        "dir": str(d),
    }
    save_job(job)
    return job


def save_job(job: Dict[str, Any]) -> None:
    job["updated_at"] = _now()
    path = Path(job["dir"]) / "job.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, indent=1))
    tmp.replace(path)


def load_job(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        path = job_dir(job_id) / "job.json"
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    root = jobs_root()
    if not root.is_dir():
        return []
    jobs = []
    for d in root.iterdir():
        p = d / "job.json"
        if p.is_file():
            try:
                jobs.append(json.loads(p.read_text()))
            except ValueError:
                continue
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs[:limit]


def update(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    job = load_job(job_id)
    if not job:
        return None
    job.update(fields)
    if fields.get("status") in ("complete", "failed", "cancelled") and not job.get("finished_at"):
        job["finished_at"] = _now()
    save_job(job)
    return job


def request_cancel(job_id: str) -> bool:
    job = load_job(job_id)
    if not job or job["status"] in ("complete", "failed", "cancelled"):
        return False
    (Path(job["dir"]) / "cancel").touch()
    update(job_id, status="cancelled", message="cancelled", progress=job.get("progress", 0))
    return True


def cancel_requested(job_id: str) -> bool:
    try:
        return (job_dir(job_id) / "cancel").exists()
    except ValueError:
        return False


def analysis_path(job: Dict[str, Any]) -> Path:
    return Path(job["dir"]) / "analysis.json"


def public_frame_url(path: str) -> Optional[str]:
    """The /api/outputs URL of a keyframe under the outputs folder, or None."""
    from backend.config import OUTPUT_DIR

    try:
        rel = Path(path).resolve().relative_to(Path(OUTPUT_DIR).resolve())
    except ValueError:
        return None
    return f"/api/outputs/{rel.as_posix()}"
