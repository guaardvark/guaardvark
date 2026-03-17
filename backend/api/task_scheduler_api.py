# backend/api/task_scheduler_api.py
# API for the unified task scheduler system
# Version 1.0 - Phase 3 implementation

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from flask import Blueprint, request, jsonify
from backend.utils.response_utils import success_response, error_response
from backend.models import Task, db
from backend.services.task_handlers import get_handler_registry, register_all_handlers

logger = logging.getLogger(__name__)

task_scheduler_bp = Blueprint(
    "task_scheduler_api", __name__, url_prefix="/api/scheduler"
)


def _ensure_handlers_registered():
    """Ensure all handlers are registered"""
    registry = get_handler_registry()
    if len(registry._handlers) == 0:
        register_all_handlers()
    return registry


@task_scheduler_bp.route("/queue/summary", methods=["GET"])
def get_queue_summary():
    """
    Get a summary of the task queue for the ProgressFooterBar.
    Returns counts and status of tasks across all handlers.
    """
    try:
        registry = _ensure_handlers_registered()

        # Get task counts by status
        pending_count = (
            db.session.query(Task)
            .filter(Task.status.in_(["pending", "queued"]))
            .count()
        )

        running_count = (
            db.session.query(Task)
            .filter(Task.status.in_(["running", "processing", "in_progress"]))
            .count()
        )

        completed_today = (
            db.session.query(Task)
            .filter(
                Task.status == "completed",
                Task.updated_at >= datetime.now() - timedelta(days=1),
            )
            .count()
        )

        failed_today = (
            db.session.query(Task)
            .filter(
                Task.status == "failed",
                Task.updated_at >= datetime.now() - timedelta(days=1),
            )
            .count()
        )

        # Get counts by handler type
        handler_counts = {}
        for handler_name in registry._handlers.keys():
            count = (
                db.session.query(Task)
                .filter(
                    Task.task_handler == handler_name,
                    Task.status.in_(
                        ["pending", "queued", "running", "processing", "in_progress"]
                    ),
                )
                .count()
            )
            if count > 0:
                handler_counts[handler_name] = count

        # Get next scheduled task
        next_scheduled = (
            db.session.query(Task)
            .filter(
                Task.schedule_type == "scheduled",
                Task.next_run_at.isnot(None),
                Task.next_run_at > datetime.now(),
            )
            .order_by(Task.next_run_at.asc())
            .first()
        )

        next_scheduled_info = None
        if next_scheduled:
            next_scheduled_info = {
                "id": next_scheduled.id,
                "handler": next_scheduled.task_handler,
                "next_run_at": (
                    next_scheduled.next_run_at.isoformat()
                    if next_scheduled.next_run_at
                    else None
                ),
            }

        # Get currently running tasks with details
        running_tasks = (
            db.session.query(Task)
            .filter(Task.status.in_(["running", "processing", "in_progress"]))
            .order_by(Task.updated_at.desc())
            .limit(5)
            .all()
        )

        running_task_details = []
        for task in running_tasks:
            handler = (
                registry.get_handler(task.task_handler) if task.task_handler else None
            )
            running_task_details.append(
                {
                    "id": task.id,
                    "job_id": task.job_id,
                    "handler": task.task_handler,
                    "handler_display": (
                        handler.display_name if handler else task.task_handler
                    ),
                    "status": task.status,
                    "progress": task.progress or 0,
                    "message": task.description or "Processing...",
                    "started_at": (
                        task.created_at.isoformat() if task.created_at else None
                    ),
                }
            )

        return success_response(
            {
                "queue_summary": {
                    "pending": pending_count,
                    "running": running_count,
                    "completed_today": completed_today,
                    "failed_today": failed_today,
                    "total_active": pending_count + running_count,
                },
                "handler_counts": handler_counts,
                "next_scheduled": next_scheduled_info,
                "running_tasks": running_task_details,
                "available_handlers": list(registry._handlers.keys()),
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"Error getting queue summary: {e}", exc_info=True)
        return error_response(f"Failed to get queue summary: {str(e)}", status_code=500)


@task_scheduler_bp.route("/handlers", methods=["GET"])
def list_handlers():
    """List all available task handlers with their schemas"""
    try:
        registry = _ensure_handlers_registered()

        handlers = []
        for name, handler in registry._handlers.items():
            handlers.append(
                {
                    "name": handler.handler_name,
                    "display_name": handler.display_name,
                    "process_type": handler.process_type,
                    "queue": handler.celery_queue,
                    "priority": handler.default_priority,
                    "config_schema": handler.config_schema,
                }
            )

        return success_response({"handlers": handlers, "count": len(handlers)})

    except Exception as e:
        logger.error(f"Error listing handlers: {e}", exc_info=True)
        return error_response(f"Failed to list handlers: {str(e)}", status_code=500)


@task_scheduler_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """List tasks with optional filtering"""
    try:
        # Get query parameters
        status = request.args.get("status")
        handler = request.args.get("handler")
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        query = db.session.query(Task)

        if status:
            if status == "active":
                query = query.filter(
                    Task.status.in_(
                        ["pending", "queued", "running", "processing", "in_progress"]
                    )
                )
            else:
                query = query.filter(Task.status == status)

        if handler:
            query = query.filter(Task.task_handler == handler)

        total = query.count()
        tasks = query.order_by(Task.updated_at.desc()).offset(offset).limit(limit).all()

        registry = _ensure_handlers_registered()

        task_list = []
        for task in tasks:
            handler_obj = (
                registry.get_handler(task.task_handler) if task.task_handler else None
            )
            task_list.append(
                {
                    "id": task.id,
                    "job_id": task.job_id,
                    "handler": task.task_handler,
                    "handler_display": (
                        handler_obj.display_name if handler_obj else task.task_handler
                    ),
                    "status": task.status,
                    "progress": task.progress or 0,
                    "description": task.description,
                    "schedule_type": task.schedule_type,
                    "cron_expression": task.cron_expression,
                    "next_run_at": (
                        task.next_run_at.isoformat() if task.next_run_at else None
                    ),
                    "last_run_at": (
                        task.last_run_at.isoformat() if task.last_run_at else None
                    ),
                    "retry_count": task.retry_count or 0,
                    "max_retries": task.max_retries or 3,
                    "error_message": task.error_message,
                    "created_at": (
                        task.created_at.isoformat() if task.created_at else None
                    ),
                    "updated_at": (
                        task.updated_at.isoformat() if task.updated_at else None
                    ),
                }
            )

        return success_response(
            {"tasks": task_list, "total": total, "limit": limit, "offset": offset}
        )

    except Exception as e:
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        return error_response(f"Failed to list tasks: {str(e)}", status_code=500)


@task_scheduler_bp.route("/tasks", methods=["POST"])
def create_task():
    """Create a new scheduled task"""
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", status_code=400)

        handler_name = data.get("handler")
        if not handler_name:
            return error_response("handler is required", status_code=400)

        registry = _ensure_handlers_registered()
        handler = registry.get_handler(handler_name)
        if not handler:
            return error_response(f"Unknown handler: {handler_name}", status_code=400)

        # Validate config against handler schema
        config = data.get("config", {})
        # TODO: Add JSON schema validation

        # Create task
        task = Task(
            description=data.get("description", f"{handler.display_name} task"),
            status="pending",
            task_handler=handler_name,
            handler_config=config,
            schedule_type=data.get("schedule_type", "immediate"),
            cron_expression=data.get("cron_expression"),
            max_retries=data.get("max_retries", 3),
            retry_delay=data.get("retry_delay", 60),
        )

        # Set next_run_at for scheduled tasks
        if task.schedule_type == "scheduled" and task.cron_expression:
            try:
                from croniter import croniter

                cron = croniter(task.cron_expression, datetime.now())
                task.next_run_at = cron.get_next(datetime)
            except Exception as e:
                logger.warning(f"Could not parse cron expression: {e}")

        db.session.add(task)
        db.session.commit()

        # If immediate, emit to start execution
        if task.schedule_type == "immediate":
            # Emit WebSocket event to notify of new task
            try:
                from backend.socketio_instance import socketio

                socketio.emit(
                    "task_created",
                    {"task_id": task.id, "handler": handler_name, "status": "pending"},
                    room="global_progress",
                )
            except Exception as e:
                logger.warning(f"Could not emit task_created event: {e}")

        return success_response(
            {
                "task": {
                    "id": task.id,
                    "handler": task.task_handler,
                    "status": task.status,
                    "schedule_type": task.schedule_type,
                    "created_at": (
                        task.created_at.isoformat() if task.created_at else None
                    ),
                },
                "message": "Task created successfully",
            }
        )

    except Exception as e:
        logger.error(f"Error creating task: {e}", exc_info=True)
        db.session.rollback()
        return error_response(f"Failed to create task: {str(e)}", status_code=500)


@task_scheduler_bp.route("/tasks/<int:task_id>", methods=["GET"])
def get_task(task_id):
    """Get task details"""
    try:
        task = db.session.get(Task, task_id)
        if not task:
            return error_response(f"Task {task_id} not found", status_code=404)

        registry = _ensure_handlers_registered()
        handler = registry.get_handler(task.task_handler) if task.task_handler else None

        return success_response(
            {
                "task": {
                    "id": task.id,
                    "job_id": task.job_id,
                    "handler": task.task_handler,
                    "handler_display": (
                        handler.display_name if handler else task.task_handler
                    ),
                    "config": task.handler_config,
                    "status": task.status,
                    "progress": task.progress or 0,
                    "description": task.description,
                    "schedule_type": task.schedule_type,
                    "cron_expression": task.cron_expression,
                    "next_run_at": (
                        task.next_run_at.isoformat() if task.next_run_at else None
                    ),
                    "last_run_at": (
                        task.last_run_at.isoformat() if task.last_run_at else None
                    ),
                    "retry_count": task.retry_count or 0,
                    "max_retries": task.max_retries or 3,
                    "retry_delay": task.retry_delay or 60,
                    "error_message": task.error_message,
                    "parent_task_id": task.parent_task_id,
                    "created_at": (
                        task.created_at.isoformat() if task.created_at else None
                    ),
                    "updated_at": (
                        task.updated_at.isoformat() if task.updated_at else None
                    ),
                }
            }
        )

    except Exception as e:
        logger.error(f"Error getting task {task_id}: {e}", exc_info=True)
        return error_response(f"Failed to get task: {str(e)}", status_code=500)


@task_scheduler_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    """Cancel/delete a task"""
    try:
        task = db.session.get(Task, task_id)
        if not task:
            return error_response(f"Task {task_id} not found", status_code=404)

        # Don't allow deleting running tasks without explicit cancellation
        if task.status in ["running", "processing", "in_progress"]:
            # Mark as cancelled instead of deleting
            task.status = "cancelled"
            task.error_message = "Cancelled by user"
            db.session.commit()

            # Emit cancellation event
            try:
                from backend.socketio_instance import socketio

                socketio.emit(
                    "task_cancelled",
                    {"task_id": task.id, "job_id": task.job_id},
                    room="global_progress",
                )
            except Exception:
                pass

            return success_response(
                {"message": f"Task {task_id} cancelled", "status": "cancelled"}
            )

        # Delete pending/completed/failed tasks
        db.session.delete(task)
        db.session.commit()

        return success_response(
            {"message": f"Task {task_id} deleted", "status": "deleted"}
        )

    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}", exc_info=True)
        db.session.rollback()
        return error_response(f"Failed to delete task: {str(e)}", status_code=500)


