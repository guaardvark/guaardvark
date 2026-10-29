# backend/api/batch_image_generation_api.py
# Batch Image Generation API - RESTful endpoints for mass image generation
# Integrates with unified progress system and task management

import csv
import io
import json
import logging
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from flask import Blueprint, current_app, jsonify, request, send_file, Response, stream_with_context
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename
from datetime import datetime
import time
import threading

from backend.services.job_types import batch_failure, describe_failure

logger = logging.getLogger(__name__)

# Import dependencies with fallback handling
try:
    from backend.services.batch_image_generator import (
        get_batch_image_generator,
        BatchImageRequest,
        BatchPrompt,
        BLUEPRINT_MAX_ROWS,
        start_batch_from_csv,
        start_batch_from_prompts,
        get_batch_status,
        cancel_batch,
        SEED_SPACE,
    )
    from backend.utils.response_utils import success_response, error_response
    from backend.utils.unified_progress_system import ProcessType
    service_available = True
except ImportError as e:
    logger.error(f"Failed to import batch generation dependencies: {e}")
    service_available = False
    SEED_SPACE = 2 ** 32
    # Fallback functions
    def error_response(message, status_code=500, error_code=None, data=None, details=None):
        return {"error": message, "status": status_code}

    def success_response(data=None, message="Operation completed successfully", status_code=200):
        return {"success": True, "data": data, "message": message, "status": status_code}

# Import optional services (don't break main service if unavailable)
try:
    from backend.services.settings_validator import get_settings_validator
    settings_validator_available = True
except ImportError as e:
    logger.warning(f"Settings validator not available: {e}")
    settings_validator_available = False
    get_settings_validator = None

try:
    from backend.services.model_recommender import get_model_recommender
    model_recommender_available = True
except ImportError as e:
    logger.warning(f"Model recommender not available: {e}")
    model_recommender_available = False
    get_model_recommender = None

batch_image_bp = Blueprint("batch_image", __name__, url_prefix="/api/batch-image")

# Global variables for tracking model download status (parity with video, issue #36).
_DOWNLOAD_STALL_SECONDS = 180
_IMAGE_DOWNLOAD_EPOCH = 0
model_download_lock = threading.Lock()


def _image_download_status_path() -> Path:
    return Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "image_model_download_status.json"


def _idle_image_download_status() -> dict:
    return {
        "is_downloading": False,
        "current_model": None,
        "progress": 0,
        "status": "idle",
        "error": None,
        "speed_mbps": 0,
        "downloaded_gb": 0,
        "total_gb": 0,
        "updated_at": time.time(),
        "epoch": _IMAGE_DOWNLOAD_EPOCH,
    }


def _persist_image_download_status() -> None:
    try:
        p = _image_download_status_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(model_download_status), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def _dir_bytes(d: Path) -> int:
    """Bytes currently under dest, including hf_hub_download .incomplete staging."""
    total = 0
    if d.exists():
        for f in d.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    return total


def _hf_cache_repo_dir(repo_id: str) -> Optional[Path]:
    """The Hugging Face hub cache folder for a repo id, or None for non-repo ids.

    SD and SDXL installs go through from_pretrained, which fills this folder and
    only writes the install folder at the end (save_pretrained).
    """
    if not repo_id or "/" not in repo_id or repo_id.startswith("comfy:"):
        return None
    try:
        from huggingface_hub import constants
        return Path(constants.HF_HUB_CACHE) / ("models--" + repo_id.replace("/", "--"))
    except Exception:
        return None


def _image_download_dest(img, catalog_key: str, hf_model_id: str) -> Path:
    """Directory whose growth is the download — Comfy unet/loras, or the snapshot path."""
    from backend.services.user_image_models import user_download_dir

    dest = user_download_dir(img, catalog_key)
    if dest is not None:
        return dest
    return img._get_model_path(hf_model_id)


def _image_download_watch_dirs(img, catalog_key: str, hf_model_id: str) -> List[Path]:
    """Every directory the download writes: the model's own, plus the companion
    entries a user ComfyUI model installs beside it (text_encoders/, vae/)."""
    dirs = [_image_download_dest(img, catalog_key, hf_model_id)]
    try:
        from backend.services.user_image_models import companion_dirs
        for d in companion_dirs(img, catalog_key):
            if d not in dirs:
                dirs.append(d)
    except Exception as e:  # noqa: BLE001 — progress only; the download itself decides
        logger.debug("companion watch dirs for %s skipped: %s", catalog_key, e)
    return dirs


def _reconcile_image_download_status_on_load() -> None:
    global model_download_status, _IMAGE_DOWNLOAD_EPOCH
    try:
        p = _image_download_status_path()
        persisted = json.loads(p.read_text()) if p.exists() else None
    except Exception:
        persisted = None
    if not persisted:
        model_download_status = _idle_image_download_status()
        return
    _IMAGE_DOWNLOAD_EPOCH = int(persisted.get("epoch", 0))
    if persisted.get("is_downloading") or persisted.get("status") in ("starting", "downloading"):
        persisted.update({
            "is_downloading": False,
            "status": "failed",
            "error": "Download was interrupted by a backend restart. Click Install to retry.",
            "progress": 0,
            "updated_at": time.time(),
        })
        model_download_status = persisted
        _persist_image_download_status()
    else:
        # A finished run was already reported to the person who started it;
        # carrying "completed" across restarts makes the modal announce it
        # again every time it opens. Keep the epoch, start idle.
        model_download_status = _idle_image_download_status()


model_download_status = _idle_image_download_status()
_reconcile_image_download_status_on_load()
# The image-editing pack whose Install this modal handed to the video
# downloader; the status route mirrors that run under the pack's id.
_DELEGATED_PACK: str | None = None

# Approximate model sizes in GB (HuggingFace repo total). Curated set only —
# matches offline_image_generator.available_models after the 2026-05-29 cull.
IMAGE_MODEL_SIZES = {
    "Tongyi-MAI/Z-Image-Turbo": 16.0,
    "krea/Krea-2-Turbo": 28.0,
    "krea/Krea-2-Raw": 28.0,
    "krea2-turbo": 28.0,
    "krea2-raw": 28.0,
    "stabilityai/stable-diffusion-xl-base-1.0": 6.9,
    "stabilityai/sdxl-turbo": 6.9,
    "SG161222/Realistic_Vision_V5.1_noVAE": 2.1,
    "emilianJR/epiCRealism": 2.1,
    "Efficient-Large-Model/Sana_Sprint_1.6B_1024px_diffusers": 9.7,
    "Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers": 7.7,
}

def _catalog_size_gb(path: str) -> float:
    """Download size of a catalog row. A ``comfy:`` key is the sum of its registry
    install plan (model + companions); anything else comes from IMAGE_MODEL_SIZES."""
    if path.startswith("comfy:"):
        from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, install_plan
        total = sum(float(VIDEO_MODEL_REGISTRY.get(e, {}).get("size_gb") or 0)
                    for e in install_plan(path.split(":", 1)[1]))
        if total:
            return round(total, 2)
    return IMAGE_MODEL_SIZES.get(path, 2.5)


# Why a model is missing from the picker, phrased for the person reading it.
_AVAILABILITY_REASONS = {
    "needs_licence": (
        "Gated on Hugging Face — open the model page and click "
        "'Agree and access repository' with the account your HF_TOKEN belongs to."
    ),
    "needs_token": (
        "Gated on Hugging Face — set HF_TOKEN in .env and restart the backend."
    ),
    "unreachable": (
        "Not downloaded and the source could not be reached. Check network access, "
        "or install the weights manually."
    ),
}

def _resolve_catalog_model(model_ref: str):
    """Map a catalog key (krea2-turbo, krea2-raw) or HF repo id to (key, hf_id)."""
    try:
        from backend.services.offline_image_generator import get_image_generator
        catalog = get_image_generator().available_models
    except Exception:
        return None, None
    if model_ref in catalog:
        return model_ref, catalog[model_ref]
    for key, hf_id in catalog.items():
        if model_ref == hf_id:
            return key, hf_id
    return None, None

CSV_UPLOAD_MAX_BYTES = 5 * 1024 * 1024

# Upper bound for /generate/prompts. The page allows up to 100 copies per
# prompt (utils/batchImageSettings.MAX_QUANTITY) across many lines; this stops
# a stray client from queueing a batch that would take days on one GPU.
PROMPT_BATCH_MAX_ITEMS = 2000


def _validate_csv_upload(file):
    """Validate uploaded CSV file."""
    if not file or file.filename == '':
        return False, "No file provided"

    if not file.filename.lower().endswith('.csv'):
        return False, "File must be a CSV file"

    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning

    if file_size > CSV_UPLOAD_MAX_BYTES:
        return False, "File too large (max 5MB)"

    return True, "Valid"


def _load_batch_status(generator, batch_id: str, *, include_results: bool = False):
    """Batch status from memory or disk; None when the batch does not exist."""
    return generator.find_batch_status(batch_id, include_results=include_results)


def _progress_percentage(status) -> int:
    """Share of the batch that has finished, successfully or not.

    Counting only successes left a batch with any failed image stuck below 100%
    while the queue panel (completed + failed) said it was done.
    """
    total = int(getattr(status, "total_images", 0) or 0)
    if total <= 0:
        return 0
    done = int(getattr(status, "completed_images", 0) or 0) + int(getattr(status, "failed_images", 0) or 0)
    return max(0, min(100, int((done / total) * 100)))


def _thumbnail_candidates(image_name: str) -> List[str]:
    """Thumbnail filenames a batch image may have.

    BatchImageGenerator writes thumbnails as ``<stem>.jpg`` regardless of the
    image's extension; the upload route keeps the original name. Try both.
    """
    stem_jpg = Path(image_name).with_suffix(".jpg").name
    return [stem_jpg] if stem_jpg == image_name else [stem_jpg, image_name]


def _sync_active_batch(generator, batch_id: str, mutate) -> None:
    """Apply ``mutate(status)`` to the in-memory status, if this process has one.

    Mutating endpoints rewrite batch_metadata.json, but /status and /list
    prefer the in-memory copy in ``active_batches`` (never pruned), so a rename
    or delete stayed invisible until restart. Keep the two in step.
    """
    with generator.batch_lock:
        status = generator.active_batches.get(batch_id)
        if status is None:
            return
        try:
            mutate(status)
        except Exception as e:
            logger.warning(f"Failed to sync in-memory status for batch {batch_id}: {e}")

def _resolve_subject_ids_from_prompts(prompts: Any) -> list[int]:
    """Match trigger tokens / [brackets] / cast names in prompt text to trained Subjects.

    Used when the caller (Discord bot, bare API) omits explicit ``subject_ids``.
    Reuses the same helper as the chat ``generate_image`` tool.
    """
    if not prompts:
        return []
    try:
        from backend.tools.image_tools import _resolve_cast_from_prompt
    except Exception as e:
        logger.warning("Cast-from-prompt helper unavailable: %s", e)
        return []

    found: list[int] = []
    seen: set[int] = set()
    for item in prompts if isinstance(prompts, list) else [prompts]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("prompt") or ""
        else:
            continue
        for sid in _resolve_cast_from_prompt(text or ""):
            if sid not in seen:
                seen.add(sid)
                found.append(sid)
    return found


