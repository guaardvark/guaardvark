#!/usr/bin/env python3
"""
Task Scheduler Celery Beat Tasks
Version 1.0: Provides periodic tasks for scheduled task execution and stuck task recovery

This module provides:
- check_scheduled_tasks: Runs every minute to find and execute scheduled/recurring tasks
- recover_stuck_tasks: Runs every 5 minutes to detect and recover stuck tasks
"""

import os
import logging
import datetime
import json
from celery import shared_task
from typing import List, Dict, Any, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants for stuck task detection
STUCK_TASK_THRESHOLD_MINUTES = 15
MAX_RETRY_COUNT = 3


def _get_database_url():
    """Get DATABASE_URL from environment (set by start_postgres.sh in .env)."""
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_engine = None
_SessionFactory = None

def get_db_session():
    """Get a SQLAlchemy session for database operations without Flask."""
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()


def get_scheduled_tasks() -> List[Dict[str, Any]]:
    """
    Find tasks that are ready to be executed:
    - status = 'pending' (NOT 'queued' or 'in-progress' to avoid duplicates)
    - job_id IS NULL (task hasn't been submitted yet)
    - due_date <= now (or null for immediate tasks)
    """
    session = get_db_session()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # BUG FIX #2: Only select tasks that:
        # 1. Are in 'pending' status (not 'queued' or 'in-progress')
        # 2. Don't have a job_id (haven't been submitted to Celery yet)
        # This prevents duplicate task submissions
        result = session.execute(text("""
            SELECT id, name, status, type, due_date, priority, job_id
            FROM tasks
            WHERE status = 'pending'
              AND job_id IS NULL
              AND (due_date IS NULL OR due_date <= :now)
            ORDER BY priority ASC, due_date ASC
            LIMIT 10
        """), {"now": now})

        tasks = []
        for row in result.fetchall():
            tasks.append({
                'id': row[0],
                'name': row[1],
                'status': row[2],
                'type': row[3],
                'due_date': row[4],
                'priority': row[5],
                'job_id': row[6]
            })

        return tasks

    except Exception as e:
        logger.error(f"Failed to get scheduled tasks: {e}")
        return []
    finally:
        session.close()


def get_stuck_tasks() -> List[Dict[str, Any]]:
    """
    Find tasks that appear to be stuck:
    - status = 'in-progress' OR 'queued' (tasks can get stuck in queue too)
    - updated_at < (now - threshold)
    - No active Celery task for job_id

    Returns tasks that need recovery
    """
    session = get_db_session()
    try:
        # BUG FIX #5: Check for both 'in-progress' AND 'queued' status
        # Tasks can get stuck in 'queued' if Celery never picks them up
        # (e.g., worker died, queue misconfigured, etc.)
        threshold_time = (
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(minutes=STUCK_TASK_THRESHOLD_MINUTES)
        ).isoformat()

        result = session.execute(text("""
            SELECT id, name, status, job_id, retry_count, updated_at, type
            FROM tasks
            WHERE status IN ('in-progress', 'queued')
              AND updated_at < :threshold_time
            ORDER BY updated_at ASC
        """), {"threshold_time": threshold_time})

        tasks = []
        for row in result.fetchall():
            tasks.append({
                'id': row[0],
                'name': row[1],
                'status': row[2],
                'job_id': row[3],
                'retry_count': row[4] or 0,
                'updated_at': row[5],
                'type': row[6]
            })

        return tasks

    except Exception as e:
        logger.error(f"Failed to get stuck tasks: {e}")
        return []
    finally:
        session.close()


def update_task_for_retry(task_id: int, retry_count: int, error_message: str) -> bool:
    """Update task to pending state for retry"""
    session = get_db_session()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        session.execute(text("""
            UPDATE tasks
            SET status = 'pending',
                job_id = NULL,
                retry_count = :retry_count,
                error_message = :error_message,
                updated_at = :now
            WHERE id = :task_id
        """), {"retry_count": retry_count, "error_message": error_message, "now": now, "task_id": task_id})

        session.commit()
        return True

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update task for retry: {e}")
        return False
    finally:
        session.close()


