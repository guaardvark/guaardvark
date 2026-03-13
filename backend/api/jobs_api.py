# backend/api/jobs_api.py
# Extracted from meta_api for job-related endpoints

import logging
from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from flask import Blueprint, current_app, jsonify

try:
    from backend.models import Task, db, TrainingJob
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
    from backend.celery_app import celery
except Exception:
    db = Task = TrainingJob = None
    get_unified_progress = None
    celery = None

jobs_bp = Blueprint("jobs_api", __name__, url_prefix="/api/meta")
logger = logging.getLogger(__name__)


def get_active_celery_tasks() -> Dict[str, Any]:
    """Get active Celery tasks from Redis broker."""
    if not celery:
        return {}
    
    try:
        inspect = celery.control.inspect()
        active_tasks = inspect.active()
        if not active_tasks:
            return {}
        
        # Flatten tasks from all workers
        all_tasks = {}
        for worker, tasks in active_tasks.items():
            for task in tasks:
                task_id = task.get('id')
                if task_id:
                    all_tasks[task_id] = {
                        'worker': worker,
                        'name': task.get('name'),
                        'args': task.get('args', []),
                        'kwargs': task.get('kwargs', {}),
                        'time_start': task.get('time_start')
                    }
        
        return all_tasks
    except Exception as e:
        logger.warning(f"Failed to get active Celery tasks: {e}")
        return {}

