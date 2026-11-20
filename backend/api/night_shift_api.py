"""Scheduling a job for later: the night shift and "at a time".

Auto-registered by backend.utils.blueprint_discovery. The rules live in
backend/services/night_shift.py; this file is the HTTP surface.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, request

from backend.services import night_shift
from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

night_shift_bp = Blueprint("night_shift_api", __name__, url_prefix="/api/night-shift")


def _parse_time(value) -> datetime:
    """An ISO time as naive UTC. A time without a zone is this machine's local time."""
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


@night_shift_bp.route("/status", methods=["GET"])
def get_status():
    """The window, who is at the machine, what is waiting and what ran."""
    return success_response(night_shift.status())


@night_shift_bp.route("/window", methods=["PUT"])
def put_window():
    data = request.get_json(silent=True) or {}
    try:
        window = night_shift.set_window(str(data.get("start", "")), str(data.get("end", "")))
    except ValueError:
        return error_response("start and end must be different times as HH:MM", status_code=400)
    start, end = window.split("-")
    return success_response({"window": {"start": start, "end": end}})


@night_shift_bp.route("/pause", methods=["POST"])
def post_pause():
    data = request.get_json(silent=True) or {}
    paused = bool(data.get("paused", True))
    night_shift.set_paused(paused)
    return success_response({"paused": paused})


@night_shift_bp.route("/jobs/<int:task_id>/schedule", methods=["POST"])
def post_schedule(task_id: int):
    """Hold a job for the night shift (``when: night_shift``) or for a time
    (``when: at`` with ``at``), or start a held job now (``when: now``)."""
    data = request.get_json(silent=True) or {}
    when = data.get("when")
    if when == "now":
        if not night_shift.release(task_id):
            return error_response(f"Job {task_id} is not waiting", status_code=409)
        return success_response({"task_id": task_id, "when": "now"})
    at = None
    if when == night_shift.AT:
        try:
            at = _parse_time(data.get("at"))
        except (TypeError, ValueError):
            return error_response("at must be an ISO date and time", status_code=400)
    try:
        return success_response(night_shift.schedule(task_id, when, at))
    except ValueError as e:
        return error_response(str(e), status_code=400)
    except LookupError as e:
        return error_response(str(e), status_code=409)


@night_shift_bp.route("/order", methods=["POST"])
def post_order():
    """Set the night's order: ``ids`` top to bottom."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return error_response("ids must be a list of job numbers", status_code=400)
    return success_response({"reordered": night_shift.reorder(ids)})
