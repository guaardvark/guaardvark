"""Async job manager for long-running audio generation.

Why this exists
---------------
Voice synthesis is sequential: long transcripts are split into ~220-char chunks
(see voice_gen_chatterbox._split_for_synthesis) and rendered one at a time. A
30k-char transcript is ~170 chunks at ~7s each ≈ 20+ minutes; a triple-size one
is over an hour. A single synchronous HTTP request can't survive that — the
backend proxy caps the upstream call at 600s and returns 504 while the plugin
keeps generating into a dead connection. That 504 is the bug this removes.

Design
------
- Jobs are owned by THIS plugin process. The plugin runs in its own venv and
  cannot import the backend's Celery app; routing long jobs through Celery would
  just move the held-open connection, not remove it.
- A SINGLE worker thread drains a FIFO queue and runs one generation at a time —
  matching the single GPU and the dispatcher's existing per-intent serialization.
- Job state is persisted to disk (one JSON per job) so status survives polling
  gaps and restarts. On restart, a job left 'running'/'queued' is marked 'error'
  (interrupted) — generation can't resume mid-stream, but finished outputs are
  already on disk and registered.
- Progress is reported per-chunk via a callback; cancellation is cooperative
  (the worker sets an Event the backend checks between chunks).

Single-process assumption: the plugin must run uvicorn with one worker.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from backends.base import GenerationCancelled

logger = logging.getLogger(__name__)

# runner(intent_value, params, progress_cb, cancel_event) -> finalized response dict
Runner = Callable[[str, dict, Callable[[int, int, str], None], threading.Event], dict]


@dataclass
class Job:
    id: str
    intent: str
    params: dict
    status: str = "queued"            # queued | running | done | error | cancelled
    progress: dict = field(default_factory=lambda: {"current": 0, "total": 0, "stage": ""})
    result: Optional[dict] = None     # finalized response dict on success
    error: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def public(self) -> dict:
        # params can hold a filesystem path (reference clip) — safe to expose to
        # the local UI, but drop the raw text to keep status payloads small.
        p = dict(self.params)
        if isinstance(p.get("text"), str) and len(p["text"]) > 200:
            p["text"] = p["text"][:200] + f"… ({len(p['text'])} chars)"
        d = asdict(self)
        d["params"] = p
        return d


class JobManager:
    def __init__(self, runner: Runner, jobs_dir: Path, retention: int = 50) -> None:
        self._runner = runner
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._retention = int(retention)
        self._jobs: dict[str, Job] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._queue: "queue.Queue[str]" = queue.Queue()

        self._recover_on_start()

        self._worker = threading.Thread(target=self._loop, name="audio-job-worker", daemon=True)
        self._worker.start()

    # ---- public API -------------------------------------------------------

    def submit(self, intent: str, params: dict) -> str:
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, intent=intent, params=params, created_at=time.time())
        with self._lock:
            self._jobs[job_id] = job
            self._cancels[job_id] = threading.Event()
            self._persist(job)
        self._queue.put(job_id)
        logger.info("Queued audio job %s (intent=%s)", job_id, intent)
        return job_id

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.public() for j in jobs]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("done", "error", "cancelled"):
                return False
            ev = self._cancels.get(job_id)
            if ev:
                ev.set()
            if job.status == "queued":
                # Not started yet — finalize immediately so the UI reacts now.
                job.status = "cancelled"
                job.finished_at = time.time()
                self._persist(job)
            return True

    def clear_finished(self) -> dict:
        """Drop every finished job record from memory and disk.

        Active (queued/running) jobs are untouched and their ids returned so a
        caller sweeping the .jobs directory knows which files still matter.
        """
        with self._lock:
            finished = [j for j in self._jobs.values()
                        if j.status in ("done", "error", "cancelled")]
            for job in finished:
                self._jobs.pop(job.id, None)
                self._cancels.pop(job.id, None)
                try:
                    self._job_path(job.id).unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("Could not remove job record %s: %s", job.id, e)
            active_ids = [j.id for j in self._jobs.values()]
        return {
            "removed": len(finished),
            "removed_ids": [j.id for j in finished],
            "active_ids": active_ids,
        }

    # ---- worker -----------------------------------------------------------

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs.get(job_id)
                cancel_event = self._cancels.get(job_id)
            if job is None or cancel_event is None:
                continue
            if job.status == "cancelled":   # cancelled while queued
                continue

            with self._lock:
                job.status = "running"
                job.started_at = time.time()
                self._persist(job)

            def progress_cb(current: int, total: int, stage: str = "synthesizing") -> None:
                with self._lock:
                    job.progress = {"current": current, "total": total, "stage": stage}
                    self._persist(job)

            try:
                result = self._runner(job.intent, dict(job.params), progress_cb, cancel_event)
                with self._lock:
                    job.result = result
                    job.status = "done"
            except GenerationCancelled:
                with self._lock:
                    job.status = "cancelled"
            except Exception as e:  # noqa: BLE001 — surface any backend failure as job error
                logger.exception("Audio job %s failed", job_id)
                with self._lock:
                    job.status = "error"
                    job.error = f"{type(e).__name__}: {e}"
            finally:
                with self._lock:
                    job.finished_at = time.time()
                    self._persist(job)
                    self._prune()

    # ---- persistence ------------------------------------------------------

    def _job_path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def _persist(self, job: Job) -> None:
        # Atomic write: tmp + os.replace so a status read never sees a half file.
        tmp = self._job_path(job.id).with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(asdict(job)))
            os.replace(tmp, self._job_path(job.id))
        except OSError as e:
            logger.warning("Could not persist job %s: %s", job.id, e)

    def _recover_on_start(self) -> None:
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                job = Job(**data)
            except Exception as e:  # noqa: BLE001
                logger.warning("Skipping unreadable job file %s: %s", f, e)
                continue
            if job.status in ("queued", "running"):
                # The worker that owned it is gone; it cannot be resumed.
                job.status = "error"
                job.error = "interrupted by service restart"
                job.finished_at = time.time()
                self._persist(job)
            self._jobs[job.id] = job
            self._cancels[job.id] = threading.Event()
        if self._jobs:
            logger.info("Recovered %d audio job record(s) from disk", len(self._jobs))

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values()
                    if j.status in ("done", "error", "cancelled")]
        if len(finished) <= self._retention:
            return
        finished.sort(key=lambda j: j.finished_at or 0.0)
        for job in finished[: len(finished) - self._retention]:
            self._jobs.pop(job.id, None)
            self._cancels.pop(job.id, None)
            try:
                self._job_path(job.id).unlink(missing_ok=True)
            except OSError:
                pass
