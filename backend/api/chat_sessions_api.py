#!/usr/bin/env python3
"""Chat sessions API — the modal-session toggle and the chat export.

If the chat-session model grows more knobs (per-session model preference,
RAG scope, etc.) they belong here too. Auto-discovered by blueprint_discovery.py.
"""

import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

chat_sessions_bp = Blueprint(
    "chat_sessions", __name__, url_prefix="/api/chat-sessions"
)

# Modes the frontend is allowed to set. Anything else 400s.
_VALID_MODES = ("chat", "agent")


@chat_sessions_bp.route("/<session_id>/mode", methods=["GET"])
def get_session_mode(session_id: str):
    """Return the current mode for a session.

    Returns "chat" if the session row doesn't exist yet — the frontend
    treats unknown sessions as fresh ones, which default to chat mode.
    """
    try:
        from backend.models import LLMSession, db

        session = db.session.get(LLMSession, session_id)
        mode = (session.mode if session and session.mode else "chat").strip()
        return jsonify({"success": True, "session_id": session_id, "mode": mode,
                        **_plan_mode_state(session_id, session)})
    except Exception as e:
        logger.error(f"get_session_mode failed for {session_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _plan_mode_state(session_id: str, session) -> dict:
    """plan_mode for the session (a chat not started yet follows Settings →
    Start new chats in plan mode), and whether this chat model can plan."""
    from backend.services.plan_mode import active_chat_model, availability, default_plan_mode, plan_mode_supported

    if not plan_mode_supported(session_id):
        return {"plan_mode": False, "plan_mode_available": False,
                "plan_mode_reason": "Plan mode needs a chat that can show a plan for approval."}
    on = bool(session.plan_mode) if session is not None else default_plan_mode()
    available, reason = availability(active_chat_model())
    return {"plan_mode": on, "plan_mode_available": available, "plan_mode_reason": reason}


@chat_sessions_bp.route("/<session_id>/plan-mode", methods=["PATCH"])
def set_plan_mode(session_id: str):
    """Turn plan mode on or off for a session. Body: {"on": true|false}.

    UPSERTs the session row, like the mode route, so a fresh chat can start
    in plan mode before its first message. Turning it on is refused where the
    chat cannot show a plan, or the chat model measured below the floor.
    """
    try:
        from backend.models import LLMSession, db
        from backend.services.plan_mode import active_chat_model, availability, plan_mode_supported

        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("on"), bool):
            return jsonify({"success": False, "error": "on must be true or false"}), 400
        on = body["on"]
        session = db.session.get(LLMSession, session_id)
        if on:
            if not plan_mode_supported(session_id):
                return jsonify({"success": False, "error": "Plan mode needs a chat that can show a plan for approval."}), 409
            available, reason = availability(active_chat_model())
            if not available:
                return jsonify({"success": False, "error": reason}), 409
        if session is None:
            session = LLMSession(id=session_id, user="default", plan_mode=on)
            db.session.add(session)
        else:
            session.plan_mode = on
        db.session.commit()
        logger.info(f"[CHAT-SESSION] {session_id[:12]} plan mode → {'on' if on else 'off'}")
        return jsonify({"success": True, "session_id": session_id, **_plan_mode_state(session_id, session)})
    except Exception as e:
        logger.error(f"set_plan_mode failed for {session_id}: {e}")
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e)}), 500


@chat_sessions_bp.route("/<session_id>/mode", methods=["PATCH"])
def set_session_mode(session_id: str):
    """Set the mode for a session.

    Body: {"mode": "chat"|"agent"}

    UPSERTs the session row if it doesn't exist yet so a fresh chat can
    flip into agent mode before its first user message lands. Any user
    string lands as the row's `user` field on creation; we default to
    "default" matching the rest of the codebase's anonymous-user pattern.
    """
    try:
        from backend.models import LLMSession, db

        body = request.get_json(silent=True) or {}
        mode = (body.get("mode") or "").strip().lower()
        if mode not in _VALID_MODES:
            return jsonify({
                "success": False,
                "error": f"mode must be one of {_VALID_MODES}",
            }), 400

        session = db.session.get(LLMSession, session_id)
        if session is None:
            session = LLMSession(id=session_id, user="default", mode=mode)
            db.session.add(session)
        else:
            session.mode = mode
        db.session.commit()

        logger.info(f"[CHAT-SESSION] {session_id[:12]} mode → {mode}")
        return jsonify({"success": True, "session_id": session_id, "mode": mode})
    except Exception as e:
        logger.error(f"set_session_mode failed for {session_id}: {e}")
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e)}), 500


@chat_sessions_bp.route("/export", methods=["POST"])
def export_chat_sessions():
    """Write every stored chat session to a timestamped folder under the outputs directory.

    Returns the folder (absolute and relative to the outputs directory) plus
    the session and message counts written.
    """
    try:
        from backend.services.chat_export_service import export_chats

        result = export_chats()
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"export_chat_sessions failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
