# backend/utils/progress_emitter.py
# UNIFIED PROGRESS SYSTEM COMPATIBILITY LAYER
# This file provides backward compatibility for the old progress_emitter API
# All calls are routed to the new unified progress system

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .unified_progress_system import get_unified_progress, ProcessType

logger = logging.getLogger(__name__)


def _get_socketio():
    """Legacy function - not needed in unified system"""
    return None


def emit_progress_event(
    process_id: str,
    progress: int,
    message: str,
    status: str = "processing",
    process_type: str = "unknown",
    additional_data: Optional[Dict[str, Any]] = None,
):
    """
    Emit a unified progress event - routes to unified progress system.

    Args:
        process_id: Unique identifier for the process
        progress: Progress percentage (0-100)
        message: Human-readable status message
        status: Process status ("start", "processing", "complete", "error")
        process_type: Type of process (indexing, file_generation, llm_processing, etc.)
        additional_data: Optional additional data to include in the event
    """
    try:
        progress_system = get_unified_progress()

        # Map old process types to new ProcessType enum
        process_type_mapping = {
            "indexing": ProcessType.INDEXING,
            "file_generation": ProcessType.FILE_GENERATION,
            "image_generation": ProcessType.IMAGE_GENERATION,
            "llm_processing": ProcessType.LLM_PROCESSING,
            "backup": ProcessType.BACKUP,
            "upload": ProcessType.UPLOAD,
            "task_processing": ProcessType.TASK_PROCESSING,
            "web_scraping": ProcessType.WEB_SCRAPING,
            "training": ProcessType.TRAINING,
            "analysis": ProcessType.ANALYSIS,
            "voice_processing": ProcessType.VOICE_PROCESSING,
            "document_processing": ProcessType.DOCUMENT_PROCESSING,
            "csv_processing": ProcessType.CSV_PROCESSING,
            "processing": ProcessType.UNKNOWN,
            "unknown": ProcessType.UNKNOWN,
        }

        process_type_enum = process_type_mapping.get(process_type, ProcessType.UNKNOWN)

        # Handle different status types
        if status == "start":
            # Create or update process
            existing_process = progress_system.get_process(process_id)
            if not existing_process:
                # Use process_id as custom ID if provided (for training jobs, etc.)
                progress_system.create_process(
                    process_type_enum,
                    message,
                    additional_data,
                    process_id=process_id,  # Use the provided process_id
                )
            else:
                progress_system.update_process(
                    process_id, progress, message, additional_data
                )
        elif status == "complete":
            progress_system.complete_process(process_id, message, additional_data)
        elif status == "error":
            progress_system.error_process(process_id, message, additional_data)
        elif status == "cancelled":
            progress_system.cancel_process(process_id, message, additional_data)
        else:  # processing or any other status
            # Ensure process exists before updating
            existing_process = progress_system.get_process(process_id)
            if not existing_process:
                progress_system.create_process(
                    process_type_enum, message, additional_data, process_id=process_id
                )
            progress_system.update_process(
                process_id, progress, message, additional_data
            )

        logger.debug(
            f"Emitted unified progress event for {process_id}: {progress}% - {message}"
        )

    except Exception as e:
        logger.error(f"Failed to emit progress event: {e}")


def create_progress_tracker(process_type: str, description: str = "") -> str:
    """Create a progress tracker - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()

        # Map string to ProcessType enum
        process_type_mapping = {
            "indexing": ProcessType.INDEXING,
            "file_generation": ProcessType.FILE_GENERATION,
            "image_generation": ProcessType.IMAGE_GENERATION,
            "llm_processing": ProcessType.LLM_PROCESSING,
            "backup": ProcessType.BACKUP,
            "upload": ProcessType.UPLOAD,
            "task_processing": ProcessType.TASK_PROCESSING,
            "web_scraping": ProcessType.WEB_SCRAPING,
            "training": ProcessType.TRAINING,
            "analysis": ProcessType.ANALYSIS,
            "voice_processing": ProcessType.VOICE_PROCESSING,
            "document_processing": ProcessType.DOCUMENT_PROCESSING,
            "csv_processing": ProcessType.CSV_PROCESSING,
            "processing": ProcessType.UNKNOWN,
            "unknown": ProcessType.UNKNOWN,
        }

        process_type_enum = process_type_mapping.get(process_type, ProcessType.UNKNOWN)

        process_id = progress_system.create_process(process_type_enum, description)
        logger.info(
            f"Created progress tracker {process_id} via unified progress system"
        )
        return process_id

    except Exception as e:
        logger.error(f"Failed to create progress tracker: {e}")
        # Fallback to simple ID generation
        import uuid

        return str(uuid.uuid4())


def update_progress(
    process_id: str,
    progress: int,
    message: str,
    additional_data: Optional[Dict[str, Any]] = None,
):
    """Update progress - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        progress_system.update_process(process_id, progress, message, additional_data)
        logger.debug(f"Updated progress for {process_id}: {progress}% - {message}")
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")


def complete_progress(
    process_id: str,
    message: str = "Complete",
    additional_data: Optional[Dict[str, Any]] = None,
):
    """Complete progress - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        progress_system.complete_process(process_id, message, additional_data)
        logger.info(f"Completed progress for {process_id}: {message}")
    except Exception as e:
        logger.error(f"Failed to complete progress: {e}")


def error_progress(
    process_id: str,
    message: str = "Error",
    additional_data: Optional[Dict[str, Any]] = None,
):
    """Error progress - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        progress_system.error_process(process_id, message, additional_data)
        logger.info(f"Error progress for {process_id}: {message}")
    except Exception as e:
        logger.error(f"Failed to set error progress: {e}")


def cancel_progress(
    process_id: str,
    message: str = "Cancelled",
    additional_data: Optional[Dict[str, Any]] = None,
):
    """Cancel progress - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        progress_system.cancel_process(process_id, message, additional_data)
        logger.info(f"Cancelled progress for {process_id}: {message}")
    except Exception as e:
        logger.error(f"Failed to cancel progress: {e}")


class ProgressTracker:
    """
    Context manager for progress tracking - backward compatibility wrapper.
    Routes to unified progress system.
    """

    def __init__(self, process_type: str, description: str = ""):
        self.process_type = process_type
        self.description = description
        self.process_id = None

    def __enter__(self):
        self.process_id = create_progress_tracker(self.process_type, self.description)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Error occurred
            error_progress(self.process_id, f"Error: {exc_val}")
        else:
            # Normal completion
            complete_progress(self.process_id, "Complete")
        return False  # Don't suppress exceptions

    def update(
        self,
        progress: int,
        message: str,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
        """Update progress"""
        update_progress(self.process_id, progress, message, additional_data)


logger.info(
    "Progress emitter compatibility layer loaded - routing to unified progress system"
)
