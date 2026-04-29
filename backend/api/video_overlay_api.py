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


@video_overlay_bp.route("/videos", methods=["GET"])
def list_videos():
    """List video Documents the user could overlay text onto.

    The shared /api/files/search endpoint requires a non-empty query, so we
    can't lean on it to populate a "pick a video" dropdown. This is a
    minimal, paginated-by-default list keyed off the filename extension.
    """
    limit = min(int(request.args.get("limit", 100)), 500)
    extensions = (".mp4", ".webm", ".mov", ".mkv", ".avi")
    rows = (
        DBDocument.query
        .filter(db.or_(*[DBDocument.filename.ilike(f"%{ext}") for ext in extensions]))
        .order_by(DBDocument.id.desc())
        .limit(limit)
        .all()
    )
    return success_response({
        "videos": [d.to_dict() for d in rows],
        "total": len(rows),
    })


@video_overlay_bp.route("/audio-library", methods=["GET"])
def list_audio_library():
    """List audio Documents for the editor's media library audio rail."""
    limit = min(int(request.args.get("limit", 200)), 500)
    extensions = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus")
    rows = (
        DBDocument.query
        .filter(db.or_(*[DBDocument.filename.ilike(f"%{ext}") for ext in extensions]))
        .order_by(DBDocument.id.desc())
        .limit(limit)
        .all()
    )
    return success_response({
        "audio": [d.to_dict() for d in rows],
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
    # the next free '<source>_NNN.mp4' under data/outputs/videos/editor-renders/.
    editor_renders_dir = Path("data/outputs/videos/editor-renders")
    editor_renders_dir.mkdir(parents=True, exist_ok=True)
    source_stem = Path(video_doc.filename).stem
    output_path = None
    for n in range(1, 1000):
        candidate = editor_renders_dir / f"{source_stem}_{n:03d}.mp4"
        if not candidate.exists():
            output_path = candidate.resolve()
            break
    if output_path is None:
        return error_response("Could not allocate output filename", 500, "FILENAME_ALLOCATION_FAILED")

    from backend.services.video_timeline_render import render_timeline
    try:
        render_timeline(
            video_input_path=video_path,
            output_path=output_path,
            text_elements=text_elements,
            video_trim_start=payload.get("video_trim_start"),
            video_trim_end=payload.get("video_trim_end"),
            audio_input_path=audio_path,
            audio_volume=float(payload.get("audio_volume", 1.0)),
        )
    except VideoOverlayError as e:
        logger.warning("render_timeline_endpoint failed: %s", e)
        return error_response(str(e), 500, "RENDER_FAILED")
    except Exception as e:
        logger.exception("render_timeline_endpoint unexpected failure")
        return error_response(f"{type(e).__name__}: {e}", 500, "RENDER_FAILED")

    new_doc = register_file(
        physical_path=str(output_path),
        folder_name="Videos",
        subfolder_name="Editor Renders",
        filename=output_path.name,
        file_type=".mp4",
        file_metadata={
            "source_document_id": video_doc.id,
            "source_filename": video_doc.filename,
            "audio_document_id": audio_doc_id,
            "text_element_count": len(text_elements),
            "trim_start": payload.get("video_trim_start"),
            "trim_end": payload.get("video_trim_end"),
        },
    )
    if new_doc is None:
        return error_response("Render succeeded but Document registration failed", 500, "REGISTRATION_ERROR")

    return success_response(
        data=new_doc.to_dict(),
        message="Timeline rendered",
        status_code=201,
    )


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
