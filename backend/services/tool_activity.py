"""Broadcast a slim copy of chat tool events for observers outside the chat.

Chat events go to the Socket.IO room of the session that asked, which is
right for the chat window and wrong for the System Map: it wants to see
every tool call on the machine, from any session, and never joins a room.
It listened for ``chat:tool_call`` and heard nothing (observed 2026-09-05).

``map:tool_activity`` carries only what an observer needs to pulse a module:
the tool name, the session id and whether the call succeeded. Parameters,
outputs and paths stay in the room.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

EVENT = "map:tool_activity"

_KIND = {"chat:tool_call": "call", "chat:tool_result": "result"}


def broadcast_tool_activity(event: str, payload: dict[str, Any] | None) -> None:
    """Mirror a room-scoped tool event as a machine-wide activity event."""
    kind = _KIND.get(event)
    if not kind or not isinstance(payload, dict):
        return
    tool = payload.get("tool") or payload.get("tool_name") or payload.get("name")
    if not tool:
        return
    try:
        from backend.socketio_instance import socketio
        if socketio.server is None:
            return
        result = payload.get("result") if kind == "result" else None
        socketio.emit(EVENT, {
            "kind": kind,
            "tool": str(tool),
            "session_id": payload.get("session_id"),
            "success": (result or {}).get("success") if isinstance(result, dict) else None,
            "iteration": payload.get("iteration"),
        })
    except Exception as exc:  # an observer must never break the chat
        logger.debug("tool activity broadcast skipped: %s", exc)
