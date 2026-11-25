"""Endpoints for monitoring Celery tasks."""

from flask import Blueprint
from backend.utils.response_utils import success_response, error_response

celery_monitor_bp = Blueprint("celery_monitor", __name__, url_prefix="/api/celery")


@celery_monitor_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """Return lists of active, reserved and scheduled tasks."""
    try:
        # Use delayed import to avoid circular dependency
        from backend.celery_app import celery
        from backend.utils.celery_inspect import inspect_workers
        return success_response({
            "active": inspect_workers(celery, "active"),
            "reserved": inspect_workers(celery, "reserved"),
            "scheduled": inspect_workers(celery, "scheduled"),
        }, "Celery tasks retrieved")
    except ImportError as e:
        return error_response(f"Celery not available: {str(e)}", 503, "CELERY_UNAVAILABLE")
    except Exception as e:
        return error_response(f"Celery monitoring error: {str(e)}", 500, "MONITORING_ERROR")
