#!/usr/bin/env python3
"""
Enhanced task failure handling with retry logic, dead letter queue (DLQ),
and alerting system for critical failures.
"""

import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from functools import wraps
from celery import current_task
from celery.exceptions import Retry, MaxRetriesExceededError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


# ============================================================================
# Database connection (PostgreSQL via SQLAlchemy)
# ============================================================================


def _get_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"


_engine = None
_SessionFactory = None


def get_db_session():
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()


# ============================================================================
# Dead Letter Queue (DLQ)
# ============================================================================


def init_dlq_database():
    """Initialize the DLQ database tables."""
    session = get_db_session()
    try:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id SERIAL PRIMARY KEY,
                task_id TEXT UNIQUE NOT NULL,
                task_name TEXT NOT NULL,
                args TEXT,
                kwargs TEXT,
                exception_type TEXT,
                exception_message TEXT,
                traceback TEXT,
                retry_count INTEGER DEFAULT 0,
                first_failure_time TIMESTAMP,
                last_failure_time TIMESTAMP,
                status TEXT DEFAULT 'failed',
                reprocessed_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_task_name ON dead_letter_queue(task_name)
        """))

        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_status ON dead_letter_queue(status)
        """))

        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON dead_letter_queue(created_at)
        """))

        session.commit()
        logger.info("DLQ database tables initialized")

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to initialize DLQ tables: {e}")
        raise
    finally:
        session.close()


def add_to_dlq(
    task_id: str,
    task_name: str,
    args: tuple,
    kwargs: dict,
    exception: Exception,
    traceback_str: str,
    retry_count: int = 0,
):
    """
    Add a failed task to the dead letter queue.

    Args:
        task_id: Celery task ID
        task_name: Name of the task
        args: Task arguments
        kwargs: Task keyword arguments
        exception: Exception that caused the failure
        traceback_str: Full traceback string
        retry_count: Number of retry attempts made
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            now = datetime.now().isoformat()

            # Check if task already exists in DLQ
            result = session.execute(
                text(
                    "SELECT id, retry_count, first_failure_time FROM dead_letter_queue WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            existing = result.fetchone()

            if existing:
                # Update existing entry
                session.execute(
                    text("""
                    UPDATE dead_letter_queue
                    SET retry_count = :retry_count,
                        last_failure_time = :last_failure_time,
                        exception_type = :exception_type,
                        exception_message = :exception_message,
                        traceback = :traceback,
                        status = 'failed'
                    WHERE task_id = :task_id
                """),
                    {
                        "retry_count": retry_count,
                        "last_failure_time": now,
                        "exception_type": type(exception).__name__,
                        "exception_message": str(exception),
                        "traceback": traceback_str,
                        "task_id": task_id,
                    },
                )
                logger.info(f"Updated DLQ entry for task {task_id}")
            else:
                # Insert new entry
                session.execute(
                    text("""
                    INSERT INTO dead_letter_queue
                    (task_id, task_name, args, kwargs, exception_type, exception_message,
                     traceback, retry_count, first_failure_time, last_failure_time)
                    VALUES (:task_id, :task_name, :args, :kwargs, :exception_type, :exception_message,
                            :traceback, :retry_count, :first_failure_time, :last_failure_time)
                """),
                    {
                        "task_id": task_id,
                        "task_name": task_name,
                        "args": json.dumps(args),
                        "kwargs": json.dumps(kwargs),
                        "exception_type": type(exception).__name__,
                        "exception_message": str(exception),
                        "traceback": traceback_str,
                        "retry_count": retry_count,
                        "first_failure_time": now,
                        "last_failure_time": now,
                    },
                )
                logger.info(f"Added task {task_id} to DLQ")

            session.commit()

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to add task to DLQ: {e}")


def get_dlq_entries(status: str = "failed", limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get entries from the dead letter queue.

    Args:
        status: Filter by status ('failed', 'reprocessed', or None for all)
        limit: Maximum number of entries to return

    Returns:
        List of DLQ entries
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            if status:
                result = session.execute(
                    text("""
                    SELECT * FROM dead_letter_queue
                    WHERE status = :status
                    ORDER BY last_failure_time DESC
                    LIMIT :limit
                """),
                    {"status": status, "limit": limit},
                )
            else:
                result = session.execute(
                    text("""
                    SELECT * FROM dead_letter_queue
                    ORDER BY last_failure_time DESC
                    LIMIT :limit
                """),
                    {"limit": limit},
                )

            rows = result.mappings().fetchall()
            return [dict(row) for row in rows]

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to get DLQ entries: {e}")
        return []


def reprocess_dlq_entry(entry_id: int) -> Dict[str, Any]:
    """
    Reprocess a failed task from the DLQ.

    Args:
        entry_id: DLQ entry ID

    Returns:
        Dictionary with reprocessing result
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            # Get the entry
            result = session.execute(
                text("SELECT * FROM dead_letter_queue WHERE id = :entry_id"),
                {"entry_id": entry_id},
            )
            entry = result.mappings().fetchone()

            if not entry:
                return {"error": "Entry not found"}

            # Mark as reprocessed
            session.execute(
                text("""
                UPDATE dead_letter_queue
                SET status = 'reprocessed',
                    reprocessed_time = :reprocessed_time
                WHERE id = :entry_id
            """),
                {"reprocessed_time": datetime.now().isoformat(), "entry_id": entry_id},
            )

            session.commit()

            # Re-submit the task
            from backend.celery_app import celery

            task_name = entry["task_name"]
            args = json.loads(entry["args"])
            kwargs = json.loads(entry["kwargs"])

            task = celery.send_task(task_name, args=args, kwargs=kwargs)

            logger.info(f"Reprocessed DLQ entry {entry_id} as task {task.id}")

            return {
                "success": True,
                "new_task_id": task.id,
                "original_task_id": entry["task_id"],
            }

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to reprocess DLQ entry: {e}")
        return {"error": str(e)}


def cleanup_old_dlq_entries(days: int = 30) -> int:
    """
    Clean up old DLQ entries.

    Args:
        days: Remove entries older than this many days

    Returns:
        Number of entries removed
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

            result = session.execute(
                text("""
                DELETE FROM dead_letter_queue
                WHERE status = 'reprocessed'
                AND created_at < :cutoff_date
            """),
                {"cutoff_date": cutoff_date},
            )

            deleted_count = result.rowcount
            session.commit()

            logger.info(f"Cleaned up {deleted_count} old DLQ entries")
            return deleted_count

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to cleanup DLQ entries: {e}")
        return 0


# ============================================================================
# Enhanced Retry Logic
# ============================================================================


def exponential_backoff(
    retry_count: int, base_delay: int = 60, max_delay: int = 3600
) -> int:
    """
    Calculate exponential backoff delay.

    Args:
        retry_count: Current retry attempt number
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds

    Returns:
        Delay in seconds
    """
    delay = min(base_delay * (2**retry_count), max_delay)
    return delay


def smart_retry(
    max_retries: int = 3,
    base_delay: int = 60,
    max_delay: int = 3600,
    autoretry_for: tuple = (Exception,),
    dont_autoretry_for: tuple = (),
):
    """
    Decorator for smart task retry with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        autoretry_for: Tuple of exception types to auto-retry
        dont_autoretry_for: Tuple of exception types to never retry

    Usage:
        @shared_task(bind=True)
        @smart_retry(max_retries=5, base_delay=30)
        def my_task(self, arg1, arg2):
            # task logic
            return result
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            task_name = func.__name__
            retry_count = self.request.retries

            try:
                result = func(self, *args, **kwargs)
                return result

            except dont_autoretry_for as e:
                # Don't retry these exceptions
                logger.error(f"Task {task_name} failed with non-retryable error: {e}")
                raise

            except autoretry_for as e:
                # Check if we've exceeded max retries
                if retry_count >= max_retries:
                    logger.error(f"Task {task_name} failed after {max_retries} retries")

                    # Add to DLQ
                    import traceback

                    add_to_dlq(
                        task_id=self.request.id,
                        task_name=task_name,
                        args=args,
                        kwargs=kwargs,
                        exception=e,
                        traceback_str=traceback.format_exc(),
                        retry_count=retry_count,
                    )

                    # Send alert for critical failure
                    send_failure_alert(task_name, str(e), retry_count)

                    raise MaxRetriesExceededError(
                        f"Task {task_name} failed after {max_retries} retries"
                    )

                # Calculate backoff delay
                delay = exponential_backoff(retry_count, base_delay, max_delay)

                logger.warning(
                    f"Task {task_name} failed (attempt {retry_count + 1}/{max_retries}). "
                    f"Retrying in {delay}s. Error: {e}"
                )

                # Retry the task
                raise self.retry(exc=e, countdown=delay, max_retries=max_retries)

        return wrapper

    return decorator


# ============================================================================
# Alerting System
# ============================================================================


def send_failure_alert(task_name: str, error_message: str, retry_count: int):
    """
    Send alert for critical task failure.

    Args:
        task_name: Name of the failed task
        error_message: Error message
        retry_count: Number of retries attempted
    """
    alert_message = f"""
    CRITICAL TASK FAILURE

    Task: {task_name}
    Error: {error_message}
    Retry Count: {retry_count}
    Time: {datetime.now().isoformat()}

    This task has exceeded maximum retry attempts and has been moved to the Dead Letter Queue.
    """

    logger.critical(alert_message)

    # Store alert in database
    try:
        _store_alert(task_name, error_message, retry_count, "critical")
    except Exception as e:
        logger.error(f"Failed to store alert: {e}")

    # Send email/notification (implement as needed)
    # _send_email_alert(alert_message)
    # _send_slack_alert(alert_message)


def _store_alert(task_name: str, error_message: str, retry_count: int, severity: str):
    """Store alert in database for dashboard display."""
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            # Create alerts table if it doesn't exist
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS task_alerts (
                    id SERIAL PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    error_message TEXT,
                    retry_count INTEGER,
                    severity TEXT,
                    acknowledged BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            session.execute(
                text("""
                INSERT INTO task_alerts (task_name, error_message, retry_count, severity)
                VALUES (:task_name, :error_message, :retry_count, :severity)
            """),
                {
                    "task_name": task_name,
                    "error_message": error_message,
                    "retry_count": retry_count,
                    "severity": severity,
                },
            )

            session.commit()
            logger.info(f"Alert stored for task {task_name}")

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to store alert: {e}")


def get_alerts(acknowledged: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Get task alerts from database.

    Args:
        acknowledged: Filter by acknowledged status
        limit: Maximum number of alerts to return

    Returns:
        List of alerts
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            result = session.execute(
                text("""
                SELECT * FROM task_alerts
                WHERE acknowledged = :acknowledged
                ORDER BY created_at DESC
                LIMIT :limit
            """),
                {"acknowledged": acknowledged, "limit": limit},
            )

            rows = result.mappings().fetchall()
            return [dict(row) for row in rows]

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to get alerts: {e}")
        return []


def acknowledge_alert(alert_id: int) -> bool:
    """
    Acknowledge an alert.

    Args:
        alert_id: Alert ID

    Returns:
        Success status
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            session.execute(
                text("""
                UPDATE task_alerts
                SET acknowledged = TRUE
                WHERE id = :alert_id
            """),
                {"alert_id": alert_id},
            )

            session.commit()
            logger.info(f"Acknowledged alert {alert_id}")
            return True

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to acknowledge alert: {e}")
        return False


# ============================================================================
# Task Result Persistence
# ============================================================================


def persist_task_result(
    task_id: str, task_name: str, result: Any, duration: float, status: str = "success"
):
    """
    Persist task result to database for long-term storage.

    Args:
        task_id: Task ID
        task_name: Task name
        result: Task result
        duration: Execution duration
        status: Task status
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            # Create results table if it doesn't exist
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS task_results (
                    id SERIAL PRIMARY KEY,
                    task_id TEXT UNIQUE NOT NULL,
                    task_name TEXT NOT NULL,
                    result TEXT,
                    duration REAL,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            session.execute(
                text("""
                INSERT INTO task_results
                (task_id, task_name, result, duration, status)
                VALUES (:task_id, :task_name, :result, :duration, :status)
                ON CONFLICT (task_id) DO UPDATE SET
                    task_name = EXCLUDED.task_name,
                    result = EXCLUDED.result,
                    duration = EXCLUDED.duration,
                    status = EXCLUDED.status
            """),
                {
                    "task_id": task_id,
                    "task_name": task_name,
                    "result": json.dumps(result),
                    "duration": duration,
                    "status": status,
                },
            )

            session.commit()
            logger.info(f"Persisted result for task {task_id}")

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to persist task result: {e}")


def get_task_result(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Get persisted task result.

    Args:
        task_id: Task ID

    Returns:
        Task result or None
    """
    try:
        init_dlq_database()
        session = get_db_session()

        try:
            result = session.execute(
                text("""
                SELECT * FROM task_results WHERE task_id = :task_id
            """),
                {"task_id": task_id},
            )

            row = result.mappings().fetchone()

            if row:
                row_dict = dict(row)
                row_dict["result"] = json.loads(row_dict["result"])
                return row_dict

            return None

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Failed to get task result: {e}")
        return None


# Initialize DLQ database on module import
try:
    init_dlq_database()
except Exception as e:
    logger.error(f"Failed to initialize DLQ database: {e}")
