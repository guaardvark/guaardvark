# backend/api/media_recipe_api.py
# The Media page's details view: what a generated file was made with, and
# making it again.
#
#   GET  /api/media/recipe/<document_id>
#       The recipe of a Library document (backend/services/media_recipe.py).
#
#   GET  /api/media/clip-recipe/<batch_id>/<item_id>
#       The recipe of one clip of a video batch (the Videos section lists
#       batches, not documents).
#
#   GET  /api/media/audio?limit=60
#       The newest songs and sound effects, for the Media page's Audio section.
#
#   POST /api/media/regenerate
#       body: {document_id} or {batch_id, item_id} for a clip, plus
#             same_seed (default false: a new seed, so a new take)
#       Queues one more of that item through its own queue, with the
#       settings it was made with, and returns {queue, batch_id | job_id}.

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

media_recipe_bp = Blueprint("media_recipe_api", __name__, url_prefix="/api/media")


class RegenerateError(ValueError):
    """Why an item cannot be made again; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@media_recipe_bp.route("/recipe/<int:document_id>", methods=["GET"])
def document_recipe(document_id: int):
    from backend.services.media_recipe import recipe_for_document

    recipe = recipe_for_document(document_id)
    if recipe is None:
        return jsonify({"success": False, "error": "No such file."}), 404
    return jsonify({"success": True, "recipe": recipe})


@media_recipe_bp.route("/clip-recipe/<batch_id>/<item_id>", methods=["GET"])
def clip_recipe(batch_id: str, item_id: str):
    from backend.services.media_recipe import recipe_for_clip

    recipe = recipe_for_clip(batch_id, item_id)
    if recipe is None:
        return jsonify({"success": False, "error": "No such clip."}), 404
    return jsonify({"success": True, "recipe": recipe})


@media_recipe_bp.route("/audio", methods=["GET"])
def audio_library():
    from backend.services.media_recipe import list_audio

    try:
        limit = max(1, min(200, int(request.args.get("limit", 60))))
    except (TypeError, ValueError):
        limit = 60
    return jsonify({"success": True, "items": list_audio(limit)})


@media_recipe_bp.route("/regenerate", methods=["POST"])
def regenerate():
    body = request.get_json(silent=True) or {}
    same_seed = bool(body.get("same_seed"))
    try:
        if body.get("document_id") is not None:
            queued = _regenerate_document(int(body["document_id"]), same_seed)
        elif body.get("batch_id") and body.get("item_id"):
            queued = _regenerate_clip(str(body["batch_id"]), str(body["item_id"]), same_seed)
        else:
            raise RegenerateError("Name a document_id, or a batch_id and item_id.")
    except RegenerateError as e:
        return jsonify({"success": False, "error": str(e)}), e.status
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("Regenerate failed")
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, **queued}), 201


# -- per kind ----------------------------------------------------------------

def _regenerate_document(document_id: int, same_seed: bool) -> Dict[str, Any]:
    from backend.models import Document, db
    from backend.services.media_recipe import _meta_of, recipe_for_document

    recipe = recipe_for_document(document_id)
    if recipe is None:
        raise RegenerateError("No such file.", 404)
    if not recipe["regenerate"]["available"]:
        raise RegenerateError("This file did not keep the settings it was made with.")
    meta = _meta_of(db.session.get(Document, document_id))
    if recipe["kind"] == "image":
        return _regenerate_image(meta, same_seed)
    if recipe["kind"] == "infographic":
        return _regenerate_infographic(meta, same_seed)
    if recipe["kind"] in ("song", "sound"):
        return _regenerate_audio(meta, same_seed)
    raise RegenerateError("This kind of file cannot be made again here.")


def _regenerate_audio(meta: Dict[str, Any], same_seed: bool) -> Dict[str, Any]:
    """Through Audio Studio's own request handling (audio_foundry_api), so
    the model checks and the plugin start are the page's."""
    from backend.api.audio_foundry_api import start_fx, start_music
    from backend.services.song_recipe import audio_regenerate_request

    route, payload = audio_regenerate_request(meta, same_seed)
    response, status = (start_fx if route == "fx" else start_music)(payload)
    body = response.get_json(silent=True) or {}
    if status >= 400 or body.get("success") is False:
        error = body.get("error") or body.get("detail") or body.get("message") or f"HTTP {status}"
        raise RegenerateError(str(error), status if status >= 400 else 400)
    return {"queue": "song", "job_id": body.get("job_id"), "page": "/audio",
            "document_id": (body.get("result") or {}).get("document_id") or body.get("document_id")}


def _image_batch_settings(batch_id: Optional[str]) -> Dict[str, Any]:
    from backend.services.batch_image_generator import get_batch_image_generator

    batch = get_batch_image_generator().find_batch_status(batch_id) if batch_id else None
    rd = getattr(batch, "retry_data", None) if batch else None
    if not rd:
        raise RegenerateError("The batch this came from did not keep its settings.")
    return rd


