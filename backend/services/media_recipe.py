# backend/services/media_recipe.py
# What a generated file was made with, read back from the records the
# generators already keep: the Document's metadata (output_registration
# register_file) and, for batch work, the batch's saved retry settings.
#
# A recipe is plain data for the Media page's details view:
#   kind      image | infographic | video | song | sound | file
#   media     image | video | audio | other (how to preview it)
#   prompt    what was asked for; rewritten_prompt when a rewrite replaced it
#   rows      [{key, value}] the settings, labelled by the page from `key`
#   regenerate {available, same_seed, reason}  one more, through its own queue
#   adjust    {path, query} the page that made it, opened pre-filled
#
# Which page owns which kind lives in ADJUST_PAGES, so a new generator is a
# row there and a reader below, not another branch in the page.

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ADJUST_PAGES = {
    "image": "/batch-images",
    "infographic": "/infographic",
    "video": "/video",
    "song": "/audio",
    "sound": "/audio",
}


def _meta_of(doc) -> Dict[str, Any]:
    raw = getattr(doc, "file_metadata", None)
    if not raw:
        return {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _row(rows: List[Dict[str, Any]], key: str, value: Any) -> None:
    if value is None or value == "" or value == []:
        return
    rows.append({"key": key, "value": value})


def _size(width: Any, height: Any) -> Optional[str]:
    return f"{width}x{height}" if width and height else None


def _image_batch(batch_id: Optional[str]):
    """The image batch's saved status (with per-image results), or None."""
    if not batch_id:
        return None
    try:
        from backend.services.batch_image_generator import get_batch_image_generator
        return get_batch_image_generator().find_batch_status(batch_id, include_results=False)
    except Exception as e:  # noqa: BLE001 — a recipe without its batch still shows the file's own record
        logger.debug("Image batch %s not readable: %s", batch_id, e)
        return None


def _base(doc, kind: str, media: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "media": media,
        "document_id": doc.id,
        "title": doc.filename,
        "url": f"/api/files/document/{doc.id}/download",
        "created_at": doc.uploaded_at.isoformat() if getattr(doc, "uploaded_at", None) else None,
        "batch_id": None,
        "item_id": None,
        "prompt": "",
        "rewritten_prompt": None,
        "rows": [],
        "regenerate": {"available": False, "same_seed": False, "reason": None},
        "adjust": None,
    }


def _media_of(filename: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp"):
        return "image"
    if ext in ("mp4", "webm", "mov", "mkv"):
        return "video"
    if ext in ("wav", "mp3", "flac", "ogg", "m4a"):
        return "audio"
    return "other"


# -- documents ---------------------------------------------------------------

def recipe_for_document(document_id: int) -> Optional[Dict[str, Any]]:
    """The recipe of a Library document, or None when there is no such document."""
    from backend.models import Document, db

    doc = db.session.get(Document, document_id)
    if doc is None:
        return None
    meta = _meta_of(doc)
    source = meta.get("source")
    if source == "infographic":
        return _infographic_recipe(doc, meta)
    if source == "batch_generation" and meta.get("batch_id") and _media_of(doc.filename) == "image":
        return _image_recipe(doc, meta)
    if _media_of(doc.filename) == "audio" and (meta.get("backend") or source == "music_generation"):
        return _audio_recipe(doc, meta)
    recipe = _base(doc, "file", _media_of(doc.filename))
    recipe["regenerate"]["reason"] = "no_settings"
    return recipe


def _image_recipe(doc, meta: Dict[str, Any]) -> Dict[str, Any]:
    recipe = _base(doc, "image", "image")
    batch_id = meta.get("batch_id")
    recipe["batch_id"] = batch_id
    recipe["prompt"] = meta.get("original_prompt") or ""
    recipe["rewritten_prompt"] = meta.get("rewritten_prompt")
    batch = _image_batch(batch_id)
    rd = (getattr(batch, "retry_data", None) or {}) if batch else {}
    params = rd.get("params") or {}
    cfg = params.get("ui_config") or {}

    rows = recipe["rows"]
    _row(rows, "model", meta.get("model_used") or params.get("model"))
    _row(rows, "seed", meta.get("seed_used"))
    _row(rows, "size", meta.get("dimensions"))
    _row(rows, "steps", meta.get("steps"))
    _row(rows, "guidance", meta.get("guidance"))
    _row(rows, "style", meta.get("style"))
    _row(rows, "negative_prompt", cfg.get("negativePrompt") or None)
    preset = cfg.get("selectedPreset") or params.get("content_preset")
    _row(rows, "preset", None if preset in (None, "auto") else preset)
    if cfg.get("castSubjectIds"):
        _row(rows, "cast", len(cfg["castSubjectIds"]))
    if cfg.get("directorEnabled") or params.get("director_mode"):
        _row(rows, "director", True)
    _row(rows, "batch", batch_id)

    if rd:
        recipe["regenerate"] = {"available": True, "same_seed": meta.get("seed_used") is not None, "reason": None}
    else:
        recipe["regenerate"]["reason"] = "no_batch_settings"
    recipe["adjust"] = {"path": ADJUST_PAGES["image"], "query": {"retry": batch_id, "doc": doc.id}} if rd else None
    return recipe


def _infographic_recipe(doc, meta: Dict[str, Any]) -> Dict[str, Any]:
    recipe = _base(doc, "infographic", "image")
    batch_id = meta.get("batch_id")
    spec = meta.get("infographic") or {}
    recipe["batch_id"] = batch_id
    recipe["prompt"] = meta.get("original_prompt") or meta.get("prompt") or ""
    batch = _image_batch(batch_id) if batch_id else None
    rd = (getattr(batch, "retry_data", None) or {}) if batch else {}
    model = (rd.get("params") or {}).get("model") or meta.get("model")
    seed = meta.get("seed_used", meta.get("seed"))

    rows = recipe["rows"]
    _row(rows, "model", _infographic_model_name(model))
    _row(rows, "seed", seed)
    _row(rows, "size", meta.get("dimensions") or _size(meta.get("width"), meta.get("height")))
    _row(rows, "steps", meta.get("steps"))
    _row(rows, "style", spec.get("style"))
    _row(rows, "aspect", spec.get("aspect"))
    _row(rows, "title", spec.get("title"))
    _row(rows, "callouts", spec.get("callouts"))
    _row(rows, "footer", spec.get("footer"))
    _row(rows, "hashtags", [f"#{h}" for h in spec.get("hashtags") or []])
    _row(rows, "batch", batch_id)

    if model and recipe["prompt"]:
        recipe["regenerate"] = {"available": True, "same_seed": seed is not None, "reason": None}
    else:
        recipe["regenerate"]["reason"] = "no_settings"
    query = {"retry": batch_id, "doc": doc.id} if batch_id and rd else {"doc": doc.id}
    recipe["adjust"] = {"path": ADJUST_PAGES["infographic"], "query": query}
    return recipe


def _infographic_model_name(model_id: Optional[str]) -> Optional[str]:
    if not model_id:
        return None
    try:
        from backend.services.infographic_generator import get_infographic_generator
        names = {row["id"]: row["name"] for row in get_infographic_generator().models()}
        return names.get(model_id, model_id)
    except Exception:  # noqa: BLE001 — the id still says which model
        return model_id


def _audio_recipe(doc, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Songs and sound effects; filled in with their regenerate path in song_recipe."""
    from backend.services.song_recipe import audio_recipe
    return audio_recipe(_base(doc, "song", "audio"), meta)


# -- video clips ---------------------------------------------------------------

def recipe_for_clip(batch_id: str, item_id: str) -> Optional[Dict[str, Any]]:
    """The recipe of one clip of a video batch, or None when it is unknown."""
    from backend.services.batch_video_generator import get_batch_video_generator

    gen = get_batch_video_generator()
    status = gen.get_batch_status(batch_id)
    if status is None:
        return None
    result = next((r for r in status.results or [] if r.item_id == item_id), None)
    if result is None:
        return None
    rd = status.retry_data or {}
    params = rd.get("params") or {}
    ids = list(rd.get("item_ids") or [])
    index = ids.index(item_id) if item_id in ids else None
    meta = result.metadata or {}

    if rd.get("mode") == "image":
        prompt = rd.get("prompt") or ""
        images = rd.get("image_paths") or []
        start_image = images[index] if index is not None and index < len(images) else None
    else:
        prompts = rd.get("prompts") or []
        prompt = prompts[index] if index is not None and index < len(prompts) else ""
        start_image = None

    frames = (meta.get("quality") or {}).get("frames") or {}
    recipe = {
        "kind": "video",
        "media": "video",
        "document_id": None,
        "title": (status.metadata or {}).get("display_name") or batch_id,
        "url": None,  # the page builds it from the clip's path, as the Videos section does
        "created_at": status.start_time.isoformat() if status.start_time else None,
        "batch_id": batch_id,
        "item_id": item_id,
        "prompt": prompt,
        "rewritten_prompt": meta.get("director_prompt") or meta.get("enhanced_prompt"),
        "rows": [],
        "regenerate": {"available": False, "same_seed": False, "reason": None},
        "adjust": None,
    }
    rows = recipe["rows"]
    seed = meta.get("seed", params.get("seed"))
    _row(rows, "model", _video_model_name(params.get("model")))
    _row(rows, "seed", seed)
    _row(rows, "size", _size(frames.get("width"), frames.get("height")) or _size(params.get("width"), params.get("height")))
    _row(rows, "frames", params.get("duration_frames"))
    _row(rows, "fps", params.get("fps"))
    _row(rows, "duration_s", frames.get("duration_s"))
    _row(rows, "steps", params.get("num_inference_steps"))
    _row(rows, "guidance", params.get("guidance_scale"))
    _row(rows, "speed_profile", params.get("speed_profile"))
    _row(rows, "prompt_style", params.get("prompt_style"))
    _row(rows, "negative_prompt", params.get("negative_prompt"))
    _row(rows, "lora", params.get("lora_name"))
    _row(rows, "start_image", start_image.rsplit("/", 1)[-1] if start_image else None)
    _row(rows, "batch", batch_id)

    if index is not None and rd.get("mode") in ("text", "image"):
        recipe["regenerate"] = {"available": True, "same_seed": seed is not None, "reason": None}
    else:
        recipe["regenerate"]["reason"] = "no_batch_settings"
    if rd:
        recipe["adjust"] = {"path": ADJUST_PAGES["video"], "query": {"retry": batch_id, "clip": item_id}}
    return recipe


def _video_model_name(model_id: Optional[str]) -> Optional[str]:
    if not model_id:
        return None
    try:
        from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
        return (VIDEO_MODEL_REGISTRY.get(model_id) or {}).get("name") or model_id
    except Exception:  # noqa: BLE001 — the id still says which model
        return model_id
