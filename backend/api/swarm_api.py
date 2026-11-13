"""
Swarm API — proxy endpoints for the Swarm Orchestrator plugin.

Proxies requests to the swarm service on port 8210.
Launch, merge and cleanup answer this machine or the API key
(backend/utils/auth_guard.py RUNS_CODE_PATHS); the sidecar itself takes only
the internal token these proxies add.
"""

import logging
import os
import subprocess
from pathlib import Path

import requests
from flask import Blueprint, has_request_context, request as flask_request

from backend.services.guarded_code_service import default_repo_root
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

swarm_bp = Blueprint("swarm", __name__, url_prefix="/api/swarm")

SWARM_URL = "http://localhost:8210"
SWARM_TIMEOUT = 10

INTERNAL_TOKEN_HEADER = "X-Swarm-Internal-Token"


def _internal_secret() -> str:
    """Read the shared sidecar token from env or data/.swarm_internal_secret.

    Must match the value the sidecar resolves at startup (see app.py
    _load_internal_secret). Read-only here — the sidecar owns generation.
    """
    env_secret = os.environ.get("SWARM_INTERNAL_SECRET")
    if env_secret:
        return env_secret.strip()
    try:
        secret_file = default_repo_root() / "data" / ".swarm_internal_secret"
        if secret_file.exists():
            return secret_file.read_text().strip()
    except Exception as e:
        logger.debug(f"Could not read swarm internal secret: {e}")
    return ""


def _internal_headers() -> dict:
    return {INTERNAL_TOKEN_HEADER: _internal_secret()}


def _proxy_get(path: str, timeout: int = SWARM_TIMEOUT, params: dict | None = None):
    """Proxy a GET request to the swarm service.

    ``params`` defaults to the current request's query string. Callers with no
    request (chat tools on a worker thread) pass their own, or send none.
    """
    try:
        if params is None:
            params = dict(flask_request.args) if has_request_context() else {}
        resp = requests.get(
            f"{SWARM_URL}{path}", params=params, timeout=timeout, headers=_internal_headers()
        )
    except requests.ConnectionError:
        return {"error": "Swarm service not running"}, 503
    except requests.Timeout:
        # Running (the port accepted the connection) but not answering: a
        # different fault from "not running", and reported as one.
        return {"error": f"Swarm service did not answer within {timeout} s"}, 504
    except Exception as e:
        return {"error": str(e)}, 500
    try:
        return resp.json(), resp.status_code
    except ValueError:
        return {
            "error": f"Swarm service answered HTTP {resp.status_code} with a body that is not JSON"
        }, 502


def _proxy_post(path: str, json_data: dict = None, timeout: int = SWARM_TIMEOUT):
    """Proxy a POST request to the swarm service."""
    try:
        resp = requests.post(
            f"{SWARM_URL}{path}", json=json_data, timeout=timeout, headers=_internal_headers()
        )
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


def _status_read_failed(data, status: int, fallback: str):
    """The error response for a status read the sidecar did not answer properly.

    The backend is a gateway here, so the sidecar's own failures are answered
    as 502 (or 504 for a timeout) with the sidecar's message. Its 401/403 must
    not be passed through: callers would read that as the backend refusing
    them, when it is the sidecar refusing the backend's internal token.
    """
    message = _extract_error(data, fallback) if isinstance(data, dict) else fallback
    if status == 504:
        return error_response(message, 504, "SWARM_TIMEOUT")
    if status in (401, 403):
        return error_response(
            f"Swarm service refused the backend's internal token (HTTP {status}): {message}",
            502, "SWARM_ERROR",
        )
    if status >= 500:
        return error_response(message, 502, "SWARM_ERROR")
    return error_response(message, status, "SWARM_ERROR")


# --- Health ---

def _write_plan(markdown: str) -> str:
    """Save a plan sent as text and return its path relative to the install root.
    Plans go under data/outputs so they never dirty the checkout a self-code
    swarm refuses to start on."""
    import time
    from backend.config import GUAARDVARK_ROOT

    rel = Path("data") / "outputs" / "swarm_plans" / f"plan_{time.strftime('%Y%m%d_%H%M%S')}.md"
    path = Path(GUAARDVARK_ROOT) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return rel.as_posix()


def _plan_from_prompt(prompt: str) -> str:
    """A one-task plan for a swarm asked for in plain words: one agent, one worktree."""
    title = " ".join(prompt.split())
    title = title if len(title) <= 60 else title[:57].rstrip() + "..."
    return f"# Swarm Plan: {title}\n\n## Task: {title}\n- depends_on: none\n\n{prompt.strip()}\n"


@swarm_bp.route("/health", methods=["GET"])
def health():
    data, status = _proxy_get("/health")
    if status == 503:
        return error_response("Swarm service not running", 503, "SWARM_OFFLINE")
    if status >= 400:
        return _status_read_failed(data, status, "Swarm health check failed")
    return success_response(data=data, message="Swarm service healthy")


# --- Launch ---