def _apply_character_casting(data: Dict[str, Any], params: Dict[str, Any]) -> None:
    """Resolve cast `subject_ids` for batch stills.

    Trained subjects (with ``lora_path``) are stored as ``subject_ids`` so
    ``render_character_still`` can apply identity core (trigger + class + marks)
    and family routing. Also keeps ``loras`` for diagnostics / legacy.

    When ``subject_ids`` is omitted, auto-resolves from prompt text (trigger words,
    ``[bracket]`` tokens, cast names) so Discord / bare callers load LoRAs the same
    way the chat ``generate_image`` tool does.

    Raises ValueError if every selected id is untrained or missing.
    """
    subject_ids = data.get("subject_ids") or []
    auto_resolved = False
    if not subject_ids:
        subject_ids = _resolve_subject_ids_from_prompts(data.get("prompts"))
        auto_resolved = bool(subject_ids)
        if auto_resolved:
            logger.info(
                "Auto-resolved cast subject_ids from prompts: %s", subject_ids
            )
    if not subject_ids:
        return
    from backend.models import Subject, db

    trained_ids: list[int] = []
    trained_subjects: list = []
    loras: list[str] = []
    untrained: list[str] = []
    missing: list[str] = []
    for raw in subject_ids:
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            missing.append(str(raw))
            continue
        try:
            s = db.session.get(Subject, sid)
        except Exception:
            s = None
        if s is None:
            missing.append(str(sid))
            continue
        if not getattr(s, "lora_path", None):
            untrained.append(getattr(s, "name", None) or str(sid))
            continue
        trained_ids.append(sid)
        trained_subjects.append(s)
        loras.append(s.lora_path)

    if not trained_ids:
        parts = []
        if untrained:
            parts.append(
                "not trained (no LoRA): " + ", ".join(untrained)
            )
        if missing:
            parts.append("not found: " + ", ".join(missing))
        raise ValueError(
            "Character cast failed — " + ("; ".join(parts) or "no valid subject_ids")
        )

    if trained_subjects:
        # A member holding LoRAs for several bases renders with the one for the
        # picked model; a member with none for it is refused here, before queueing.
        from backend.services.cast_lora_selection import (
            CastLoraRefusal,
            select_cast_loras,
            selected_lora_paths,
        )
        try:
            selection = select_cast_loras(trained_subjects, params.get("model"))
        except CastLoraRefusal as e:
            raise ValueError(str(e)) from e
        if not selection.legacy:
            loras = selected_lora_paths(selection)

    params["subject_ids"] = trained_ids
    params["loras"] = loras
    if auto_resolved:
        params.setdefault("_cast_warnings", []).append(
            f"auto-resolved cast from prompt: {trained_ids}"
        )
    if untrained or missing:
        # Partial cast: proceed with trained only, surface warning via params.
        warn = []
        if untrained:
            warn.append(f"skipped untrained: {', '.join(untrained)}")
        if missing:
            warn.append(f"skipped missing: {', '.join(missing)}")
        params.setdefault("_cast_warnings", []).extend(warn)
        logger.warning("Character casting partial: %s", "; ".join(warn))


_FALSE_STRINGS = frozenset({"false", "0", "no", "off", ""})


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce a JSON or form-field value to bool.

    The CSV route reads ``request.form``, where every value is a string, and
    ``bool("false")`` is True — so the toggles could never be turned off there.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in _FALSE_STRINGS


class UnknownImageModel(ValueError):
    """The request names an image model the catalog does not have."""


def unknown_image_model_message(model: Any) -> Optional[str]:
    """The refusal for a model name the image catalog does not have, else None.

    The catalog is offline_image_generator.available_models (user-added models
    included). 'auto', or no name, is the router's pick and always allowed.
    When the catalog cannot be read the name is left for the generator to judge.
    """
    name = str(model or "").strip()
    if not name or name == "auto":
        return None
    try:
        from backend.services.offline_image_generator import get_image_generator
        generator = get_image_generator()
        catalog = generator.available_models
        hidden = set(getattr(generator, "hidden_models", None) or ())
    except Exception as e:  # noqa: BLE001 — no catalog to check against
        logger.warning("image model catalog unavailable, '%s' not checked: %s", name, e)
        return None
    if name in catalog:
        return None
    known = ", ".join(sorted(k for k in catalog if k not in hidden))
    return f"Unknown image model '{name}'. Known: auto, {known}."


