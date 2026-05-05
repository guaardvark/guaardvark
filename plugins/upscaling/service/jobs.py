"""Job state machine and history for video upscaling jobs."""
import threading
import time
import uuid
from collections import OrderedDict
from enum import Enum
from typing import Any, Dict, List, Optional


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobManager:
    """Thread-safe job tracking with ring buffer for completed jobs."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._jobs: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def create_job(
        self,
        input_path: str,
        output_path: str,
        model: str,
        scale: float,
        denoise_strength: float = 0.5,
    ) -> Dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": JobStatus.PENDING.value,
            "input_path": input_path,
            "output_path": output_path,
            "model": model,
            "scale": scale,
            "denoise_strength": denoise_strength,
            "progress": 0.0,
            "fps": 0.0,
            "eta_seconds": None,
            "frames_done": 0,
            "frames_total": 0,
            "error": None,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        return dict(job)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def start_job(self, job_id: str, total_frames: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = JobStatus.RUNNING.value
                job["frames_total"] = total_frames
                job["started_at"] = time.time()

    def update_progress(self, job_id: str, frames_done: int, fps: float = 0.0) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job["frames_total"] > 0:
                job["frames_done"] = frames_done
                job["progress"] = frames_done / job["frames_total"]
                job["fps"] = fps
                remaining = job["frames_total"] - frames_done
                job["eta_seconds"] = round(remaining / fps) if fps > 0 else None

    def complete_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = JobStatus.COMPLETED.value
                job["progress"] = 1.0
                job["completed_at"] = time.time()
                self._evict_old()

    def fail_job(self, job_id: str, error: str = "") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = JobStatus.FAILED.value
                job["error"] = error
                job["completed_at"] = time.time()
                self._evict_old()

    def cancel_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = JobStatus.CANCELLED.value
                job["completed_at"] = time.time()
                self._evict_old()

    @property
    def active_job_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j["status"] in (JobStatus.RUNNING.value, JobStatus.PENDING.value)
            )

    def clear_finished(self) -> int:
        """Remove all completed/failed/cancelled jobs. Returns the count removed."""
        terminal_statuses = (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        )
        with self._lock:
            to_remove = [jid for jid, j in self._jobs.items() if j["status"] in terminal_statuses]
            for jid in to_remove:
                del self._jobs[jid]
            return len(to_remove)

    def _evict_old(self):
        """Remove oldest completed/failed/cancelled jobs beyond max_history."""
        terminal = [
            jid for jid, j in self._jobs.items()
            if j["status"] in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value)
        ]
        while len(terminal) > self.max_history:
            oldest = terminal.pop(0)
            del self._jobs[oldest]
