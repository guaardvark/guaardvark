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
import re
import uuid
from pathlib import Path

import requests
from flask import Blueprint, request as flask_request, current_app, jsonify, send_file
from werkzeug.utils import secure_filename
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path

logger = logging.getLogger(__name__)

# Where uploaded reference clips live. Lives under data/uploads/ so it gets
# the standard backup/portability treatment, but in its own subdirectory so
# voice references don't get mixed into the user's general document tree.
_ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
_MAX_REF_BYTES = 25 * 1024 * 1024  # 25 MB — plenty for a 10s clip even uncompressed

# Where a consent record written here came from (stored in the record).
_CONSENT_SOURCE = "audio_studio"


def _consent():
    """Voice-cloning consent rules, shared with the plugin (backends/voice_consent.py)."""
    from backend.services.audio_foundry_models import voice_consent
    return voice_consent()


def _voice_ref_dir() -> Path:
    """Resolve the absolute voice_references directory; create it if missing."""
    upload_root = Path(current_app.config.get("UPLOAD_FOLDER", "data/uploads"))
    if not upload_root.is_absolute():
        upload_root = Path.cwd() / upload_root
    target = _consent().references_dir(upload_root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# The import names a clip after its file: a secure_filename stem, plus the
# Files-app " (n)" it adds when that name is taken (filename_resolver).
_COLLISION_SUFFIX = re.compile(r" \([1-9][0-9]{0,3}\)$")


def _valid_clip_ref(ref: str) -> bool:
    """Whether ``ref`` has the shape of a clip reference: a clip id (the file
    name without its extension) or the file name itself. Either way the stem
    is a secure_filename, optionally followed by the import's " (n)", so it
    can hold no path separator or parent reference."""
    if not ref or ref != ref.strip():
        return False
    stem = ref
    suffix = Path(ref).suffix
    if suffix.lower() in _ALLOWED_AUDIO_EXTS:
        stem = ref[: -len(suffix)]
    base = _COLLISION_SUFFIX.sub("", stem)
    return bool(base) and secure_filename(base) == base


def _clip_file(ref: str):
    """The audio file ``ref`` names inside voice_references, or None.

    ``ref`` is a file name (``me.wav``) or a clip id (``me``). An exact file
    name wins, so a client can tell ``me.wav`` from ``me.mp3``; an id matches
    a whole stem only, never a longer name that starts with it (``me.v2.wav``).
    Files are compared by name, so ``ref`` never becomes part of a path.
    """
    d = _voice_ref_dir()
    by_id = None
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in _ALLOWED_AUDIO_EXTS or not f.is_file() or not _is_safe_ref_path(f):
            continue
        if f.name == ref:
            return f
        if by_id is None and f.stem == ref:
            by_id = f
    return by_id


def _confirmed_json(action: str):
    """None when the request is JSON carrying ``{"confirmed": true}``, else the
    400 to answer. Clip mutations take only that, sent after the person
    confirmed in the Studio; a browser form cannot send JSON."""
    if not flask_request.is_json:
        return {"error": f'{action} only when the request is JSON: {{"confirmed": true}}.'}, 400
    body = flask_request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("confirmed") is not True:
        return {"error": f"{action} only when the person confirms it (confirmed: true)."}, 400
    return None


def _safe_ref_path(p) -> Path:
    """``p`` as a Path inside the voice_references directory, or raise PathEscapesRoot."""
    return contained(_voice_ref_dir(), p)


def _is_safe_ref_path(p: Path) -> bool:
    """Reject anything that would escape the voice_references directory."""
    try:
        _safe_ref_path(p)
        return True
    except PathEscapesRoot:
        return False

audio_foundry_bp = Blueprint("audio_foundry", __name__, url_prefix="/api/audio-foundry")

AUDIO_FOUNDRY_URL = "http://127.0.0.1:8206"
QUICK_TIMEOUT = 10        # /health, /status, /config — return fast or fail fast
GENERATION_TIMEOUT = 600  # /generate/* — songs up to 4 minutes plus model load


def _not_running(why: str = ""):
    """The service itself is down. ``plugin_running: false`` tells callers
    this apart from a 503 the running service sends (a model that cannot run
    on this machine), which comes back verbatim with FastAPI's ``detail``."""
    from backend.services.plugin_bridge import service_down_message
    message = "Audio Foundry service not running" + (f" ({why})" if why else "")
    return jsonify({
        "error": message,
        # What to do about it, the same sentence every tool gives for a down service.
        "message": service_down_message("audio_foundry", context="audio", why=why),
        "error_kind": "service_down",
        "plugin_running": False,
    }), 503


def _proxy_get(path: str, timeout: int = QUICK_TIMEOUT):
    try:
        resp = requests.get(f"{AUDIO_FOUNDRY_URL}{path}", timeout=timeout)
        return jsonify(resp.json()), resp.status_code
    except requests.ConnectionError:
        return _not_running()
    except Exception as e:
        logger.exception("Audio Foundry GET %s failed", path)
        return jsonify({"error": str(e)}), 500


def _proxy_post(path: str, json_data: dict, timeout: int):
    try:
        resp = requests.post(f"{AUDIO_FOUNDRY_URL}{path}", json=json_data, timeout=timeout)
        try:
            body = resp.json()
        except ValueError:
            explanation = " ".join(resp.text.split())[:500] or "empty response"
            return jsonify({"detail": f"Audio Foundry HTTP {resp.status_code}: {explanation}"}), (
                resp.status_code if resp.status_code >= 400 else 502
            )
        return jsonify(body), resp.status_code
    except requests.ConnectionError:
        return _not_running()
    except requests.Timeout:
        return jsonify({"error": f"Audio Foundry request timed out after {timeout}s"}), 504
    except Exception as e:
        logger.exception("Audio Foundry POST %s failed", path)
        return jsonify({"error": str(e)}), 500


def _audio_foundry_up() -> bool:
    try:
        return requests.get(f"{AUDIO_FOUNDRY_URL}/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def _proxy_generate(path: str, json_data: dict):
    """POST a generation, starting Audio Foundry first when it is down.

    The start happens only with GUAARDVARK_JOB_SERVICE_START on; the status and
    health routes the Studio polls never start it.
    """
    from backend.services.plugin_bridge import job_service_start_enabled, start_for_job
    if job_service_start_enabled():
        ok, why = start_for_job("audio", "generating", is_up=_audio_foundry_up)
        if not ok:
            return _not_running(why)
    return _proxy_post(path, json_data, GENERATION_TIMEOUT)


def _proxy_delete(path: str, timeout: int = QUICK_TIMEOUT):
    try:
        resp = requests.delete(f"{AUDIO_FOUNDRY_URL}{path}", timeout=timeout)
        return jsonify(resp.json()), resp.status_code
    except requests.ConnectionError:
        return _not_running()
    except Exception as e:
        logger.exception("Audio Foundry DELETE %s failed", path)
        return jsonify({"error": str(e)}), 500


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
    """The voice catalog, from the plugin while it runs.

    When it cannot be reached the Kokoro part is read here, from the same
    catalog file and the same local Hugging Face cache check the plugin uses,
    with ``plugin_running: false``: the Audio Studio and the Cast page's voice
    pickers list the voices, and which are installed, with the plugin stopped.
    """
    try:
        resp = requests.get(f"{AUDIO_FOUNDRY_URL}/voices", timeout=QUICK_TIMEOUT)
        return jsonify(resp.json()), resp.status_code
    except (requests.ConnectionError, requests.Timeout):
        pass
    except Exception as e:
        logger.exception("Audio Foundry GET /voices failed")
        return jsonify({"error": str(e)}), 500
    try:
        from backend.services.audio_foundry_models import kokoro_catalog, kokoro_voice_groups
        kokoro = {"default": kokoro_catalog()["default"], "groups": kokoro_voice_groups()}
    except Exception as e:  # noqa: BLE001 - the catalog ships in the checkout
        logger.warning("Kokoro voice catalog unreadable: %s", e)
        return _not_running("voice catalog unreadable")
    return jsonify({"kokoro": kokoro, "plugin_running": False}), 200


# ---------- Model catalog / install (plugin-offline safe) -------------------
#
# Weights live in the shared Hugging Face cache. Listing and download run in
# this process so a stopped sidecar does not hide the Install button.


@audio_foundry_bp.route("/models", methods=["GET"])
def list_models():
    from backend.services.audio_foundry_models import list_models as _list
    payload = _list()
    return jsonify({"success": True, **payload}), 200


@audio_foundry_bp.route("/models/download", methods=["POST"])
def download_model():
    from backend.services.audio_foundry_models import start_download
    body = flask_request.get_json(silent=True) or {}
    model_id = str(body.get("id") or body.get("model_id") or "").strip()
    if not model_id:
        return jsonify({"success": False, "error": "id is required"}), 400
    payload, status = start_download(model_id)
    return jsonify(payload), status


@audio_foundry_bp.route("/models/download-status", methods=["GET"])
def download_status():
    from backend.services.audio_foundry_models import download_status as _status
    return jsonify(_status()), 200


@audio_foundry_bp.route("/generate/voice", methods=["POST"])
def generate_voice():
    data = flask_request.get_json(silent=True) or {}
    ref = data.get("reference_clip_path")
    if ref:
        # A clone needs a clip in voice_references with an explicit consent
        # record. The plugin checks again where it clones.
        consent = _consent()
        try:
            clip = consent.require_consent(ref, _voice_ref_dir())
        except consent.ConsentRequired as e:
            return jsonify({"error": str(e), "needs_consent": True}), 403
        # Forward the real path that was checked. A relative value would
        # otherwise be read against the sidecar's working directory.
        data = {**data, "reference_clip_path": str(clip)}
    body, status_code = _proxy_generate("/generate/voice", data)
    return body, status_code


@audio_foundry_bp.route("/plan-music", methods=["POST"])
def plan_music():
    """ACE-Step 1.5: turn a description into a caption, lyrics and metadata.

    No audio. The planner is the 1.7B model already loaded with ACE-Step 1.5,
    so this does not call Ollama. ACE-Step v1 still uses /rewrite-music-prompt.
    """
    payload = flask_request.get_json(silent=True) or {}
    text = (payload.get("style_prompt") or payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "style_prompt is required"}), 400
    from backend.services.audio_foundry_models import missing_parts
    missing = missing_parts("ace-step-1.5")
    if missing:
        return jsonify({"success": False, "needs_install": "ace-step-1.5", "error": (
            f"ACE-Step 1.5 is not installed (missing {missing[0]}). Open Audio Studio → "
            "Manage models and Install it; arranging never downloads on its own.")}), 400
    body, status_code = _proxy_post(
        "/plan/music",
        {**payload, "style_prompt": text, "model": "ace-step-1.5"},
        GENERATION_TIMEOUT,
    )
    return body, status_code


@audio_foundry_bp.route("/generate/music", methods=["POST"])
def generate_music():
    """Music generation. ``model`` picks the backend: the sidecar's ACE-Step v1
    (default, or "ace-step"), the sidecar's optional ACE-Step 1.5
    ("ace-step-1.5"), or MiniMax Music 3 through ComfyUI, which returns a job id
    to poll at /generate/music/status/<id> like the sidecar's own jobs. Any other
    value is refused rather than answered by a different model."""
    payload = flask_request.get_json(silent=True) or {}
    model = str(payload.get("model") or "").strip()
    if model and model != "ace-step" and not model.startswith("minimax-music3"):
        from backend.services.audio_foundry_models import missing_parts
        if model != "ace-step-1.5":
            return jsonify({"success": False, "error": (
                f"Unknown music model {model!r}: use 'ace-step' (default), 'ace-step-1.5' "
                "or a MiniMax Music 3 id")}), 400
        missing = missing_parts(model)
        if missing:
            return jsonify({"success": False, "needs_install": model, "error": (
                f"ACE-Step 1.5 is not installed (missing {missing[0]}). Open Audio Studio → "
                "Manage models and Install it; generation never downloads on its own.")}), 400
    if model.startswith("minimax-music3"):
        from backend.services import comfyui_music_generator as m3
        from backend.services.plugin_bridge import job_service_start_enabled
        from backend.services.video_model_registry import prepare_video_model, preflight_video_model
        from backend.services.video_model_registry import comfyui_down_failure
        check = prepare_video_model if job_service_start_enabled() else preflight_video_model
        ready, err = check(model)
        if not ready:
            err = comfyui_down_failure(err, "audio")
            kind = getattr(getattr(err, "kind", None), "value", None)
            return jsonify({"success": False, "error": err,
                            **({"error_kind": kind} if kind else {})}), 400
        try:
            seconds = float(payload.get("duration_s") or payload.get("seconds") or 60)
        except (TypeError, ValueError):
            seconds = 60.0
        job_id = m3.start_job(
            app=current_app._get_current_object(),
            caption=payload.get("style_prompt") or payload.get("caption") or "",
            lyrics="" if payload.get("instrumental_only") else (payload.get("lyrics") or ""),
            seconds=seconds, seed=payload.get("seed"), steps=payload.get("steps"), model_id=model,
        )
        return jsonify({"success": True, "job_id": job_id, "model": model, "status": "queued",
                        "attribution": "MiniMax-Music3"}), 202
    body, status_code = _proxy_generate("/generate/music", payload)
    return body, status_code


@audio_foundry_bp.route("/generate/music/status/<job_id>", methods=["GET"])
def music_job_status(job_id):
    from flask import jsonify
    from backend.services import comfyui_music_generator as m3
    job = m3.job_status(job_id)
    if job is None:
        return jsonify({"success": False, "error": "unknown job"}), 404
    return jsonify({"success": True, **job}), 200


@audio_foundry_bp.route("/rewrite-music-prompt", methods=["POST"])
def rewrite_music_prompt():
    """Translate natural-language music intent to ACE-Step-friendly tag prompts.

    Runs on the main backend (where Ollama lives) BEFORE the frontend hits
    /generate/music. Order matters: this call must complete while Ollama is
    still in VRAM. The subsequent /generate/music call requests VRAM via the
    orchestrator and will evict Ollama to make room for ACE-Step.

    Body: {"text": str, "instrumental": bool}
    Returns:
        200 {"style_prompt": str, "negative_prompt": "", "tags_used": [str]}
            (negative_prompt stays empty: ACE-Step v1 has no negative conditioning)
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
    body, status_code = _proxy_generate("/generate/fx", flask_request.get_json(silent=True) or {})
    return body, status_code


# ---------- Async job lifecycle ---------------------------------------------
#
# Large transcripts return 202 + job_id from /generate/* (when the client sends
# async:true). These routes proxy the plugin's job status/list/cancel so the
# frontend can poll. Status/cancel are quick — QUICK_TIMEOUT, not the 600s
# generation timeout, so a slow status call fails fast instead of hanging.


@audio_foundry_bp.route("/jobs/<job_id>", methods=["GET"])
def job_status(job_id):
    """Job status. A MiniMax Music 3 job (run here through ComfyUI) answers in
    the sidecar's shape so the page polls both the same way."""
    from backend.services import comfyui_music_generator as m3
    job = m3.job_status(job_id)
    if job is not None:
        from flask import jsonify
        status = {"failed": "error"}.get(job["status"], job["status"])
        result = None
        if status == "done":
            result = {
                "path": job.get("path"), "document_id": job.get("document_id"),
                "duration_s": job.get("seconds"), "model": job.get("model"),
                "attribution": job.get("attribution"), "seed": job.get("seed"),
            }
        return jsonify({"id": job_id, "status": status, "error": job.get("error"),
                        "error_kind": job.get("error_kind"),
                        "progress": {"current": 0, "total": 0}, "result": result}), 200
    body, status_code = _proxy_get(f"/jobs/{job_id}")
    return body, status_code


@audio_foundry_bp.route("/jobs", methods=["GET"])
def jobs_list():
    body, status_code = _proxy_get("/jobs")
    return body, status_code


@audio_foundry_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def job_cancel(job_id):
    body, status_code = _proxy_post(f"/jobs/{job_id}/cancel", {}, QUICK_TIMEOUT)
    return body, status_code


@audio_foundry_bp.route("/jobs", methods=["DELETE"])
def jobs_clear():
    """Clear finished jobs from the sidecar's history. Active jobs untouched."""
    body, status_code = _proxy_delete("/jobs")
    return body, status_code


# ---------- Voice reference clips (Chatterbox cloning) ----------------------
#
# Chatterbox does zero-shot voice cloning from a 5-10s reference audio clip.
# These endpoints let the frontend upload a reference clip, list existing
# ones, preview them, and delete. The audio_foundry FastAPI service expects
# `reference_clip_path` to be an absolute filesystem path it can read, so we
# return the resolved absolute path on upload.
#
# A clip is cloned only with a consent record (<clip>.consent), written when
# the person confirms the consent statement: at upload (consent_confirmed) or
# later through POST /voice-clips/<id>/consent. DELETE on that path withdraws
# it and keeps the clip; DELETE /voice-clips/<id> removes clip and record. The
# rules live in the plugin's backends/voice_consent.py and are enforced here
# and in the plugin.
#
# <id> in these routes is the clip's id (its file name without the extension)
# or its file name; see _clip_file.


@audio_foundry_bp.route("/voice-clips", methods=["GET"])
def list_voice_clips():
    """List uploaded reference clips. Frontend uses this to populate a picker
    so users can re-use clips across sessions without re-uploading."""
    try:
        d = _voice_ref_dir()
        consent = _consent()
        clips = []
        for f in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in _ALLOWED_AUDIO_EXTS:
                clips.append({
                    "id": f.stem,
                    "filename": f.name,
                    "path": str(f.resolve()),
                    "size_bytes": f.stat().st_size,
                    "modified_ts": f.stat().st_mtime,
                    # Only a clip with a consent record can be cloned.
                    "consented": consent.has_consent(f),
                })
        return {"clips": clips, "consent_statement": consent.STATEMENT}, 200
    except Exception as e:
        logger.exception("voice clip list failed")
        return jsonify({"error": str(e)}), 500


@audio_foundry_bp.route("/voice-clips/upload", methods=["POST"])
def upload_voice_clip():
    """Receive a multipart audio upload, save under data/uploads/voice_references/,
    and return the resolved absolute path that callers can pass as
    `reference_clip_path` to /generate/voice.

    The clip can be cloned only when the form also carries
    ``consent_confirmed=true``: the person confirmed the consent statement
    (``GET /voice-clips`` returns it as ``consent_statement``), and a consent
    record is written for it. Without that the clip is stored but cannot be
    cloned, which suits other uses such as the Video page's audio guide.
    """
    if "file" not in flask_request.files:
        return {"error": "No file part in request (expected multipart field 'file')"}, 400

    f = flask_request.files["file"]
    if not f or not f.filename:
        return {"error": "Empty filename"}, 400

    # Use the user's preferred display name when present; otherwise fall back
    # to the secured raw filename. The on-disk name is derived from this so
    # the user sees a recognizable filename in the picker — no more `<hex>.wav`
    # mystery files in voice_references/. Files-app suffix on collision.
    display_name = flask_request.form.get("name") or f.filename
    raw_ext = Path(secure_filename(f.filename) or "").suffix.lower()
    if raw_ext not in _ALLOWED_AUDIO_EXTS:
        return {
            "error": f"Unsupported audio format {raw_ext!r}. Allowed: {sorted(_ALLOWED_AUDIO_EXTS)}",
        }, 400

    # Sanitize the display name for filesystem use — keep readable, drop
    # path separators and weird control chars. secure_filename strips the
    # extension if the user supplied one in display_name (e.g. "my voice.wav"),
    # so we re-attach raw_ext to be sure.
    safe_stem = secure_filename(Path(display_name).stem) or "voice_clip"
    desired = f"{safe_stem}{raw_ext}"

    from backend.utils.filename_resolver import resolve_filesystem_filename
    chosen_name = resolve_filesystem_filename(_voice_ref_dir(), desired)
    target = _voice_ref_dir() / chosen_name
    # The DB-style "id" returned to the frontend used to be a uuid hex;
    # keep that shape so existing callers don't break — derive it from the
    # chosen filename's stem now (e.g. "narration" → id="narration").
    asset_id = Path(chosen_name).stem

    # A record left under this name by a clip removed outside the Studio must
    # not cover the new recording, even when the bytes are the same.
    try:
        _consent().remove_record(target)
    except OSError as e:
        logger.warning("stale consent record for %s not removed: %s", target.name, e)
        return jsonify({"error": f"An old consent record for '{target.name}' could not be removed: {e}"}), 500

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
        return jsonify({"error": str(e)}), 500

    consented = False
    if _truthy(flask_request.form.get("consent_confirmed")):
        try:
            _consent().write_record(target, source=_CONSENT_SOURCE)
            consented = True
        except OSError as e:
            logger.warning("voice clip consent record not written for %s: %s", target.name, e)

    return {
        "id": asset_id,
        "filename": target.name,
        "display_name": display_name,
        "path": str(target.resolve()),
        "size_bytes": written,
        "consented": consented,
    }, 201


@audio_foundry_bp.route("/voice-clips/<clip_id>/consent", methods=["POST"])
def confirm_voice_clip_consent(clip_id):
    """Record consent for a clip already imported, e.g. one imported before
    consent was asked for. Body: ``{"confirmed": true}``, sent after the
    person confirmed ``consent_statement``."""
    if not _valid_clip_ref(clip_id):
        return {"error": "Invalid clip id"}, 400
    body = flask_request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return {"error": "Consent is recorded only when the person confirms it (confirmed: true)."}, 400
    clip = _clip_file(clip_id)
    if clip is None:
        return {"error": "Clip not found"}, 404
    try:
        record = _consent().write_record(clip, source=_CONSENT_SOURCE)
    except OSError as e:
        logger.exception("voice clip consent record failed")
        return jsonify({"error": f"Consent could not be recorded: {e}"}), 500
    return {"id": clip.stem, "filename": clip.name, "consented": True,
            "recorded_at": record["recorded_at"], "statement": record["statement"]}, 200


@audio_foundry_bp.route("/voice-clips/<clip_id>/consent", methods=["DELETE"])
def withdraw_voice_clip_consent(clip_id):
    """Withdraw consent for a clip. Its consent record is removed; the clip
    stays and is not cloned again until consent is recorded again (POST on
    this path). JSON body ``{"confirmed": true}``.

    auth_guard leaves this as open as recording consent (the POST above and
    the upload), so withdrawing is never harder than giving it. A clone that
    has already started has read the clip and finishes; one that starts later
    is refused (ChatterboxBackend checks when it takes the model).
    """
    if not _valid_clip_ref(clip_id):
        return {"error": "Invalid clip id"}, 400
    refusal = _confirmed_json("Consent is withdrawn")
    if refusal:
        return refusal
    clip = _clip_file(clip_id)
    if clip is None:
        return {"error": "Clip not found"}, 404
    try:
        had_consent = _consent().remove_record(clip)
    except OSError as e:
        logger.exception("voice clip consent withdrawal failed")
        return jsonify({"error": f"Consent could not be withdrawn: {e}"}), 500
    return {"id": clip.stem, "filename": clip.name, "consented": False,
            "withdrawn": had_consent}, 200


@audio_foundry_bp.route("/voice-clips/<clip_id>/download", methods=["GET"])
def download_voice_clip(clip_id):
    """Stream a reference clip back to the browser so the UI can preview it
    in an <audio> tag before generation."""
    if not _valid_clip_ref(clip_id):
        return {"error": "Invalid clip id"}, 400
    # Audio files only: the clip's consent record shares its name.
    f = _clip_file(clip_id)
    if f is None:
        return {"error": "Clip not found"}, 404
    # Explicit mimetype so <audio> elements don't get application/octet-stream
    # (which produces "No decoders for requested formats" in browser).
    ext = f.suffix.lower()
    if ext == ".mp3":
        mime = "audio/mpeg"
    elif ext == ".wav":
        mime = "audio/wav"
    elif ext == ".flac":
        mime = "audio/flac"
    elif ext == ".ogg":
        mime = "audio/ogg"
    else:
        mime = None  # let send_file / werkzeug guess
    return send_file(f, as_attachment=False, download_name=f.name, mimetype=mime)


@audio_foundry_bp.route("/voice-clips/<clip_id>", methods=["DELETE"])
def delete_voice_clip(clip_id):
    """Delete one imported clip and its consent record. JSON body
    ``{"confirmed": true}``.

    The consent record goes first, so a failure part-way leaves a clip that
    cannot be cloned rather than a record without its clip. Other clips, such
    as ``me.v2.wav`` beside ``me.wav``, are never touched. auth_guard protects
    this route like the Cast Library's deletes (this machine, or the API key).
    A clone that has already started has read the clip and finishes.
    """
    if not _valid_clip_ref(clip_id):
        return {"error": "Invalid clip id"}, 400
    refusal = _confirmed_json("The clip is deleted")
    if refusal:
        return refusal
    clip = _clip_file(clip_id)
    if clip is None:
        return {"error": "Clip not found"}, 404
    try:
        _consent().remove_record(clip)
    except OSError as e:
        logger.exception("voice clip consent record not removed")
        return jsonify({"error": f"The clip was not deleted: its consent record could not be removed ({e})."}), 500
    try:
        clip.unlink()
    except OSError as e:
        logger.exception("voice clip not deleted")
        return jsonify({"error": f"Consent for '{clip.name}' was withdrawn, but the clip could not be deleted: {e}",
                        "consented": False}), 500
    return {"deleted": clip.name, "id": clip.stem}, 200
