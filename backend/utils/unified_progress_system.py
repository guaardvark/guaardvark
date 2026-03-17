# backend/utils/unified_progress_system.py
# Unified Progress System - Consolidates all progress tracking mechanisms
# Replaces fragmented progress_emitter.py, progress_manager.py, and window events

import json
import logging
import uuid
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
from enum import Enum
import threading
import time

logger = logging.getLogger(__name__)


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
        additional_data: Optional[Dict[str, Any]] = None,
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
            **self.additional_data,
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
            if (
                not self._nvidia_smi_path
                or not Path(str(self._nvidia_smi_path)).is_file()
            ):
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
                shell=False,  # Explicitly disable shell for security
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

                    mem_percent = (
                        round(float((used_mem / total_mem) * 100), 2)
                        if total_mem > 0
                        else 0
                    )
                    power_percent = (
                        round(float((power_draw / power_limit) * 100), 2)
                        if power_draw is not None and power_limit is not None
                        else None
                    )

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
                        "tool": "nvidia-smi",
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
                subprocess.check_output(
                    [str(self._nvitop_path), "--version"], text=True, timeout=5
                )
            return {
                "available": True,
                "tool": "nvitop",
                "note": "nvitop detected but detailed metrics not implemented",
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
        self._gpu_monitor = GPUMonitor()
        self._gpu_monitoring_enabled = True

    def initialize(
        self, output_dir: Optional[str] = None, socketio=None, flask_app=None
    ):
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

            if (
                hasattr(current_app, "extensions")
                and "socketio" in current_app.extensions
            ):
                return current_app.extensions["socketio"]
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
        process_id: Optional[str] = None,
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
            message=f"Starting {process_type.value}..."
            + (f" ({description})" if description else ""),
            status=ProcessStatus.START,
            process_type=process_type,
            additional_data=additional_data or {},
        )

        with self._lock:
            # Cancel any pending cleanup timer from a previous run with the same ID
            # This prevents a stale cleanup from nuking a freshly re-created process
            cleanup_key = f"{safe_process_id}_cleanup"
            if cleanup_key in self._timeout_timers:
                self._timeout_timers[cleanup_key].cancel()
                self._timeout_timers.pop(cleanup_key, None)
                logger.info(
                    f"Cancelled stale cleanup timer for re-created process: {safe_process_id}"
                )

            self._active_processes[safe_process_id] = event
            self._process_history[safe_process_id] = [event]

        # Emit the event through all channels
        self._emit_event(event)

        # Create file-based tracking if enabled
        if self._file_based_enabled:
            self._create_file_tracking(safe_process_id, event)

        # Schedule initial timeout for stuck processes (10 minutes)
        # This ensures even newly created processes that never update will timeout
        self._schedule_timeout_timer(
            safe_process_id, 2400.0
        )  # 40 minutes — indexing can take 15-25 min per doc

        logger.info(
            f"Created progress process: {safe_process_id} ({process_type.value}) with 40min timeout"
        )
        return safe_process_id

    def update_process(
        self,
        process_id: str,
        progress: int,
        message: str,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update an existing process"""
        # Validate process_id to prevent directory traversal
        if not process_id or ".." in process_id or "/" in process_id:
            logger.error(f"Invalid process_id format: {process_id}")
            return False

        with self._lock:
            if process_id not in self._active_processes:
                # Try to load from file system (for cross-process communication) with retry
                max_retries = 5  # Increased from 3 to 5 attempts
                for attempt in range(max_retries):
                    if self._load_process_from_file(process_id):
                        logger.info(
                            f"Successfully loaded process {process_id} on attempt {attempt + 1}"
                        )
                        break
                    if attempt < max_retries - 1:
                        # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s
                        wait_time = 0.1 * (2**attempt)
                        logger.debug(
                            f"Process {process_id} load attempt {attempt + 1} failed, retrying in {wait_time}s"
                        )
                        time.sleep(wait_time)
                else:
                    logger.warning(
                        f"Process {process_id} not found for update after {max_retries} attempts"
                    )
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
                additional_data=merged_additional_data,
            )

            self._active_processes[process_id] = event
            self._process_history[process_id].append(event)

        # Emit the event through all channels
        self._emit_event(event)

        # Update file-based tracking if enabled
        if self._file_based_enabled:
            self._update_file_tracking(process_id, event)

        # Reschedule timeout timer for this process (5 minutes from now)
        # This extends the timeout each time the process is updated
        self._schedule_timeout_timer(
            process_id, 2400.0
        )  # 40 minutes — reset on each update

        return True

    def complete_process(
        self,
        process_id: str,
        message: str = "Complete",
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a process as complete"""
        return self._finish_process(
            process_id, ProcessStatus.COMPLETE, message, additional_data
        )

    def error_process(
        self,
        process_id: str,
        message: str = "Error occurred",
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a process as error"""
        return self._finish_process(
            process_id, ProcessStatus.ERROR, message, additional_data
        )

    def cancel_process(
        self,
        process_id: str,
        message: str = "Cancelled",
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Cancel a process"""
        return self._finish_process(
            process_id, ProcessStatus.CANCELLED, message, additional_data
        )

    def _finish_process(
        self,
        process_id: str,
        status: ProcessStatus,
        message: str,
        additional_data: Optional[Dict[str, Any]] = None,
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
                progress=(
                    100 if status == ProcessStatus.COMPLETE else current_event.progress
                ),
                message=message,
                status=status,
                process_type=current_event.process_type,
                additional_data=merged_additional_data,
            )

            self._active_processes[process_id] = event
            self._process_history[process_id].append(event)

        # Emit the event through all channels
        self._emit_event(event)

        # Update file-based tracking if enabled
        if self._file_based_enabled:
            self._finish_file_tracking(process_id, event)

        # Cancel any existing timeout timers since process is now finished
        self._cancel_timeout_timer(process_id)

        # BUG FIX: Reduced cleanup delay from 30s to 5s
        # The 30s delay caused periodic frontend syncs (every 30s) to pull stale
        # completed jobs back into activeProcesses, preventing ProgressFooterBar from hiding
        # Updated to 60s to give frontend enough time to read final state before disk cleanup
        cleanup_timer = threading.Timer(60.0, self._cleanup_process, args=[process_id])
        cleanup_timer.start()
        self._timeout_timers[f"{process_id}_cleanup"] = cleanup_timer

        logger.info(f"Finished progress process: {process_id} ({status.value})")
        return True

    def _schedule_timeout_timer(self, process_id: str, timeout_seconds: float):
        """Schedule or reschedule a timeout timer for a process"""
        # Cancel any existing timeout timer first
        self._cancel_timeout_timer(process_id)

        # Schedule new timeout timer
        timeout_timer = threading.Timer(
            timeout_seconds, self._timeout_stuck_process, args=[process_id]
        )
        timeout_timer.start()
        self._timeout_timers[f"{process_id}_timeout"] = timeout_timer

        logger.debug(f"Scheduled timeout timer for {process_id}: {timeout_seconds}s")

    def _cancel_timeout_timer(self, process_id: str):
        """Cancel timeout timer for a process"""
        timeout_key = f"{process_id}_timeout"
        if timeout_key in self._timeout_timers:
            self._timeout_timers[timeout_key].cancel()
            self._timeout_timers.pop(timeout_key, None)
            logger.debug(f"Cancelled timeout timer for {process_id}")

    def _emit_event(self, event: ProgressEvent):
        """Emit progress event through all available channels"""
        event_data = event.to_dict()

        # 1. Always publish to Redis so the Flask relay thread can re-emit to actual clients.
        # This is critical because Celery workers have a SocketIO instance (from create_app)
        # but no connected clients — direct SocketIO emit goes nowhere in Celery context.
        try:
            import redis as _redis

            r = _redis.Redis(host="localhost", port=6379, db=0)
            r.publish("guaardvark:progress", json.dumps(event_data))
        except Exception:
            pass  # Redis unavailable — fall through to direct SocketIO

        # 2. Also emit directly via SocketIO (works in Flask process where clients are connected)
        if self._socketio_enabled:
            socketio = self._get_socketio()
            if socketio:
                try:
                    socketio.emit(
                        "job_progress", event_data, to=event.process_id, namespace="/"
                    )
                    socketio.emit(
                        "job_progress", event_data, to="global_progress", namespace="/"
                    )
                except Exception as e:
                    logger.error(f"Failed to emit SocketIO progress event: {e}")

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
                "additional_data": event.additional_data,
            }

            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=4)

        except Exception as e:
            logger.error(f"Failed to create file tracking for {process_id}: {e}")

    def _load_process_from_file(self, process_id: str) -> bool:
        """Load a process from file system into memory (for cross-process communication)"""
        if not self._output_dir:
            return False

        try:
            progress_dir = Path(str(self._output_dir)) / ".progress_jobs"
            job_dir = progress_dir / process_id
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
                additional_data=metadata.get("additional_data", {}),
            )

            # Add to active processes in memory
            self._active_processes[process_id] = event

            # Initialize process history list if it doesn't exist
            if process_id not in self._process_history:
                self._process_history[process_id] = [event]

            logger.info(
                f"Loaded process {process_id} from file system for cross-process update"
            )
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
            metadata_file = progress_dir / process_id / "metadata.json"

            if metadata_file.exists():
                with open(metadata_file, "r+", encoding="utf-8") as f:
                    metadata = json.load(f)
                    metadata.update(
                        {
                            "status": event.status.value,
                            "progress": event.progress,
                            "message": event.message,
                            "last_update_utc": event.timestamp.isoformat(),
                            "additional_data": event.additional_data,
                        }
                    )
                    f.seek(0)
                    json.dump(metadata, f, indent=4)
                    f.truncate()

        except Exception as e:
            logger.error(f"Failed to update file tracking for {process_id}: {e}")

    def _finish_file_tracking(self, process_id: str, event: ProgressEvent):
        """Finish file-based tracking for a process"""
        if not self._output_dir:
            return

        try:
            progress_dir = Path(str(self._output_dir)) / ".progress_jobs"
            metadata_file = progress_dir / process_id / "metadata.json"

            if metadata_file.exists():
                with open(metadata_file, "r+", encoding="utf-8") as f:
                    metadata = json.load(f)
                    metadata.update(
                        {
                            "status": event.status.value,
                            "progress": event.progress,
                            "message": event.message,
                            "last_update_utc": event.timestamp.isoformat(),
                            "is_complete": True,
                            "completion_time_utc": event.timestamp.isoformat(),
                            "additional_data": event.additional_data,
                        }
                    )
                    f.seek(0)
                    json.dump(metadata, f, indent=4)
                    f.truncate()

        except Exception as e:
            logger.error(f"Failed to finish file tracking for {process_id}: {e}")

    def _timeout_stuck_process(self, process_id: str):
        """Handle timeout for stuck processes"""
        with self._lock:
            # Check if process still exists before timing it out
            if process_id not in self._active_processes:
                logger.debug(f"Process {process_id} already completed before timeout")
                return

            logger.warning(f"⏰ Process {process_id} timed out - marking as error")

        # Call error_process outside the lock to avoid deadlock
        self.error_process(process_id, "Process timed out after 5 minutes")

    def _cleanup_process(self, process_id: str):
        """Clean up completed processes from memory and file system"""
        with self._lock:
            if process_id in self._active_processes:
                current = self._active_processes[process_id]
                # Guard: don't clean up if the process was re-created and is still active
                if current.status not in (
                    ProcessStatus.COMPLETE,
                    ProcessStatus.ERROR,
                    ProcessStatus.CANCELLED,
                ):
                    logger.info(
                        f"⏭️ Skipping cleanup for {process_id} — process is active (status={current.status.value})"
                    )
                    return
                self._active_processes.pop(process_id, None)
                logger.info(f"🧹 Cleaned up process from memory: {process_id}")

        # Clean up all timers associated with this process
        timers_to_remove = []
        for timer_key in self._timeout_timers:
            if timer_key.startswith(process_id):
                self._timeout_timers[timer_key].cancel()
                timers_to_remove.append(timer_key)

        for timer_key in timers_to_remove:
            self._timeout_timers.pop(timer_key, None)

        # Also clean up file-based tracking
        if self._file_based_enabled and self._output_dir:
            try:
                progress_dir = Path(self._output_dir) / ".progress_jobs"
                job_dir = progress_dir / process_id
                if job_dir.exists():
                    shutil.rmtree(job_dir)
                    logger.info(f"🧹 Cleaned up process files: {process_id}")
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

    def get_process_history(self, process_id: str) -> List[ProgressEvent]:
        """Get history for a specific process"""
        with self._lock:
            return self._process_history.get(process_id, []).copy()

    def get_processes_by_type(self, process_type: ProcessType) -> List[ProgressEvent]:
        """Get all active processes of a specific type"""
        with self._lock:
            return [
                event
                for event in self._active_processes.values()
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
                    "process_count": 0,
                }

            # Calculate aggregate progress
            total_progress = sum(
                event.progress for event in self._active_processes.values()
            )
            average_progress = total_progress / len(self._active_processes)

            # Get most recent message
            most_recent = max(
                self._active_processes.values(), key=lambda e: e.timestamp
            )

            return {
                "active": True,
                "progress": round(average_progress),
                "message": most_recent.message,
                "process_count": len(self._active_processes),
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
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update process with GPU monitoring information"""
        # Get GPU metrics if monitoring is enabled
        if self._gpu_monitoring_enabled:
            try:
                gpu_metrics = self._gpu_monitor.get_gpu_metrics()
                if gpu_metrics.get("available"):
                    additional_data = additional_data or {}
                    additional_data["gpu_metrics"] = gpu_metrics
                    additional_data["gpu_processing_active"] = (
                        self._gpu_monitor.is_gpu_processing_active()
                    )
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


def update_progress(
    process_id: str, progress: int, message: str, process_type: str = "unknown"
):
    """Update progress (backward compatibility)"""
    return _unified_progress.update_process(process_id, progress, message)


def complete_progress(
    process_id: str, message: str = "Complete", process_type: str = "unknown"
):
    """Complete progress (backward compatibility)"""
    return _unified_progress.complete_process(process_id, message)


def error_progress(
    process_id: str, message: str = "Error", process_type: str = "unknown"
):
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
        self.process_id = _unified_progress.create_process(
            self.process_type, self.description
        )
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
