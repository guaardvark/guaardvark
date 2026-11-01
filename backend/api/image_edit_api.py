"""The Image Edit page: photo uploads, face checks, likeness consent, and runs.

Every run is one of the photo tools chat and MCP use (generate_identity,
edit_image, outpaint_image, remove_background) with the page's typed settings,
started as a tool job (backend/services/tool_jobs.py) and polled over
GET /api/tools/jobs/<id>. The page chooses the mode; nothing here guesses it
from wording.

Pictures are named, not pathed: a photo uploaded here lives in
outputs/edit_inputs and a result in outputs/generated_images, and the page
refers to either by its served URL. Nothing outside those two folders can be
named.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, request
from werkzeug.utils import secure_filename

from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

image_edit_bp = Blueprint("image_edit", __name__, url_prefix="/api/image-edit")

UPLOAD_DIR_NAME = "edit_inputs"
RESULT_DIR_NAME = "generated_images"
_SERVED_PREFIXES = {
    f"/api/outputs/{UPLOAD_DIR_NAME}/": UPLOAD_DIR_NAME,
    f"/api/outputs/{RESULT_DIR_NAME}/": RESULT_DIR_NAME,
}
# A photo larger than this on its longest side is scaled down on upload; every
# model the page drives works at or below about 2 megapixels.
UPLOAD_MAX_SIDE = 2048
UPLOAD_MAX_BYTES = 40 * 1024 * 1024

# What the progress footer calls each mode's job.
MODE_TITLES = {
    "identity": "Image Edit: new scene, same face",
    "edit": "Image Edit: edit the photo",
    "expand": "Image Edit: expand",
    "background": "Image Edit: remove background",
}
MODE_TOOLS = {
    "identity": "generate_identity",
    "edit": "edit_image",
    "expand": "outpaint_image",
    "background": "remove_background",
}
EDIT_ENGINES = (
    ("qwen-image-edit", "qwen-image-edit"),
    ("flux-kontext-dev", "flux-kontext-dev"),
    ("moss-image-edit", "moss-image-edit"),
)
# PuLID-FLUX canvases. Measured 2026-10-31 on a 16 GB card (fp8, 20 steps, the
# pack's weight and start): 1360x768 (Video Gen's 16:9) 44 s and 14.8 GB peak,
# 1920x1088 60 s and 15.1 GB, 1088x1920 62 s and 15.1 GB, with the face held at
# each; 768x1024 is the 2026-09-19 default.
IDENTITY_SIZES = {
    "portrait": (768, 1024),
    "square": (1024, 1024),
    "landscape": (1024, 768),
    "video": (1360, 768),
    "fullhd": (1920, 1088),
    "vertical": (1088, 1920),
}
EXPAND_MAX_PX = 1024


def _outputs_dir() -> str:
    from backend.config import OUTPUT_DIR
    return OUTPUT_DIR


def _served_url(folder: str, name: str) -> str:
    return f"/api/outputs/{folder}/{name}"


def _resolve(ref: Any) -> Optional[str]:
    """The disk path of a picture the page named by its served URL, or None."""
    if not isinstance(ref, str):
        return None
    ref = ref.split("?", 1)[0].strip()
    for prefix, folder in _SERVED_PREFIXES.items():
        if ref.startswith(prefix):
            name = ref[len(prefix):]
            safe = secure_filename(name)
            if not safe or safe != name:
                return None
            path = os.path.join(_outputs_dir(), folder, safe)
            return path if os.path.isfile(path) else None
    return None


def _face_report(path: str) -> Dict[str, Any]:
    from backend.services.face_reference import FaceCheckUnavailable, find_faces
    try:
        found = find_faces(path)
        return {"faces": found, "face_check_error": None}
    except FaceCheckUnavailable as e:
        return {"faces": None, "face_check_error": str(e)}
    except Exception as e:  # noqa: BLE001 - an unreadable photo is reported, not raised
        logger.warning("face check failed for %s: %s", path, e)
        return {"faces": None, "face_check_error": f"The face check could not read this photo: {e}"}


def _picture_row(path: str, folder: str, *, check_faces: bool) -> Dict[str, Any]:
    from PIL import Image
    from backend.services.consent_records import has_consent
    with Image.open(path) as image:
        width, height = image.size
    row: Dict[str, Any] = {
        "url": _served_url(folder, os.path.basename(path)),
        "name": os.path.basename(path),
        "width": width,
        "height": height,
        "consented": has_consent(path),
    }
    if check_faces:
        row.update(_face_report(path))
    return row


def _pack_state(pack_id: str) -> Dict[str, Any]:
    from backend.services.image_editing_packs import pack_by_id, pack_installed
    pack = pack_by_id(pack_id)
    if pack is None:
        return {"id": pack_id, "installed": False, "name": pack_id, "enabled": False}
    return {"id": pack_id, "installed": pack_installed(pack), "name": pack["name"],
            "short": pack.get("short"), "enabled": True}


@image_edit_bp.route("/capabilities", methods=["GET"])
def capabilities():
    """What each mode can do on this machine, and the pack that unlocks it."""
    try:
        from backend.services.comfyui_image_generator import PULID_IDENTITY_DEFAULTS, PULID_MIN_STEPS
        from backend.services.face_reference import MAX_REFERENCES, detector_path
        from backend.services.image_editing_packs import missing_message

        pulid = _pack_state("pulid-flux")
        engines = [dict(_pack_state(pack_id), model=model) for pack_id, model in EDIT_ENGINES]
        engines = [e for e in engines if e.get("enabled")]
        qwen = next((e for e in engines if e["id"] == "qwen-image-edit"), {"installed": False})
        any_engine = any(e["installed"] for e in engines)
        bg_packs = [_pack_state("bgremove-birefnet"), _pack_state("bgremove-u2net")]
        expand_ready = qwen["installed"] or any(
            e["installed"] for e in engines if e["id"] == "flux-kontext-dev")

        data = {
            "modes": {
                "identity": {
                    "available": pulid["installed"],
                    "reason": None if pulid["installed"] else missing_message("generate_identity"),
                    "install": "pulid-flux",
                    "max_references": MAX_REFERENCES,
                    "face_check": detector_path() is not None,
                    "defaults": {
                        "weight": PULID_IDENTITY_DEFAULTS["weight"],
                        "start_at": PULID_IDENTITY_DEFAULTS["start_at"],
                        "steps": PULID_MIN_STEPS,
                        "min_steps": PULID_MIN_STEPS,
                        "size": "portrait",
                    },
                    "sizes": {k: {"width": w, "height": h} for k, (w, h) in IDENTITY_SIZES.items()},
                },
                "edit": {
                    "available": any_engine,
                    "reason": None if any_engine else missing_message("edit_image"),
                    "install": "qwen-image-edit",
                    "engines": engines,
                    "max_references": 3 if qwen["installed"] else 1,
                },
                "expand": {
                    "available": expand_ready,
                    "reason": None if expand_ready else missing_message("outpaint_image"),
                    "install": "qwen-image-edit",
                    "adds_canvas": bool(qwen["installed"]),
                    "max_px": EXPAND_MAX_PX,
                },
                "background": {
                    "available": any(p["installed"] for p in bg_packs),
                    "reason": None if any(p["installed"] for p in bg_packs) else missing_message("remove_background"),
                    "install": "bgremove-birefnet",
                },
            },
        }
        return success_response(data=data, message="Image Edit capabilities")
    except Exception as e:  # noqa: BLE001
        logger.error("image edit capabilities failed: %s", e, exc_info=True)
        return error_response(f"Could not read what this machine can edit: {e}", 500)


@image_edit_bp.route("/uploads", methods=["POST"])
def upload():
    """Store one photo for the page (EXIF orientation applied, saved as PNG)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    file = request.files.get("file")
    if file is None or not file.filename:
        return error_response("Send the photo as the 'file' field.", 400)
    raw = file.read(UPLOAD_MAX_BYTES + 1)
    if len(raw) > UPLOAD_MAX_BYTES:
        return error_response(f"The photo is over {UPLOAD_MAX_BYTES // (1024 * 1024)} MB.", 413)
    import io
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except (UnidentifiedImageError, OSError) as e:
        return error_response(f"That file is not a picture this page can read: {e}", 400)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    longest = max(image.size)
    if longest > UPLOAD_MAX_SIDE:
        ratio = UPLOAD_MAX_SIDE / float(longest)
        image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                             Image.LANCZOS)
    folder = os.path.join(_outputs_dir(), UPLOAD_DIR_NAME)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"page_src_{uuid.uuid4().hex[:12]}.png")
    image.save(path)
    check = str(request.form.get("check_faces", "")).lower() in ("1", "true", "yes")
    return success_response(data=_picture_row(path, UPLOAD_DIR_NAME, check_faces=check),
                            message="Photo stored", status_code=201)


