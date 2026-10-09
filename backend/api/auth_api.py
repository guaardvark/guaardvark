"""This install's API key and network access, for Settings → Access.

GET /api/auth/status is open: it tells a browser whether the install has a key,
whether the request came from the Guaardvark machine, whether it carried the
right key, and whether this browser is signed in. It never returns the key or
anything derived from it.

POST /api/auth/session takes the key once and signs this browser in with an
HttpOnly cookie that holds a token derived from it (backend/utils/
api_session.py); DELETE /api/auth/session signs it out. The browser never keeps
the key.

/api/auth/key creates (POST), replaces (PUT) and removes (DELETE) the key. It is
in auth_guard's protected list, so it answers the Guaardvark machine while the
install has no key, and the current key or a signed-in browser once it has one.
A new key is in the response body once, for copying to other devices, and the
same response signs the calling browser in with it.

POST /api/auth/network-access turns network access on or off (JSON
{"enabled": bool}). It is protected like the key: this machine, the key, or,
while network access is on, any device on the local network.
"""

import hmac
import logging

from flask import Blueprint, jsonify, request

from backend.services import api_key_service as keys
from backend.services import network_access_service as network
from backend.utils import api_session, auth_guard

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")

JSON_REQUIRED = "Send the request as JSON (Content-Type: application/json)."


def _no_store(response, status=200):
    # Status depends on the request's credentials; key bodies must not sit in
    # a cache.
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _error(message: str, code: str, status: int):
    return _no_store(jsonify({"error": message, "code": code}), status)


@auth_bp.route("/status", methods=["GET"])
def auth_status():
    state = keys.key_state()
    session = api_session.session_state()
    may_run = auth_guard.caller_is_authorized()
    access = network.network_access_state()
    return _no_store(jsonify({
        "key_required": state.configured,
        "this_machine": auth_guard.request_is_from_this_machine(),
        # The X-API-Key header: what the Test button sends for a typed key.
        "key_ok": auth_guard.request_carries_valid_key(),
        "session_ok": session == api_session.VALID,
        # A sign-in cookie was sent and is not accepted (the key changed).
        "session_rejected": session == api_session.REJECTED,
        "can_run_protected": may_run,
        "can_manage_key": may_run and state.manageable,
        "manage_note": state.manage_note,
        "restart_needed": state.restart_needed,
        "docker": state.docker,
        "tool_endpoints_protected": auth_guard.tool_endpoints_protected(),
        "protected": auth_guard.protected_summary(),
        "machine": auth_guard.machine_name(),
        "network_access": access.enabled,
        "can_manage_network_access": may_run and access.manageable,
        "network_access_note": access.manage_note,
        # Whether this device is one network access opens to.
        "on_local_network": auth_guard.request_is_from_local_network(),
    }))


@auth_bp.route("/session", methods=["POST"])
def sign_in():
    """Sign this browser in. JSON only, so a page on another site cannot send
    it without the browser asking first."""
    if not request.is_json:
        return _error(JSON_REQUIRED, "json_required", 415)
    provided = (request.get_json(silent=True) or {}).get("key")
    provided = provided.strip() if isinstance(provided, str) else ""
    if not provided:
        return _error("Paste this install's API key.", "key_missing", 400)
    key = auth_guard.configured_api_key()
    if not key:
        return _error(
            f"This install has no API key yet. Create one in Settings → Access on {auth_guard.machine_name()}.",
            "no_key", 409,
        )
    if not hmac.compare_digest(provided.encode("utf-8"), key.encode("utf-8")):
        logger.warning("[AUTH] Sign-in with a wrong API key from %s", auth_guard._effective_client_ip())
        return _error("That is not this install's API key.", "wrong_key", 401)
    logger.info("[AUTH] Browser signed in from %s", auth_guard._effective_client_ip())
    response, status = _no_store(jsonify({"session": True}))
    return api_session.set_session_cookie(response, key), status


@auth_bp.route("/session", methods=["DELETE"])
def sign_out():
    response, status = _no_store(jsonify({"session": False}))
    return api_session.clear_session_cookie(response), status


def _checked():
    """The before_request hook already ran; this keeps the route closed in an
    app that registers the blueprint without it. POST and PUT take JSON only, so
    a page on another site cannot send them without the browser asking first."""
    refused = auth_guard.check_endpoint_auth()
    if refused is not None:
        return refused
    if request.method in ("POST", "PUT") and not request.is_json:
        return _error(JSON_REQUIRED, "json_required", 415)
    return None


def _changed(key: str, status: int, verb: str):
    logger.info("[AUTH] API key %s from %s", verb, auth_guard._effective_client_ip())
    response, status = _no_store(jsonify({"key": key, "key_required": True, "session": True}), status)
    # The browser that made the change is signed in with the new key.
    return api_session.set_session_cookie(response, key), status


@auth_bp.route("/key", methods=["POST"])
def create_key():
    refused = _checked()
    if refused is not None:
        return refused
    try:
        key = keys.create_key()
    except keys.KeyChangeRefused as e:
        return _error(e.message, e.code, 409)
    except OSError as e:
        return _error(f"Could not write .env: {e}", "env_write_failed", 500)
    return _changed(key, 201, "created")


@auth_bp.route("/key", methods=["PUT"])
def replace_key():
    refused = _checked()
    if refused is not None:
        return refused
    try:
        key = keys.replace_key()
    except keys.KeyChangeRefused as e:
        return _error(e.message, e.code, 409)
    except OSError as e:
        return _error(f"Could not write .env: {e}", "env_write_failed", 500)
    return _changed(key, 200, "replaced")


@auth_bp.route("/key", methods=["DELETE"])
def remove_key():
    refused = _checked()
    if refused is not None:
        return refused
    try:
        removed = keys.remove_key()
    except keys.KeyChangeRefused as e:
        return _error(e.message, e.code, 409)
    except OSError as e:
        return _error(f"Could not write .env: {e}", "env_write_failed", 500)
    if removed:
        logger.info("[AUTH] API key removed from %s", auth_guard._effective_client_ip())
    response, status = _no_store(jsonify({"removed": removed, "key_required": False, "session": False}))
    return api_session.clear_session_cookie(response), status


@auth_bp.route("/network-access", methods=["POST"])
def set_network_access():
    refused = _checked()
    if refused is not None:
        return refused
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return _error('Send {"enabled": true} or {"enabled": false}.', "enabled_missing", 400)
    try:
        network.set_network_access(enabled)
    except network.NetworkAccessRefused as e:
        return _error(e.message, "network_access_not_manageable", 409)
    except OSError as e:
        return _error(f"Could not write .env: {e}", "env_write_failed", 500)
    logger.info("[AUTH] Network access turned %s from %s", "on" if enabled else "off", auth_guard._effective_client_ip())
    return _no_store(jsonify({"network_access": enabled}))
