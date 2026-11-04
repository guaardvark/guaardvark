"""A video queued from chat plays in the reply that queued it.

generate_video returns as soon as its batch is queued, because a render can
outlast any chat turn. The reply is saved with the batch under
``pendingMedia``. ``follow`` waits for the batch on a daemon thread, then moves
the clip into the reply's ``generatedImages`` and tells the open chat with
``chat:media_ready``. ``resolve_history`` settles any batch that finished while
nothing was waiting (a restart, a closed tab) when the chat's history is read.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

POLL_S = 3.0
# A batch still running after this long is left to the history read.
MAX_WAIT_S = 3 * 60 * 60

_DONE = ("completed", "error", "cancelled")
_GONE = {"error": "The video batch is no longer there (it was deleted or never started)."}
# Polls a batch may be missing before the watcher calls it gone.
_MISSING_POLLS = 20
_following: set = set()
_following_lock = threading.Lock()


def pending_entry(tool_name: str, metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The ``pendingMedia`` entry for a generate_video result that queued a
    batch instead of waiting for it, else None."""
    meta = metadata or {}
    if tool_name != "generate_video" or not meta.get("queued") or not meta.get("batch_id"):
        return None
    return {
        "batch_id": meta["batch_id"],
        "type": "video",
        "caption": str(meta.get("prompt") or "")[:200],
        "studio_url": meta.get("studio_url") or f"/video?batch={meta['batch_id']}",
    }


def _settle(status) -> Optional[Dict[str, Any]]:
    """What a finished batch puts in the reply: ``{"media": [...]}`` or
    ``{"error": text}``. None while it is still running."""
    if status is None or getattr(status, "status", None) not in _DONE:
        return None
    batch_id = status.batch_id
    media: List[Dict[str, Any]] = []
    for result in status.results or []:
        if not (result.success and result.video_path):
            continue
        item = {
            # video_path is batch-relative; the serving route accepts it verbatim.
            "url": f"/api/batch-video/video/{batch_id}/{result.video_path}",
            "type": "video",
            "alt": "Generated video",
            "batch_id": batch_id,
        }
        if result.thumbnail_path:
            item["poster"] = f"/api/batch-video/video/{batch_id}/{result.thumbnail_path}"
        media.append(item)
    if media:
        return {"media": media}
    from backend.services.job_types import RenderErrorKind, batch_failure, describe_failure, record_text
    failure = batch_failure(status) or describe_failure(
        RenderErrorKind.OUTPUT_MISSING, "The batch finished without a video.")
    return {"error": record_text(failure)}


def _apply(extra: Dict[str, Any], batch_id: str, settled: Dict[str, Any]) -> Dict[str, Any]:
    """``extra`` with ``batch_id`` moved out of pendingMedia: its clips into
    generatedImages, or its failure kept on the entry. Pure."""
    extra = dict(extra or {})
    pending = []
    for entry in extra.get("pendingMedia") or []:
        if entry.get("batch_id") != batch_id:
            pending.append(entry)
        elif settled.get("error"):
            pending.append({**entry, "status": "failed", "error": settled["error"]})
    media = list(extra.get("generatedImages") or [])
    have = {m.get("url") for m in media}
    media += [m for m in settled.get("media") or [] if m["url"] not in have]
    if media:
        extra["generatedImages"] = media
    if pending:
        extra["pendingMedia"] = pending
    else:
        extra.pop("pendingMedia", None)
    return extra


def _save(message_id: int, batch_id: str, settled: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Write the settled batch into the saved reply; returns its new extra_data."""
    from backend.models import LLMMessage, db
    message = db.session.get(LLMMessage, message_id)
    if message is None:
        return None
    pending = (message.extra_data or {}).get("pendingMedia") or []
    if not any(e.get("batch_id") == batch_id and e.get("status") != "failed" for e in pending):
        return None
    message.extra_data = _apply(message.extra_data, batch_id, settled)
    db.session.commit()
    return message.extra_data


def _announce(session_id: str, message_id: int, batch_id: str, settled: Dict[str, Any]) -> None:
    try:
        from backend.socketio_instance import socketio
        socketio.emit("chat:media_ready", {
            "session_id": session_id,
            "message_id": message_id,
            "batch_id": batch_id,
            "media": settled.get("media") or [],
            "error": settled.get("error"),
        }, room=session_id)
    except Exception:  # noqa: BLE001 - the saved reply already has it
        logger.debug("chat:media_ready not emitted for %s", batch_id, exc_info=True)


def follow(app, session_id: str, message_id: Optional[int], batch_id: str) -> None:
    """Wait for ``batch_id`` in the background and put its clip in reply
    ``message_id``."""
    if not message_id or not batch_id:
        return
    if app is None:
        from flask import current_app
        app = current_app._get_current_object()
    with _following_lock:
        if batch_id in _following:
            return
        _following.add(batch_id)

    def _run():
        try:
            from backend.services.batch_video_generator import get_batch_video_generator
            generator = get_batch_video_generator()
            deadline = time.monotonic() + MAX_WAIT_S
            settled = None
            missing = 0
            while time.monotonic() < deadline:
                status = generator.get_batch_status(batch_id)
                missing = missing + 1 if status is None else 0
                settled = _GONE if missing >= _MISSING_POLLS else _settle(status)
                if settled:
                    break
                time.sleep(POLL_S)
            if not settled:
                return
            with app.app_context():
                if _save(message_id, batch_id, settled) is not None:
                    _announce(session_id, message_id, batch_id, settled)
        except Exception:  # noqa: BLE001
            logger.warning("chat video follow-up failed for %s", batch_id, exc_info=True)
        finally:
            with _following_lock:
                _following.discard(batch_id)

    threading.Thread(target=_run, name=f"chat-media-{batch_id}", daemon=True).start()


def resolve_history(messages) -> None:
    """Settle the pendingMedia of LLMMessage rows whose batch has finished,
    in place (called with an app context, before the rows are formatted)."""
    rows = [m for m in messages if isinstance(m.extra_data, dict) and m.extra_data.get("pendingMedia")]
    if not rows:
        return
    try:
        from backend.models import db
        from backend.services.batch_video_generator import get_batch_video_generator
        generator = get_batch_video_generator()
        changed = False
        for row in rows:
            extra = row.extra_data
            for entry in list(extra.get("pendingMedia") or []):
                if entry.get("status") == "failed" or not entry.get("batch_id"):
                    continue
                status = generator.get_batch_status(entry["batch_id"])
                settled = _GONE if status is None else _settle(status)
                if settled:
                    extra = _apply(extra, entry["batch_id"], settled)
                    changed = True
            row.extra_data = extra
        if changed:
            db.session.commit()
    except Exception:  # noqa: BLE001 - history still loads with the placeholder
        logger.warning("pending chat media not resolved", exc_info=True)
