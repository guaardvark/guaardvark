# backend/utils/progress_manager.py
# UNIFIED PROGRESS SYSTEM COMPATIBILITY LAYER
# This file provides backward compatibility for the old progress_manager API
# All calls are routed to the new unified progress system

import logging
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import json
from datetime import datetime, timezone

from .unified_progress_system import get_unified_progress, ProcessType

logger = logging.getLogger(__name__)

def _get_socketio():
    """Legacy function - not needed in unified system"""
    return None

def _get_progress_base_dir(output_dir_config: str) -> str:
    """Get progress base directory"""
    if not output_dir_config:
        raise ValueError("OUTPUT_DIR configuration not provided to progress_manager")
    
    base_path = Path(output_dir_config) / ".progress_jobs" 
    base_path.mkdir(parents=True, exist_ok=True)
    return str(base_path.resolve())

def _get_job_dir(progress_base_dir: str, job_id: str) -> str:
    """Get job directory"""
    job_dir = Path(progress_base_dir) / job_id
    return str(job_dir.resolve())

def get_default_output_dir() -> str:
    """Get default output directory"""
    from backend.config import OUTPUT_DIR
    return OUTPUT_DIR

def start_job(
    output_dir_config: str,
    final_output_filename: str,
    command_label: Optional[str] = None,
    initial_model_name_version: Optional[str] = None,
    user_specifications: Optional[str] = None,
) -> str:
    """Start a new job - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        
        # Create process in unified system
        description = command_label or f"Processing {final_output_filename}"
        process_id = progress_system.create_process(
            ProcessType.FILE_GENERATION,
            description,
            additional_data={
                "output_filename": final_output_filename,
                "command_label": command_label,
                "model": initial_model_name_version,
                "user_specifications": user_specifications
            }
        )
        
        logger.info(f"Started job {process_id} via unified progress system")
        return process_id
        
    except Exception as e:
        logger.error(f"Failed to start job: {e}")
        # Fallback to simple ID generation
        import uuid
        return str(uuid.uuid4())

def log_item_processed(
    output_dir_config: str,
    job_id: str,
    item_identifier: str,
    item_data: Dict[str, Any],
    item_processing_model_name_version: str,
) -> bool:
    """Log processed item - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        
        # Get current process
        process = progress_system.get_process(job_id)
        if not process:
            logger.warning(f"Process {job_id} not found")
            return False
        
        # Calculate progress based on additional data
        additional_data = process.additional_data or {}
        processed_count = additional_data.get("processed_count", 0) + 1
        total_expected = additional_data.get("total_items_expected", 100)
        
        progress_percent = min(100, int((processed_count / total_expected) * 100))
        
        # Update progress
        progress_system.update_process(
            job_id,
            progress_percent,
            f"Processed {processed_count} items",
            additional_data={
                **additional_data,
                "processed_count": processed_count,
                "last_processed_item": item_identifier,
                "last_processed_model": item_processing_model_name_version
            }
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to log processed item: {e}")
        return False

def update_job_status(
    output_dir_config: str,
    job_id: str,
    status_message: str,
    is_complete: bool = False,
    final_output_actual_path: Optional[str] = None,
    total_items_expected: Optional[int] = None,
) -> bool:
    """Update job status - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        
        if is_complete:
            # Complete the process
            progress_system.complete_process(job_id, status_message)
        else:
            # Update process
            process = progress_system.get_process(job_id)
            current_progress = process.progress if process else 0
            
            additional_data = {}
            if final_output_actual_path:
                additional_data["final_output_path"] = final_output_actual_path
            if total_items_expected:
                additional_data["total_items_expected"] = total_items_expected
            
            progress_system.update_process(job_id, current_progress, status_message, additional_data)
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")
        return False

def get_processed_items_data(
    output_dir_config: str, job_id: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Get processed items data - simplified for unified system"""
    try:
        progress_system = get_unified_progress()
        process = progress_system.get_process(job_id)
        
        if not process:
            return [], []
        
        # Return simplified data
        additional_data = process.additional_data or {}
        processed_count = additional_data.get("processed_count", 0)
        
        # Generate dummy processed items list for compatibility
        processed_items = []
        processed_identifiers = []
        
        for i in range(processed_count):
            item_id = f"item_{i}"
            processed_items.append({
                "item_identifier": item_id,
                "data": {"processed": True},
                "model": additional_data.get("last_processed_model", "unknown")
            })
            processed_identifiers.append(item_id)
        
        return processed_items, processed_identifiers
        
    except Exception as e:
        logger.error(f"Failed to get processed items data: {e}")
        return [], []

def get_job_metadata(output_dir_config: str, job_id: str) -> Optional[Dict[str, Any]]:
    """Get job metadata - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        process = progress_system.get_process(job_id)
        
        if not process:
            return None
        
        # Convert to legacy metadata format
        additional_data = process.additional_data or {}
        metadata = {
            "job_id": job_id,
            "job_status": process.status.value.upper(),
            "start_time_utc": process.timestamp.isoformat(),
            "last_update_utc": process.timestamp.isoformat(),
            "target_output_filename": additional_data.get("output_filename", ""),
            "command_label_invoked": additional_data.get("command_label", ""),
            "script_generating_model_name_version": additional_data.get("model", ""),
            "user_specifications": additional_data.get("user_specifications", ""),
            "processed_item_count": additional_data.get("processed_count", 0),
            "total_items_expected": additional_data.get("total_items_expected", 0),
            "is_complete": process.status.value in ["complete", "error", "cancelled"],
            "progress_percent": process.progress
        }
        
        return metadata
        
    except Exception as e:
        logger.error(f"Failed to get job metadata: {e}")
        return None

# Legacy functions for backward compatibility
def complete_job(output_dir_config: str, job_id: str, message: str = "Complete") -> bool:
    """Complete a job - routes to unified progress system"""
    return update_job_status(output_dir_config, job_id, message, is_complete=True)

def cancel_job(output_dir_config: str, job_id: str, message: str = "Cancelled") -> bool:
    """Cancel a job - routes to unified progress system"""
    try:
        progress_system = get_unified_progress()
        progress_system.cancel_process(job_id, message)
        return True
    except Exception as e:
        logger.error(f"Failed to cancel job: {e}")
        return False

logger.info("Progress manager compatibility layer loaded - routing to unified progress system")