def mark_task_failed(task_id: int, error_message: str) -> bool:
    """Mark task as failed after max retries exceeded"""
    session = get_db_session()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        session.execute(text("""
            UPDATE tasks
            SET status = 'failed',
                error_message = :error_message,
                updated_at = :now
            WHERE id = :task_id
        """), {"error_message": error_message, "now": now, "task_id": task_id})

        session.commit()
        return True

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to mark task as failed: {e}")
        return False
    finally:
        session.close()


def check_celery_task_active(job_id: str) -> bool:
    """
    Check if a Celery task is still actively running.
    Returns True if task appears to be active, False if not found or completed.
    """
    if not job_id:
        return False

    try:
        # Try to check task state via Celery
        from celery.result import AsyncResult
        from backend.celery_app import celery

        # Extract Celery task ID if it follows our naming convention
        celery_task_id = job_id

        result = AsyncResult(celery_task_id, app=celery)
        state = result.state

        # Active states
        active_states = {'PENDING', 'STARTED', 'RETRY', 'RECEIVED'}

        if state in active_states:
            logger.debug(f"Task {job_id} is in active state: {state}")
            return True

        # Check if task was recently updated in Redis
        # (Additional check for tasks that might be running but state not updated)
        return False

    except Exception as e:
        logger.warning(f"Could not check Celery task state for {job_id}: {e}")
        # If we can't determine state, assume it's not active to allow recovery
        return False


@shared_task(bind=True)
def check_scheduled_tasks(self):
    """
    Celery Beat task that runs every minute.
    Finds tasks where:
      - status = 'pending'
      - due_date <= now (or null for immediate tasks)
    Submits them to execute_unified_task.

    Returns dict with stats about processed tasks.
    """
    logger.info("Running scheduled task check...")

    try:
        scheduled_tasks = get_scheduled_tasks()

        if not scheduled_tasks:
            logger.debug("No scheduled tasks found")
            return {'processed': 0, 'message': 'No scheduled tasks found'}

        logger.info(f"Found {len(scheduled_tasks)} scheduled tasks to process")

        # Import the unified task executor
        from backend.tasks.unified_task_executor import execute_unified_task

        submitted_count = 0
        for task in scheduled_tasks:
            task_id = task['id']

            # Skip if task already has a job_id (might be queued already)
            if task.get('job_id'):
                logger.debug(f"Task {task_id} already has job_id, skipping")
                continue

            try:
                # BUG FIX #3: Update task status BEFORE submitting to Celery
                # This prevents a race condition where the Celery task starts
                # executing and updates status to 'in-progress', then this code
                # overwrites it back to 'queued'.
                job_id = f"task_{task_id}"
                session = get_db_session()
                try:
                    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    # Use conditional update to prevent race with concurrent schedulers
                    result = session.execute(text("""
                        UPDATE tasks
                        SET job_id = :job_id, status = 'queued', updated_at = :now
                        WHERE id = :task_id AND status = 'pending' AND job_id IS NULL
                    """), {"job_id": job_id, "now": now, "task_id": task_id})
                    rows_affected = result.rowcount
                    session.commit()
                finally:
                    session.close()

                # Only submit to Celery if we successfully claimed the task
                if rows_affected == 0:
                    logger.debug(f"Task {task_id} already claimed by another scheduler, skipping")
                    continue

                # Now submit task to Celery
                result = execute_unified_task.apply_async(
                    args=[task_id],
                    queue=_get_queue_for_task_type(task.get('type'))
                )

                logger.info(f"Submitted scheduled task {task_id} ({task['name']}) with Celery ID: {result.id}")
                submitted_count += 1

            except Exception as e:
                logger.error(f"Failed to submit scheduled task {task_id}: {e}")
                # Revert task status if Celery submission failed
                try:
                    revert_session = get_db_session()
                    revert_session.execute(text("""
                        UPDATE tasks SET job_id = NULL, status = 'pending'
                        WHERE id = :task_id AND status = 'queued'
                    """), {"task_id": task_id})
                    revert_session.commit()
                    revert_session.close()
                except Exception:
                    pass

        return {
            'processed': submitted_count,
            'total_found': len(scheduled_tasks),
            'message': f'Submitted {submitted_count} tasks for execution'
        }

    except Exception as e:
        logger.error(f"Error in check_scheduled_tasks: {e}", exc_info=True)
        return {'error': str(e)}


