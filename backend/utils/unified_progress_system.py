# backend/utils/unified_progress_system.py
# Unified Progress System - Consolidates all progress tracking mechanisms
# Replaces fragmented progress_emitter.py, progress_manager.py, and window events

import json
import logging
import os
import uuid
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Callable
from enum import Enum
import threading
import time

from backend.utils.path_guard import PathEscapesRoot, contained

logger = logging.getLogger(__name__)


def _duration_words(seconds: float) -> str:
    """A timer's length as a person says it: "40 minutes", "3 hours"."""
    minutes = max(1, round(seconds / 60))
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically so readers never see a truncated/empty file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
    os.replace(tmp, path)


# Open processes by the thread that opened them, across every UnifiedProgressSystem
# instance (the image batch keeps its own): footer_jobs asks whether a unit of
# work already shows in the footer before it opens another entry for it. Keyed
# by the Thread object, not its ident: the OS reuses idents, and a process left
# open by a finished thread must not make a later thread look like it has one.
_open_by_thread: Dict[threading.Thread, Set[str]] = {}
_open_lock = threading.Lock()


def _note_open(process_id: str) -> None:
    with _open_lock:
        for thread in [t for t in _open_by_thread if not t.is_alive()]:
            del _open_by_thread[thread]
        _open_by_thread.setdefault(threading.current_thread(), set()).add(process_id)


def _note_closed(process_id: str) -> None:
    with _open_lock:
        for thread in list(_open_by_thread):
            _open_by_thread[thread].discard(process_id)
            if not _open_by_thread[thread]:
                del _open_by_thread[thread]


class ProcessStatus(Enum):
    """Process status enumeration"""
    START = "start"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


class ProcessType(Enum):
    """Process type enumeration"""
    INDEXING = "indexing"
    FILE_GENERATION = "file_generation"
    IMAGE_GENERATION = "image_generation"
    LLM_PROCESSING = "llm_processing"
    BACKUP = "backup"
    UPLOAD = "upload"
    TASK_PROCESSING = "task_processing"
    WEB_SCRAPING = "web_scraping"
    TRAINING = "training"
    ANALYSIS = "analysis"
    VOICE_PROCESSING = "voice_processing"
    DOCUMENT_PROCESSING = "document_processing"
    CSV_PROCESSING = "csv_processing"
    WORDPRESS_PULL = "wordpress_pull"
    WORDPRESS_PUSH = "wordpress_push"
    WORDPRESS_PROCESSING = "wordpress_processing"
    VIDEO_RENDER = "video_render"
    UPSCALE = "upscale"
    OUTREACH = "outreach"
    UNKNOWN = "unknown"


class ProgressEvent:
    """Represents a progress event"""
    def __init__(
        self,
        process_id: str,
        progress: int,
        message: str,
        status: ProcessStatus,
        process_type: ProcessType,
        additional_data: Optional[Dict[str, Any]] = None
    ):
        self.process_id = process_id
        self.progress = max(0, min(100, progress))
        self.message = message
        self.status = status
        self.process_type = process_type
        self.timestamp = datetime.now(timezone.utc)
        self.additional_data = additional_data or {}
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "job_id": self.process_id,
            "progress": self.progress,
            "message": self.message,
            "status": self.status.value,
            "process_type": self.process_type.value,
            "timestamp": self.timestamp.isoformat(),
            **self.additional_data
        }


