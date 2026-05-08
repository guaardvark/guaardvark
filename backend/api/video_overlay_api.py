"""Video text overlay API.

Thin Flask blueprint wrapping `backend.services.video_text_overlay.add_text_to_video`.
Takes a video Document by id, runs ffmpeg drawtext, registers the new file
as a separate Document so the user keeps the original. Auto-discovered by
backend.utils.blueprint_discovery.

POST /api/video-overlay/text
    body: {
      "document_id": <int>,                    # required, existing video Document
      "text": "<str>",                         # required
      "font_size": 48,                         # optional
      "font_color": "white" | "#rrggbb",       # optional
      "position": "bottom-center" | ...,       # optional, see _POSITION_EXPRESSIONS
      "border": true,                          # optional, outline for legibility
      "border_width": 2,                       # optional
      "border_color": "black",                 # optional
      "box_background": false,                 # optional, translucent backdrop
      "box_color": "black@0.5",                # optional
      "box_border_width": 10,                  # optional
    }
    returns: 201 with the new Document JSON, or 4xx/5xx with an error envelope.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from flask import Blueprint, request

from backend.models import Document as DBDocument, db
from backend.services.output_registration import register_file
from backend.services.video_text_overlay import VideoOverlayError, add_text_to_video
from backend.utils.response_utils import error_response, success_response
from backend.celery_app import celery

logger = logging.getLogger(__name__)

video_overlay_bp = Blueprint("video_overlay_api", __name__, url_prefix="/api/video-overlay")

# Where rendered overlay outputs land. Lives under data/outputs/ so the
# existing backup/portability rules apply, in its own subfolder so the
# files don't mingle with raw model outputs.
_OVERLAY_SUBDIR = Path("data/outputs/videos/text-overlay")

# Conservative cap on user-supplied text length — drawtext can technically
# render huge strings but the UX collapses well before that and a 10k-char
# value is almost certainly an injection attempt or a paste accident.
_MAX_TEXT_LEN = 500

# Same shape as the service's _POSITION_EXPRESSIONS; kept here for input
# validation so a typo gets a 400 instead of a silent fallback. Keep these
# two lists in sync.
_VALID_POSITIONS = {
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
}


def _parse_int(value, default, field):
    """Try to parse; on ValueError return a 400-shaped tuple, otherwise the int."""
    if value is None:
        return default, None
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, error_response(f"{field} must be an integer", 400, "INVALID_FIELD")

def _parse_float(value, default, field):
    """Try to parse; on ValueError return a 400-shaped tuple, otherwise the float."""
    if value is None:
        return default, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, error_response(f"{field} must be a number", 400, "INVALID_FIELD")

def _resolve_video_path(doc: DBDocument) -> Path | None:
    """Resolve a video Document to its on-disk bytes.

    Delegates to backend.services.document_path_resolver which handles the
    divergent paths that legacy generators produced (paths under UPLOAD_DIR,
    paths under plugins/comfyui/ComfyUI/output/, paths that disagree with
    Document.filename, etc.). Phase 1 of the Video Editor plan
    (plans/2026-04-29-video-editor.md §4) established this resolver as the
    bridge for rows predating the filename-structure invariant.
    """
    from backend.services.document_path_resolver import resolve_document_path
    return resolve_document_path(doc)


# Thumbnail URL builders — kept here so the frontend doesn't have to know
# which endpoint serves which media kind. If we ever swap the thumbnail
# routing (caching CDN, signed URLs, etc.) this is the only place to change.
def _video_thumb_url(doc: DBDocument) -> str:
    # ?document_id= form routes through resolve_document_path on the backend,
    # so comfyui-output videos thumbnail correctly even though they live
    # outside data/uploads/.
    return f"/api/files/thumbnail?document_id={doc.id}"


def _image_thumb_url(doc: DBDocument) -> str:
    # Images are small enough that the original IS the thumbnail; the
    # frontend can size it down with CSS. No need for a separate cache.
    return f"/api/files/document/{doc.id}/download"


def _decorate_with_thumbnail(doc: DBDocument, kind: str) -> dict:
    row = doc.to_dict()
    if kind == "video":
        row["thumbnail_url"] = _video_thumb_url(doc)
    elif kind == "image":
        row["thumbnail_url"] = _image_thumb_url(doc)
    else:
        # Audio: no visual thumb. Frontend renders the kind-specific icon.
        row["thumbnail_url"] = None
    return row


@video_overlay_bp.route("/videos", methods=["GET"])
def list_videos():
    """List video Documents the user could overlay text onto.

    The shared /api/files/search endpoint requires a non-empty query, so we
    can't lean on it to populate a "pick a video" dropdown. This is a
    minimal, paginated-by-default list keyed off the filename extension.
    """
    limit, err = _parse_int(request.args.get("limit"), 100, "limit")
    if err:
        return err
    limit = min(limit, 500)
    extensions = (".mp4", ".webm", ".mov", ".mkv", ".avi")
    rows = (
        DBDocument.query
        .filter(db.or_(*[DBDocument.filename.ilike(f"%{ext}") for ext in extensions]))
        .order_by(DBDocument.id.desc())
        .limit(limit)
        .all()
    )
    return success_response({
        "videos": [_decorate_with_thumbnail(d, "video") for d in rows],
        "total": len(rows),
    })


@video_overlay_bp.route("/audio-library", methods=["GET"])
def list_audio_library():
    """List audio Documents for the editor's media library audio rail."""
    limit, err = _parse_int(request.args.get("limit"), 200, "limit")
    if err:
        return err
    limit = min(limit, 500)
    extensions = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus")
    rows = (
        DBDocument.query
        .filter(db.or_(*[DBDocument.filename.ilike(f"%{ext}") for ext in extensions]))
        .order_by(DBDocument.id.desc())
        .limit(limit)
        .all()
    )
    return success_response({
        "audio": [_decorate_with_thumbnail(d, "audio") for d in rows],
        "total": len(rows),
    })