def _parse_generation_params(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parse and validate generation parameters.
    
    Returns:
        Tuple of (params, validation_info) where validation_info contains warnings and recommendations
    """
    params: Dict[str, Any] = {}
    validation_info: Dict[str, Any] = {
        "warnings": [],
        "errors": [],
        "recommendations": [],
        "corrected_values": {}
    }

    # Generation settings — absolute safety bounds only (quality sliders own the rest).
    params['max_workers'] = min(max(int(data.get('max_workers', 2)), 1), 4)  # 1-4 workers
    params['preserve_order'] = _as_bool(data.get('preserve_order'), True)
    params['generate_thumbnails'] = _as_bool(data.get('generate_thumbnails'), True)
    params['save_metadata'] = _as_bool(data.get('save_metadata'), True)
    # A typed seed: the first image renders with it, each next image with the
    # next number (create_batch_from_prompts). Blank keeps a seed per image.
    raw_seed = data.get('seed')
    if raw_seed not in (None, ""):
        try:
            seed = int(str(raw_seed).strip())
        except (TypeError, ValueError):
            seed = -1
        if 0 <= seed < SEED_SPACE:
            params['seed'] = seed
        else:
            validation_info["warnings"].append(f"Seed {raw_seed!r} is not a whole number from 0 to {SEED_SPACE - 1}; ignored.")
    if 'ui_config' in data:
        params['ui_config'] = data['ui_config']

    # Model selection — a name the catalog does not have is refused, never swapped
    # for 'auto': the reply would name a model that is not the one rendering.
    refusal = unknown_image_model_message(data.get('model'))
    if refusal:
        raise UnknownImageModel(refusal)
    params['model'] = str(data.get('model') or 'auto').strip() or 'auto'

    # Default image parameters — family-aware (stills_defaults), not SD-era 512/20/7.5
    from backend.services.stills_defaults import resolve_stills_defaults
    params['style'] = data.get('style', 'realistic')
    params['negative_prompt'] = str(data.get('negative_prompt') or '').strip()
    _raw_w = data.get('width', None)
    _raw_h = data.get('height', None)
    _raw_steps = data.get('steps', None)
    params['steps_explicit'] = _as_bool(data.get('steps_explicit'), False)
    _raw_g = data.get('guidance', None)
    _resolved = resolve_stills_defaults(
        params['model'],
        width=int(_raw_w) if _raw_w is not None else None,
        height=int(_raw_h) if _raw_h is not None else None,
        steps=int(_raw_steps) if _raw_steps is not None else None,
        steps_explicit=params['steps_explicit'],
        guidance=float(_raw_g) if _raw_g is not None else None,
        replace_legacy_sd_markers=True,
    )
    params['width'] = int(_resolved['width'])
    params['height'] = int(_resolved['height'])
    # Absolute bounds 1-100 hold for every value; the floor and the model clamps
    # are what a typed value bypasses.
    params['steps'] = min(max(int(_resolved['steps']), 1), 100)
    params['steps_requested'] = _resolved['steps_requested']
    params['steps_notice'] = _resolved['steps_notice']

    # Guidance scale - will be validated by SettingsValidator
    guidance = float(_resolved['guidance'])

    # Use SettingsValidator for comprehensive validation. 'auto' is resolved to a
    # concrete model by the generator's router, so validating it here would apply
    # the validator's unknown-model (SD 1.5) rules to a Z-Image/FLUX render.
    if service_available and settings_validator_available and get_settings_validator and params['model'] != 'auto':
        try:
            validator = get_settings_validator()
            validation_result = validator.validate_settings(
                model=params['model'],
                guidance=guidance,
                steps=params['steps'],
                width=params['width'],
                height=params['height'],
                auto_correct=True
            )

            # Start from user guidance; corrected_values may override hard clamps only.
            params['guidance'] = guidance
            if validation_result.corrected_values:
                corrections = dict(validation_result.corrected_values)
                if params['steps_explicit']:
                    corrections.pop("steps", None)
                params.update(corrections)
                validation_info["corrected_values"] = corrections

            # Collect warnings and recommendations
            validation_info["warnings"] = validation_result.warnings
            validation_info["errors"] = validation_result.errors
            validation_info["recommendations"] = validation_result.recommendations

            # Log warnings
            for warning in validation_result.warnings:
                logger.warning(f"Settings validation warning: {warning}")

            # Log errors
            for error in validation_result.errors:
                logger.error(f"Settings validation error: {error}")

            # FLUX (and any model with force_max_workers): serialize on the GPU.
            model_info = validator.get_model_info(params['model'])
            force_w = model_info.get("force_max_workers")
            if force_w is not None:
                if params['max_workers'] > int(force_w):
                    validation_info["warnings"].append(
                        f"{params['model']}: max_workers reduced {params['max_workers']}→{force_w} (VRAM safety)"
                    )
                params['max_workers'] = min(params['max_workers'], int(force_w))

        except Exception as e:
            logger.warning(f"Settings validation failed, using fallback: {e}")
            # Fallback: only hard-correct known SDXL black-image band
            is_sdxl = 'xl' in params['model'].lower() and 'turbo' not in params['model'].lower()
            if is_sdxl:
                if guidance > 9.0:
                    logger.warning(f"Guidance {guidance} too high for SDXL, auto-correcting to 7.5")
                    guidance = 7.5
                    params['guidance'] = guidance
                    validation_info["warnings"].append("Guidance auto-corrected to 7.5 for SDXL")
                elif guidance < 1.0:
                    params['guidance'] = 6.0
                else:
                    params['guidance'] = guidance
            else:
                params['guidance'] = min(max(guidance, 0.0), 30.0)
            if 'flux' in params['model'].lower():
                params['max_workers'] = 1
    else:
        # Fallback validation if service not available
        is_sdxl = 'xl' in params['model'].lower() and 'turbo' not in params['model'].lower()
        if is_sdxl:
            if guidance > 9.0:
                logger.warning(f"Guidance {guidance} too high for SDXL, auto-correcting to 7.5")
                guidance = 7.5
                params['guidance'] = guidance
            elif guidance < 1.0:
                params['guidance'] = 6.0
            else:
                params['guidance'] = guidance
        else:
            params['guidance'] = min(max(guidance, 0.0), 30.0)
        if 'flux' in str(params.get('model', '')).lower():
            params['max_workers'] = 1

    # Quality enhancement parameters
    params['content_preset'] = data.get('content_preset') or None  # None = auto-detect
    params['auto_enhance'] = _as_bool(data.get('auto_enhance'), True)
    params['enhance_anatomy'] = _as_bool(data.get('enhance_anatomy'), True)
    params['enhance_faces'] = _as_bool(data.get('enhance_faces'), True)
    params['enhance_hands'] = _as_bool(data.get('enhance_hands'), True)

    # Director intelligence (opt-in; mirrors batch-video exactly). Safe defaults = disabled.
    params['director_mode'] = _as_bool(data.get('director_mode'), False)
    params['director_guidance'] = data.get('director_guidance') or data.get('extra_guidance')
    params['storyboard_concept'] = data.get('storyboard_concept')
    params['planning_mode'] = data.get('planning_mode', 'narrative')
    params['director_model'] = data.get('director_model')
    params['user_treatment'] = data.get('user_treatment') or data.get('treatment')

    # Face restoration parameters
    params['restore_faces'] = _as_bool(data.get('restore_faces'), False)
    params['face_restoration_weight'] = float(data.get('face_restoration_weight', 0.5))

    # Transparent-background (rembg post-process) — RGBA PNG for icons/clip-art/logos
    params['remove_background'] = _as_bool(data.get('remove_background'), False)
    # No page sends this: models install from Install (Manage models, the
    # /models/download route), and a run whose model is not installed is
    # refused with where to install it.
    params['allow_model_download'] = _as_bool(data.get('allow_model_download'), False)

    # User context
    params['user_id'] = data.get('user_id')
    params['project_id'] = data.get('project_id')

    return params, validation_info

@batch_image_bp.route("/status", methods=["GET"])
def get_service_status():
    """Get batch image generation service status."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()

        # Get basic status without image generator details to avoid serialization issues
        try:
            status = {
                "service_available": generator.service_available,
                "active_batches": len([b for b in generator.active_batches.values() if b.status == "running"]),
                "total_tracked_batches": len(generator.active_batches),
                "base_output_dir": str(generator.base_output_dir),
                "cache_dir": str(generator.cache_dir),
                "image_generator_available": generator.image_generator is not None
            }
            if generator.image_generator and hasattr(generator.image_generator, "get_status"):
                status["image_generator"] = generator.image_generator.get_status()
        except Exception as status_error:
            logger.error(f"Error creating basic status: {status_error}")
            status = {"service_available": False, "error": str(status_error)}

        return success_response(status)

    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return error_response(str(e), 500)


def _auto_resolves_to(gen) -> Optional[str]:
    """The catalog key Auto renders with on this box for a neutral prompt, or
    None when the generator cannot say.

    The same selection the batch intake uses: the Stills default from Settings
    when the generator can honour it (configured_default_for_auto, which also
    covers a ComfyUI-only default), else the per-card router for an empty
    prompt (_auto_select_model, which _resolve_batch_model_key also asks).
    """
    if gen is None:
        return None
    preferred = getattr(gen, "configured_default_for_auto", None)
    if callable(preferred):
        try:
            key = preferred()
            if key:
                return str(key)
        except Exception as e:  # noqa: BLE001
            logger.debug("Stills default not consulted for the models list: %s", e)
    select = getattr(gen, "_auto_select_model", None)
    if callable(select):
        try:
            key = select("", "realistic")
            return str(key) if key else None
        except Exception as e:  # noqa: BLE001
            logger.debug("Auto selection failed for the models list: %s", e)
    return None


@batch_image_bp.route("/models", methods=["GET"])
def list_models():
    """List all available models and their installation status."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        
        if not generator.image_generator:
            return error_response("Image generator not initialized", 503)
            
        # Curated, ordered, menu-ready list carrying label/description/recommended
        # and a per-model usability verdict. Single source of truth. Hugging Face
        # is asked about models that are not on disk only when the person asks
        # (?check=1, Check access in Manage models), never on page load.
        check = request.args.get("check", "").lower() in ("1", "true", "yes")
        meta = generator.image_generator.get_available_models(probe_remote=check)
        from backend.services.stills_defaults import prompt_style, prompt_style_for_family
        models = []
        unavailable = []
        for model_id, info in sorted(meta.items(), key=lambda kv: kv[1].get("order", 99)):
            family = info.get("family")
            row = {
                "id": model_id,
                "path": info["id"],
                "is_downloaded": info["downloaded"],
                "name": info.get("label", model_id),
                "label": info.get("label", model_id),
                "description": info.get("description", ""),
                "recommended": info.get("recommended", False),
                "size_gb": info.get("size_gb") or _catalog_size_gb(info["id"]),
                "availability": info.get("availability", "downloadable"),
                "gated": bool(info.get("gated")),
                # User catalog rows (Manage Image Models → Add new model).
                "user": bool(info.get("user")),
                "family": family,
                "kind": info.get("kind"),
                # ComfyUI models: the files (model + companions) Install still has
                # to fetch, in the editing packs' "<entry>:<file>" shape.
                "missing_files": list(info.get("missing_files") or []),
                # "natural": an LLM text encoder that reads prompts as prose, so
                # the page's content-type tags and anatomy phrasing are not
                # applied (offline_image_generator.enhance_prompt_for_quality).
                # "tags": CLIP-era models that take the tag suffixes.
                "prompt_style": prompt_style_for_family(family) if family else prompt_style(model_id),
            }
            # A model the user cannot actually run must not sit in the picker — that
            # is how a gated Krea 2 got selected and silently produced SD 1.5 output.
            # Report it separately so the UI can explain rather than just hide.
            if info.get("selectable", True):
                models.append(row)
            else:
                row["reason"] = _AVAILABILITY_REASONS.get(
                    row["availability"], "Unavailable on this machine."
                )
                unavailable.append(row)

        from backend.services.user_image_models import catalog_rows, family_choices
        try:
            from backend.services.image_editing_packs import pack_rows
            editing = pack_rows()
        except Exception as e:  # noqa: BLE001 — the picker must list even if a pack probe fails
            logger.warning("image editing pack rows failed: %s", e)
            editing = []
        auto_model = _auto_resolves_to(generator.image_generator)
        return success_response({
            "models": models,
            "unavailable_models": unavailable,
            "default_model": "auto",
            # What Auto renders with today, so the page can say which controls
            # apply before a model is picked.
            "auto_model": auto_model,
            "auto_prompt_style": prompt_style(auto_model or "auto"),
            # User LoRAs never enter the picker; the page shows them as chips and
            # the modal lists them with Install / Remove.
            "adapters": catalog_rows(generator.image_generator),
            "families": family_choices(),
            # Chat photo tools' packs (Qwen-Image-Edit, PuLID, background
            # removal): installed through the video downloader, listed here.
            "editing": editing,
        })

    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/models/download", methods=["POST"])
def download_model():
    """Start downloading a specific model with real-time file size progress monitoring."""
    data = request.get_json()
    if not data or 'model_path' not in data:
        return error_response("No model_path provided", 400)
    return _start_image_model_download(data['model_path'])


def _start_image_model_download(model_path: str):
    """Start the background download for a catalog key or HF id (the Install button)."""
    global model_download_status, _IMAGE_DOWNLOAD_EPOCH, _DELEGATED_PACK

    try:
        # Image-editing packs (chat photo tools) install through the video
        # downloader; the status route mirrors that run under the pack id.
        from backend.services.image_editing_packs import pack_by_id
        pack_key = str(model_path or "").strip()
        if pack_key.startswith("comfy:"):
            pack_key = pack_key[len("comfy:"):]
        if pack_by_id(pack_key):
            from backend.api import batch_video_generation_api as _video_api
            resp = _video_api.start_video_model_download(pack_key)
            status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)
            if status < 400:
                _DELEGATED_PACK = pack_key
            return resp

        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()

        if not generator.image_generator:
            return error_response("Image generator not initialized", 503)

        catalog_key, hf_model_id = _resolve_catalog_model(model_path)
        if not hf_model_id:
            return error_response(
                f"Unknown model '{model_path}' — not in the allowed model set", 400
            )

        img = generator.image_generator
        if img._is_model_downloaded(catalog_key) or img._is_model_downloaded(hf_model_id):
            return success_response({
                "message": f"{catalog_key} is already installed",
                "already_installed": True,
                "catalog_key": catalog_key,
                "model_path": hf_model_id,
            })

        # Comfy-only catalog entries carry a "comfy:" sentinel, not an HF repo id —
        # feeding it to the diffusers path fails HF validation ('comfy:flux-dev').
        # Their assets (unet + encoders + VAE) install into ComfyUI/models/ via the
        # video registry downloader, which knows the gated repo + companion files.
        # The status endpoint below mirrors that downloader for flux plans.
        if str(hf_model_id).startswith("comfy:"):
            from backend.api import batch_video_generation_api as _video_api
            from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
            if catalog_key not in VIDEO_MODEL_REGISTRY:
                return error_response(
                    f"'{catalog_key}' is ComfyUI-only but has no VIDEO_MODEL_REGISTRY "
                    "entry — no install path for it", 501
                )
            return _video_api.start_video_model_download(catalog_key)

        user_entry = (getattr(img, "user_entries", None) or {}).get(catalog_key) or {}
        file_bytes = sum(int(f.get("size") or 0) for f in (user_entry.get("files") or []))
        size_gb = user_entry.get("size_gb")
        if size_gb:
            estimated_size_gb = float(size_gb)
        elif file_bytes:
            estimated_size_gb = round(file_bytes / (1024 ** 3), 3)
        else:
            estimated_size_gb = IMAGE_MODEL_SIZES.get(catalog_key, IMAGE_MODEL_SIZES.get(hf_model_id, 2.5))
        if user_entry:
            # A user ComfyUI model's Install also fetches the companions its
            # family's shipped entry requires; the progress bar counts them.
            try:
                from backend.services.user_image_models import missing_companion_size_gb
                estimated_size_gb = round(estimated_size_gb + missing_companion_size_gb(img, catalog_key), 3)
            except Exception as e:  # noqa: BLE001 — the estimate only shapes the progress bar
                logger.debug("companion size for %s skipped: %s", catalog_key, e)

        with model_download_lock:
            now = time.time()
            st = model_download_status
            # Keep 409 while the worker thread is alive. Stall marks status failed
            # but does not clear this flag until download_task's finally — a retry
            # must not start a second hf_hub_download into the same dest.
            if st.get("is_downloading"):
                return error_response(f"Already downloading model: {st.get('current_model')}", 409)

            _IMAGE_DOWNLOAD_EPOCH += 1
            epoch = _IMAGE_DOWNLOAD_EPOCH
            model_download_status = {
                "is_downloading": True,
                "current_model": hf_model_id,
                "progress": 0,
                "status": "starting",
                "error": None,
                "speed_mbps": 0,
                "downloaded_gb": 0,
                "total_gb": estimated_size_gb,
                "updated_at": now,
                "epoch": epoch,
            }
            _persist_image_download_status()

        def download_task(hf_model_id, total_gb, epoch):
            _start_time = time.time()
            total_bytes = int(total_gb * 1024**3) if total_gb else 1
            dests = _image_download_watch_dirs(generator.image_generator, catalog_key, hf_model_id)
            hf_cache = _hf_cache_repo_dir(hf_model_id)
            watched = [d for d in (*dests, hf_cache) if d is not None]
            baseline = sum(_dir_bytes(d) for d in watched)
            stalled = threading.Event()

            try:
                with model_download_lock:
                    if model_download_status.get("epoch") == epoch:
                        model_download_status["status"] = "downloading"
                        model_download_status["updated_at"] = time.time()
                        _persist_image_download_status()

                stop_monitor = threading.Event()

                def _monitor_progress():
                    last_bytes = -1
                    last_change = time.time()
                    while not stop_monitor.is_set():
                        try:
                            downloaded = max(0, sum(_dir_bytes(d) for d in watched) - baseline)
                            now_m = time.time()
                            if downloaded != last_bytes:
                                last_bytes = downloaded
                                last_change = now_m
                            elif (now_m - last_change) > _DOWNLOAD_STALL_SECONDS:
                                stalled.set()
                                with model_download_lock:
                                    if model_download_status.get("epoch") != epoch:
                                        return
                                    model_download_status.update({
                                        "status": "failed",
                                        "error": "Download stalled (no new bytes for 3 minutes). Click Install to retry.",
                                        "progress": 0,
                                        "updated_at": now_m,
                                    })
                                    _persist_image_download_status()
                                stop_monitor.set()
                                return
                            elapsed = now_m - _start_time
                            speed = (downloaded / (1024 * 1024)) / max(elapsed, 0.1)
                            pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)
                            with model_download_lock:
                                if model_download_status.get("epoch") != epoch:
                                    return
                                model_download_status.update({
                                    "progress": pct,
                                    "speed_mbps": round(speed, 1),
                                    "downloaded_gb": round(downloaded / 1024**3, 2),
                                    "updated_at": now_m,
                                })
                                _persist_image_download_status()
                        except Exception:
                            pass
                        stop_monitor.wait(1.0)

                monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                monitor_thread.start()

                try:
                    success, dl_error = generator.image_generator._download_model(
                        hf_model_id, stop=stalled
                    )
                finally:
                    stop_monitor.set()
                    monitor_thread.join(timeout=2)

                with model_download_lock:
                    if model_download_status.get("epoch") != epoch:
                        return
                    if stalled.is_set():
                        return
                    if success:
                        model_download_status.update({
                            "status": "completed",
                            "progress": 100,
                            "downloaded_gb": total_gb,
                            "total_gb": total_gb,
                            "updated_at": time.time(),
                        })
                    else:
                        model_download_status.update({
                            "status": "failed",
                            "error": dl_error or "Failed to download model",
                            "progress": 0,
                            "updated_at": time.time(),
                        })
                    _persist_image_download_status()
            except Exception as e:
                logger.error(f"Error in model download thread: {e}")
                with model_download_lock:
                    if model_download_status.get("epoch") == epoch and not stalled.is_set():
                        model_download_status.update({
                            "status": "failed",
                            "error": str(e),
                            "progress": 0,
                            "updated_at": time.time(),
                        })
                        _persist_image_download_status()
            finally:
                with model_download_lock:
                    if model_download_status.get("epoch") == epoch:
                        model_download_status["is_downloading"] = False
                        model_download_status["updated_at"] = time.time()
                        _persist_image_download_status()

        thread = threading.Thread(target=download_task, args=(hf_model_id, estimated_size_gb, epoch))
        thread.daemon = True
        thread.start()

        return success_response({
            "message": f"Started downloading model {hf_model_id}",
            "status": "downloading",
            "catalog_key": catalog_key,
            "model_path": hf_model_id,
        })

    except Exception as e:
        logger.error(f"Error starting model download: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/models/download-status", methods=["GET"])
def get_download_status():
    """Get the current model download status."""
    global model_download_status, _DELEGATED_PACK
    try:
        with model_download_lock:
            status = dict(model_download_status)
        if not status.get("is_downloading"):
            # Comfy-only entries (flux-dev) and image-editing packs delegate to
            # the video registry downloader — mirror its status so this modal's
            # poller tracks progress/completion of the delegated install.
            # Module-attribute access on purpose: the video module REBINDS its
            # status dict per download, so a from-import would go stale.
            from backend.api import batch_video_generation_api as _video_api
            with _video_api._video_model_download_lock:
                vstatus = dict(_video_api._video_model_download_status)
            if _DELEGATED_PACK:
                # The video run names each companion in turn; the modal's row
                # is the pack, so report the pack. A finished run is reported
                # once, then forgotten, so reopening the modal stays quiet.
                vstatus["current_model"] = _DELEGATED_PACK
                if not vstatus.get("is_downloading"):
                    _DELEGATED_PACK = None
                return success_response(vstatus)
            # A FLUX run is this modal's only while it is newer than the last
            # image install; afterwards its error would shadow that install.
            if (
                str(vstatus.get("current_model") or "").startswith("flux")
                and float(vstatus.get("updated_at") or 0) >= float(status.get("updated_at") or 0)
            ):
                return success_response(vstatus)
        return success_response(status)
    except Exception as e:
        logger.error(f"Error getting download status: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/validate-settings", methods=["POST"])
def validate_settings():
    """Validate generation settings and return warnings/recommendations."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data:
            return error_response("No data provided", 400)

        # Get settings to validate — family defaults when omitted
        from backend.services.stills_defaults import resolve_stills_defaults
        model = data.get('model', 'auto')
        _r = resolve_stills_defaults(
            model,
            width=int(data['width']) if data.get('width') is not None else None,
            height=int(data['height']) if data.get('height') is not None else None,
            steps=int(data['steps']) if data.get('steps') is not None else None,
            steps_explicit=_as_bool(data.get('steps_explicit'), False),
            guidance=float(data['guidance']) if data.get('guidance') is not None else None,
        )
        guidance = float(_r['guidance'])
        steps = int(_r['steps'])
        width = int(_r['width'])
        height = int(_r['height'])

        # Use SettingsValidator
        if not settings_validator_available or not get_settings_validator:
            return error_response("Settings validator not available", 503)
        
        validator = get_settings_validator()
        validation_result = validator.validate_settings(
            model=model,
            guidance=guidance,
            steps=steps,
            width=width,
            height=height,
            auto_correct=False  # Don't auto-correct, just validate
        )

        # Get model recommendations
        model_recommendations = validator.get_model_recommendations(model)

        return success_response({
            "is_valid": validation_result.is_valid,
            "warnings": validation_result.warnings,
            "errors": validation_result.errors,
            "recommendations": validation_result.recommendations,
            "model_recommendations": model_recommendations
        })

    except Exception as e:
        logger.error(f"Error validating settings: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/models/from-hf", methods=["POST"])
def preview_hf_image_model():
    """Parse a Hugging Face paste and list its weight files. Does not download."""
    from backend.services.user_image_models import preview_hf_url
    from backend.services.video_model_registry import classify_hf_download_error
    from backend.services.user_video_models import parse_hf_url
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return error_response("Paste a Hugging Face URL or org/repo.", 400)
    repo_id = None
    try:
        repo_id = parse_hf_url(url)["hf_repo"]
        return success_response(preview_hf_url(url))
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        logger.error("HF image preview failed: %s", e)
        return error_response(classify_hf_download_error(e, repo_id=repo_id), 400)


@batch_image_bp.route("/models/user", methods=["POST"])
def add_user_image_model():
    """Register a user image model or LoRA, then Install it unless told not to."""
    from backend.services.user_image_models import add_user_model, preview_hf_url
    from backend.services.user_model_families import DuplicateUserModel, hf_inspect_url
    from backend.services.video_model_registry import classify_hf_download_error
    if not service_available:
        return error_response("Batch image generation service not available", 503)
    data = request.get_json(silent=True) or {}
    hf_repo = (data.get("hf_repo") or "").strip()
    revision = (data.get("revision") or "main").strip() or "main"
    files = data.get("files") if isinstance(data.get("files"), list) else []
    url = (data.get("url") or "").strip()
    inspected = None
    if not url and hf_repo:
        src = None
        if len(files) == 1 and isinstance(files[0], dict):
            src = files[0].get("src")
        url = hf_inspect_url(hf_repo, revision, src)
    if url:
        try:
            inspected = preview_hf_url(url)
        except ValueError as e:
            return error_response(str(e), 400)
        except Exception as e:
            logger.error("HF image re-inspect failed: %s", e)
            return error_response(classify_hf_download_error(e, repo_id=hf_repo or None), 400)
        hf_repo = inspected["hf_repo"]
        revision = inspected.get("revision") or revision
        if inspected.get("unwired"):
            return error_response(inspected["unwired"].get("reason") or "That architecture is not wired yet.", 400)
        if inspected.get("src") and not files:
            files = [{"src": inspected["src"]}]
        has_model_index = bool(inspected.get("has_model_index"))
    else:
        return error_response("Paste a Hugging Face URL or org/repo.", 400)
    # Z-Image LoRAs load through Diffusers, which takes plain LoRA files only. Read
    # the file's header now so a LoKr file or a full checkpoint is turned away with a
    # reason before its download starts.
    if (data.get("role") or "").strip() == "lora" and (data.get("family") or "").strip() == "zimage":
        from backend.services.zimage_lora_check import hf_lora_problem
        srcs = [f.get("src") if isinstance(f, dict) else f for f in files]
        if len(srcs) == 1 and srcs[0]:
            problem = hf_lora_problem(hf_repo, srcs[0], revision)
            if problem:
                return error_response(
                    f"{Path(srcs[0]).name} can't be added as a Z-Image LoRA. {problem}", 400
                )
    generator = get_batch_image_generator()
    if not generator.image_generator:
        return error_response("Image generator not initialized", 503)
    try:
        mid, entry = add_user_model(
            generator.image_generator,
            role=(data.get("role") or "").strip(),
            family=(data.get("family") or "").strip(),
            hf_repo=hf_repo,
            files=files,
            has_model_index=has_model_index,
            name=(data.get("name") or "").strip() or None,
            description=(data.get("description") or "").strip() or None,
            revision=revision,
            known_files=inspected["files"] if inspected else None,
        )
    except DuplicateUserModel as e:
        return error_response(str(e), 409, data={"id": e.model_id})
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        logger.error("add user image model failed: %s", e)
        return error_response(str(e), 500)
    download_payload = None
    if str(data.get("install", "true")).lower() != "false":
        dl = _start_image_model_download(mid)
        resp_obj, status = dl if isinstance(dl, tuple) else (dl, getattr(dl, "status_code", 200))
        body = resp_obj.get_json(silent=True) or {}
        if status >= 400 or not body.get("success"):
            err = (body.get("error") or {})
            download_payload = {
                "error": err.get("message") if isinstance(err, dict) else (err or body.get("message")),
                "status": status,
            }
        else:
            download_payload = body.get("data")
    return success_response({
        "id": mid,
        "entry": {k: entry.get(k) for k in ("name", "role", "family", "kind", "hf_repo", "files", "size_gb", "applies_to", "engine")},
        "download": download_payload,
    })


@batch_image_bp.route("/models/user/<model_id>", methods=["DELETE"])
def delete_user_image_model(model_id):
    """Remove a user-added image model or LoRA. Shipped keys are refused."""
    from backend.services.user_image_models import remove_user_model
    if not service_available:
        return error_response("Batch image generation service not available", 503)
    delete_files = str(
        request.args.get("delete_files") or (request.get_json(silent=True) or {}).get("delete_files") or ""
    ).lower() in ("1", "true", "yes")
    generator = get_batch_image_generator()
    try:
        result = remove_user_model(generator.image_generator, model_id, delete_files=delete_files)
    except ValueError as e:
        return error_response(str(e), 403)
    except KeyError as e:
        return error_response(str(e), 404)
    except Exception as e:
        logger.error("remove user image model failed: %s", e)
        return error_response(str(e), 500)
    return success_response(result)


@batch_image_bp.route("/model-info/<model>", methods=["GET"])
def get_model_info(model: str):
    """Get model configuration and recommendations."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        if not settings_validator_available or not get_settings_validator:
            return error_response("Settings validator not available", 503)
        
        validator = get_settings_validator()
        model_info = validator.get_model_info(model)
        recommendations = validator.get_model_recommendations(model)

        return success_response({
            "model": model,
            "configuration": model_info,
            "recommendations": recommendations
        })

    except Exception as e:
        logger.error(f"Error getting model info: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/face-restoration-status", methods=["GET"])
def get_face_restoration_status():
    """Get face restoration service availability status."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        try:
            from backend.services.face_restoration_service import get_face_restoration_service
            face_service = get_face_restoration_service()
            status = face_service.get_service_status()
            return success_response(status)
        except Exception as e:
            logger.warning(f"Could not get face restoration status: {e}")
            return success_response({
                "service_available": False,
                "error": str(e)
            })

    except Exception as e:
        logger.error(f"Error getting face restoration status: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/presets", methods=["GET"])
def get_content_presets():
    """Get available content presets for image generation."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()

        if not generator.image_generator:
            return error_response("Image generator not initialized", 503)

        # Get content presets with their configurations
        presets = {}
        for name, config in generator.image_generator.content_presets.items():
            presets[name] = {
                "name": name,
                "label": name.replace("_", " ").title(),
                "description": _get_preset_description(name),
                # No sampling values here on purpose — content presets shape the prompt,
                # they do not choose steps/guidance/size. Those are per-model and come
                # from settings_validator; see the note on content_presets.
            }

        # Also include available styles
        styles = list(generator.image_generator.style_configs.keys())

        return success_response({
            "presets": presets,
            "styles": styles,
            "default_preset": "general",
            "default_style": "realistic"
        })

    except Exception as e:
        logger.error(f"Error getting content presets: {e}")
        return error_response(str(e), 500)


def _get_preset_description(preset_name: str) -> str:
    """Get human-readable description for preset."""
    descriptions = {
        "person_portrait": "Best for portraits and headshots - optimizes for facial features",
        "person_full_body": "Best for full-body shots - ensures correct proportions",
        "person_working": "Best for people doing activities - ensures logical tool/object interactions",
        "product_photo": "Best for product photography - clean, professional look",
        "landscape": "Best for scenic and nature images - vivid colors and composition",
        "infographic_preset": "Best for diagrams and icons - flat, clean vector style",
        "general": "General purpose - balanced settings for any content"
    }
    return descriptions.get(preset_name, "Custom preset")


@batch_image_bp.route("/analyze-prompt", methods=["POST"])
def analyze_prompt():
    """Analyze a prompt and return content detection results."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data or 'prompt' not in data:
            return error_response("No prompt provided", 400)

        prompt = data['prompt']
        if not prompt or not isinstance(prompt, str):
            return error_response("Invalid prompt", 400)

        generator = get_batch_image_generator()

        if not generator.image_generator:
            return error_response("Image generator not initialized", 503)

        # Detect content type
        detection = generator.image_generator.detect_content_type(prompt)

        # Get model recommendations
        model_recommendations = []
        if model_recommender_available and get_model_recommender:
            try:
                recommender = get_model_recommender()
                recommendations = recommender.recommend_models(
                    detection=detection,
                    prioritize_quality=True
                )
                # Return top 3 recommendations
                model_recommendations = [
                    {
                        "model": rec.model,
                        "score": rec.score,
                        "reasoning": rec.reasoning,
                        "recommended_settings": rec.recommended_settings
                    }
                    for rec in recommendations[:3]
                ]
            except Exception as e:
                logger.warning(f"Could not generate model recommendations: {e}")

        return success_response({
            "prompt": prompt,
            "detection": detection,
            "model_recommendations": model_recommendations
        })

    except Exception as e:
        logger.error(f"Error analyzing prompt: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/prompt-tokens", methods=["POST"])
def prompt_tokens():
    """How many tokens a prompt uses out of what the chosen model reads.

    ``limit`` is None when the model has no declared cap, or when it renders
    through ComfyUI, whose tokenizers read the whole prompt; ``tokens`` is None
    when the model's tokenizer is not on disk.
    """
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json(silent=True) or {}
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            return error_response("No prompt provided", 400)
        model = str(data.get("model") or "auto").strip() or "auto"

        gen = get_batch_image_generator().image_generator
        if not gen:
            return error_response("Image generator not initialized", 503)

        from backend.services.prompt_token_budget import count_prompt_tokens, load_tokenizer, max_prompt_tokens
        from backend.services.stills_pipeline import zimage_via_comfyui_enabled

        key = model
        if model.lower() == "auto":
            preferred = gen.configured_default_for_auto()
            key = preferred if preferred and gen.is_comfy_only_model(preferred) else gen._auto_select_model(prompt)
        if not key:
            return success_response({"model": None, "family": None, "tokens": None, "limit": None})

        family = gen._model_family(key)
        limit = max_prompt_tokens(family)
        if family == "zimage" and zimage_via_comfyui_enabled():
            limit = None
        tokens = None
        if limit:
            tokenizer = load_tokenizer(gen._get_model_path(gen._resolve_model_ref(key)))
            if tokenizer is not None:
                tokens = count_prompt_tokens(family, prompt, tokenizer)

        return success_response({"model": key, "family": family, "tokens": tokens, "limit": limit})

    except Exception as e:
        logger.error(f"Error counting prompt tokens: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/enhance-prompt", methods=["POST"])
def enhance_prompt():
    """Enhance a prompt with quality and consistency improvements."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data or 'prompt' not in data:
            return error_response("No prompt provided", 400)

        prompt = data['prompt']
        if not prompt or not isinstance(prompt, str):
            return error_response("Invalid prompt", 400)

        # Get optional parameters
        style = data.get('style', 'realistic')
        content_preset = data.get('content_preset')  # None = auto-detect
        auto_enhance = data.get('auto_enhance', True)
        enhance_anatomy = data.get('enhance_anatomy', True)
        enhance_faces = data.get('enhance_faces', True)
        enhance_hands = data.get('enhance_hands', True)
        model = data.get('model', '')

        generator = get_batch_image_generator()

        if not generator.image_generator:
            return error_response("Image generator not initialized", 503)

        # Family-aware so the preview applies the same long-context token
        # budgeting the generation path does (krea2/zimage cap at 512 tokens).
        family = ""
        if model and model != 'auto':
            try:
                family = generator.image_generator._model_family(
                    generator.image_generator.available_models.get(model, model)
                )
            except Exception:
                family = ""
        if not family:
            # "auto" resolves to the product default (Z-Image); the preview must
            # show that family's prompt policy, not the tag-stuffed SD one.
            try:
                from backend.services.stills_defaults import model_family
                family = model_family(model or "auto")
            except Exception:
                family = ""

        # Enhance the prompt
        enhanced_prompt, negative_prompt, detection = generator.image_generator.enhance_prompt_for_quality(
            prompt=prompt,
            style=style,
            content_preset=content_preset,
            auto_enhance=auto_enhance,
            enhance_anatomy=enhance_anatomy,
            enhance_faces=enhance_faces,
            enhance_hands=enhance_hands,
            family=family
        )

        # Get recommended settings from preset
        preset_name = detection.get("preset_used", "general")
        preset = generator.image_generator.content_presets.get(preset_name, {})

        # Get model recommendations
        model_recommendations = []
        if model_recommender_available and get_model_recommender:
            try:
                recommender = get_model_recommender()
                recommendations = recommender.recommend_models(
                    detection=detection,
                    prioritize_quality=True
                )
                # Return top 3 recommendations
                model_recommendations = [
                    {
                        "model": rec.model,
                        "score": rec.score,
                        "reasoning": rec.reasoning,
                        "recommended_settings": rec.recommended_settings
                    }
                    for rec in recommendations[:3]
                ]
            except Exception as e:
                logger.warning(f"Could not generate model recommendations: {e}")

        return success_response({
            "original_prompt": prompt,
            "enhanced_prompt": enhanced_prompt,
            "negative_prompt": negative_prompt,
            "detection": detection,
            # Deliberately no content-derived "recommended_settings": the prompt's
            # content type cannot know which model will render it, and the sampling
            # values it used to emit were SD 1.5-era. Per-model recipes come from
            # settings_validator / model_recommendations below.
            "model_recommendations": model_recommendations
        })

    except Exception as e:
        logger.error(f"Error enhancing prompt: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/expand-concept", methods=["POST"])
def expand_concept():
    """Director-powered: turn high-level idea + N into a reviewable plan (treatment + per-shot prompts).
    Non-GPU, cheap Ollama call. Returns plan for UI editing + later launch.
    """
    try:
        if not service_available:
            return error_response("Batch image service not available", 503)
        data = request.get_json() or {}
        idea = (data.get("idea") or data.get("concept") or "").strip()
        if not idea:
            return error_response("idea or concept required", 400)
        n = max(1, min(int(data.get("n") or data.get("quantity") or 4), 24))
        look = data.get("look_and_feel") or data.get("style") or ""
        treatment = data.get("user_treatment") or data.get("treatment")
        mode = data.get("planning_mode", "narrative")
        guidance = data.get("director_guidance") or data.get("extra_guidance")
        dmodel = data.get("director_model")

        from backend.services.media_director import expand_image_plan
        plan = expand_image_plan(
            idea, n,
            look_and_feel=look,
            user_treatment=treatment,
            planning_mode=mode,
            extra_guidance=guidance,
            model=dmodel,
            cast_descriptors=data.get("cast_descriptors"),
        )
        return success_response({"plan": plan, "n": n})
    except Exception as e:
        logger.error(f"expand-concept error: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/generate/csv", methods=["POST"])
def generate_from_csv():
    """Start batch generation from uploaded CSV file."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        # Validate file upload
        if 'file' not in request.files:
            return error_response("No file uploaded", 400)

        file = request.files['file']
        valid, message = _validate_csv_upload(file)
        if not valid:
            return error_response(message, 400)

        # Read CSV content
        csv_content = file.read().decode('utf-8')

        # Parse generation parameters
        form_data = request.form.to_dict()
        params, validation_info = _parse_generation_params(form_data)

        # Start batch generation
        batch_id = start_batch_from_csv(csv_content, **params)

        response_data = {
            "batch_id": batch_id,
            "message": "Batch generation started",
            "parameters": params
        }
        
        # Include validation warnings if any
        if validation_info.get("warnings") or validation_info.get("recommendations"):
            response_data["validation"] = {
                "warnings": validation_info.get("warnings", []),
                "recommendations": validation_info.get("recommendations", []),
                "corrected_values": validation_info.get("corrected_values", {})
            }

        return success_response(response_data, status_code=201)

    except UnknownImageModel as e:
        return error_response(str(e), 400)
    except ValueError as e:
        logger.warning(f"Invalid CSV data: {e}")
        return error_response(f"Invalid CSV: {str(e)}", 400)
    except Exception as e:
        logger.error(f"Error starting CSV batch generation: {e}")
        return error_response(str(e), 500)

class BatchRequestError(ValueError):
    """A prompts batch the caller has to change; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def queue_prompts_batch(data: dict) -> dict:
    """Validate a prompts batch as Image Gen sends it and queue it.

    Returns the route's response data. Shared by POST /generate/prompts and
    the Media page's Regenerate, so both go through the same parameter
    parsing, character casting and LoRA resolution.
    """
    prompts = data.get('prompts', [])
    logger.debug(f"Batch image API received prompts (count={len(prompts)})")
    if not prompts:
        raise BatchRequestError("No prompts provided")
    if not isinstance(prompts, list):
        raise BatchRequestError("Prompts must be a list")

    validated_prompts = []
    for i, prompt in enumerate(prompts):
        if isinstance(prompt, str):
            validated_prompts.append(prompt.strip())
        elif isinstance(prompt, dict) and 'prompt' in prompt:
            validated_prompts.append(prompt['prompt'].strip())
        else:
            logger.warning(f"Invalid prompt at index {i}: {prompt}")

    if not validated_prompts:
        raise BatchRequestError("No valid prompts found")

    if len(validated_prompts) > PROMPT_BATCH_MAX_ITEMS:
        raise BatchRequestError(
            f"Too many prompts ({len(validated_prompts)}); maximum is {PROMPT_BATCH_MAX_ITEMS} per batch"
        )

    params, validation_info = _parse_generation_params(data)

    # Character casting: subject_ids → identity core via render_character_still
    _apply_character_casting(data, params)

    cast_warnings = params.pop("_cast_warnings", None) or []
    if cast_warnings:
        validation_info.setdefault("warnings", []).extend(cast_warnings)

    # User LoRAs (Manage Image Models): ids → files + strength. A cast render
    # goes through the character pipeline, which owns its own adapters.
    adapters = data.get("adapters") if isinstance(data.get("adapters"), list) else []
    if adapters:
        from backend.services.user_image_models import resolve_user_loras
        paths, scale, lora_err = resolve_user_loras(
            get_batch_image_generator().image_generator, params.get("model") or "auto", adapters
        )
        if lora_err:
            raise BatchRequestError(lora_err)
        if params.get("subject_ids"):
            validation_info.setdefault("warnings", []).append(
                "A cast character is selected, so your LoRAs were not stacked on this render."
            )
        else:
            params["adapter_loras"] = paths
            params["adapter_scale"] = scale

    batch_id = start_batch_from_prompts(validated_prompts, **params)

    response_data = {
        "batch_id": batch_id,
        "message": "Batch generation started",
        "prompt_count": len(validated_prompts),
        "parameters": {k: v for k, v in params.items() if not str(k).startswith("_")},
    }

    # Include validation warnings if any
    if validation_info.get("warnings") or validation_info.get("recommendations"):
        response_data["validation"] = {
            "warnings": validation_info.get("warnings", []),
            "recommendations": validation_info.get("recommendations", []),
            "corrected_values": validation_info.get("corrected_values", {})
        }
    return response_data


@batch_image_bp.route("/generate/prompts", methods=["POST"])
def generate_from_prompts():
    """Start batch generation from JSON prompt list."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data:
            return error_response("No JSON data provided", 400)

        return success_response(queue_prompts_batch(data), status_code=201)

    except BatchRequestError as e:
        return error_response(str(e), e.status)
    except ValueError as e:
        logger.warning(f"Invalid batch image request: {e}")
        return error_response(str(e), 400)
    except Exception as e:
        logger.error(f"Error starting prompts batch generation: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/queue", methods=["GET"])
def get_image_batch_queue():
    """Snapshot of the in-process image batch queue for the UI panel.

    Same idea as /api/batch-video/queue — one worker drains batches in order so
    the operator can stack jobs without waiting.
    """
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)
        generator = get_batch_image_generator()
        return success_response({"queue": generator.list_queue()})
    except Exception as e:
        logger.error(f"Failed to get image batch queue: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/status/<batch_id>", methods=["GET"])
