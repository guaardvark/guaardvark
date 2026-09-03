"""Export every stored chat session to a structured folder under the outputs directory.

One call writes ``<OUTPUT_DIR>/chat-exports/chats-<timestamp>/`` containing:

* ``index.json`` — export metadata plus a one-line summary of every session.
* ``sessions/<session_id>.json`` — the session, its messages in order, and any
  rolling summaries.
* ``sessions/<session_id>.md`` — the same transcript as readable Markdown.

Sessions come from ``llm_sessions``; Roofing Brain answers are not persisted
there and are therefore not part of this export.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import config
from backend.models import LLMMessage, LLMSession, LLMSessionSummary, db

EXPORT_SUBDIR = "chat-exports"
_TITLE_MAX = 80
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _title_for(messages: List[LLMMessage]) -> str:
    """First line of the first user message, else the first message of any role."""
    for candidate in (m for m in messages if m.role == "user"):
        text = (candidate.content or "").strip()
        if text:
            break
    else:
        text = next(((m.content or "").strip() for m in messages if (m.content or "").strip()), "")
    first_line = text.splitlines()[0] if text else ""
    if len(first_line) > _TITLE_MAX:
        first_line = first_line[: _TITLE_MAX - 1].rstrip() + "…"
    return first_line or "(empty session)"


def _session_payload(session: LLMSession) -> Dict[str, Any]:
    messages = (
        LLMMessage.query.filter_by(session_id=session.id)
        .order_by(LLMMessage.timestamp.asc(), LLMMessage.id.asc())
        .all()
    )
    summaries = (
        LLMSessionSummary.query.filter_by(session_id=session.id)
        .order_by(LLMSessionSummary.id.asc())
        .all()
    )
    return {
        "id": session.id,
        "title": _title_for(messages),
        "user": session.user,
        "project_id": session.project_id,
        "mode": session.mode,
        "created_at": _iso(session.created_at),
        "message_count": len(messages),
        "first_message_at": _iso(messages[0].timestamp) if messages else None,
        "last_message_at": _iso(messages[-1].timestamp) if messages else None,
        "messages": [m.to_dict() for m in messages],
        "summaries": [s.to_dict() for s in summaries],
    }


def _markdown_for(payload: Dict[str, Any]) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"- Session: `{payload['id']}`",
        f"- User: {payload['user']}",
        f"- Mode: {payload['mode']}",
        f"- Project: {payload['project_id'] if payload['project_id'] is not None else '—'}",
        f"- Created: {payload['created_at'] or '—'}",
        f"- Messages: {payload['message_count']}",
        "",
    ]
    for message in payload["messages"]:
        stamp = message.get("timestamp") or ""
        lines.append(f"## {message['role']}  {stamp}".rstrip())
        lines.append("")
        lines.append(message.get("content") or "")
        lines.append("")
    if payload["summaries"]:
        lines.append("## Rolling summaries")
        lines.append("")
        for summary in payload["summaries"]:
            lines.append(
                f"- ({summary.get('created_at') or '—'}, {summary.get('message_count') or 0} messages) "
                f"{summary.get('summary') or ''}"
            )
        lines.append("")
    return "\n".join(lines)


def export_chats(output_dir: Optional[str] = None) -> Dict[str, Any]:
    """Write every chat session to a new timestamped folder and return its summary.

    Returns ``{"directory", "relative_directory", "sessions", "messages", "exported_at"}``.
    """
    base = output_dir or config.OUTPUT_DIR
    exported_at = datetime.now()
    folder_name = f"chats-{exported_at.strftime('%Y%m%d-%H%M%S')}"
    target = os.path.join(base, EXPORT_SUBDIR, folder_name)
    sessions_dir = os.path.join(target, "sessions")
    os.makedirs(sessions_dir, exist_ok=True)

    index_entries: List[Dict[str, Any]] = []
    total_messages = 0
    for session in LLMSession.query.order_by(LLMSession.created_at.asc(), LLMSession.id.asc()).all():
        payload = _session_payload(session)
        total_messages += payload["message_count"]
        stem = _SAFE_ID.sub("_", session.id) or "session"
        with open(os.path.join(sessions_dir, f"{stem}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        with open(os.path.join(sessions_dir, f"{stem}.md"), "w", encoding="utf-8") as fh:
            fh.write(_markdown_for(payload))
        index_entries.append(
            {
                "id": session.id,
                "title": payload["title"],
                "user": payload["user"],
                "project_id": payload["project_id"],
                "mode": payload["mode"],
                "created_at": payload["created_at"],
                "message_count": payload["message_count"],
                "last_message_at": payload["last_message_at"],
                "files": {"json": f"sessions/{stem}.json", "markdown": f"sessions/{stem}.md"},
            }
        )

    index = {
        "exported_at": exported_at.isoformat(),
        "session_count": len(index_entries),
        "message_count": total_messages,
        "sessions": index_entries,
    }
    with open(os.path.join(target, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    return {
        "directory": target,
        "relative_directory": os.path.join(EXPORT_SUBDIR, folder_name),
        "sessions": len(index_entries),
        "messages": total_messages,
        "exported_at": index["exported_at"],
    }