@shared_task(bind=True)
def recover_stuck_tasks(self):
    """
    Celery Beat task that runs every 5 minutes.
    Finds tasks where:
      - status = 'in-progress'
      - updated_at < (now - 15 minutes)
      - No active Celery task for job_id

    Recovery:
      - If retry_count < max_retries: Reset to 'pending', increment retry_count, requeue
      - If retry_count >= max_retries: Mark as 'failed'

    Returns dict with recovery stats.
    """
    logger.info("Running stuck task recovery check...")

    try:
        stuck_tasks = get_stuck_tasks()

        if not stuck_tasks:
            logger.debug("No stuck tasks found")
            return {'recovered': 0, 'failed': 0, 'message': 'No stuck tasks found'}

        logger.info(f"Found {len(stuck_tasks)} potentially stuck tasks")

        # Import the unified task executor
        from backend.tasks.unified_task_executor import execute_unified_task

        recovered_count = 0
        failed_count = 0
        skipped_count = 0

        for task in stuck_tasks:
            task_id = task['id']
            job_id = task.get('job_id')
            retry_count = task.get('retry_count', 0)

            # Check if Celery task is actually still running
            if check_celery_task_active(job_id):
                logger.info(f"Task {task_id} appears still active in Celery, skipping")
                skipped_count += 1
                continue

            logger.warning(f"Task {task_id} ({task['name']}) appears stuck, last update: {task['updated_at']}")

            if retry_count < MAX_RETRY_COUNT:
                # Reset for retry
                new_retry_count = retry_count + 1
                error_message = f"Auto-recovered from stuck state (attempt {new_retry_count}/{MAX_RETRY_COUNT})"

                if update_task_for_retry(task_id, new_retry_count, error_message):
                    # Resubmit to Celery
                    try:
                        result = execute_unified_task.apply_async(
                            args=[task_id],
                            queue=_get_queue_for_task_type(task.get('type')),
                            countdown=10  # Small delay before retry
                        )
                        logger.info(f"Requeued stuck task {task_id} for retry {new_retry_count}/{MAX_RETRY_COUNT}")
                        recovered_count += 1
                    except Exception as e:
                        logger.error(f"Failed to requeue task {task_id}: {e}")

            else:
                # Max retries exceeded, mark as failed
                error_message = f"Task failed after {MAX_RETRY_COUNT} automatic recovery attempts"
                if mark_task_failed(task_id, error_message):
                    logger.warning(f"Task {task_id} marked as failed after max retries")
                    failed_count += 1

                    # Notify progress system
                    try:
                        from backend.utils.unified_progress_system import get_unified_progress
                        progress_system = get_unified_progress()
                        process_id = f"task_{task_id}"
                        progress_system.error_process(process_id, error_message)
                    except Exception:
                        pass

        return {
            'recovered': recovered_count,
            'failed': failed_count,
            'skipped': skipped_count,
            'total_found': len(stuck_tasks),
            'message': f'Recovered {recovered_count}, failed {failed_count}, skipped {skipped_count}'
        }

    except Exception as e:
        logger.error(f"Error in recover_stuck_tasks: {e}", exc_info=True)
        return {'error': str(e)}


def _get_queue_for_task_type(task_type: str) -> str:
    """Get appropriate Celery queue for task type"""
    queue_mapping = {
        'file_generation': 'generation',
        'csv_generation': 'generation',
        'code_generation': 'generation',
        'content_generation': 'generation',
        'image_generation': 'generation',
        'video_generation': 'generation',
        'indexing': 'indexing',
        'data_analysis': 'default',
        'web_scraping': 'default',
    }
    return queue_mapping.get(task_type, 'default')


# Health check task for monitoring
@shared_task
def scheduler_health_check():
    """Simple health check task"""
    return {
        'status': 'healthy',
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'service': 'task_scheduler_celery'
    }


# Export for Celery discovery
__all__ = ['check_scheduled_tasks', 'recover_stuck_tasks', 'scheduler_health_check']
