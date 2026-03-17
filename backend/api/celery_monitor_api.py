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

        insp = celery.control.inspect()
        return success_response(
            "Celery tasks retrieved",
            {
                "active": insp.active() or {},
                "reserved": insp.reserved() or {},
                "scheduled": insp.scheduled() or {},
            },
        )
    except ImportError as e:
        return error_response(
            f"Celery not available: {str(e)}", 503, "CELERY_UNAVAILABLE"
        )
    except Exception as e:
        return error_response(
            f"Celery monitoring error: {str(e)}", 500, "MONITORING_ERROR"
        )