def image_regenerate_body(meta: Dict[str, Any], rd: Dict[str, Any], same_seed: bool) -> Dict[str, Any]:
    """The Image Gen request that makes this one image again: the page's own
    form as it was sent (its ui_config snapshot) with this image's prompt, or
    the batch's core settings for a batch from before snapshots."""
    params = rd.get("params") or {}
    cfg = params.get("ui_config") or {}
    typed = meta.get("original_prompt") or ""
    rewritten = meta.get("rewritten_prompt")
    body: Dict[str, Any] = {}
    if cfg:
        body.update(cfg.get("params") or {})
        body["negative_prompt"] = cfg.get("negativePrompt") or ""
        body["subject_ids"] = cfg.get("castSubjectIds") or []
        preset = cfg.get("selectedPreset")
        body["content_preset"] = None if preset in (None, "auto") else preset
        for key, flag in (("auto_enhance", "autoEnhance"), ("enhance_anatomy", "enhanceAnatomy"),
                          ("enhance_faces", "enhanceFaces"), ("enhance_hands", "enhanceHands")):
            if isinstance(cfg.get(flag), bool):
                body[key] = cfg[flag]
        body["director_mode"] = bool(cfg.get("directorEnabled"))
        if cfg.get("directorGuidance"):
            body["director_guidance"] = cfg["directorGuidance"]
        if cfg.get("selectedAdapters"):
            body["adapters"] = [{"id": a} for a in cfg["selectedAdapters"]]
    else:
        for key in ("model", "style", "width", "height", "steps", "guidance", "content_preset",
                    "auto_enhance", "restore_faces", "remove_background"):
            if params.get(key) is not None:
                body[key] = params[key]
    if rewritten:
        # The text the model drew from; a second rewrite would ask for another picture.
        body["director_mode"] = False
        body["auto_enhance"] = False
    body["prompts"] = [rewritten or typed]
    seed = meta.get("seed_used") if same_seed else None
    body.pop("seed", None)
    if seed is not None:
        body["seed"] = seed
    if cfg:
        # The filed prompt carries Look & Feel, which the form keeps in its own field.
        look = (cfg.get("lookAndFeel") or "").strip()
        form_prompt = typed[: -(len(look) + 2)] if look and typed.endswith(f", {look}") else typed
        body["ui_config"] = {**cfg, "inputMode": "single", "batchItems": form_prompt or rewritten or "",
                             "quantity": 1, "seedText": str(seed) if seed is not None else ""}
    return body


def _regenerate_image(meta: Dict[str, Any], same_seed: bool) -> Dict[str, Any]:
    from backend.api.batch_image_generation_api import BatchRequestError, queue_prompts_batch

    rd = _image_batch_settings(meta.get("batch_id"))
    try:
        data = queue_prompts_batch(image_regenerate_body(meta, rd, same_seed))
    except BatchRequestError as e:
        raise RegenerateError(str(e), e.status)
    return {"queue": "image", "batch_id": data["batch_id"], "page": "/batch-images"}


def _regenerate_infographic(meta: Dict[str, Any], same_seed: bool) -> Dict[str, Any]:
    from backend.services.infographic_generator import (
        ASPECT_DIMS,
        InfographicSpec,
        get_infographic_generator,
    )

    batch_id = meta.get("batch_id")
    rd: Dict[str, Any] = {}
    if batch_id:
        rd = _image_batch_settings(batch_id)
    spec_fields = dict(meta.get("infographic") or {})
    if not spec_fields:
        # A single render from before specs were filed: its prompt as written.
        width, height = meta.get("width"), meta.get("height")
        ratio = (width / height) if width and height else 16 / 9
        aspect = min(ASPECT_DIMS, key=lambda a: abs(ASPECT_DIMS[a][0] / ASPECT_DIMS[a][1] - ratio))
        spec_fields = {"raw_prompt": meta.get("prompt") or "", "aspect": aspect}
    spec = InfographicSpec(**{k: v for k, v in spec_fields.items() if k in InfographicSpec.__dataclass_fields__})
    params = rd.get("params") or {}
    model = params.get("model") or meta.get("model")
    seed = meta.get("seed_used", meta.get("seed")) if same_seed else None
    cfg = params.get("ui_config")
    ui_config = ({**cfg, "quantity": 1, "seedText": str(seed) if seed is not None else ""}
                 if isinstance(cfg, dict) else None)
    queued = get_infographic_generator().queue_batch(
        [spec], model=model, quantity=1, seed=seed, ui_config=ui_config,
    )
    return {"queue": "infographic", "batch_id": queued["batch_id"], "page": "/infographic"}


def _regenerate_clip(batch_id: str, item_id: str, same_seed: bool) -> Dict[str, Any]:
    from backend.api.batch_video_generation_api import _saved_batch_preflight
    from backend.services.batch_video_generator import get_batch_video_generator
    from backend.services.media_recipe import recipe_for_clip

    recipe = recipe_for_clip(batch_id, item_id)
    if recipe is None:
        raise RegenerateError("No such clip.", 404)
    if not recipe["regenerate"]["available"]:
        raise RegenerateError("This clip's batch did not keep the settings to make it again.")
    gen = get_batch_video_generator()
    status = gen.get_batch_status(batch_id)
    saved = dict((status.retry_data or {}).get("params") or {})
    refused = _saved_batch_preflight(saved) if saved else None
    if refused:
        response, code = refused if isinstance(refused, tuple) else (refused, 400)
        payload = response.get_json(silent=True) or {}
        raise RegenerateError(payload.get("message") or "The video model is not ready.", code)
    seed = None
    if same_seed:
        seed = next((row["value"] for row in recipe["rows"] if row["key"] == "seed"), None)
    new_status = gen.rerender_item(batch_id, item_id, seed=seed, mark_review=False)
    if new_status is None:
        raise RegenerateError("No such clip.", 404)
    return {"queue": "video", "batch_id": new_status.batch_id, "page": "/video"}
