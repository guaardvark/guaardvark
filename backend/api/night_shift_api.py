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
    (``when: at`` with ``at``), start a held job now (``when: now``), or pause
    a scheduled one (``when: hold``): it waits, with no time, until one of
    the others."""
    data = request.get_json(silent=True) or {}
    when = data.get("when")
    if when == "hold":
        if not night_shift.hold(task_id):
            return error_response(f"Job {task_id} is not waiting", status_code=409)
        return success_response({"task_id": task_id, "when": "hold"})
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


@night_shift_bp.route("/kinds", methods=["GET"])
def get_kinds():
    """What can be scheduled: features that take one prompt, and the kinds
    their own pages schedule."""
    from backend.services import schedulable

    return success_response({"kinds": schedulable.catalog()})


@night_shift_bp.route("/jobs", methods=["POST"])
def post_job():
    """Save a job to run later: ``when`` is ``night_shift`` or ``at`` with ``at``.

    One of three bodies:
    * ``feature`` + ``prompt`` (+ ``options``, ``count``): a feature from
      GET /kinds, described in one line.
    * ``tool`` + ``arguments``: a chat tool call.
    * ``type`` (a hand-off kind) + ``request`` (the body its start route
      takes), with ``variant``, ``path`` and ``name``: what a feature's own
      page sends.
    """
    data = request.get_json(silent=True) or {}
    when = data.get("when")
    at = None
    if when == night_shift.AT:
        try:
            at = _parse_time(data.get("at"))
        except (TypeError, ValueError):
            return error_response("at must be an ISO date and time", status_code=400)
    try:
        if data.get("feature"):
            try:
                count = max(1, min(int(data.get("count") or 1), 20))
            except (TypeError, ValueError):
                return error_response("count must be a whole number", status_code=400)
            options = data.get("options") if isinstance(data.get("options"), dict) else None
            jobs = [night_shift.schedule_feature(data["feature"], str(data.get("prompt") or ""), when, at,
                                                 options, by_person=True) for _ in range(count)]
            return success_response({**jobs[0], "jobs": jobs})
        if data.get("tool"):
            arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
            return success_response(night_shift.schedule_tool_call(
                str(data["tool"]), arguments, str(data.get("name") or ""), when, at, by_person=True))
        return success_response(night_shift.create_handoff_job(
            data.get("type"), str(data.get("name") or ""), data.get("request"), data.get("variant"), when, at,
            data.get("path")))
    except ValueError as e:
        return error_response(str(e), status_code=400)


@night_shift_bp.route("/jobs/<int:task_id>", methods=["PUT"])
def put_job(task_id: int):
    """Change a job that has not started: ``name``, ``request`` (the body it
    will send, checked as a new job's is) and ``variant``. 409 once it started."""
    data = request.get_json(silent=True) or {}
    body = data.get("request")
    if body is not None and not isinstance(body, dict):
        return error_response("request must be the body the job is started with", status_code=400)
    try:
        return success_response(night_shift.update_job(task_id, data.get("name"), body, data.get("variant")))
    except ValueError as e:
        return error_response(str(e), status_code=400)
    except LookupError as e:
        return error_response(str(e), status_code=409)


@night_shift_bp.route("/jobs/<int:task_id>", methods=["DELETE"])
def delete_job(task_id: int):
    """Take a job that has not started off the schedule."""
    if not night_shift.remove(task_id):
        return error_response(f"Job {task_id} is not waiting", status_code=409)
    return success_response({"task_id": task_id, "removed": True})


@night_shift_bp.route("/order", methods=["POST"])
def post_order():
    """Set the night's order: ``ids`` top to bottom."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return error_response("ids must be a list of job numbers", status_code=400)
    return success_response({"reordered": night_shift.reorder(ids)})