def get_batch_generation_status(batch_id: str):
    """Get status of specific batch generation."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        include_results = request.args.get('include_results') == 'true'
        status = _load_batch_status(generator, batch_id, include_results=include_results)
        if not status:
            return error_response("Batch not found", 404)

        # Convert status to serializable format
        status_data = {
            "batch_id": status.batch_id,
            "status": status.status,
            "total_images": status.total_images,
            "completed_images": status.completed_images,
            "failed_images": status.failed_images,
            "start_time": status.start_time.isoformat() if status.start_time else None,
            "end_time": status.end_time.isoformat() if status.end_time else None,
            "output_dir": status.output_dir,
            "estimated_time_remaining": status.estimated_time_remaining,
            "error": status.error,
            "display_name": getattr(status, "display_name", None) or status.batch_id,
            "kind": getattr(status, "kind", "image"),
            "retry_data": getattr(status, "retry_data", None),
            "gpu_wait_reason": getattr(status, "gpu_wait_reason", None),
            "progress_percentage": _progress_percentage(status),
            # The failure with its kind and next step (job_types), as video batches report it.
            "error_kind": getattr(status, "error_kind", None),
            "failure": batch_failure(status, surface="image"),
        }

        # Include results if requested
        if include_results:
            status_data['steps_notices'] = list(dict.fromkeys(
                r.metadata['steps_notice'] for r in (status.results or [])
                if (r.metadata or {}).get('steps_notice')
            ))
            status_data['results'] = [
                {
                    "prompt_id": r.prompt_id,
                    "success": r.success,
                    "image_path": r.image_path,
                    "thumbnail_path": r.thumbnail_path,
                    "generation_time": r.generation_time,
                    "error": r.error,
                    "error_kind": getattr(r, "error_kind", None),
                    "failure": (describe_failure(getattr(r, "error_kind", None), r.error, "image")
                                if not r.success else None),
                    "metadata": r.metadata
                }
                for r in status.results
            ]

        return success_response(status_data)

    except Exception as e:
        logger.error(f"Error getting batch status: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/cancel/<batch_id>", methods=["POST"])
def cancel_batch_generation(batch_id: str):
    """Cancel running batch generation."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        success = cancel_batch(batch_id)
        if not success:
            return error_response("Batch not found or cannot be cancelled", 404)

        return success_response({
            "batch_id": batch_id,
            "message": "Batch generation cancelled"
        })

    except Exception as e:
        logger.error(f"Error cancelling batch: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/list", methods=["GET"])
