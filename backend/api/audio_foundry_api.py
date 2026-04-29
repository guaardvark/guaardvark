"""
Audio Foundry API — proxy endpoints for the audio_foundry plugin service.

Modeled on upscaling_api.py: thin Flask blueprint that forwards JSON requests
to the FastAPI service running on port 8206 and returns its responses verbatim.

No auth token — audio_foundry runs locally and unauthenticated, same as
vision_pipeline. If that ever changes, mirror upscaling_api.py's
_get_auth_token + _auth_headers pattern here.

Generation endpoints get a long timeout (10 min) because ACE-Step can take
1-4 minutes to render a song and Chatterbox can take ~30s for long text.
The frontend (AudioFoundryPage.jsx) expects a synchronous response with the
output file path, so we wait. Future async/Celery routing is in config.yaml's
`async_threshold_s` but isn't wired through this proxy yet.
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import requests
from flask import Blueprint, request as flask_request, current_app, send_file
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

# Where uploaded reference clips live. Lives under data/uploads/ so it gets
# the standard backup/portability treatment, but in its own subdirectory so
# voice references don't get mixed into the user's general document tree.
_VOICE_REF_SUBDIR = "voice_references"
_ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
_MAX_REF_BYTES = 25 * 1024 * 1024  # 25 MB — plenty for a 10s clip even uncompressed


def _voice_ref_dir() -> Path:
    """Resolve the absolute voice_references directory; create it if missing."""
    upload_root = Path(current_app.config.get("UPLOAD_FOLDER", "data/uploads"))
    if not upload_root.is_absolute():
        upload_root = Path.cwd() / upload_root
    target = upload_root / _VOICE_REF_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _is_safe_ref_path(p: Path) -> bool:
    """Reject anything that would escape the voice_references directory."""
    try:
        p.resolve().relative_to(_voice_ref_dir().resolve())
        return True
    except (ValueError, OSError):
        return False

audio_foundry_bp = Blueprint("audio_foundry", __name__, url_prefix="/api/audio-foundry")

AUDIO_FOUNDRY_URL = "http://127.0.0.1:8206"
QUICK_TIMEOUT = 10        # /health, /status, /config — return fast or fail fast
GENERATION_TIMEOUT = 600  # /generate/* — songs up to 4 minutes plus model load


def _proxy_get(path: str, timeout: int = QUICK_TIMEOUT):
    try:
        resp = requests.get(f"{AUDIO_FOUNDRY_URL}{path}", timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Audio Foundry service not running"}, 503
    except Exception as e:
        logger.exception("Audio Foundry GET %s failed", path)
        return {"error": str(e)}, 500


def _proxy_post(path: str, json_data: dict, timeout: int):
    try:
        resp = requests.post(f"{AUDIO_FOUNDRY_URL}{path}", json=json_data, timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Audio Foundry service not running"}, 503
    except requests.Timeout:
        return {"error": f"Audio Foundry request timed out after {timeout}s"}, 504
    except Exception as e:
        logger.exception("Audio Foundry POST %s failed", path)
        return {"error": str(e)}, 500


@audio_foundry_bp.route("/health", methods=["GET"])
def health():
    body, status = _proxy_get("/health")
    return body, status


@audio_foundry_bp.route("/status", methods=["GET"])
def status():
    body, status_code = _proxy_get("/status")
    return body, status_code


@audio_foundry_bp.route("/config", methods=["GET"])
def config():
    body, status_code = _proxy_get("/config")
    return body, status_code


@audio_foundry_bp.route("/voices", methods=["GET"])
def voices():
    body, status_code = _proxy_get("/voices")
    return body, status_code


@audio_foundry_bp.route("/generate/voice", methods=["POST"])
def generate_voice():
    body, status_code = _proxy_post(
        "/generate/voice",
        flask_request.get_json(silent=True) or {},
        GENERATION_TIMEOUT,
    )
    return body, status_code


@audio_foundry_bp.route("/generate/music", methods=["POST"])
def generate_music():
    body, status_code = _proxy_post(
        "/generate/music",
        flask_request.get_json(silent=True) or {},
        GENERATION_TIMEOUT,
    )
    return body, status_code


@audio_foundry_bp.route("/rewrite-music-prompt", methods=["POST"])
def rewrite_music_prompt():
    """Translate natural-language music intent to ACE-Step-friendly tag prompts.

    Runs on the main backend (where Ollama lives) BEFORE the frontend hits
    /generate/music. Order matters: this call must complete while Ollama is
    still in VRAM. The subsequent /generate/music call requests VRAM via the
    orchestrator and will evict Ollama to make room for ACE-Step.

    Body: {"text": str, "instrumental": bool}
    Returns:
        200 {"style_prompt": str, "negative_prompt": str, "tags_used": [str]}
        200 {"fallback": true, "reason": str, "style_prompt": text} if rewrite failed
        400 if text is empty
    """
    data = flask_request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    instrumental = bool(data.get("instrumental", True))

    if not text:
        return {"error": "text is required"}, 400

    # Local import — keeps cold-start light and lets the rest of the file load
    # even if backend.utils.music_prompt_rewriter has an issue at import time.
    from backend.utils.music_prompt_rewriter import rewrite_music_prompt as _rewrite

    try:
        result = _rewrite(text, instrumental=instrumental)
    except Exception as e:
        logger.exception("rewrite-music-prompt unexpected failure")
        return {
            "fallback": True,
            "reason": f"rewriter exception: {e}",
            "style_prompt": text,
            "negative_prompt": "",
            "tags_used": [],
        }, 200

    if result is None:
        # Rewriter declined (Ollama down, bad JSON, empty output) — return
        # a fallback shape so the frontend can still proceed with the raw
        # prompt without special-casing the error path.
        return {
            "fallback": True,
            "reason": "rewriter unavailable or refused; using raw prompt",
            "style_prompt": text,
            "negative_prompt": "",
            "tags_used": [],
        }, 200

    return {
        "fallback": False,
        "style_prompt": result["style_prompt"],
        "negative_prompt": result["negative_prompt"],
        "tags_used": result["tags_used"],
    }, 200


@audio_foundry_bp.route("/generate/fx", methods=["POST"])
def generate_fx():
    body, status_code = _proxy_post(
        "/generate/fx",
        flask_request.get_json(silent=True) or {},
        GENERATION_TIMEOUT,
    )
    return body, status_code


# ---------- Voice reference clips (Chatterbox cloning) ----------------------
#
# Chatterbox does zero-shot voice cloning from a 5-10s reference audio clip.
# These endpoints let the frontend upload a reference clip, list existing
# ones, preview them, and delete. The audio_foundry FastAPI service expects
# `reference_clip_path` to be an absolute filesystem path it can read, so we
# return the resolved absolute path on upload.


@audio_foundry_bp.route("/voice-clips", methods=["GET"])
def list_voice_clips():
    """List uploaded reference clips. Frontend uses this to populate a picker
    so users can re-use clips across sessions without re-uploading."""
    try:
        d = _voice_ref_dir()
        clips = []
        for f in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in _ALLOWED_AUDIO_EXTS:
                clips.append({
                    "id": f.stem,
                    "filename": f.name,
                    "path": str(f.resolve()),
                    "size_bytes": f.stat().st_size,
                    "modified_ts": f.stat().st_mtime,
                })
        return {"clips": clips}, 200
    except Exception as e:
        logger.exception("voice clip list failed")
        return {"error": str(e)}, 500


@audio_foundry_bp.route("/voice-clips/upload", methods=["POST"])
def upload_voice_clip():
    """Receive a multipart audio upload, save under data/uploads/voice_references/,
    and return the resolved absolute path that callers can pass as
    `reference_clip_path` to /generate/voice."""
    if "file" not in flask_request.files:
        return {"error": "No file part in request (expected multipart field 'file')"}, 400

    f = flask_request.files["file"]
    if not f or not f.filename:
        return {"error": "Empty filename"}, 400

    # Use the user's preferred display name when present; otherwise fall back
    # to the raw filename. We never trust either for filesystem placement —
    # final on-disk name uses a UUID + the validated extension.
    display_name = flask_request.form.get("name") or f.filename
    raw_ext = Path(secure_filename(f.filename) or "").suffix.lower()
    if raw_ext not in _ALLOWED_AUDIO_EXTS:
        return {
            "error": f"Unsupported audio format {raw_ext!r}. Allowed: {sorted(_ALLOWED_AUDIO_EXTS)}",
        }, 400

    asset_id = uuid.uuid4().hex
    target = _voice_ref_dir() / f"{asset_id}{raw_ext}"

    # Stream-write with a size cap so a malicious / runaway upload can't fill disk.
    written = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = f.stream.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_REF_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    return {"error": f"File exceeds {_MAX_REF_BYTES // (1024*1024)} MB cap"}, 413
                out.write(chunk)
    except Exception as e:
        target.unlink(missing_ok=True)
        logger.exception("voice clip upload failed")
        return {"error": str(e)}, 500

    return {
        "id": asset_id,
        "filename": target.name,
        "display_name": display_name,
        "path": str(target.resolve()),
        "size_bytes": written,
    }, 201


@audio_foundry_bp.route("/voice-clips/<clip_id>/download", methods=["GET"])
def download_voice_clip(clip_id):
    """Stream a reference clip back to the browser so the UI can preview it
    in an <audio> tag before generation."""
    safe_id = secure_filename(clip_id)
    if not safe_id or safe_id != clip_id:
        return {"error": "Invalid clip id"}, 400
    d = _voice_ref_dir()
    matches = list(d.glob(f"{safe_id}.*"))
    if not matches:
        return {"error": "Clip not found"}, 404
    f = matches[0]
    if not _is_safe_ref_path(f):
        return {"error": "Path traversal blocked"}, 403
    return send_file(f, as_attachment=False, download_name=f.name)


@audio_foundry_bp.route("/voice-clips/<clip_id>", methods=["DELETE"])
def delete_voice_clip(clip_id):
    """Remove a reference clip from disk."""
    safe_id = secure_filename(clip_id)
    if not safe_id or safe_id != clip_id:
        return {"error": "Invalid clip id"}, 400
    d = _voice_ref_dir()
    matches = list(d.glob(f"{safe_id}.*"))
    if not matches:
        return {"error": "Clip not found"}, 404
    for f in matches:
        if _is_safe_ref_path(f):
            try:
                f.unlink()
            except OSError as e:
                logger.warning("Could not delete %s: %s", f, e)
    return {"deleted": clip_id}, 200