@video_overlay_bp.route("/image-library", methods=["GET"])
def list_image_library():
    """List image Documents for the editor's media library image rail."""
    limit, err = _parse_int(request.args.get("limit"), 200, "limit")
    if err:
        return err
    limit = min(limit, 500)
    extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff")
    rows = (
        DBDocument.query
        .filter(db.or_(*[DBDocument.filename.ilike(f"%{ext}") for ext in extensions]))
        .order_by(DBDocument.id.desc())
        .limit(limit)
        .all()
    )
    return success_response({
        "images": [_decorate_with_thumbnail(d, "image") for d in rows],
        "total": len(rows),
    })


@video_overlay_bp.route("/render-timeline", methods=["POST"])
def render_timeline_endpoint():
    """Render a Video Editor timeline to a final mp4.

    Body shape (TimelineState — see frontend/src/pages/VideoEditorPage.jsx):
      {
        video_document_id: int,
        video_trim_start: float | null,
        video_trim_end: float | null,
        text_elements: [{text, fontSize, fontColor, x, y, rotation,
                          startSeconds, endSeconds}, ...],
        audio_document_id: int | null,
        audio_volume: float,
      }

    Returns the new Document on success. JobOperationGate integration and
    Celery routing for long renders are wired in Phase 8.
    """
    payload = request.get_json(silent=True) or {}
    
    audio_volume, err = _parse_float(payload.get("audio_volume"), 1.0, "audio_volume")
    if err:
        return err

    video_doc_id = payload.get("video_document_id")
    if not isinstance(video_doc_id, int):
        return error_response("video_document_id (int) is required", 400, "MISSING_FIELDS")

    video_doc = db.session.get(DBDocument, video_doc_id)
    if video_doc is None:
        return error_response("Video document not found", 404, "DOCUMENT_NOT_FOUND")
    video_path = _resolve_video_path(video_doc)
    if video_path is None:
        return error_response(f"Video file not on disk: {video_doc.path}", 404, "FILE_NOT_FOUND")

    # Audio is optional; resolve if present.
    audio_path = None
    audio_doc_id = payload.get("audio_document_id")
    if isinstance(audio_doc_id, int):
        audio_doc = db.session.get(DBDocument, audio_doc_id)
        if audio_doc is None:
            return error_response("Audio document not found", 404, "AUDIO_NOT_FOUND")
        audio_path = _resolve_video_path(audio_doc)  # same resolver works for any media kind
        if audio_path is None:
            return error_response(f"Audio file not on disk: {audio_doc.path}", 404, "AUDIO_NOT_ON_DISK")

    text_elements = payload.get("text_elements") or []
    if not isinstance(text_elements, list):
        return error_response("text_elements must be an array", 400, "INVALID_TEXT_ELEMENTS")

    # Source-named output via the resolver (Phase 3 of filename plan): pick
    # a unique UUID suffix to avoid collisions under concurrent renders.
    editor_renders_dir = Path("data/outputs/videos/editor-renders")
    editor_renders_dir.mkdir(parents=True, exist_ok=True)
    source_stem = Path(video_doc.filename).stem
    output_path = (editor_renders_dir / f"{source_stem}_{uuid.uuid4().hex[:8]}.mp4").resolve()

    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
    progress_system = get_unified_progress()
    job_id = progress_system.create_process(ProcessType.VIDEO_RENDER, f"Render: {source_stem}")

    from backend.tasks.video_render_tasks import create_video_render_tasks
    # The task function is bound to the celery app
    celery.send_task(
        "video_render_tasks.render_timeline_task",
        args=(payload, str(output_path), job_id),
        queue="renders"
    )

    return success_response(
        data={"job_id": job_id, "status": "pending"},
        message="Render dispatched",
        status_code=202,
    )

