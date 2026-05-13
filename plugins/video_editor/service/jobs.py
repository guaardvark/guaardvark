"""In-memory job table for the video_editor plugin.

Threaded worker pool — mirrors the vision_pipeline pattern. No Celery, no
Redis. The plugin restart wipes the table; acceptable for single-user Guaardvark
where any in-flight render either finished (output is on disk + registered) or
gets restarted by the user.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | failed
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    progress: float = 0.0  # 0.0 .. 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }


class JobTable:
    def __init__(self, max_entries: int = 200, worker_threads: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._executor = ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix="video_editor_job",
        )

    def submit(self, kind: str, fn: Callable[[Job], dict[str, Any]]) -> Job:
        """Create a Job and schedule fn(job) on the worker pool."""
        job = Job(id=uuid.uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            self._gc_if_full()
        self._executor.submit(self._run, job, fn)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, *, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def _run(self, job: Job, fn: Callable[[Job], dict[str, Any]]) -> None:
        with self._lock:
            job.status = "running"
            job.started_at = time.time()
        try:
            result = fn(job)
            with self._lock:
                job.result = result
                job.status = "done"
                job.progress = 1.0
                job.finished_at = time.time()
        except Exception as e:  # noqa: BLE001 — surface anything to the caller
            logger.exception("job %s (%s) failed", job.id, job.kind)
            with self._lock:
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
                job.status = "failed"
                job.finished_at = time.time()

    def _gc_if_full(self) -> None:
        if len(self._jobs) <= self._max_entries:
            return
        # Drop oldest terminal jobs first.
        terminal = sorted(
            (j for j in self._jobs.values() if j.status in ("done", "failed")),
            key=lambda j: j.finished_at or 0,
        )
        overflow = len(self._jobs) - self._max_entries
        for j in terminal[:overflow]:
            self._jobs.pop(j.id, None)