def list_batch_generations():
    """List all batch generations including completed ones from disk."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        batches = generator.list_all_batches()

        # Convert to serializable format with error handling
        # Look up folder IDs and thumbnails for completed batches (batch query)
        from backend.models import Folder, Document as DBDocument

        completed_batch_ids = [b.batch_id for b in batches if b.status == "completed"]
        folder_cache = {}
        if completed_batch_ids:
            # Folders are stored at /Images/<batch_id> (batch_name defaults to batch_id)
            folder_paths = [f"/Images/{bid}" for bid in completed_batch_ids]
            folders = Folder.query.filter(Folder.path.in_(folder_paths)).all()
            path_to_folder = {f.path: f for f in folders}
            for bid in completed_batch_ids:
                folder = path_to_folder.get(f"/Images/{bid}")
                if folder:
                    folder_cache[bid] = folder

        # Batch-fetch thumbnails for all matched folders (avoid N+1)
        thumb_cache = {}
        if folder_cache:
            folder_ids = [f.id for f in folder_cache.values()]
            # Get up to 4 most recent docs per folder using a window function
            all_thumb_docs = (
                DBDocument.query
                .filter(DBDocument.folder_id.in_(folder_ids))
                .order_by(DBDocument.folder_id, DBDocument.uploaded_at.desc())
                .all()
            )
            for doc in all_thumb_docs:
                fid = doc.folder_id
                if fid not in thumb_cache:
                    thumb_cache[fid] = []
                if len(thumb_cache[fid]) < 4:
                    thumb_cache[fid].append(doc)

        batch_list = []
        for batch in batches:
            try:
                batch_data = {
                    "batch_id": batch.batch_id,
                    "display_name": getattr(batch, "display_name", None) or batch.batch_id,
                    "kind": getattr(batch, "kind", "image"),
                    "status": batch.status,
                    "total_images": batch.total_images,
                    "completed_images": batch.completed_images,
                    "failed_images": batch.failed_images,
                    "start_time": batch.start_time.isoformat() if batch.start_time else None,
                    "end_time": batch.end_time.isoformat() if batch.end_time else None,
                    "retry_data": getattr(batch, "retry_data", None),
                    "can_retry": bool(getattr(batch, "retry_data", None)),
                    "progress_percentage": _progress_percentage(batch),
                }

                # Add folder_id and thumbnail URLs for completed batches
                folder = folder_cache.get(batch.batch_id)
                if folder:
                    batch_data["folder_id"] = folder.id
                    batch_data["thumbnail_urls"] = [
                        f"/api/files/document/{doc.id}/download"
                        for doc in thumb_cache.get(folder.id, [])
                    ]

                batch_list.append(batch_data)
            except Exception as batch_error:
                logger.warning(f"Failed to serialize batch {getattr(batch, 'batch_id', 'unknown')}: {batch_error}")
                # Add a safe fallback entry
                batch_list.append({
                    "batch_id": getattr(batch, 'batch_id', 'unknown'),
                    "status": "error",
                    "total_images": 0,
                    "completed_images": 0,
                    "failed_images": 0,
                    "start_time": None,
                    "end_time": None,
                    "progress_percentage": 0,
                    "error": "Serialization failed"
                })

        return success_response({
            "batches": batch_list,
            "total_batches": len(batch_list)
        })

    except Exception as e:
        logger.error(f"Error listing batches: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/download/<batch_id>", methods=["GET"])
def download_batch_results(batch_id: str):
    """Download batch results as ZIP file."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        # Completed batches from before this process started only exist on disk,
        # and the history cards offer Download for all of them.
        generator = get_batch_image_generator()
        status = _load_batch_status(generator, batch_id)
        if not status:
            return error_response("Batch not found", 404)

        # Anything terminal can be downloaded: an errored batch may still hold the
        # images that did render.
        if status.status not in ("completed", "cancelled", "error"):
            return error_response("Batch not ready for download", 400)

        if not status.output_dir or not os.path.exists(status.output_dir):
            return error_response("Batch output directory not found", 404)

        # Create temporary ZIP file
        output_dir = Path(status.output_dir)

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
            with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:

                # Add all images
                images_dir = output_dir / "images"
                if images_dir.exists():
                    for image_file in images_dir.iterdir():
                        if image_file.is_file():
                            zipf.write(image_file, f"images/{image_file.name}")

                # Add thumbnails if they exist
                thumbnails_dir = output_dir / "thumbnails"
                if thumbnails_dir.exists():
                    for thumb_file in thumbnails_dir.iterdir():
                        if thumb_file.is_file():
                            zipf.write(thumb_file, f"thumbnails/{thumb_file.name}")

                # Add metadata file
                metadata_file = output_dir / "batch_metadata.json"
                if metadata_file.exists():
                    zipf.write(metadata_file, "batch_metadata.json")

            zip_filename = f"batch_{secure_filename(batch_id) or 'batch'}_results.zip"

            def generate():
                # The temp file must go even if the client disconnects mid-stream,
                # which raises GeneratorExit inside the loop.
                try:
                    with open(temp_zip.name, 'rb') as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break
                            yield chunk
                finally:
                    try:
                        os.unlink(temp_zip.name)
                    except OSError:
                        pass

            return Response(
                stream_with_context(generate()),
                mimetype='application/zip',
                headers={'Content-Disposition': f'attachment; filename="{zip_filename}"'}
            )

    except Exception as e:
        logger.error(f"Error downloading batch results: {e}")
        return error_response(str(e), 500)

