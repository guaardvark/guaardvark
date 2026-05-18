"""Video Editor API — proxy blueprint for the video_editor plugin service.

Modeled on audio_foundry_api.py: thin forwarder to the FastAPI service on 8207.
Auto-discovered by backend.utils.blueprint_discovery.

Resolves Document IDs to absolute paths before forwarding — frontend callers
pass `document_id` for audio_path / video_paths and we substitute the on-disk
file path the plugin actually opens.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import requests
from flask import Blueprint, jsonify, request as flask_request

logger = logging.getLogger(__name__)

video_editor_bp = Blueprint("video_editor", __name__, url_prefix="/api/video-editor")

PLUGIN_URL = "http://127.0.0.1:8207"
QUICK_TIMEOUT = 10        # /health, /status, /config, /jobs
RENDER_TIMEOUT = 1200     # /beat-sync/render returns a job_id immediately,
                          # but a synchronous melt encode can take ~minutes;
                          # keep generous in case render_mp4=true is requested.


def _proxy_get(path: str, timeout: int = QUICK_TIMEOUT):
    try:
        resp = requests.get(f"{PLUGIN_URL}{path}", timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Video Editor service not running"}, 503
    except Exception as e:  # noqa: BLE001
        logger.exception("Video Editor GET %s failed", path)
        return {"error": str(e)}, 500


def _proxy_post(path: str, json_data: dict, timeout: int):
    try:
        resp = requests.post(f"{PLUGIN_URL}{path}", json=json_data, timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Video Editor service not running"}, 503
    except requests.Timeout:
        return {"error": f"Video Editor request timed out after {timeout}s"}, 504
    except Exception as e:  # noqa: BLE001
        logger.exception("Video Editor POST %s failed", path)
        return {"error": str(e)}, 500


def _resolve_document(doc_id: Any) -> Optional[str]:
    """Resolve a Document row by id to its absolute path. Returns None if missing."""
    if not doc_id:
        return None
    try:
        from backend.models import Document  # local import — avoid cycle on module load
    except ImportError:
        return None
    doc = Document.query.get(doc_id)
    if not doc:
        return None
    path = doc.file_path or doc.path or doc.filename
    if not path:
        return None
    p = Path(path)
    return str(p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve())


def _expand_paths(payload: dict[str, Any]) -> dict[str, Any]:
    """In-place: replace document_id / video_document_ids with absolute file paths.

    Frontend may send either `audio_path` (string) or `audio_document_id` (int).
    Same for the video pool.
    """
    if "audio_document_id" in payload and not payload.get("audio_path"):
        resolved = _resolve_document(payload.pop("audio_document_id"))
        if resolved:
            payload["audio_path"] = resolved

    if "video_document_ids" in payload and not payload.get("video_paths"):
        ids = payload.pop("video_document_ids") or []
        paths = [_resolve_document(d) for d in ids]
        payload["video_paths"] = [p for p in paths if p]

    return payload


# ---------- read-side ---------------------------------------------------------

@video_editor_bp.route("/health", methods=["GET"])
def health():
    body, status = _proxy_get("/health")
    return jsonify(body), status


@video_editor_bp.route("/status", methods=["GET"])
def status():
    body, status_code = _proxy_get("/status")
    return jsonify(body), status_code


@video_editor_bp.route("/config", methods=["GET"])
def config():
    body, status_code = _proxy_get("/config")
    return jsonify(body), status_code


@video_editor_bp.route("/jobs", methods=["GET"])
def list_jobs():
    body, status_code = _proxy_get(f"/jobs?limit={flask_request.args.get('limit', 50)}")
    return jsonify(body), status_code


@video_editor_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    body, status_code = _proxy_get(f"/jobs/{job_id}")
    return jsonify(body), status_code


# ---------- write-side --------------------------------------------------------

@video_editor_bp.route("/beat-sync/render", methods=["POST"])
def beat_sync_render():
    payload = flask_request.get_json(silent=True) or {}
    payload = _expand_paths(payload)
    body, status_code = _proxy_post("/beat-sync/render", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/auto-editor/trim", methods=["POST"])
def auto_editor_trim():
    payload = flask_request.get_json(silent=True) or {}
    if "document_id" in payload and not payload.get("input_path"):
        resolved = _resolve_document(payload.pop("document_id"))
        if resolved:
            payload["input_path"] = resolved
    body, status_code = _proxy_post("/auto-editor/trim", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/shotcut/compose", methods=["POST"])
def shotcut_compose():
    payload = flask_request.get_json(silent=True) or {}
    body, status_code = _proxy_post("/shotcut/compose", payload, timeout=QUICK_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/shotcut/compose-arrangement", methods=["POST"])
def shotcut_compose_arrangement():
    """Multi-clip render path. Resolves the song document_id if provided."""
    payload = flask_request.get_json(silent=True) or {}
    if "song_document_id" in payload and not payload.get("audio_path"):
        resolved = _resolve_document(payload.pop("song_document_id"))
        if resolved:
            payload["audio_path"] = resolved
    body, status_code = _proxy_post("/shotcut/compose-arrangement", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/catalog/filters", methods=["GET"])
def list_filter_catalog():
    body, status_code = _proxy_get("/catalog/filters")
    return jsonify(body), status_code


@video_editor_bp.route("/catalog/transitions", methods=["GET"])
def list_transition_catalog():
    body, status_code = _proxy_get("/catalog/transitions")
    return jsonify(body), status_code


@video_editor_bp.route("/vision/rescan-clip", methods=["POST"])
def rescan_clip():
    """Force-bust the cache for one clip and re-run vision analysis."""
    payload = flask_request.get_json(silent=True) or {}
    if "document_id" in payload and not payload.get("source_path"):
        resolved = _resolve_document(payload.pop("document_id"))
        if resolved:
            payload["source_path"] = resolved
    body, status_code = _proxy_post("/vision/rescan-clip", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/vision/clip-hash", methods=["POST"])
def get_clip_hash():
    """Resolve a clip's content hash for building frame-thumbnail URLs."""
    payload = flask_request.get_json(silent=True) or {}
    if "document_id" in payload and not payload.get("source_path"):
        resolved = _resolve_document(payload.pop("document_id"))
        if resolved:
            payload["source_path"] = resolved
    body, status_code = _proxy_post("/vision/clip-hash", payload, timeout=QUICK_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/vision/frames/<clip_hash>/<int:frame_index>", methods=["GET"])
def get_sampled_frame(clip_hash: str, frame_index: int):
    """Stream a sampled JPEG frame from the plugin to the browser."""
    import requests
    try:
        resp = requests.get(
            f"{PLUGIN_URL}/vision/frames/{clip_hash}/{frame_index}",
            timeout=QUICK_TIMEOUT,
            stream=False,
        )
    except requests.ConnectionError:
        return jsonify({"error": "Video Editor service not running"}), 503

    if resp.status_code != 200:
        return jsonify({"error": f"plugin returned {resp.status_code}"}), resp.status_code
    from flask import Response
    return Response(resp.content, mimetype="image/jpeg")


# ---------- A1 endpoints: bin-driven Plan pipeline ---------------------------

@video_editor_bp.route("/recipes", methods=["GET"])
def list_recipes():
    body, status_code = _proxy_get("/recipes")
    return jsonify(body), status_code


@video_editor_bp.route("/plan", methods=["POST"])
def submit_plan():
    """Bin + song → arrangement. Resolves bin clip document_ids to paths first."""
    payload = flask_request.get_json(silent=True) or {}

    # Expand bin_clips' document_id → source_path
    expanded_bin: list[dict[str, Any]] = []
    for entry in payload.get("bin_clips") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("source_path"):
            expanded_bin.append(entry)
            continue
        doc_id = entry.get("document_id")
        if doc_id:
            path = _resolve_document(doc_id)
            if path:
                expanded_bin.append({
                    "clip_id": entry.get("clip_id") or f"doc{doc_id}",
                    "source_path": path,
                    "document_id": doc_id,
                })
    payload["bin_clips"] = expanded_bin

    # Expand song
    if not payload.get("song_path") and payload.get("song_document_id"):
        path = _resolve_document(payload["song_document_id"])
        if path:
            payload["song_path"] = path

    body, status_code = _proxy_post("/plan", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/vision/scan-clips", methods=["POST"])
def vision_scan_clips():
    """A1: returns neutral defaults. A3: real vision call inside the plugin."""
    payload = flask_request.get_json(silent=True) or {}
    if "document_ids" in payload and not payload.get("clip_paths"):
        ids = payload.pop("document_ids") or []
        payload["clip_paths"] = [p for p in (_resolve_document(d) for d in ids) if p]
    body, status_code = _proxy_post("/vision/scan-clips", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/open-in-shotcut", methods=["POST"])
def open_in_shotcut():
    payload = flask_request.get_json(silent=True) or {}
    body, status_code = _proxy_post("/open-in-shotcut", payload, timeout=QUICK_TIMEOUT)
    return jsonify(body), status_code
