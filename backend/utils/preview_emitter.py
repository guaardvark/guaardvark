# backend/utils/preview_emitter.py
"""Live latent-preview frames. Separate from unified progress on purpose.

Sampler JPEGs must not enter UPS additional_data: that dict is rewritten to
`.progress_jobs/<id>/metadata.json` and republished on `guaardvark:progress`
every step. This module publishes `guaardvark:preview` / Socket.IO
`job_preview` only. Failures are swallowed — a preview must never fail a
generation.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
logger = logging.getLogger(__name__)

REDIS_CHANNEL = "guaardvark:preview"
SOCKET_EVENT = "job_preview"
MAX_PREVIEW_BYTES = 400 * 1024


def emit_preview_event(
    process_id: str,
    mime: str,
    image_bytes: bytes,
) -> bool:
    """Publish one preview frame. Returns True if Redis or Socket.IO accepted it."""
    if not process_id or not image_bytes:
        return False
    if len(image_bytes) > MAX_PREVIEW_BYTES:
        logger.debug(
            "Dropping preview for %s: %d bytes exceeds %d",
            process_id, len(image_bytes), MAX_PREVIEW_BYTES,
        )
        return False

    payload = {
        "job_id": process_id,
        "mime": mime or "image/jpeg",
        "b64": base64.b64encode(image_bytes).decode("ascii"),
        "ts": time.time(),
    }
    sent = False
    sent = _publish_redis(payload) or sent
    sent = _emit_socketio(process_id, payload) or sent
    return sent


def _publish_redis(payload: dict) -> bool:
    try:
        import redis as _redis
        r = _redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        r.publish(REDIS_CHANNEL, json.dumps(payload))
        return True
    except Exception:
        return False


def _emit_socketio(process_id: str, payload: dict) -> bool:
    socketio = _get_socketio()
    if not socketio:
        return False
    try:
        socketio.emit(SOCKET_EVENT, payload, to=process_id, namespace="/")
        socketio.emit(SOCKET_EVENT, payload, to="global_progress", namespace="/")
        return True
    except Exception as e:
        logger.debug("job_preview Socket.IO emit failed: %s", e)
        return False


def _get_socketio():
    try:
        from flask import current_app
        if hasattr(current_app, "extensions") and "socketio" in current_app.extensions:
            return current_app.extensions["socketio"]
    except Exception:
        pass
    try:
        from backend.socketio_instance import socketio
        return socketio
    except Exception:
        return None