# Generated images are Bates-stamped and never rewritten in place, so they can be
# cached hard. Without an explicit max_age Werkzeug stamps every send_file response
# `Cache-Control: no-cache`, which forces a revalidation round-trip per thumbnail on
# every visit to the batch page.
IMAGE_CACHE_MAX_AGE = 86400


def _resolve_batch_output_dir(generator, batch_id: str) -> Optional[Path]:
    """Find a batch's output directory without scanning every batch folder.

    get_batch_status() only tracks in-flight batches, so requests for completed
    batches always missed and fell through to list_all_batches(), which does an
    iterdir plus a json.load of *every* batch folder. At one request per thumbnail
    that is O(batches^2) disk work per page load. Batch folders are named after the
    batch id, so try the direct path first and keep the scan only as a fallback for
    layouts where that does not hold.
    """
    status = _load_batch_status(generator, batch_id)
    if status and status.output_dir:
        return Path(status.output_dir)
    return None


def _contained_file(base_dir: Path, name: str) -> Optional[Path]:
    """Resolve ``name`` as a direct child of ``base_dir``.

    Returns None when the name is empty, carries a path separator, or resolves
    (through ``..`` or a symlink) anywhere other than directly inside ``base_dir``.
    Werkzeug has already URL-decoded the route segment once; it must not be
    decoded again here.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        return None
    base = base_dir.resolve()
    joined = safe_join(str(base), name)
    if joined is None:
        return None
    candidate = Path(joined).resolve()
    return candidate if candidate.parent == base else None


def _ensure_thumbnail(generator, source: Path, thumbnail_dir: Path) -> Optional[Path]:
    """Return a cached 256px thumbnail for `source`, generating it if missing.

    Thumbnails go missing whenever generate_thumbnails was off or PIL failed during
    the run. The old fallback shipped the full-resolution image instead, so a 1300px
    PNG got decoded into a multi-megabyte bitmap just to fill a ~200px box — the main
    reason the batch page balloons in memory. Build the thumbnail once, on demand,
    and reuse it from disk thereafter.
    """
    try:
        thumb_path = thumbnail_dir / (source.stem + ".jpg")
        if thumb_path.exists() and thumb_path.stat().st_mtime >= source.stat().st_mtime:
            return thumb_path
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        created = generator._create_thumbnail(str(source), thumbnail_dir)
        return Path(created) if created else None
    except Exception as e:
        logger.warning(f"On-demand thumbnail generation failed for {source}: {e}")
        return None


@batch_image_bp.route("/image/<batch_id>/<image_name>", methods=["GET"])
def get_batch_image(batch_id: str, image_name: str):
    """Get individual image from batch."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        output_dir = _resolve_batch_output_dir(generator, batch_id)

        if not output_dir:
            return error_response("Batch not found", 404)

        want_thumbnail = request.args.get('thumbnail') == 'true'
        thumbnail_dir = output_dir / "thumbnails"
        images_dir = output_dir / "images"

        # Check if it's a thumbnail request
        if want_thumbnail:
            image_path = _contained_file(thumbnail_dir, image_name)
            if image_path is None:
                return error_response("Invalid image name", 400)

            # Special case: BatchImageGenerator saves thumbnails as .jpg
            if not image_path.exists():
                jpg_path = _contained_file(thumbnail_dir, Path(image_name).with_suffix('.jpg').name)
                if jpg_path is not None:
                    image_path = jpg_path
        else:
            image_path = _contained_file(images_dir, image_name)
            if image_path is None:
                return error_response("Invalid image name", 400)

        if not image_path.exists():
            # Thumbnail requested but absent: build it from the full image rather than
            # serving the full image itself. Use stem matching because thumbnails are
            # always .jpg while images keep their original extension.
            if want_thumbnail:
                stem = Path(image_name).stem
                candidates = [f"{stem}{ext}" for ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif')]
                candidates.append(image_name)

                for candidate_name in candidates:
                    candidate = _contained_file(images_dir, candidate_name)
                    if candidate is None or not candidate.exists():
                        continue
                    generated = _ensure_thumbnail(generator, candidate, thumbnail_dir)
                    if generated:
                        return send_file(str(generated), mimetype='image/jpeg',
                                         max_age=IMAGE_CACHE_MAX_AGE)
                    # Thumbnailing failed (no PIL, unreadable file). Serving the
                    # full-resolution image is heavy but beats showing nothing.
                    logger.info(f"Thumbnail generation failed, serving full image: {candidate}")
                    return send_file(str(candidate), max_age=IMAGE_CACHE_MAX_AGE)
            logger.warning(f"Image not found: {image_path} (requested: {image_name})")
            listing_dir = thumbnail_dir if want_thumbnail else images_dir
            if listing_dir.exists():
                available_files = [f.name for f in listing_dir.iterdir() if f.is_file()]
                logger.warning(f"Available files in {listing_dir}: {available_files[:5]}")
            return error_response(f"Image not found: {image_name}", 404)

        # Determine MIME type from extension
        mime_type = 'image/png'  # default
        if image_path.suffix.lower() in ['.jpg', '.jpeg']:
            mime_type = 'image/jpeg'
        elif image_path.suffix.lower() == '.png':
            mime_type = 'image/png'
        elif image_path.suffix.lower() == '.gif':
            mime_type = 'image/gif'
        elif image_path.suffix.lower() == '.webp':
            mime_type = 'image/webp'

        return send_file(str(image_path), mimetype=mime_type, max_age=IMAGE_CACHE_MAX_AGE)

    except Exception as e:
        logger.error(f"Error serving batch image: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/image/<batch_id>/<image_name>", methods=["DELETE"])
def delete_batch_image(batch_id: str, image_name: str):
    """Delete a single image from a batch."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        status = _load_batch_status(generator, batch_id)
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        output_dir = Path(status.output_dir)
        images_dir = output_dir / "images"
        thumbnails_dir = output_dir / "thumbnails"

        image_path = _contained_file(images_dir, image_name)
        if image_path is None:
            return error_response("Invalid image name", 400)
        thumbnail_paths = []
        for cand in _thumbnail_candidates(image_name):
            p = _contained_file(thumbnails_dir, cand)
            if p is None:
                return error_response("Invalid image name", 400)
            thumbnail_paths.append(p)

        deleted_files = []
        errors = []

        if image_path.exists():
            try:
                image_path.unlink()
                deleted_files.append(str(image_path))
                logger.info(f"Deleted image: {image_path}")
            except Exception as e:
                errors.append(f"Failed to delete image: {str(e)}")

        for thumbnail_path in thumbnail_paths:
            if thumbnail_path.exists():
                try:
                    thumbnail_path.unlink()
                    deleted_files.append(str(thumbnail_path))
                    logger.info(f"Deleted thumbnail: {thumbnail_path}")
                except Exception as e:
                    errors.append(f"Failed to delete thumbnail: {str(e)}")

        if not deleted_files:
            return error_response(f"Image not found: {image_name}", 404)

        def _drop_result(results):
            kept, removed = [], 0
            for r in results:
                path = r.get('image_path') if isinstance(r, dict) else getattr(r, 'image_path', None)
                if path and Path(path).name == image_name:
                    removed += 1
                    continue
                kept.append(r)
            return kept, removed

        # Update batch metadata if it exists
        metadata_file = output_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)

                removed = 0
                if 'results' in metadata:
                    metadata['results'], removed = _drop_result(metadata['results'] or [])
                if removed and 'completed_images' in metadata:
                    metadata['completed_images'] = max(0, int(metadata.get('completed_images') or 0) - removed)
                metadata['updated_at'] = datetime.now().isoformat()

                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not update metadata: {e}")

        def _sync(st):
            st.results, removed = _drop_result(list(st.results or []))
            if removed:
                st.completed_images = max(0, st.completed_images - removed)

        _sync_active_batch(generator, batch_id, _sync)

        if errors:
            return error_response(f"Deleted files but encountered errors: {'; '.join(errors)}", 207)
        
        return success_response({
            "batch_id": batch_id,
            "image_name": image_name,
            "deleted_files": deleted_files,
            "message": "Image deleted successfully"
        })

    except Exception as e:
        logger.error(f"Error deleting batch image: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/image/<batch_id>/<image_name>/rename", methods=["PUT"])
def rename_batch_image(batch_id: str, image_name: str):
    """Rename a single image in a batch."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data or 'new_name' not in data:
            return error_response("new_name is required", 400)

        new_name = data.get('new_name', '').strip()
        if not new_name:
            return error_response("New name cannot be empty", 400)

        # Validate new filename
        invalid_chars = '<>:"/\\|?*\x00-\x1f'
        if any(char in invalid_chars for char in new_name):
            return error_response("Filename contains invalid characters", 400)

        generator = get_batch_image_generator()
        status = _load_batch_status(generator, batch_id)
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        output_dir = Path(status.output_dir)
        images_dir = output_dir / "images"
        thumbnails_dir = output_dir / "thumbnails"

        old_image_path = _contained_file(images_dir, image_name)
        if old_image_path is None:
            return error_response("Invalid image name", 400)

        # Preserve file extension
        old_ext = Path(image_name).suffix
        if old_ext and not new_name.endswith(old_ext):
            new_name = new_name + old_ext

        safe_new_name = secure_filename(new_name)
        if not safe_new_name or safe_new_name in (".", ".."):
            return error_response("Invalid new name", 400)

        if not old_image_path.exists():
            return error_response(f"Image not found: {image_name}", 404)

        new_image_path = images_dir / safe_new_name
        if new_image_path.exists():
            return error_response(f"Image with name '{new_name}' already exists", 409)

        try:
            old_image_path.rename(new_image_path)
            logger.info(f"Renamed image: {old_image_path} -> {new_image_path}")
        except Exception as e:
            return error_response(f"Failed to rename image: {str(e)}", 500)

        # The thumbnail keeps whichever extension it was written with (.jpg for
        # generated batches, original for uploads); only its stem changes.
        new_thumbnail_name = None
        for old_cand in _thumbnail_candidates(image_name):
            old_thumbnail_path = _contained_file(thumbnails_dir, old_cand)
            if old_thumbnail_path is None or not old_thumbnail_path.exists():
                continue
            new_thumbnail_name = Path(safe_new_name).with_suffix(old_thumbnail_path.suffix).name
            new_thumbnail_path = thumbnails_dir / new_thumbnail_name
            try:
                old_thumbnail_path.rename(new_thumbnail_path)
                logger.info(f"Renamed thumbnail: {old_thumbnail_path} -> {new_thumbnail_path}")
            except Exception as e:
                logger.warning(f"Failed to rename thumbnail: {e}")
                new_thumbnail_name = None
            break

        def _rename_result(r):
            is_dict = isinstance(r, dict)
            path = r.get('image_path') if is_dict else getattr(r, 'image_path', None)
            if not path or Path(path).name != image_name:
                return
            new_path = str(Path(path).parent / safe_new_name)
            thumb = r.get('thumbnail_path') if is_dict else getattr(r, 'thumbnail_path', None)
            new_thumb = str(Path(thumb).parent / new_thumbnail_name) if (thumb and new_thumbnail_name) else thumb
            if is_dict:
                r['image_path'] = new_path
                if thumb:
                    r['thumbnail_path'] = new_thumb
            else:
                r.image_path = new_path
                if thumb:
                    r.thumbnail_path = new_thumb

        # Update batch metadata if it exists
        metadata_file = output_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)

                for result in metadata.get('results') or []:
                    if isinstance(result, dict):
                        _rename_result(result)
                metadata['updated_at'] = datetime.now().isoformat()

                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not update metadata: {e}")

        _sync_active_batch(
            generator, batch_id,
            lambda st: [_rename_result(r) for r in (st.results or [])],
        )

        return success_response({
            "batch_id": batch_id,
            "old_name": image_name,
            "new_name": safe_new_name,
            "message": "Image renamed successfully"
        })

    except Exception as e:
        logger.error(f"Error renaming batch image: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/preview/<batch_id>", methods=["GET"])