@image_edit_bp.route("/faces", methods=["POST"])
def faces():
    """Run the face check on a picture already on the page."""
    body = request.get_json(silent=True) or {}
    path = _resolve(body.get("image"))
    if not path:
        return error_response("That picture is not one this page uploaded or made.", 404)
    folder = os.path.basename(os.path.dirname(path))
    return success_response(data=_picture_row(path, folder, check_faces=True), message="Face check")


@image_edit_bp.route("/consent", methods=["POST"])
def consent():
    """Record that the person may use the likeness in this photo.

    The page asks the same question the chat card does, once per photo; the
    record sits next to the photo and under its content hash, so chat will not
    ask again for the same picture.
    """
    body = request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return error_response("Consent is recorded only when the person confirms it.", 400)
    path = _resolve(body.get("image"))
    if not path:
        return error_response("That picture is not one this page uploaded or made.", 404)
    from backend.services.consent_records import record_consent
    try:
        record = record_consent(path, "ui_upload", note="Image Edit page")
    except (OSError, ValueError) as e:
        return error_response(f"The consent record could not be stored: {e}", 500)
    return success_response(data={"url": _served_url(os.path.basename(os.path.dirname(path)),
                                                     os.path.basename(path)),
                                  "consented": True, "recorded_at": record.get("recorded_at")},
                            message="Consent recorded")