class GPUMonitor:
    """GPU monitoring utility for tracking GPU-bound processing states"""
    
    def __init__(self):
        self._nvidia_smi_path = shutil.which("nvidia-smi")
        self._nvitop_path = shutil.which("nvitop")
        self._gpu_available = bool(self._nvidia_smi_path or self._nvitop_path)
        
    def get_gpu_metrics(self) -> Dict[str, Any]:
        """Get current GPU metrics"""
        if not self._gpu_available:
            return {"available": False, "error": "No GPU monitoring tools found"}
            
        try:
            if self._nvidia_smi_path:
                return self._get_nvidia_smi_metrics()
            elif self._nvitop_path:
                return self._get_nvitop_metrics()
        except Exception as e:
            logger.warning(f"GPU monitoring failed: {e}")
            return {"available": False, "error": str(e)}
            
        return {"available": False, "error": "No GPU monitoring tools available"}
    
    def _get_nvidia_smi_metrics(self) -> Dict[str, Any]:
        """Get metrics using nvidia-smi"""
        try:
            # Validate nvidia-smi path for security
            if not self._nvidia_smi_path or not Path(str(self._nvidia_smi_path)).is_file():
                return {"available": False, "error": "nvidia-smi path invalid"}
                
            output = subprocess.check_output(
                [
                    str(self._nvidia_smi_path),  # Ensure string type
                    "--query-gpu=temperature.gpu,utilization.gpu,memory.used,"
                    "memory.total,power.draw,power.limit",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=5,
                shell=False  # Explicitly disable shell for security
            )
            
            if output:
                parts = [p.strip() for p in output.strip().split(",")]
                if len(parts) >= 6:
                    temp = float(parts[0])
                    utilization = float(parts[1])
                    used_mem = float(parts[2])
                    total_mem = float(parts[3])
                    power_draw = float(parts[4]) if parts[4] != "N/A" else None
                    power_limit = float(parts[5]) if parts[5] != "N/A" else None
                    
                    mem_percent = round(float((used_mem / total_mem) * 100), 2) if total_mem > 0 else 0
                    power_percent = round(float((power_draw / power_limit) * 100), 2) if power_draw is not None and power_limit is not None else None
                    
                    return {
                        "available": True,
                        "temperature": temp,
                        "utilization": utilization,
                        "memory_used_mb": used_mem,
                        "memory_total_mb": total_mem,
                        "memory_percent": mem_percent,
                        "power_draw_w": power_draw,
                        "power_limit_w": power_limit,
                        "power_percent": power_percent,
                        "tool": "nvidia-smi"
                    }
        except subprocess.TimeoutExpired:
            logger.warning("nvidia-smi command timed out")
            return {"available": False, "error": "nvidia-smi timeout"}
        except Exception as e:
            logger.warning(f"nvidia-smi error: {e}")
            return {"available": False, "error": f"nvidia-smi error: {e}"}
            
        return {"available": False, "error": "Invalid nvidia-smi output"}
    
    def _get_nvitop_metrics(self) -> Dict[str, Any]:
        """Get metrics using nvitop (basic implementation)"""
        try:
            # Basic nvitop check - could be enhanced with more detailed parsing
            if self._nvitop_path:
                subprocess.check_output([str(self._nvitop_path), "--version"], text=True, timeout=5)
            return {
                "available": True,
                "tool": "nvitop",
                "note": "nvitop detected but detailed metrics not implemented"
            }
        except Exception as e:
            logger.warning(f"nvitop error: {e}")
            return {"available": False, "error": f"nvitop error: {e}"}
    
    def is_gpu_processing_active(self, threshold: float = 30.0) -> bool:
        """Check if GPU is actively processing (utilization above threshold)"""
        metrics = self.get_gpu_metrics()
        if metrics.get("available") and metrics.get("utilization"):
            return metrics["utilization"] > threshold
        return False
    
    def get_gpu_memory_usage(self) -> Optional[float]:
        """Get GPU memory usage percentage"""
        metrics = self.get_gpu_metrics()
        if metrics.get("available") and metrics.get("memory_percent"):
            return metrics["memory_percent"]
        return None

class UnifiedProgressSystem:
    """Unified progress tracking system that consolidates all progress mechanisms"""
    
    def __init__(self):
        self._active_processes: Dict[str, ProgressEvent] = {}
        self._process_history: Dict[str, List[ProgressEvent]] = {}
        self._listeners: Set[Any] = set()
        self._lock = threading.RLock()
        self._output_dir: Optional[str] = None
        self._socketio = None
        self._flask_app = None  # Store Flask app instance for thread-safe context
        self._file_based_enabled = True
        self._socketio_enabled = True
        self._timeout_timers: Dict[str, threading.Timer] = {}
        # Called once when a process is cancelled (Activity's Cancel), for an owner
        # that cannot poll often enough to see the record before it is cleaned up.
        self._cancel_listeners: Dict[str, Callable[[], None]] = {}
        self._gpu_monitor = GPUMonitor()
        self._gpu_monitoring_enabled = True

    def initialize(self, output_dir: Optional[str] = None, socketio=None, flask_app=None):
        """Initialize the progress system

        Args:
            output_dir: Directory for file-based progress tracking
            socketio: Flask-SocketIO instance
            flask_app: Flask app instance for thread-safe context
        """
        self._output_dir = output_dir
        self._socketio = socketio
        self._flask_app = flask_app
        
        # Create progress directory if file-based tracking is enabled
        if self._file_based_enabled and output_dir:
            try:
                progress_dir = Path(output_dir) / ".progress_jobs"
                progress_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Initialized progress directory: {progress_dir}")
            except Exception as e:
                logger.warning(f"Failed to create progress directory: {e}")
                self._file_based_enabled = False
    
    def _get_socketio(self):
        """Get SocketIO instance with delayed import to avoid circular imports"""
        if self._socketio is not None:
            return self._socketio

        try:
            from flask import current_app  # pyre-ignore[21]
            if hasattr(current_app, 'extensions') and 'socketio' in current_app.extensions:
                return current_app.extensions['socketio']
            else:
                try:
                    from backend.app import socketio  # pyre-ignore[21]
                    return socketio
                except ImportError:
                    return None
        except Exception:
            return None
    
    def create_process(
        self,
        process_type: ProcessType,
        description: str = "",
        additional_data: Optional[Dict[str, Any]] = None,
        process_id: Optional[str] = None
    ) -> str:
        """Create a new progress process and return its ID

        Args:
            process_type: Type of process
            description: Process description
            additional_data: Additional metadata
            process_id: Optional custom process ID (if not provided, generates one)
        """
        if not process_id:
            uid_str = str(uuid.uuid4().hex)
            process_id = f"{process_type.value}_{uid_str[:8]}"
            
        # Ensure process_id is treated strictly as str from this point down for Pyre
        safe_process_id: str = str(process_id)
        
        event = ProgressEvent(
            process_id=safe_process_id,
            progress=0,
            message=f"Starting {process_type.value}..." + (f" ({description})" if description else ""),
            status=ProcessStatus.START,
            process_type=process_type,
            additional_data=additional_data or {}
        )
        
        with self._lock:
            # Cancel any pending cleanup timer from a previous run with the same ID
            # This prevents a stale cleanup from nuking a freshly re-created process
            cleanup_key = f"{safe_process_id}_cleanup"
            if cleanup_key in self._timeout_timers:
                self._timeout_timers[cleanup_key].cancel()
                self._timeout_timers.pop(cleanup_key, None)
                logger.info(f"Cancelled stale cleanup timer for re-created process: {safe_process_id}")

            self._active_processes[safe_process_id] = event
            self._process_history[safe_process_id] = [event]
        _note_open(safe_process_id)

        # Emit the event through all channels
        self._emit_event(event)
        
        # Create file-based tracking if enabled
        if self._file_based_enabled:
            self._create_file_tracking(safe_process_id, event)
        
        # Schedule initial timeout for stuck processes.
        # Video renders (maxed Wan) can run 1–3h; activity-aware Comfy wait
        # keeps them alive — the footer must not declare error at 40min.
        initial_ttl = 10800.0 if process_type == ProcessType.VIDEO_RENDER else 2400.0
        self._schedule_timeout_timer(safe_process_id, initial_ttl)

        logger.info(
            f"Created progress process: {safe_process_id} ({process_type.value}) "
            f"with {int(initial_ttl // 60)}min timeout"
        )
        return safe_process_id
    
    def update_process(
        self,
        process_id: str,
        progress: int,
        message: str,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update an existing process"""
        # Validate process_id to prevent directory traversal
        if not process_id or '..' in process_id or '/' in process_id:
            logger.error(f"Invalid process_id format: {process_id}")
            return False
            
        with self._lock:
            if process_id not in self._active_processes:
                # Try to load from file system (for cross-process communication) with retry
                max_retries = 5  # Increased from 3 to 5 attempts
                for attempt in range(max_retries):
                    if self._load_process_from_file(process_id):
                        logger.info(f"Successfully loaded process {process_id} on attempt {attempt + 1}")
                        break
                    if attempt < max_retries - 1:
                        # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s
                        wait_time = 0.1 * (2 ** attempt)
                        logger.debug(f"Process {process_id} load attempt {attempt + 1} failed, retrying in {wait_time}s")
                        time.sleep(wait_time)
                else:
                    logger.warning(f"Process {process_id} not found for update after {max_retries} attempts")
                    return False
            
            current_event = self._active_processes[process_id]
            
            # Merge additional_data: preserve existing and update with new values
            existing_additional_data = current_event.additional_data or {}
            merged_additional_data = {**existing_additional_data}
            if additional_data:
                merged_additional_data.update(additional_data)
            
            event = ProgressEvent(
                process_id=process_id,
                progress=progress,
                message=message,
                status=ProcessStatus.PROCESSING,
                process_type=current_event.process_type,
                additional_data=merged_additional_data
            )
            
            self._active_processes[process_id] = event
            self._process_history[process_id].append(event)
        
        # Emit the event through all channels
        self._emit_event(event)
        
        # Update file-based tracking if enabled
        if self._file_based_enabled:
            self._update_file_tracking(process_id, event)
        
        # Reschedule timeout timer — video_render uses a long TTL so maxed Wan
        # jobs are not marked error while Comfy is still at 99% util.
        ttl = 10800.0 if event.process_type == ProcessType.VIDEO_RENDER else 2400.0
        self._schedule_timeout_timer(process_id, ttl)
        
        return True
    
    def has_open_process_on_this_thread(self) -> bool:
        """True while a process this thread opened, in any instance, has not finished."""
        with _open_lock:
            return bool(_open_by_thread.get(threading.current_thread()))

    def complete_process(
        self,
        process_id: str,
        message: str = "Complete",
        additional_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Mark a process as complete"""
        return self._finish_process(process_id, ProcessStatus.COMPLETE, message, additional_data)
    
    def error_process(
        self,
        process_id: str,
        message: str = "Error occurred",
        additional_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Mark a process as error"""
        return self._finish_process(process_id, ProcessStatus.ERROR, message, additional_data)
    
    def cancel_process(
        self,
        process_id: str,
        message: str = "Cancelled",
        additional_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Cancel a process, and tell its cancel listener if it has one."""
        cancelled = self._finish_process(process_id, ProcessStatus.CANCELLED, message, additional_data)
        with self._lock:
            listener = self._cancel_listeners.pop(process_id, None)
        if cancelled and listener:
            try:
                listener()
            except Exception as e:  # a listener must not fail the cancel
                logger.warning(f"Cancel listener for {process_id} failed: {e}")
        return cancelled

    def on_cancel(self, process_id: str, callback: Callable[[], None]) -> None:
        """Call `callback` once if `process_id` is cancelled before it is cleaned up."""
        with self._lock:
            self._cancel_listeners[process_id] = callback
    
    def _finish_process(
        self,
        process_id: str,
        status: ProcessStatus,
        message: str,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Internal method to finish a process"""
        with self._lock:
            if process_id not in self._active_processes:
                # Try to load from file system (for cross-process communication)
                if not self._load_process_from_file(process_id):
                    logger.warning(f"Process {process_id} not found for completion")
                    return False
            
            current_event = self._active_processes[process_id]
            
            # Merge additional_data: preserve existing and update with new values
            existing_additional_data = current_event.additional_data or {}
            merged_additional_data = {**existing_additional_data}
            if additional_data:
                merged_additional_data.update(additional_data)
            
            event = ProgressEvent(
                process_id=process_id,
                progress=100 if status == ProcessStatus.COMPLETE else current_event.progress,
                message=message,
                status=status,
                process_type=current_event.process_type,
                additional_data=merged_additional_data
            )
            
            self._active_processes[process_id] = event
            self._process_history[process_id].append(event)
        _note_closed(process_id)
        
        # Emit the event through all channels
        self._emit_event(event)
        
        # Update file-based tracking if enabled
        if self._file_based_enabled:
            self._finish_file_tracking(process_id, event)
        
        # Cancel any existing timeout timers since process is now finished
        self._cancel_timeout_timer(process_id)
        self._schedule_cleanup(process_id)

        logger.info(f"Finished progress process: {process_id} ({status.value})")
        return True

    def _schedule_cleanup(self, process_id: str):
        """Drop a finished process from memory and disk after a short delay."""
        # BUG FIX: Reduced cleanup delay from 30s to 5s
        # The 30s delay caused periodic frontend syncs (every 30s) to pull stale
        # completed jobs back into activeProcesses, preventing ProgressFooterBar from hiding
        # Updated to 60s to give frontend enough time to read final state before disk cleanup
        cleanup_timer = threading.Timer(60.0, self._cleanup_process, args=[process_id])
        # Housekeeping timers must never keep the interpreter alive at exit.
        cleanup_timer.daemon = True
        cleanup_timer.start()
        with self._lock:
            self._timeout_timers[f"{process_id}_cleanup"] = cleanup_timer

    def apply_relayed_event(self, event_data: Dict[str, Any]) -> bool:
        """Bring this process's copy of a job up to date from an event another
        process published (the Redis relay in app.py).

        A job created here and continued in a Celery worker (an upload's
        indexing) is otherwise only updated in the worker, and the copy here
        stays at its last value until its timeout marks it as an error. Only a
        job this process already holds and has not finished is touched, and
        nothing is emitted or published, so the relay cannot loop. The relay
        also hears this process's own events; applying them again changes
        nothing. A worker's "start" for a job held here is its re-creation of
        the same id and is skipped.
        """
        process_id = str(event_data.get("job_id") or "")
        try:
            status = ProcessStatus(event_data.get("status"))
        except ValueError:
            return False
        if not process_id or status == ProcessStatus.START:
            return False
        terminal = status in (ProcessStatus.COMPLETE, ProcessStatus.ERROR, ProcessStatus.CANCELLED)

        with self._lock:
            current = self._active_processes.get(process_id)
            if current is None or current.status in (
                    ProcessStatus.COMPLETE, ProcessStatus.ERROR, ProcessStatus.CANCELLED):
                return False
            progress = event_data.get("progress")
            progress = int(progress) if isinstance(progress, (int, float)) else current.progress
            message = event_data.get("message") or current.message
            # This process's own updates come back through the relay unchanged.
            if (status, progress, message) == (current.status, current.progress, current.message):
                return False
            event = ProgressEvent(
                process_id=process_id,
                progress=progress,
                message=message,
                status=status,
                process_type=current.process_type,
                additional_data=current.additional_data,
            )
            self._active_processes[process_id] = event
            self._process_history.setdefault(process_id, []).append(event)
            # Timers change under the same lock, so a request thread finishing
            # or updating the job cannot interleave with them.
            if terminal:
                _note_closed(process_id)
                self._cancel_timeout_timer(process_id)
                self._schedule_cleanup(process_id)
            else:
                ttl = 10800.0 if event.process_type == ProcessType.VIDEO_RENDER else 2400.0
                self._schedule_timeout_timer(process_id, ttl)
        return True
    
    def _schedule_timeout_timer(self, process_id: str, timeout_seconds: float):
        """Schedule or reschedule a timeout timer for a process"""
        # Cancel any existing timeout timer first
        self._cancel_timeout_timer(process_id)
        
        # Schedule new timeout timer
        timeout_timer = threading.Timer(timeout_seconds, self._timeout_stuck_process, args=[process_id, timeout_seconds])
        timeout_timer.daemon = True
        timeout_timer.start()
        with self._lock:
            self._timeout_timers[f"{process_id}_timeout"] = timeout_timer
        
        logger.debug(f"Scheduled timeout timer for {process_id}: {timeout_seconds}s")
    
    def _cancel_timeout_timer(self, process_id: str):
        """Cancel timeout timer for a process"""
        # The relay thread, request threads and the timers themselves all change
        # this dict; the lock is re-entrant, so callers already holding it are fine.
        with self._lock:
            timer = self._timeout_timers.pop(f"{process_id}_timeout", None)
        if timer is not None:
            timer.cancel()
            logger.debug(f"Cancelled timeout timer for {process_id}")
    
    def _emit_event(self, event: ProgressEvent):
        """Emit progress event through all available channels"""
        event_data = event.to_dict()

        # 1. Always publish to Redis so the Flask relay thread can re-emit to actual clients.
        # This is critical because Celery workers have a SocketIO instance (from create_app)
        # but no connected clients — direct SocketIO emit goes nowhere in Celery context.
        try:
            import redis as _redis
            r = _redis.Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
            r.publish('guaardvark:progress', json.dumps(event_data))
        except Exception:
            pass  # Redis unavailable — fall through to direct SocketIO

        # 2. Also emit directly via SocketIO (works in Flask process where clients are connected)
        if self._socketio_enabled:
            socketio = self._get_socketio()
            if socketio:
                try:
                    socketio.emit("job_progress", event_data, to=event.process_id, namespace='/')
                    socketio.emit("job_progress", event_data, to="global_progress", namespace='/')
                except Exception as e:
                    logger.error(f"Failed to emit SocketIO progress event: {e}")

                # 2b. Phase 4 of the Tasks/Jobs unification — canonical 'job:event'
                # alongside the legacy 'job_progress'. Adapter normalizes the
                # processType/process_type casing inconsistency and produces the
                # same Job shape /api/jobs/* serves. Frontend consumers can move
                # to the new event on their own schedule; old listeners stay
                # working until Phase 8 deprecates them.
                try:
                    from backend.services.job_registry import adapt_unified_progress
                    from backend.services.job_history_service import record_terminal_job
                    job = adapt_unified_progress(event_data)
                    job_dict = job.to_dict()
                    socketio.emit("job:event", job_dict, to="jobs:all", namespace='/')
                    socketio.emit("job:event", job_dict, to=f"jobs:{job.kind.value}", namespace='/')

                    # Phase 5 — persist terminal-status jobs to job_history.
                    # No-ops for non-terminal status; idempotent on retry.
                    record_terminal_job(job)
                except Exception as e:
                    # Non-fatal — old job_progress emit above is still in flight.
                    logger.warning("Canonical job:event emit failed (non-fatal): %s", e)
        
        # 3. Notify listeners
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Progress listener error: {e}")
        
        # 3. Emit window event for frontend compatibility
        try:
            # This would be handled by the frontend to emit window events
            # We'll create a helper function for this
            pass
        except Exception as e:
            logger.debug(f"Window event emission not available: {e}")
    
    def _create_file_tracking(self, process_id: str, event: ProgressEvent):
        """Create file-based tracking for a process"""
        if not self._output_dir:
            return
            
        try:
            progress_dir = Path(str(self._output_dir)) / ".progress_jobs"
            job_dir = progress_dir / process_id
            job_dir.mkdir(parents=True, exist_ok=True)
            
            metadata_file = job_dir / "metadata.json"
            metadata = {
                "job_id": process_id,
                "process_type": event.process_type.value,
                "status": event.status.value,
                "progress": event.progress,
                "message": event.message,
                "start_time_utc": event.timestamp.isoformat(),
                "last_update_utc": event.timestamp.isoformat(),
                "is_complete": False,
                "additional_data": event.additional_data
            }
            
            _atomic_write_json(metadata_file, metadata)
                
        except Exception as e:
            logger.error(f"Failed to create file tracking for {process_id}: {e}")
    
    def _load_process_from_file(self, process_id: str) -> bool:
        """Load a process from file system into memory (for cross-process communication)"""
        if not self._output_dir:
            return False
            
        try:
            progress_dir = Path(str(self._output_dir)) / ".progress_jobs"
            job_dir = contained(progress_dir, process_id)
            metadata_file = job_dir / "metadata.json"
            
            if not metadata_file.exists():
                return False
                
            with open(metadata_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            
            # Reconstruct the ProgressEvent from file
            process_type = ProcessType(metadata["process_type"])
            status = ProcessStatus(metadata["status"])
            
            event = ProgressEvent(
                process_id=process_id,
                progress=metadata["progress"],
                message=metadata["message"],
                status=status,
                process_type=process_type,
                additional_data=metadata.get("additional_data", {})
            )
            
            # Add to active processes in memory
            self._active_processes[process_id] = event
            
            # Initialize process history list if it doesn't exist
            if process_id not in self._process_history:
                self._process_history[process_id] = [event]
            
            logger.info(f"Loaded process {process_id} from file system for cross-process update")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load process {process_id} from file: {e}")
            return False
    
    def _update_file_tracking(self, process_id: str, event: ProgressEvent):
        """Update file-based tracking for a process"""
        if not self._output_dir:
            return
            
        try:
            progress_dir = Path(str(self._output_dir)) / ".progress_jobs"
            metadata_file = contained(progress_dir, process_id, "metadata.json")
            
            if metadata_file.exists():
                raw = metadata_file.read_text(encoding="utf-8")
                metadata = json.loads(raw) if raw.strip() else {}
            else:
                metadata = {"job_id": process_id}
            metadata.update({
                "status": event.status.value,
                "progress": event.progress,
                "message": event.message,
                "last_update_utc": event.timestamp.isoformat(),
                "additional_data": event.additional_data
            })
            _atomic_write_json(metadata_file, metadata)
                    
        except Exception as e:
            logger.error(f"Failed to update file tracking for {process_id}: {e}")
    
    def _finish_file_tracking(self, process_id: str, event: ProgressEvent):
        """Finish file-based tracking for a process"""
        if not self._output_dir:
            return
            
        try:
            progress_dir = Path(str(self._output_dir)) / ".progress_jobs"
            metadata_file = contained(progress_dir, process_id, "metadata.json")
            
            if metadata_file.exists():
                raw = metadata_file.read_text(encoding="utf-8")
                metadata = json.loads(raw) if raw.strip() else {}
            else:
                metadata = {"job_id": process_id}
            metadata.update({
                "status": event.status.value,
                "progress": event.progress,
                "message": event.message,
                "last_update_utc": event.timestamp.isoformat(),
                "is_complete": True,
                "completion_time_utc": event.timestamp.isoformat(),
                "additional_data": event.additional_data
            })
            _atomic_write_json(metadata_file, metadata)
                    
        except Exception as e:
            logger.error(f"Failed to finish file tracking for {process_id}: {e}")
    
    def _timeout_stuck_process(self, process_id: str, timeout_seconds: Optional[float] = None):
        """Handle timeout for stuck processes"""
        with self._lock:
            # Check if process still exists before timing it out
            if process_id not in self._active_processes:
                logger.debug(f"Process {process_id} already completed before timeout")
                return
            
            logger.warning(f"Process {process_id} timed out - marking as error")
        
        # Call error_process outside the lock to avoid deadlock
        waited = f" after {_duration_words(timeout_seconds)}" if timeout_seconds else ""
        self.error_process(process_id, f"Process timed out{waited} without progress")
    
    def _cleanup_process(self, process_id: str):
        """Clean up completed processes from memory and file system"""
        with self._lock:
            if process_id in self._active_processes:
                current = self._active_processes[process_id]
                # Guard: don't clean up if the process was re-created and is still active
                if current.status not in (ProcessStatus.COMPLETE, ProcessStatus.ERROR, ProcessStatus.CANCELLED):
                    logger.info(f"Skipping cleanup for {process_id} — process is active (status={current.status.value})")
                    return
                self._active_processes.pop(process_id, None)
                self._process_history.pop(process_id, None)
                self._cancel_listeners.pop(process_id, None)
                logger.info(f"Cleaned up process from memory: {process_id}")
        
        # Clean up this process's own timers: its exact keys, not a prefix match,
        # which would also cancel the timers of a process whose id starts with this one.
        with self._lock:
            timers = [self._timeout_timers.pop(f"{process_id}{suffix}", None)
                      for suffix in ("_timeout", "_cleanup")]
        for timer in timers:
            if timer is not None:
                timer.cancel()
        
        # Also clean up file-based tracking
        if self._file_based_enabled and self._output_dir:
            try:
                progress_dir = Path(self._output_dir) / ".progress_jobs"
                job_dir = contained(progress_dir, process_id)
                if job_dir.exists():
                    shutil.rmtree(job_dir)
                    logger.info(f"Cleaned up process files: {process_id}")
            except Exception as e:
                logger.error(f"Failed to clean up process files for {process_id}: {e}")
    
    def get_active_processes(self) -> Dict[str, ProgressEvent]:
        """Get all active processes"""
        with self._lock:
            return self._active_processes.copy()
    
    def get_process(self, process_id: str) -> Optional[ProgressEvent]:
        """Get a specific process"""
        with self._lock:
            return self._active_processes.get(process_id)
    
    def get_job_status(self, process_id: str) -> Optional[Dict[str, Any]]:
        """One job's state by its id, or None when nothing knows it.

        Live from this process's memory while it tracks the job; otherwise
        from the metadata.json record the process running it writes under
        OUTPUT_DIR/.progress_jobs, which is the only place a job running in
        the Celery worker shows up here. Both go about a minute after the job
        finishes (_cleanup_process). The id comes from a request, so the
        record is only read from inside that directory.
        """
        event = self.get_process(process_id)
        if event is not None:
            status = event.status.value
            return {
                "job_id": process_id,
                "status": status,
                "progress": event.progress,
                "message": event.message,
                "process_type": event.process_type.value,
                "is_complete": status in ("complete", "error", "cancelled"),
                "updated_at": event.timestamp.isoformat(),
                "additional_data": event.additional_data or {},
                "source": "live",
            }
        if not self._output_dir:
            return None
        try:
            metadata_file = contained(Path(str(self._output_dir)) / ".progress_jobs", process_id, "metadata.json")
        except ValueError:
            return None
        try:
            raw = metadata_file.read_text(encoding="utf-8")
            record = json.loads(raw) if raw.strip() else None
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        return {
            "job_id": process_id,
            "status": record.get("status"),
            "progress": record.get("progress"),
            "message": record.get("message", ""),
            "process_type": record.get("process_type"),
            "is_complete": bool(record.get("is_complete")),
            "updated_at": record.get("last_update_utc"),
            "additional_data": record.get("additional_data") or {},
            "source": "record",
        }

    def get_process_history(self, process_id: str) -> List[ProgressEvent]:
        """Get history for a specific process"""
        with self._lock:
            return self._process_history.get(process_id, []).copy()
    
    def get_processes_by_type(self, process_type: ProcessType) -> List[ProgressEvent]:
        """Get all active processes of a specific type"""
        with self._lock:
            return [
                event for event in self._active_processes.values()
                if event.process_type == process_type
            ]
    
    def add_listener(self, listener: Any):
        """Add a progress event listener"""
        with self._lock:
            self._listeners.add(listener)
    
    def remove_listener(self, listener: Any):
        """Remove a progress event listener"""
        with self._lock:
            self._listeners.discard(listener)
    
    def get_global_progress(self) -> Dict[str, Any]:
        """Get global progress summary"""
        with self._lock:
            if not self._active_processes:
                return {
                    "active": False,
                    "progress": 0,
                    "message": "",
                    "process_count": 0
                }
            
            # Calculate aggregate progress
            total_progress = sum(event.progress for event in self._active_processes.values())
            average_progress = total_progress / len(self._active_processes)
            
            # Get most recent message
            most_recent = max(self._active_processes.values(), key=lambda e: e.timestamp)
            
            return {
                "active": True,
                "progress": round(average_progress),
                "message": most_recent.message,
                "process_count": len(self._active_processes)
            }

    def get_gpu_metrics(self) -> Dict[str, Any]:
        """Get current GPU metrics for monitoring"""
        if not self._gpu_monitoring_enabled:
            return {"enabled": False}
        
        try:
            metrics = self._gpu_monitor.get_gpu_metrics()
            metrics["enabled"] = True
            return metrics
        except Exception as e:
            logger.warning(f"GPU metrics collection failed: {e}")
            return {"enabled": True, "error": str(e)}

    def is_gpu_processing_active(self, threshold: float = 30.0) -> bool:
        """Check if GPU is actively processing"""
        if not self._gpu_monitoring_enabled:
            return False
        
        try:
            return self._gpu_monitor.is_gpu_processing_active(threshold)
        except Exception as e:
            logger.warning(f"GPU processing check failed: {e}")
            return False

    def get_gpu_memory_usage(self) -> Optional[float]:
        """Get GPU memory usage percentage"""
        if not self._gpu_monitoring_enabled:
            return None
        
        try:
            return self._gpu_monitor.get_gpu_memory_usage()
        except Exception as e:
            logger.warning(f"GPU memory check failed: {e}")
            return None

    def update_process_with_gpu_info(
        self,
        process_id: str,
        progress: int,
        message: str,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update process with GPU monitoring information"""
        # Get GPU metrics if monitoring is enabled
        if self._gpu_monitoring_enabled:
            try:
                gpu_metrics = self._gpu_monitor.get_gpu_metrics()
                if gpu_metrics.get("available"):
                    additional_data = additional_data or {}
                    additional_data["gpu_metrics"] = gpu_metrics
                    additional_data["gpu_processing_active"] = self._gpu_monitor.is_gpu_processing_active()
            except Exception as e:
                logger.warning(f"Failed to get GPU metrics for process update: {e}")
        
        return self.update_process(process_id, progress, message, additional_data)


# Global instance
_unified_progress = UnifiedProgressSystem()

def get_unified_progress() -> UnifiedProgressSystem:
    """Get the global unified progress system instance"""
    return _unified_progress

def get_unified_progress_system() -> UnifiedProgressSystem:
    """Get the global unified progress system instance (alias for compatibility)"""
    return _unified_progress

# Convenience functions for backward compatibility
def create_progress_tracker(process_type: str, description: str = "") -> str:
    """Create a progress tracker (backward compatibility)"""
    try:
        process_type_enum = ProcessType(process_type)
    except ValueError:
        process_type_enum = ProcessType.UNKNOWN
    
    return _unified_progress.create_process(process_type_enum, description)

def update_progress(process_id: str, progress: int, message: str, process_type: str = "unknown"):
    """Update progress (backward compatibility)"""
    return _unified_progress.update_process(process_id, progress, message)

def complete_progress(process_id: str, message: str = "Complete", process_type: str = "unknown"):
    """Complete progress (backward compatibility)"""
    return _unified_progress.complete_process(process_id, message)

def error_progress(process_id: str, message: str = "Error", process_type: str = "unknown"):
    """Error progress (backward compatibility)"""
    return _unified_progress.error_process(process_id, message)

# Context manager for easy progress tracking
class ProgressTracker:
    """Context manager for tracking progress of a process"""
    
    def __init__(self, process_type: ProcessType, description: str = ""):
        self.process_type = process_type
        self.description = description
        self.process_id: Optional[str] = None
        
    def __enter__(self):
        self.process_id = _unified_progress.create_process(self.process_type, self.description)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        process_id = self.process_id
        if process_id:
            if exc_type:
                _unified_progress.error_process(process_id, f"Error: {exc_val}")
            else:
                _unified_progress.complete_process(process_id, "Complete")
    
    def update(self, progress: int, message: str):
        """Update progress within the context"""
        process_id = self.process_id
        if process_id:
            _unified_progress.update_process(process_id, progress, message)
    
    def complete(self, message: str = "Complete"):
        """Mark as complete within the context"""
        process_id = self.process_id
        if process_id:
            _unified_progress.complete_process(process_id, message)
    
    def error(self, message: str = "Error"):
        """Mark as error within the context"""
        process_id = self.process_id
        if process_id:
            _unified_progress.error_process(process_id, message) 