def get_batch_preview(batch_id: str):
    """Get preview/thumbnail image for a batch (first available image)."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        output_dir = _resolve_batch_output_dir(generator, batch_id)

        if not output_dir:
            return error_response("Batch not found", 404)

        # Try to get first thumbnail, then first image
        thumbnail_dir = output_dir / "thumbnails"
        images_dir = output_dir / "images"

        # Supported image extensions (keep in sync with generator output formats)
        image_patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"]

        # Look for first thumbnail
        if thumbnail_dir.exists():
            thumbnails = []
            for pattern in image_patterns:
                thumbnails.extend(sorted(thumbnail_dir.glob(pattern)))
            if thumbnails:
                return send_file(str(thumbnails[0]), max_age=IMAGE_CACHE_MAX_AGE)

        # No thumbnail on disk: build one from the first image instead of shipping a
        # full-resolution render into the ~200px preview tile.
        if images_dir.exists():
            images = []
            for pattern in image_patterns:
                images.extend(sorted(images_dir.glob(pattern)))
            if images:
                generated = _ensure_thumbnail(generator, images[0], thumbnail_dir)
                if generated:
                    return send_file(str(generated), mimetype='image/jpeg',
                                     max_age=IMAGE_CACHE_MAX_AGE)
                return send_file(str(images[0]), max_age=IMAGE_CACHE_MAX_AGE)

        return error_response("No images found in batch", 404)

    except Exception as e:
        logger.error(f"Error serving batch preview: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/delete/<batch_id>", methods=["DELETE"])
def delete_batch(batch_id: str):
    """Delete a batch and all its files."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        generator = get_batch_image_generator()
        status = _load_batch_status(generator, batch_id)
        if not status:
            return error_response("Batch not found", 404)

        # A batch that is running, or still waiting in the queue, must not lose
        # its directory: the worker would pick it up and fail every prompt into
        # a folder that no longer exists. Cancel it first.
        if status.status in generator._ACTIVE_STATUSES:
            return error_response(
                "Batch is still queued or running — cancel it before deleting.", 409
            )

        # Delete the batch directory
        if status.output_dir and os.path.exists(status.output_dir):
            import shutil
            try:
                shutil.rmtree(status.output_dir)
                logger.info(f"Deleted batch directory: {status.output_dir}")
            except Exception as e:
                logger.error(f"Error deleting batch directory: {e}")
                return error_response(f"Failed to delete batch files: {str(e)}", 500)

        generator.forget_batch(batch_id)

        # The directory is gone, so the rows that pointed into it have to go
        # too — otherwise the Documents tree keeps an empty folder and the
        # Jobs page keeps history for a batch that no longer exists.
        db_removed = {}
        try:
            from backend.services.generation_history_service import delete_batch_history_rows
            db_removed = delete_batch_history_rows(
                image_batch_ids=[batch_id], triggered_by="batch_image_delete"
            )
        except Exception as e:
            logger.error(f"Batch {batch_id} files deleted but its database rows remain: {e}")

        return success_response({
            "batch_id": batch_id,
            "deleted": db_removed,
            "message": "Batch deleted successfully"
        })

    except Exception as e:
        logger.error(f"Error deleting batch: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/rename/<batch_id>", methods=["PUT"])