def _int(value, default=None, *, low=None, high=None):
    if value in (None, ""):
        return default
    number = int(float(value))
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def _params_for(mode: str, body: Dict[str, Any], source: str, refs: List[str]):
    """(tool parameters, None) or (None, (message, status)) for one run."""
    settings = body.get("settings") or {}
    text = str(body.get("prompt") or "").strip()
    if mode == "identity":
        from backend.services.consent_records import has_consent
        if body.get("scene"):
            # Second step: put the faces on the scene the first step rendered.
            scene = _resolve(body.get("scene"))
            if not scene:
                return None, ("That scene is no longer on this machine; render it again.", 400)
            params = {"scene": scene, "consented": True, "face": _int(settings.get("face"))}
            if settings.get("person2_face") not in (None, ""):
                params["person2_face"] = _int(settings.get("person2_face"))
            if settings.get("weight") not in (None, ""):
                params["weight"] = float(settings["weight"])
            return params, None
        if not text:
            return None, ("Describe the new scene.", 400)
        second = []
        for ref in (body.get("person2") or [])[:2]:
            path = _resolve(ref)
            if not path:
                return None, ("A photo of the second person is no longer on this machine; add it again.", 400)
            second.append(path)
        for path in (source, *refs, *second):
            if not has_consent(path):
                return None, (f"Confirm you may use the likeness in {os.path.basename(path)} first.", 409)
        width, height = IDENTITY_SIZES.get(str(settings.get("size") or "portrait"), IDENTITY_SIZES["portrait"])
        params = {"prompt": text, "image": source, "consented": True,
                  "width": width, "height": height}
        for i, ref in enumerate(refs[:2], start=2):
            params[f"reference_image_{i}"] = ref
        if settings.get("others") or second:
            params["others"] = True
            for key, path in zip(("person2_image", "person2_image_2"), second):
                params[key] = path
        steps = _int(settings.get("steps"))
        if steps:
            params["steps"] = steps
        seed = _int(settings.get("seed"), low=0, high=2**32 - 1)
        if seed is not None:
            params["seed"] = seed
        if settings.get("weight") not in (None, ""):
            params["weight"] = float(settings["weight"])
        return params, None
    if mode == "edit":
        if not text:
            return None, ("Say what to change.", 400)
        params = {"instruction": text, "image": source, "model": str(settings.get("engine") or "auto")}
        for i, ref in enumerate(refs[:2], start=2):
            params[f"reference_image_{i}"] = ref
        steps = _int(settings.get("steps"))
        if steps:
            params["steps"] = steps
        return params, None
    if mode == "expand":
        pad = {side: _int(settings.get(side), 0, low=0, high=EXPAND_MAX_PX) for side in ("left", "right", "top", "bottom")}
        if not any(pad.values()):
            return None, ("Choose how far to extend at least one side.", 400)
        return {"image": source, "instruction": text, **{k: v - v % 8 for k, v in pad.items()}}, None
    if mode == "background":
        return {"image": source}, None
    return None, (f"Unknown mode '{mode}'.", 400)


@image_edit_bp.route("/run", methods=["POST"])
def run():
    """Start one edit as a tool job; answer with the job to poll."""
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "")
    tool_name = MODE_TOOLS.get(mode)
    if tool_name is None:
        return error_response(f"Unknown mode '{mode}'.", 400)
    source = _resolve(body.get("image"))
    if not source:
        return error_response("Add a photo first.", 400)
    refs = []
    for ref in (body.get("references") or [])[:2]:
        path = _resolve(ref)
        if not path:
            return error_response("A reference photo is no longer on this machine; add it again.", 400)
        refs.append(path)
    try:
        params, refusal = _params_for(mode, body, source, refs)
    except (TypeError, ValueError) as e:
        return error_response(f"A setting is not a number: {e}", 400)
    if refusal:
        message, status = refusal
        return error_response(message, status)

    registry = getattr(current_app, "tool_registry", None)
    if registry is None:
        from backend.tools import initialize_all_tools
        registry = initialize_all_tools()
    if registry is None or not registry.get_tool(tool_name):
        return error_response(f"The {tool_name} tool is not available on this install.", 503)

    from backend.services import tool_jobs
    from backend.services.tool_confirmation import trusted_caller

    def _run():
        # The person pressed the button on this page; that is the approval.
        with trusted_caller("rest:image_edit"):
            return registry.execute_tool(tool_name, **params)

    try:
        title = MODE_TITLES[mode]
        if mode == "identity" and params.get("others"):
            title = "Image Edit: scene for the faces"
        elif mode == "identity" and params.get("scene"):
            title = "Image Edit: putting the faces on"
        snapshot = tool_jobs.submit(tool_name, _run, for_mcp=False, title=title)
    except tool_jobs.ToolJobsBusy as e:
        return error_response(str(e), 429)
    logger.info("Image Edit page: %s started as %s", tool_name, snapshot["job_id"])
    return success_response(data={**snapshot, "mode": mode}, message="Started", status_code=202)