@task_scheduler_bp.route("/tasks/<int:task_id>/retry", methods=["POST"])
def retry_task(task_id):
    """Retry a failed task"""
    try:
        task = db.session.get(Task, task_id)
        if not task:
            return error_response(f"Task {task_id} not found", status_code=404)

        if task.status not in ["failed", "cancelled", "error"]:
            return error_response(
                f"Task {task_id} is not in a retriable state (status: {task.status})",
                status_code=400,
            )

        # Reset task for retry
        task.status = "pending"
        task.retry_count = (task.retry_count or 0) + 1
        task.error_message = None
        task.progress = 0
        db.session.commit()

        # Emit retry event
        try:
            from backend.socketio_instance import socketio

            socketio.emit(
                "task_retried",
                {
                    "task_id": task.id,
                    "handler": task.task_handler,
                    "retry_count": task.retry_count,
                },
                room="global_progress",
            )
        except Exception:
            pass

        return success_response(
            {
                "message": f"Task {task_id} queued for retry",
                "retry_count": task.retry_count,
            }
        )

    except Exception as e:
        logger.error(f"Error retrying task {task_id}: {e}", exc_info=True)
        db.session.rollback()
        return error_response(f"Failed to retry task: {str(e)}", status_code=500)


@task_scheduler_bp.route("/execute/<int:task_id>", methods=["POST"])
def execute_task(task_id):
    """Execute a task immediately (for testing/manual execution)"""
    try:
        task = db.session.get(Task, task_id)
        if not task:
            return error_response(f"Task {task_id} not found", status_code=404)

        if task.status not in ["pending", "queued"]:
            return error_response(
                f"Task {task_id} is not pending (status: {task.status})",
                status_code=400,
            )

        registry = _ensure_handlers_registered()
        handler = registry.get_handler(task.task_handler)
        if not handler:
            return error_response(
                f"Handler {task.task_handler} not found", status_code=400
            )

        # Update task status
        task.status = "running"
        task.last_run_at = datetime.now()
        db.session.commit()

        # Emit start event
        try:
            from backend.socketio_instance import socketio
            from backend.utils.unified_progress_system import create_progress_job

            job_id = task.job_id or f"task_{task.id}"
            task.job_id = job_id
            db.session.commit()

            # Create progress job
            create_progress_job(
                job_id=job_id,
                process_type=handler.process_type,
                total_items=100,
                description=task.description or f"Executing {handler.display_name}",
            )

            socketio.emit(
                "job_progress",
                {
                    "job_id": job_id,
                    "status": "start",
                    "progress": 0,
                    "message": f"Starting {handler.display_name}...",
                    "process_type": handler.process_type,
                },
                room="global_progress",
            )
        except Exception as e:
            logger.warning(f"Could not emit start event: {e}")

        # Execute handler
        config = task.handler_config or {}

        def progress_callback(progress: int, message: str, data: Optional[Dict] = None):
            task.progress = progress
            db.session.commit()

            try:
                from backend.socketio_instance import socketio

                emit_data = {
                    "job_id": task.job_id,
                    "status": "processing",
                    "progress": progress,
                    "message": message,
                    "process_type": handler.process_type,
                }
                if data:
                    emit_data["additional_data"] = data
                socketio.emit("job_progress", emit_data, room="global_progress")
            except Exception:
                pass

        try:
            result = handler.execute(task, config, progress_callback)

            # Update task based on result
            task.status = result.status.value
            task.progress = 100 if result.status.value == "success" else task.progress
            task.error_message = result.error_message
            db.session.commit()

            # Emit completion event
            try:
                from backend.socketio_instance import socketio

                socketio.emit(
                    "job_progress",
                    {
                        "job_id": task.job_id,
                        "status": (
                            "complete" if result.status.value == "success" else "error"
                        ),
                        "progress": (
                            100 if result.status.value == "success" else task.progress
                        ),
                        "message": result.message,
                        "process_type": handler.process_type,
                    },
                    room="global_progress",
                )
            except Exception:
                pass

            return success_response(
                {
                    "task_id": task.id,
                    "status": task.status,
                    "result": {
                        "status": result.status.value,
                        "message": result.message,
                        "output_files": result.output_files,
                        "output_data": result.output_data,
                        "items_processed": result.items_processed,
                        "items_total": result.items_total,
                        "duration_seconds": result.duration_seconds,
                    },
                }
            )

        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            db.session.commit()

            # Emit error event
            try:
                from backend.socketio_instance import socketio

                socketio.emit(
                    "job_progress",
                    {
                        "job_id": task.job_id,
                        "status": "error",
                        "progress": task.progress or 0,
                        "message": str(e),
                        "process_type": handler.process_type,
                    },
                    room="global_progress",
                )
            except Exception:
                pass

            return error_response(f"Task execution failed: {str(e)}", status_code=500)

    except Exception as e:
        logger.error(f"Error executing task {task_id}: {e}", exc_info=True)
        db.session.rollback()
        return error_response(f"Failed to execute task: {str(e)}", status_code=500)