def prepare_launch(body: dict):
    """Turn a launch request into the body the sidecar takes, or refuse it.

    The one launch path for the Swarm page (POST /api/swarm/launch) and the
    launch_swarm chat/MCP tool. A plan may arrive as plan_path, as markdown
    (plan_markdown), or as a sentence (prompt or goal) that becomes a one-task
    plan; the sidecar only reads plan_path. Self-code swarms are pinned to the
    configured repository, never auto-merge, and need acknowledge_dirty_tree
    on a dirty tree.

    Returns (body, None) to send, or (None, (message, http_status, code)).
    """
    body = dict(body or {})
    markdown = str(body.pop("plan_markdown", "") or "")
    prompt = str(body.pop("prompt", "") or body.pop("goal", "") or "").strip()
    if not body.get("plan_path"):
        if markdown.strip():
            body["plan_path"] = _write_plan(markdown)
        elif prompt:
            body["plan_path"] = _write_plan(_plan_from_prompt(prompt))
        else:
            return None, ("A plan is required: plan_path, plan_markdown, or a goal", 400, "NO_PLAN")

    # Securely resolve target repository path.
    # If the request targets GUAARDVARK_ROOT (or defaults to it), we MUST treat it
    # as a self_code swarm to prevent parameter-tampering security bypasses.
    repo_path_str = body.get("repo_path")
    if repo_path_str:
        try:
            repo_path = Path(repo_path_str).resolve()
            is_targeting_self = (repo_path == default_repo_root())
        except Exception as e:
            return None, (f"Invalid repo_path: {e}", 400, None)
    else:
        is_targeting_self = True
        repo_path = default_repo_root()

    if is_targeting_self or body.get("self_code"):
        body["self_code"] = True
        body["auto_merge"] = False
        if repo_path != default_repo_root():
            return None, ("Self-code swarms may only target the configured repository root", 403, None)
        if not (repo_path / ".git").exists():
            return None, ("Configured repository root is not a git repository", 400, None)
        status = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status.stdout.strip() and not body.get("acknowledge_dirty_tree"):
            return None, (
                "Repository has uncommitted changes; acknowledge_dirty_tree is required for self-code swarms",
                409,
                "DIRTY_TREE",
            )
    return body, None


@swarm_bp.route("/launch", methods=["POST"])
def launch():
    body, refusal = prepare_launch(flask_request.get_json() or {})
    if refusal:
        message, status, code = refusal
        return error_response(message, status, code) if code else error_response(message, status)
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
    if status >= 400:
        return _status_read_failed(data, status, "Swarm status unavailable")
    return success_response(data=data, message="Status retrieved")


@swarm_bp.route("/status/<swarm_id>", methods=["GET"])
def swarm_status(swarm_id):
    data, status = _proxy_get(f"/swarm/status/{swarm_id}")
    if status == 404:
        return error_response("Swarm not found", 404, "SWARM_NOT_FOUND")
    if status == 503:
        return error_response("Swarm service not running", 503, "SWARM_OFFLINE")
    if status >= 400:
        return _status_read_failed(data, status, "Swarm status unavailable")
    return success_response(data=data.get("data", data), message="Status retrieved")


# --- Logs ---

@swarm_bp.route("/<swarm_id>/logs/<task_id>", methods=["GET"])
def task_logs(swarm_id, task_id):
    lines = flask_request.args.get("lines", 50, type=int)
    data, status = _proxy_get(f"/swarm/{swarm_id}/logs/{task_id}?lines={lines}")
    if status >= 400:
        return error_response(_extract_error(data, "Logs unavailable"), status)
    return success_response(data=data, message="Logs retrieved")


@swarm_bp.route("/<swarm_id>/diff/<task_id>", methods=["GET"])
def task_diff(swarm_id, task_id):
    data, status = _proxy_get(f"/swarm/{swarm_id}/diff/{task_id}")
    if status >= 400:
        return error_response(_extract_error(data, "Diff unavailable"), status)
    return success_response(data=data, message="Diff retrieved")


@swarm_bp.route("/<swarm_id>/bus/state", methods=["GET", "POST"])
def bus_state(swarm_id):
    # Only an explicit POST writes: Flask also routes HEAD here.
    if flask_request.method == "POST":
        data, status = _proxy_post(f"/swarm/{swarm_id}/bus/state", flask_request.get_json() or {})
    else:
        data, status = _proxy_get(f"/swarm/{swarm_id}/bus/state")
    if status >= 400:
        return error_response(_extract_error(data, "Bus state unavailable"), status)
    return success_response(data=data, message="Bus state updated" if flask_request.method == "POST" else "Bus state retrieved")


@swarm_bp.route("/<swarm_id>/bus/broadcast", methods=["POST"])
def bus_broadcast(swarm_id):
    data, status = _proxy_post(f"/swarm/{swarm_id}/bus/broadcast", flask_request.get_json() or {})
    if status >= 400:
        return error_response(_extract_error(data, "Bus broadcast failed"), status)
    return success_response(data=data, message="Bus event broadcast")


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
def template_content(filename: str):
    data, status = _proxy_get(f"/swarm/templates/{filename}")
    if status >= 400:
        return error_response(_extract_error(data, "Template not found"), status)
    return success_response(data=data, message="Template retrieved")


@swarm_bp.route("/templates/save", methods=["POST"])
def save_template():
    body = flask_request.get_json() or {}
    data, status = _proxy_post("/swarm/templates/save", body)
    if status >= 400:
        return error_response(_extract_error(data, "Save failed"), status)
    return success_response(data=data, message="Template saved")



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


# --- Event Hook (Internal) ---

@swarm_bp.route("/event", methods=["POST"])
def swarm_event():
    """Receive an event from the swarm service and broadcast via Socket.IO."""
    # check that it's local (for safety)
    if flask_request.remote_addr not in ("127.0.0.1", "localhost"):
        return error_response("Internal only", 403)

    body = flask_request.get_json() or {}
    event_type = body.get("event_type")
    task_id = body.get("task_id", "swarm")
    data = body.get("data", {})

    if not event_type:
        return error_response("event_type required", 400)

    try:
        from backend.socketio_events import emit_swarm_event
        emit_swarm_event(event_type, task_id, data)
        return success_response(message="Event broadcasted")
    except Exception as e:
        logger.error(f"Failed to broadcast swarm event: {e}")
        return error_response(str(e), 500)