def detect_stuck_jobs(jobs: List[Dict[str, Any]], active_celery_tasks: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect jobs that appear to be stuck (no Celery task, old timestamps)."""
    stuck_jobs = []
    # Use timezone-aware datetime to match potentially timezone-aware timestamps
    current_time = datetime.now(timezone.utc)
    
    # Also check TrainingJob records for celery_task_id
    training_job_celery_ids = {}
    if db and TrainingJob:
        try:
            from flask import current_app
            with current_app.app_context():
                training_jobs = db.session.query(TrainingJob).filter(
                    TrainingJob.status.in_(["pending", "running"]),
                    TrainingJob.celery_task_id.isnot(None)
                ).all()
                for tj in training_jobs:
                    training_job_celery_ids[tj.celery_task_id] = tj.job_id
        except Exception as e:
            logger.warning(f"Could not check TrainingJob records: {e}")

    for job in jobs:
        if job.get('is_complete'):
            continue

        # Check if job has an active Celery task
        job_id = job.get('job_id', '')
        has_celery_task = False
        
        # Check multiple ways the job_id might appear in Celery tasks
        for task_id, task_info in active_celery_tasks.items():
            # First check: task_id matches a TrainingJob's celery_task_id
            if task_id in training_job_celery_ids and training_job_celery_ids[task_id] == job_id:
                has_celery_task = True
                break
            
            if not task_info.get('args'):
                continue
            
            args = task_info['args']
            # Check if job_id matches first argument (most common case)
            if len(args) > 0 and str(args[0]) == job_id:
                has_celery_task = True
                break
            
            # Check if job_id is in any argument (for nested structures)
            if any(str(arg) == job_id for arg in args if arg):
                has_celery_task = True
                break
            
            # Check kwargs for job_id
            kwargs = task_info.get('kwargs', {})
            if 'job_id' in kwargs and str(kwargs['job_id']) == job_id:
                has_celery_task = True
                break

        # Check last update time
        last_update_str = job.get('last_update') or job.get('last_update_utc')
        is_stale = False
        last_update = None
        if last_update_str:
            try:
                # Parse ISO timestamp and make it timezone-aware if needed
                last_update = datetime.fromisoformat(last_update_str.replace('Z', '+00:00'))
                # Ensure timezone-aware
                if last_update.tzinfo is None:
                    last_update = last_update.replace(tzinfo=timezone.utc)
                time_diff = current_time - last_update
                is_stale = time_diff > timedelta(hours=1)  # Consider stale after 1 hour
            except ValueError:
                is_stale = True  # Invalid timestamp = stale
        else:
            is_stale = True  # No timestamp = stale

        # Mark as stuck only if BOTH conditions: no Celery task AND stale
        # Non-Celery processes (background threads like CSV gen) are legitimate
        if not has_celery_task and is_stale:
            stuck_reasons = []
            if not has_celery_task:
                stuck_reasons.append("no active Celery task")
            if is_stale:
                hours_stale = (current_time - last_update).total_seconds() / 3600 if last_update else 999
                stuck_reasons.append(f"no update for {hours_stale:.1f}h")
            
            job_copy = job.copy()
            job_copy['stuck_reasons'] = stuck_reasons
            job_copy['is_stuck'] = True
            stuck_jobs.append(job_copy)
    
    return stuck_jobs

def get_active_jobs(output_dir_config: str) -> List[Dict[str, Any]]:
    """Get list of active jobs with enhanced Celery integration and stuck job detection."""
    if not output_dir_config:
        return []
    
    progress_dir = Path(output_dir_config) / ".progress_jobs"
    if not progress_dir.exists():
        return []
    
    active_jobs = []
    try:
        # Get active Celery tasks for cross-reference
        active_celery_tasks = get_active_celery_tasks()
        
        for job_dir in progress_dir.iterdir():
            if job_dir.is_dir():
                metadata_file = job_dir / "metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        
                        # Skip completed jobs - only return truly active ones
                        is_complete = metadata.get("is_complete", False)
                        if is_complete:
                            continue

                        job_data = {
                            "id": metadata.get("job_id"),
                            "job_id": metadata.get("job_id"),
                            "process_type": metadata.get("process_type", "unknown"),
                            "status": metadata.get("status", metadata.get("job_status", "UNKNOWN")),
                            "progress": metadata.get("progress", metadata.get("processed_item_count", 0)),
                            "total": metadata.get("total_items_expected"),
                            "start_time": metadata.get("start_time_utc"),
                            "last_update": metadata.get("last_update_utc"),
                            "last_update_utc": metadata.get("last_update_utc"),
                            "timestamp": metadata.get("last_update_utc"),  # For DevToolsPage compatibility
                            "is_complete": False,  # Always false since we filter completed ones
                            "message": metadata.get("message", ""),
                            "description": metadata.get("message", ""),  # For DevToolsPage compatibility
                            "output_filename": metadata.get("target_output_filename"),
                            "command": metadata.get("command_label_invoked"),
                            "model": metadata.get("script_generating_model_name_version"),
                            "additional_data": metadata.get("additional_data", {})
                        }

                        active_jobs.append(job_data)
        
        return active_jobs
        
    except Exception as e:
        logger.error(f"Error reading active jobs: {e}", exc_info=True)
        return []


@jobs_bp.route("/active_jobs", methods=["GET"])
def get_active_jobs_route():
    """Get list of active jobs with enhanced stuck job detection."""
    output_dir = current_app.config.get("OUTPUT_DIR")
    if not output_dir:
        return jsonify({"error": "Server configuration error: Output directory not set."}), 500
    
    try:
        active_jobs = get_active_jobs(output_dir)
        active_celery_tasks = get_active_celery_tasks()
        
        # Also include TrainingJob records that might not have progress entries
        training_jobs_in_progress = []
        if db and TrainingJob:
            try:
                training_jobs = db.session.query(TrainingJob).filter(
                    TrainingJob.status.in_(["pending", "running"])
                ).all()
                for tj in training_jobs:
                    # Check if this job already exists in active_jobs
                    if not any(j.get('job_id') == tj.job_id for j in active_jobs):
                        # Create a progress job entry for it
                        training_jobs_in_progress.append({
                            "id": tj.job_id,
                            "job_id": tj.job_id,
                            "process_type": "training",
                            "status": tj.status.upper(),
                            "progress": tj.progress or 0,
                            "last_update": tj.started_at.isoformat() if tj.started_at else None,
                            "last_update_utc": tj.started_at.isoformat() if tj.started_at else None,
                            "is_complete": False,
                            "message": f"{tj.pipeline_stage or 'training'}: {tj.name or tj.job_id}",
                            "description": f"{tj.pipeline_stage or 'training'}: {tj.name or tj.job_id}",
                            "additional_data": {"training_job_id": tj.id}
                        })
            except Exception as e:
                logger.warning(f"Could not load TrainingJob records: {e}")
        
        # Merge training jobs into active jobs
        all_active_jobs = active_jobs + training_jobs_in_progress
        stuck_jobs = detect_stuck_jobs(all_active_jobs, active_celery_tasks)
        
        # Log stuck jobs for monitoring
        if stuck_jobs:
            logger.warning(f"Detected {len(stuck_jobs)} stuck jobs: {[job.get('job_id', 'unknown') for job in stuck_jobs]}")
            
        # Filter stuck jobs out of active_jobs so they don't block the UI
        stuck_ids = {job.get('job_id') for job in stuck_jobs if job.get('job_id')}
        real_active_jobs = [job for job in all_active_jobs if job.get('job_id') not in stuck_ids]
        
        # Include metadata about system state
        response_data = {
            "active_jobs": real_active_jobs,
            "stuck_jobs": stuck_jobs,
            "stuck_count": len(stuck_jobs),
            "total_jobs": len(all_active_jobs),
            "celery_tasks_count": len(active_celery_tasks),
            "system_healthy": len(stuck_jobs) == 0
        }
        
        return jsonify(response_data), 200
    except Exception as e:
        logger.error(f"Error getting active jobs: {e}", exc_info=True)
        return jsonify({"error": "Failed to get active jobs."}), 500


@jobs_bp.route("/cleanup_stuck_jobs", methods=["POST"])
def cleanup_stuck_jobs_route():
    """Clean up stuck jobs that have no active Celery tasks."""
    output_dir = current_app.config.get("OUTPUT_DIR")
    if not output_dir:
        return jsonify({"error": "Server configuration error: Output directory not set."}), 500
    
    try:
        active_jobs = get_active_jobs(output_dir)
        active_celery_tasks = get_active_celery_tasks()
        
        # Also include TrainingJob records
        training_jobs_in_progress = []
        if db and TrainingJob:
            try:
                training_jobs = db.session.query(TrainingJob).filter(
                    TrainingJob.status.in_(["pending", "running"])
                ).all()
                for tj in training_jobs:
                    if not any(j.get('job_id') == tj.job_id for j in active_jobs):
                        training_jobs_in_progress.append({
                            "job_id": tj.job_id,
                            "last_update_utc": tj.started_at.isoformat() if tj.started_at else None,
                            "is_complete": False,
                            "additional_data": {"training_job_id": tj.id}
                        })
            except Exception as e:
                logger.warning(f"Could not load TrainingJob records: {e}")
        
        all_active_jobs = active_jobs + training_jobs_in_progress
        stuck_jobs = detect_stuck_jobs(all_active_jobs, active_celery_tasks)
        
        cleaned_count = 0
        progress_dir = Path(output_dir) / ".progress_jobs"
        training_jobs_updated = 0
        
        for stuck_job in stuck_jobs:
            job_id = stuck_job.get('job_id')
            if not job_id:
                continue
            
            # Check if this is a TrainingJob
            is_training_job = stuck_job.get('additional_data', {}).get('training_job_id') is not None
            
            if is_training_job and db and TrainingJob:
                # Update TrainingJob status to failed
                try:
                    training_job = db.session.query(TrainingJob).filter(
                        TrainingJob.job_id == job_id
                    ).first()
                    if training_job and training_job.status in ["pending", "running"]:
                        training_job.status = "failed"
                        training_job.error_message = f"Job marked as stuck: {', '.join(stuck_job.get('stuck_reasons', []))}"
                        db.session.commit()
                        training_jobs_updated += 1
                        logger.info(f"Marked training job {job_id} as failed (stuck)")
                except Exception as e:
                    logger.error(f"Failed to update training job {job_id}: {e}")
                    if db:
                        db.session.rollback()
            
            # Remove the progress directory for this stuck job
            job_dir = progress_dir / job_id
            if job_dir.exists() and job_dir.is_dir():
                try:
                    import shutil
                    shutil.rmtree(job_dir)
                    logger.info(f"Cleaned up stuck job directory: {job_id}")
                    cleaned_count += 1
                except Exception as e:
                    logger.error(f"Failed to clean up job {job_id}: {e}")
        
        # Also clean up completed training jobs that are older than 24 hours
        if db and TrainingJob:
            try:
                from datetime import timedelta
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                completed_training_jobs = db.session.query(TrainingJob).filter(
                    TrainingJob.status.in_(["completed", "failed", "cancelled"]),
                    TrainingJob.completed_at < cutoff_time
                ).all()
                
                for tj in completed_training_jobs:
                    job_dir = progress_dir / tj.job_id
                    if job_dir.exists() and job_dir.is_dir():
                        try:
                            import shutil
                            shutil.rmtree(job_dir)
                            cleaned_count += 1
                            logger.debug(f"Cleaned up old completed training job: {tj.job_id}")
                        except Exception as e:
                            logger.warning(f"Could not clean up old job {tj.job_id}: {e}")
            except Exception as e:
                logger.warning(f"Could not clean up old training jobs: {e}")
        
        return jsonify({
            "message": f"Cleaned up {cleaned_count} stuck jobs",
            "cleaned_count": cleaned_count,
            "stuck_jobs_found": len(stuck_jobs),
            "training_jobs_updated": training_jobs_updated
        }), 200
        
    except Exception as e:
        logger.error(f"Error cleaning up stuck jobs: {e}", exc_info=True)
        return jsonify({"error": "Failed to clean up stuck jobs."}), 500

@jobs_bp.route("/cancel_job/<string:job_id>", methods=["POST"])
def cancel_job_route(job_id):
    """Cancel a running job and update any linked Task."""
    if not get_unified_progress:
        return jsonify({"error": "Progress system is not available."}), 503

    output_dir = current_app.config.get("OUTPUT_DIR")
    if not output_dir:
        return (
            jsonify({"error": "Server configuration error: Output directory not set."}),
            500,
        )

    try:
        # Use unified progress system to cancel the process
        progress_system = get_unified_progress()
        progress_system.cancel_process(job_id, "CANCELLED")

        if db and Task:
            task_entry = db.session.query(Task).filter_by(job_id=job_id).first()
            if task_entry:
                task_entry.status = "cancelled"
                db.session.commit()

        return jsonify({"message": f"Job {job_id} cancelled."}), 200
    except Exception as e:
        logger.error(f"Error cancelling job {job_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to cancel job."}), 500


@jobs_bp.route("/delete_job/<string:job_id>", methods=["DELETE"])
def delete_job_route(job_id):
    """Delete a job completely from the progress system."""
    output_dir = current_app.config.get("OUTPUT_DIR")
    if not output_dir:
        return (
            jsonify({"error": "Server configuration error: Output directory not set."}),
            500,
        )

    try:
        # Remove from unified progress system
        progress_system = get_unified_progress()
        progress_system._cleanup_process(job_id)

        # Remove from file system
        progress_dir = Path(output_dir) / ".progress_jobs"
        job_dir = progress_dir / job_id
        if job_dir.exists() and job_dir.is_dir():
            import shutil
            shutil.rmtree(job_dir)
            logger.info(f"Deleted job directory: {job_id}")

        # Update linked task if exists
        if db and Task:
            task_entry = db.session.query(Task).filter_by(job_id=job_id).first()
            if task_entry:
                task_entry.job_id = None
                task_entry.status = "pending"
                db.session.commit()

        return jsonify({"message": f"Job {job_id} deleted."}), 200
    except Exception as e:
        logger.error(f"Error deleting job {job_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to delete job."}), 500


@jobs_bp.route("/retry_job/<string:job_id>", methods=["POST"])
def retry_job_route(job_id):
    """Retry a failed job by creating a new job with the same parameters."""
    output_dir = current_app.config.get("OUTPUT_DIR")
    if not output_dir:
        return (
            jsonify({"error": "Server configuration error: Output directory not set."}),
            500,
        )

    try:
        # Get the original job metadata
        progress_dir = Path(output_dir) / ".progress_jobs"
        job_dir = progress_dir / job_id
        metadata_file = job_dir / "metadata.json"
        
        if not metadata_file.exists():
            return jsonify({"error": "Job metadata not found."}), 404

        with open(metadata_file, 'r', encoding='utf-8') as f:
            original_metadata = json.load(f)

        # Get linked task for context
        linked_task = None
        if db and Task:
            linked_task = db.session.query(Task).filter_by(job_id=job_id).first()

        # Create new job with same parameters
        progress_system = get_unified_progress()
        additional_data = original_metadata.get("additional_data", {})
        
        # If we have a linked task, use its parameters
        if linked_task:
            additional_data.update({
                "task_id": linked_task.id,
                "task_name": linked_task.name,
                "task_type": linked_task.type,
                "output_filename": linked_task.output_filename,
                "model_name": linked_task.model_name,
                "prompt_text": linked_task.prompt_text
            })

        new_job_id = progress_system.create_process(
            process_type=ProcessType(original_metadata.get("process_type", "unknown")),
            description=f"Retry of {job_id}",
            additional_data=additional_data
        )

        # Update linked task with new job_id
        if linked_task:
            linked_task.job_id = new_job_id
            linked_task.status = "processing"
            db.session.commit()

        return jsonify({
            "message": f"Job {job_id} retry initiated.",
            "new_job_id": new_job_id
        }), 200
    except Exception as e:
        logger.error(f"Error retrying job {job_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to retry job."}), 500
