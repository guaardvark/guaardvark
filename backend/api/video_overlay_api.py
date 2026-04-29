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
    """Document.path is sometimes relative to the project root, sometimes absolute.

    The mp4s registered by comfyui_video_generator land as relative to project
    root (e.g. `plugins/comfyui/ComfyUI/output/cogvideo_00008.mp4`), but a
    user-uploaded file might be stored absolute. Try both.
    """
    candidate = Path(doc.path)
    if candidate.is_file():
        return candidate.resolve()
    project_relative = Path.cwd() / candidate
    if project_relative.is_file():
        return project_relative.resolve()
    return None


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
    output_path = (_OVERLAY_SUBDIR / f"{uuid.uuid4().hex}.mp4").resolve()

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
