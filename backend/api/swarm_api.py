"""
Swarm API — proxy endpoints for the Swarm Orchestrator plugin.

Proxies requests to the swarm service on port 8210.
No auth needed — this is a local-only orchestration service.
"""

import logging

import requests
from flask import Blueprint, request as flask_request

from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

swarm_bp = Blueprint("swarm", __name__, url_prefix="/api/swarm")

SWARM_URL = "http://localhost:8210"
SWARM_TIMEOUT = 10


def _proxy_get(path: str, timeout: int = SWARM_TIMEOUT):
    """Proxy a GET request to the swarm service."""
    try:
        params = dict(flask_request.args)
        resp = requests.get(f"{SWARM_URL}{path}", params=params, timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Swarm service not running"}, 503
    except Exception as e:
        return {"error": str(e)}, 500


def _proxy_post(path: str, json_data: dict = None, timeout: int = SWARM_TIMEOUT):
    """Proxy a POST request to the swarm service."""
    try:
        resp = requests.post(f"{SWARM_URL}{path}", json=json_data, timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Swarm service not running"}, 503
    except Exception as e:
        return {"error": str(e)}, 500


def _extract_error(data: dict, fallback: str = "Request failed") -> str:
    """Extract a usable error message from a FastAPI/proxy response.

    FastAPI 422 responses put validation errors in 'detail' as a list.
    """
    detail = data.get("detail")
    if isinstance(detail, list):
        # FastAPI validation error — grab the first message
        msgs = [d.get("msg", str(d)) for d in detail if isinstance(d, dict)]
        return "; ".join(msgs) if msgs else fallback
    if isinstance(detail, str):
        return detail
    return data.get("error", data.get("message", fallback))


# --- Health ---

@swarm_bp.route("/health", methods=["GET"])
def health():
    data, status = _proxy_get("/health")
    if status == 503:
        return error_response("Swarm service not running", 503, "SWARM_OFFLINE")
    return success_response(data=data, message="Swarm service healthy")


# --- Launch ---

@swarm_bp.route("/launch", methods=["POST"])
def launch():
    body = flask_request.get_json() or {}
    data, status = _proxy_post("/swarm/launch", body, timeout=30)
    if status >= 400:
        return error_response(_extract_error(data, "Launch failed"), status)
    return success_response(data=data, message=data.get("message", "Swarm launched"))


# --- Status ---

@swarm_bp.route("/status", methods=["GET"])
def all_status():
    data, status = _proxy_get("/swarm/status")
    if status == 503:
        return success_response(data={"swarms": [], "count": 0}, message="Swarm service offline")
    return success_response(data=data, message="Status retrieved")


@swarm_bp.route("/status/<swarm_id>", methods=["GET"])
def swarm_status(swarm_id):
    data, status = _proxy_get(f"/swarm/status/{swarm_id}")
    if status == 404:
        return error_response("Swarm not found", 404, "SWARM_NOT_FOUND")
    if status == 503:
        return error_response("Swarm service not running", 503, "SWARM_OFFLINE")
    return success_response(data=data.get("data", data), message="Status retrieved")


# --- Logs ---

@swarm_bp.route("/<swarm_id>/logs/<task_id>", methods=["GET"])
def task_logs(swarm_id, task_id):
    lines = flask_request.args.get("lines", 50, type=int)
    data, status = _proxy_get(f"/swarm/{swarm_id}/logs/{task_id}?lines={lines}")
    if status >= 400:
        return error_response(_extract_error(data, "Logs unavailable"), status)
    return success_response(data=data, message="Logs retrieved")


# --- Cancel ---

@swarm_bp.route("/cancel", methods=["POST"])
def cancel():
    body = flask_request.get_json() or {}
    data, status = _proxy_post("/swarm/cancel", body)
    if status >= 400:
        return error_response(_extract_error(data, "Cancel failed"), status)
    return success_response(data=data, message=data.get("message", "Cancelled"))


# --- Merge ---

@swarm_bp.route("/merge", methods=["POST"])
def merge():
    body = flask_request.get_json() or {}
    data, status = _proxy_post("/swarm/merge", body, timeout=120)
    if status >= 400:
        return error_response(_extract_error(data, "Merge failed"), status)
    return success_response(data=data, message="Merge completed")


# --- Cleanup ---

@swarm_bp.route("/cleanup", methods=["POST"])
def cleanup():
    body = flask_request.get_json() or {}
    data, status = _proxy_post("/swarm/cleanup", body)
    if status >= 400:
        return error_response(_extract_error(data, "Cleanup failed"), status)
    return success_response(data=data, message=data.get("message", "Cleaned up"))


# --- Templates ---

@swarm_bp.route("/templates", methods=["GET"])
def templates():
    data, status = _proxy_get("/swarm/templates")
    if status == 503:
        return success_response(data={"templates": [], "count": 0}, message="Swarm service offline")
    return success_response(data=data, message="Templates retrieved")


@swarm_bp.route("/templates/<filename>", methods=["GET"])
def template_content(filename):
    data, status = _proxy_get(f"/swarm/templates/{filename}")
    if status >= 400:
        return error_response(_extract_error(data, "Template not found"), status)
    return success_response(data=data, message="Template retrieved")


# --- Connectivity ---

@swarm_bp.route("/connectivity", methods=["GET"])
def connectivity():
    data, status = _proxy_get("/swarm/connectivity")
    if status == 503:
        return success_response(
            data={"online": False, "flight_mode": True, "backends": []},
            message="Swarm service offline",
        )
    return success_response(data=data, message="Connectivity checked")


# --- History ---

@swarm_bp.route("/history", methods=["GET"])
def history():
    limit = flask_request.args.get("limit", 20, type=int)
    data, status = _proxy_get(f"/swarm/history?limit={limit}")
    if status == 503:
        return success_response(data={"swarms": [], "count": 0}, message="Swarm service offline")
    return success_response(data=data, message="History retrieved")