def rename_batch(batch_id: str):
    """Rename a batch by updating its metadata."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data or 'name' not in data:
            return error_response("Name is required", 400)

        new_name = data.get('name', '').strip()
        if not new_name:
            return error_response("Name cannot be empty", 400)

        generator = get_batch_image_generator()
        status = _load_batch_status(generator, batch_id)
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        # Update metadata file
        metadata_file = Path(status.output_dir) / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                metadata['display_name'] = new_name
                metadata['updated_at'] = datetime.now().isoformat()
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                logger.info(f"Renamed batch {batch_id} to {new_name}")
            except Exception as e:
                logger.error(f"Error updating metadata: {e}")
                return error_response(f"Failed to update metadata: {str(e)}", 500)
        else:
            # Create metadata file if it doesn't exist
            try:
                metadata = {
                    "batch_id": batch_id,
                    "display_name": new_name,
                    "status": status.status,
                    "total_images": status.total_images,
                    "completed_images": status.completed_images,
                    "failed_images": status.failed_images,
                    "start_time": status.start_time.isoformat() if status.start_time else None,
                    "end_time": status.end_time.isoformat() if status.end_time else None,
                    "updated_at": datetime.now().isoformat()
                }
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.error(f"Error creating metadata: {e}")
                return error_response(f"Failed to create metadata: {str(e)}", 500)

        def _set_name(st):
            st.display_name = new_name

        _sync_active_batch(generator, batch_id, _set_name)

        return success_response({
            "batch_id": batch_id,
            "display_name": new_name,
            "message": "Batch renamed successfully"
        })

    except Exception as e:
        logger.error(f"Error renaming batch: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/folder", methods=["POST"])
def create_batch_folder():
    """Create a folder for organizing batches."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data or 'name' not in data:
            return error_response("Folder name is required", 400)

        folder_name = data.get('name', '').strip()
        if not folder_name:
            return error_response("Folder name cannot be empty", 400)

        # Sanitize folder name
        from werkzeug.utils import secure_filename
        safe_folder_name = secure_filename(folder_name)
        if not safe_folder_name:
            return error_response("Invalid folder name", 400)

        generator = get_batch_image_generator()
        folder_path = generator.base_output_dir / "_folders" / safe_folder_name
        
        # Check if folder already exists
        if folder_path.exists():
            return error_response("Folder already exists", 409)

        # Create folder
        folder_path.mkdir(parents=True, exist_ok=True)
        
        # Create metadata file for folder
        metadata_file = folder_path / ".folder_metadata.json"
        folder_metadata = {
            "name": folder_name,
            "safe_name": safe_folder_name,
            "created_at": datetime.now().isoformat(),
            "type": "folder"
        }
        with open(metadata_file, 'w') as f:
            import json
            json.dump(folder_metadata, f, indent=2)

        logger.info(f"Created batch folder: {folder_path}")

        return success_response({
            "folder_name": folder_name,
            "folder_path": str(folder_path),
            "message": "Folder created successfully"
        }, status_code=201)

    except Exception as e:
        logger.error(f"Error creating folder: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/move/<batch_id>", methods=["POST"])
def move_batch_to_folder(batch_id: str):
    """Move a batch to a folder."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        folder_name = data.get('folder_name', '').strip() if data else None

        generator = get_batch_image_generator()
        status = _load_batch_status(generator, batch_id)
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        if status.status in generator._ACTIVE_STATUSES:
            return error_response("Batch is still queued or running — wait for it to finish before moving.", 409)

        current_path = Path(status.output_dir)
        
        if folder_name:
            # Move to folder
            from werkzeug.utils import secure_filename
            safe_folder_name = secure_filename(folder_name)
            folder_path = generator.base_output_dir / "_folders" / safe_folder_name
            
            if not folder_path.exists():
                return error_response("Folder not found", 404)
            
            new_path = folder_path / current_path.name
        else:
            # Move to root
            new_path = generator.base_output_dir / current_path.name

        # Check if destination already exists
        if new_path.exists() and new_path != current_path:
            return error_response("Destination already exists", 409)

        # Move the directory
        import shutil
        try:
            shutil.move(str(current_path), str(new_path))
            logger.info(f"Moved batch {batch_id} from {current_path} to {new_path}")
        except Exception as e:
            logger.error(f"Error moving batch: {e}")
            return error_response(f"Failed to move batch: {str(e)}", 500)

        # Update metadata if it exists
        metadata_file = new_path / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                metadata['folder'] = folder_name if folder_name else None
                metadata['updated_at'] = datetime.now().isoformat()
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not update metadata: {e}")

        def _set_dir(st):
            st.output_dir = str(new_path)

        _sync_active_batch(generator, batch_id, _set_dir)

        return success_response({
            "batch_id": batch_id,
            "folder_name": folder_name,
            "new_path": str(new_path),
            "message": "Batch moved successfully"
        })

    except Exception as e:
        logger.error(f"Error moving batch: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/upload", methods=["POST"])
def upload_images():
    """Upload images to a batch or create a new batch from uploaded images."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        if 'files' not in request.files:
            return error_response("No files uploaded", 400)

        files = request.files.getlist('files')
        if not files:
            return error_response("No files provided", 400)

        generator = get_batch_image_generator()
        
        # Create a new batch folder for uploaded images
        from uuid import uuid4
        batch_id = f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        batch_dir = generator.base_output_dir / batch_id
        images_dir = batch_dir / "images"
        thumbnails_dir = batch_dir / "thumbnails"
        
        images_dir.mkdir(parents=True, exist_ok=True)
        thumbnails_dir.mkdir(parents=True, exist_ok=True)

        uploaded_files = []
        for file in files:
            if file and file.filename:
                # Validate file type
                filename = secure_filename(file.filename)
                if not any(filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    continue

                # Save original image
                image_path = images_dir / filename
                file.save(str(image_path))
                
                # Create thumbnail (simple resize - could be enhanced)
                try:
                    from PIL import Image
                    img = Image.open(image_path)
                    img.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    thumbnail_path = thumbnails_dir / filename
                    img.save(thumbnail_path)
                except Exception as e:
                    logger.warning(f"Could not create thumbnail for {filename}: {e}")
                    thumbnail_path = None

                uploaded_files.append({
                    "filename": filename,
                    "image_path": str(image_path),
                    "thumbnail_path": str(thumbnail_path) if thumbnail_path else None
                })

        if not uploaded_files:
            # Clean up empty directory
            import shutil
            shutil.rmtree(batch_dir)
            return error_response("No valid image files uploaded", 400)

        # Create metadata — use "results" key so the status endpoint can find them
        results = [
            {
                "prompt_id": f["filename"],
                "success": True,
                "image_path": f["image_path"],
                "thumbnail_path": f.get("thumbnail_path"),
                "generation_time": 0.0,
                "error": None,
                "metadata": {},
            }
            for f in uploaded_files
        ]
        metadata = {
            "batch_id": batch_id,
            "display_name": f"Uploaded Images - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "status": "completed",
            "total_images": len(uploaded_files),
            "completed_images": len(uploaded_files),
            "failed_images": 0,
            "start_time": datetime.now().isoformat(),
            "end_time": datetime.now().isoformat(),
            "type": "upload",
            "results": results,
        }

        metadata_file = batch_dir / "batch_metadata.json"
        with open(metadata_file, 'w') as f:
            import json
            json.dump(metadata, f, indent=2)

        logger.info(f"Created upload batch {batch_id} with {len(uploaded_files)} images")

        return success_response({
            "batch_id": batch_id,
            "uploaded_count": len(uploaded_files),
            "message": f"Successfully uploaded {len(uploaded_files)} image(s)"
        }, status_code=201)

    except Exception as e:
        logger.error(f"Error uploading images: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/template", methods=["GET"])
def get_csv_template():
    """Get CSV template for batch generation."""
    try:
        # Size/steps/guidance are left blank so each row takes the selected
        # model's own recipe (stills_defaults). Filled-in values are honoured
        # verbatim, so the sample keeps them empty rather than shipping SD-1.5
        # numbers that would run unchanged on Z-Image or FLUX.
        template_content = (
            "prompt,negative_prompt,style,width,height,steps,guidance,seed\n"
            '"A beautiful sunset over mountains",,"realistic",,,,,\n'
            '"A cat sitting on a windowsill","blurry, low quality","artistic",,,,,42\n'
            '"Abstract geometric patterns in blue","","artistic",1024,1024,,,\n'
            '"Portrait of a wise old wizard","cartoon, anime","realistic",,,,,123\n'
        )

        # Served straight from memory: the previous temp-file + send_file +
        # unlink sequence deleted the file before Flask streamed it.
        return Response(
            template_content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename="batch_generation_template.csv"'},
        )

    except Exception as e:
        logger.error(f"Error generating CSV template: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/generate/blueprints", methods=["POST"])
def generate_blueprints_batch():
    """
    Generates 'Data Blueprint' images for cities based on CSV input.
    Fast, offline, CPU-only. Runs in a background thread so large batches (e.g. 1000+ rows)
    do not timeout the request; client should poll /api/batch-image/status/<batch_id>.
    """
    try:
        if 'file' not in request.files:
            return error_response("No CSV file uploaded", 400)

        file = request.files['file']
        is_valid, message = _validate_csv_upload(file)
        if not is_valid:
            return error_response(message, 400)

        try:
            csv_content = file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return error_response("CSV must be UTF-8 encoded", 400)
        stream = io.StringIO(csv_content, newline=None)
        reader = csv.DictReader(stream)
        row_count = sum(1 for row in reader if (row.get('city') or row.get('City') or row.get('name')))

        if row_count > BLUEPRINT_MAX_ROWS:
            return error_response(
                f"CSV has {row_count} rows with city data. Maximum is {BLUEPRINT_MAX_ROWS}. "
                "Split the file into smaller batches or reduce the number of rows.",
                400
            )

        if row_count == 0:
            return error_response("No rows with city data found in CSV", 400)

        generator = get_batch_image_generator()
        batch_id = generator.start_blueprint_batch(csv_content)  # returns immediately; work runs in background

        return success_response({
            "batch_id": batch_id,
            "message": f"Blueprint batch started ({row_count} rows). Poll status at /api/batch-image/status/{batch_id}",
            "type": "blueprint",
            "total_images": row_count,
        }, status_code=201)

    except Exception as e:
        logger.error(f"Error starting blueprint batch: {e}")
        return error_response(str(e), 500)

# Error handlers
@batch_image_bp.errorhandler(413)
def request_entity_too_large(error):
    """Handle file too large error."""
    return error_response("File too large", 413)

@batch_image_bp.errorhandler(400)
def bad_request(error):
    """Handle bad request errors."""
    return error_response("Bad request", 400)

@batch_image_bp.errorhandler(500)
def internal_server_error(error):
    """Handle internal server errors."""
    logger.error(f"Internal server error in batch image API: {error}")
    return error_response("Internal server error", 500)