@video_overlay_bp.route("/render-status/<job_id>", methods=["GET"])
def render_status(job_id):
    """Polled by the frontend after dispatch; mirrors progress_system state."""
    from backend.utils.unified_progress_system import get_unified_progress
    progress_system = get_unified_progress()
    proc = progress_system.get_process(job_id)
    if proc is None:
        return error_response("Job not found", 404, "JOB_NOT_FOUND")
    return success_response({
        "job_id": job_id,
        "status": proc.status.value if hasattr(proc.status, "value") else proc.status,
        "progress": proc.progress,
        "message": proc.message,
        "document_id": (proc.additional_data or {}).get("document_id"),
    })


@video_overlay_bp.route("/text", methods=["POST"])
def overlay_text():
    """Render a single text element onto an existing video and register the result."""
    payload = request.get_json(silent=True) or {}

    document_id = payload.get("document_id")
    text = (payload.get("text") or "").strip()

    if not isinstance(document_id, int):
        return error_response(
            "document_id (int) is required",
            status_code=400,
            error_code="MISSING_FIELDS",
        )
    if not text:
        return error_response("text is required and cannot be empty", 400, "MISSING_FIELDS")
    if len(text) > _MAX_TEXT_LEN:
        return error_response(
            f"text exceeds {_MAX_TEXT_LEN} characters",
            400,
            "TEXT_TOO_LONG",
        )

    position = payload.get("position", "bottom-center")
    if position not in _VALID_POSITIONS:
        return error_response(
            f"position must be one of {sorted(_VALID_POSITIONS)}",
            400,
            "INVALID_POSITION",
        )

    doc = db.session.get(DBDocument, document_id)
    if not doc:
        return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")

    input_path = _resolve_video_path(doc)
    if input_path is None:
        return error_response(
            f"Document file not on disk: {doc.path}",
            404,
            "FILE_NOT_FOUND",
        )

    # Reuse the input's extension; the encoder we picked produces .mp4 anyway,
    # but if a user has, say, a .mov stored we still want the convention to be
    # right. The actual container ffmpeg writes is determined by libx264 +
    # the path — sticking with .mp4 keeps things broadly compatible.
    # Source-named with sequential suffix per the filename plan. Pattern:
    # "<source_basename>_001.mp4", "_002.mp4" on subsequent renders of the
    # same source. Bumps until it finds a free slot in _OVERLAY_SUBDIR.
    _OVERLAY_SUBDIR.mkdir(parents=True, exist_ok=True)
    source_stem = Path(doc.filename).stem
    n = 1
    while True:
        candidate = _OVERLAY_SUBDIR / f"{source_stem}_{n:03d}.mp4"
        if not candidate.exists():
            output_path = candidate.resolve()
            break
        n += 1
        if n > 999:
            # Absurdity guard — shouldn't happen in practice
            output_path = (_OVERLAY_SUBDIR / f"{source_stem}_{uuid.uuid4().hex[:8]}.mp4").resolve()
            break

    try:
        add_text_to_video(
            input_path=input_path,
            output_path=output_path,
            text=text,
            font_size=int(payload.get("font_size", 48)),
            font_color=payload.get("font_color", "white"),
            position=position,
            border=bool(payload.get("border", True)),
            border_width=int(payload.get("border_width", 2)),
            border_color=payload.get("border_color", "black"),
            box_background=bool(payload.get("box_background", False)),
            box_color=payload.get("box_color", "black@0.5"),
            box_border_width=int(payload.get("box_border_width", 10)),
        )
    except VideoOverlayError as e:
        logger.warning("video text overlay failed: %s", e)
        return error_response(str(e), 500, "OVERLAY_FAILED")
    except Exception as e:
        logger.exception("video text overlay unexpected failure")
        return error_response(f"{type(e).__name__}: {e}", 500, "OVERLAY_FAILED")

    # Register the new file as a separate Document. file_metadata records the
    # source so the UI can show "made from <original>" and the user can find
    # the original later if needed.
    new_doc = register_file(
        physical_path=str(output_path),
        folder_name="Videos",
        subfolder_name="Text Overlay",
        filename=f"{Path(doc.filename).stem}-text.mp4",
        file_type=".mp4",
        file_metadata={
            "source_document_id": doc.id,
            "source_filename": doc.filename,
            "overlay_text": text,
            "position": position,
            "font_size": int(payload.get("font_size", 48)),
            "font_color": payload.get("font_color", "white"),
        },
    )

    if new_doc is None:
        return error_response(
            "Overlay rendered but Document registration failed; check logs",
            500,
            "REGISTRATION_ERROR",
        )

    return success_response(
        data=new_doc.to_dict(),
        message="Text overlay rendered",
        status_code=201,
    )
