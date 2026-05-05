#!/usr/bin/env python3
"""Chat sessions API — minimal endpoints for the modal-session feature.

Today this is just the agent-mode toggle. If the chat-session model grows
more knobs (per-session model preference, RAG scope, etc.) they belong
here too. Auto-discovered by blueprint_discovery.py.
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
        return jsonify({"success": True, "session_id": session_id, "mode": mode})
    except Exception as e:
        logger.error(f"get_session_mode failed for {session_id}: {e}")
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
