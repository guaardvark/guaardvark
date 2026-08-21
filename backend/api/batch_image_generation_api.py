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
        cancel_batch
    )
    from backend.utils.response_utils import success_response, error_response
    from backend.utils.unified_progress_system import ProcessType
    service_available = True
except ImportError as e:
    logger.error(f"Failed to import batch generation dependencies: {e}")
    service_available = False
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

# Global variables for tracking model download status
model_download_status = {
    "is_downloading": False,
    "current_model": None,
    "progress": 0,
    "status": "idle",
    "error": None,
    "speed_mbps": 0,
    "downloaded_gb": 0,
    "total_gb": 0,
}
model_download_lock = threading.Lock()

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
}

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

def _validate_csv_upload(file):
    """Validate uploaded CSV file."""
    if not file or file.filename == '':
        return False, "No file provided"

    if not file.filename.lower().endswith('.csv'):
        return False, "File must be a CSV file"

    # Check file size (max 5MB)
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning

    if file_size > 5 * 1024 * 1024:  # 5MB
        return False, "File too large (max 5MB)"

    return True, "Valid"

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
    params['preserve_order'] = bool(data.get('preserve_order', True))
    params['generate_thumbnails'] = bool(data.get('generate_thumbnails', True))
    params['save_metadata'] = bool(data.get('save_metadata', True))
    if 'ui_config' in data:
        params['ui_config'] = data['ui_config']

    # Model selection — validate against the canonical catalog (single source of
    # truth) so this can never drift from offline_image_generator.available_models.
    # 'auto' is allowed: the generator's router picks the best downloaded model.
    try:
        from backend.services.offline_image_generator import get_image_generator
        valid_models = set(get_image_generator().available_models.keys()) | {'auto'}
    except Exception:
        valid_models = {'auto'}
    model = data.get('model', 'auto')
    params['model'] = model if model in valid_models else 'auto'

    # Default image parameters — family-aware (stills_defaults), not SD-era 512/20/7.5
    from backend.services.stills_defaults import resolve_stills_defaults
    params['style'] = data.get('style', 'realistic')
    params['negative_prompt'] = str(data.get('negative_prompt') or '').strip()
    _raw_w = data.get('width', None)
    _raw_h = data.get('height', None)
    _raw_steps = data.get('steps', None)
    _raw_g = data.get('guidance', None)
    _resolved = resolve_stills_defaults(
        params['model'],
        width=int(_raw_w) if _raw_w is not None else None,
        height=int(_raw_h) if _raw_h is not None else None,
        steps=int(_raw_steps) if _raw_steps is not None else None,
        guidance=float(_raw_g) if _raw_g is not None else None,
        replace_legacy_sd_markers=True,
    )
    params['width'] = int(_resolved['width'])
    params['height'] = int(_resolved['height'])
    # Absolute step bounds 1–100 — model validator recommends ranges but does not
    # throttle quality when hard_clamp=False (FLUX / Z-Image / Krea).
    params['steps'] = min(max(int(_resolved['steps']), 1), 100)

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
                params.update(validation_result.corrected_values)
                validation_info["corrected_values"] = validation_result.corrected_values

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
    params['content_preset'] = data.get('content_preset')  # None = auto-detect
    params['auto_enhance'] = data.get('auto_enhance', True)
    params['enhance_anatomy'] = data.get('enhance_anatomy', True)
    params['enhance_faces'] = data.get('enhance_faces', True)
    params['enhance_hands'] = data.get('enhance_hands', True)

    # Director intelligence (opt-in; mirrors batch-video exactly). Safe defaults = disabled.
    params['director_mode'] = bool(data.get('director_mode', False))
    params['director_guidance'] = data.get('director_guidance') or data.get('extra_guidance')
    params['storyboard_concept'] = data.get('storyboard_concept')
    params['planning_mode'] = data.get('planning_mode', 'narrative')
    params['director_model'] = data.get('director_model')
    params['user_treatment'] = data.get('user_treatment') or data.get('treatment')

    # Face restoration parameters
    params['restore_faces'] = data.get('restore_faces', False)
    params['face_restoration_weight'] = float(data.get('face_restoration_weight', 0.5))

    # Transparent-background (rembg post-process) — RGBA PNG for icons/clip-art/logos
    params['remove_background'] = bool(data.get('remove_background', False))

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
        except Exception as status_error:
            logger.error(f"Error creating basic status: {status_error}")
            status = {"service_available": False, "error": str(status_error)}

        return success_response(status)

    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return error_response(str(e), 500)


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
        # and a per-model usability verdict. Single source of truth.
        meta = generator.image_generator.get_available_models()
        models = []
        unavailable = []
        for model_id, info in sorted(meta.items(), key=lambda kv: kv[1].get("order", 99)):
            row = {
                "id": model_id,
                "path": info["id"],
                "is_downloaded": info["downloaded"],
                "name": info.get("label", model_id),
                "label": info.get("label", model_id),
                "description": info.get("description", ""),
                "recommended": info.get("recommended", False),
                "size_gb": IMAGE_MODEL_SIZES.get(info["id"], 2.5),
                "availability": info.get("availability", "downloadable"),
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

        return success_response({
            "models": models,
            "unavailable_models": unavailable,
            "default_model": "auto",
        })

    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return error_response(str(e), 500)


@batch_image_bp.route("/models/download", methods=["POST"])
def download_model():
    """Start downloading a specific model with real-time file size progress monitoring."""
    global model_download_status

    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data or 'model_path' not in data:
            return error_response("No model_path provided", 400)

        model_path = data['model_path']
        generator = get_batch_image_generator()

        if not generator.image_generator:
            return error_response("Image generator not initialized", 503)

        catalog_key, hf_model_id = _resolve_catalog_model(model_path)
        if not hf_model_id:
            return error_response(
                f"Unknown model '{model_path}' — not in the allowed model set", 400
            )

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

        estimated_size_gb = IMAGE_MODEL_SIZES.get(catalog_key, IMAGE_MODEL_SIZES.get(hf_model_id, 2.5))

        with model_download_lock:
            if model_download_status["is_downloading"]:
                return error_response(f"Already downloading model: {model_download_status['current_model']}", 409)

            model_download_status = {
                "is_downloading": True,
                "current_model": hf_model_id,
                "progress": 0,
                "status": "starting",
                "error": None,
                "speed_mbps": 0,
                "downloaded_gb": 0,
                "total_gb": estimated_size_gb,
            }

        def download_task(hf_model_id, total_gb):
            _start_time = time.time()
            total_bytes = int(total_gb * 1024**3)

            try:
                with model_download_lock:
                    model_download_status["status"] = "downloading"

                # Monitor download progress by watching file sizes on disk
                stop_monitor = threading.Event()

                def _monitor_progress():
                    while not stop_monitor.is_set():
                        try:
                            downloaded = 0
                            # Check HF cache for .incomplete files (active downloads)
                            cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
                            if cache_dir.exists():
                                for f in cache_dir.rglob("*.incomplete"):
                                    try:
                                        downloaded += f.stat().st_size
                                    except OSError:
                                        pass
                            # Check target model directory for completed files
                            target_dir = generator.image_generator._get_model_path(hf_model_id)
                            if target_dir.exists():
                                for f in target_dir.rglob("*"):
                                    if f.is_file():
                                        try:
                                            downloaded += f.stat().st_size
                                        except OSError:
                                            pass

                            elapsed = time.time() - _start_time
                            speed = (downloaded / (1024 * 1024)) / max(elapsed, 0.1)
                            pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)

                            with model_download_lock:
                                model_download_status.update({
                                    "progress": pct,
                                    "speed_mbps": round(speed, 1),
                                    "downloaded_gb": round(downloaded / 1024**3, 2),
                                })
                        except Exception:
                            pass
                        stop_monitor.wait(1.0)

                monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                monitor_thread.start()

                try:
                    success, dl_error = generator.image_generator._download_model(hf_model_id)
                finally:
                    stop_monitor.set()
                    monitor_thread.join(timeout=2)

                with model_download_lock:
                    if success:
                        model_download_status.update({
                            "status": "completed",
                            "progress": 100,
                            "downloaded_gb": total_gb,
                            "total_gb": total_gb,
                        })
                    else:
                        model_download_status.update({
                            "status": "failed",
                            "error": dl_error or "Failed to download model",
                            "progress": 0,
                        })
            except Exception as e:
                logger.error(f"Error in model download thread: {e}")
                with model_download_lock:
                    model_download_status.update({
                        "status": "failed",
                        "error": str(e),
                        "progress": 0,
                    })
            finally:
                with model_download_lock:
                    model_download_status["is_downloading"] = False

        # Start download in background
        thread = threading.Thread(target=download_task, args=(hf_model_id, estimated_size_gb))
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
    global model_download_status
    try:
        with model_download_lock:
            status = dict(model_download_status)
        if not status.get("is_downloading"):
            # Comfy-only entries (flux-dev) delegate to the video registry
            # downloader — mirror its status for flux plans so this modal's
            # poller tracks progress/completion of the delegated install.
            # Module-attribute access on purpose: the video module REBINDS its
            # status dict per download, so a from-import would go stale.
            from backend.api import batch_video_generation_api as _video_api
            with _video_api._video_model_download_lock:
                vstatus = dict(_video_api._video_model_download_status)
            if str(vstatus.get("current_model") or "").startswith("flux"):
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

    except ValueError as e:
        logger.warning(f"Invalid CSV data: {e}")
        return error_response(f"Invalid CSV: {str(e)}", 400)
    except Exception as e:
        logger.error(f"Error starting CSV batch generation: {e}")
        return error_response(str(e), 500)

@batch_image_bp.route("/generate/prompts", methods=["POST"])
def generate_from_prompts():
    """Start batch generation from JSON prompt list."""
    try:
        if not service_available:
            return error_response("Batch image generation service not available", 503)

        data = request.get_json()
        if not data:
            return error_response("No JSON data provided", 400)

        # Extract prompts
        prompts = data.get('prompts', [])
        logger.debug(f"Batch image API received prompts (count={len(prompts)})")
        if not prompts:
            return error_response("No prompts provided", 400)

        if not isinstance(prompts, list):
            return error_response("Prompts must be a list", 400)

        # Validate prompts
        validated_prompts = []
        for i, prompt in enumerate(prompts):
            if isinstance(prompt, str):
                validated_prompts.append(prompt.strip())
            elif isinstance(prompt, dict) and 'prompt' in prompt:
                validated_prompts.append(prompt['prompt'].strip())
            else:
                logger.warning(f"Invalid prompt at index {i}: {prompt}")

        if not validated_prompts:
            return error_response("No valid prompts found", 400)

        # Parse generation parameters
        params, validation_info = _parse_generation_params(data)

        # Character casting: subject_ids → identity core via render_character_still
        try:
            _apply_character_casting(data, params)
        except ValueError as e:
            return error_response(str(e), 400)

        cast_warnings = params.pop("_cast_warnings", None) or []
        if cast_warnings:
            validation_info.setdefault("warnings", []).extend(cast_warnings)

        # Start batch generation
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

        return success_response(response_data, status_code=201)

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
        status = generator.get_batch_status(batch_id)
        
        # If not in active batches, try to load from disk
        if not status:
            all_batches = generator.list_all_batches()
            status = next((b for b in all_batches if b.batch_id == batch_id), None)
            
            # If found on disk but needs results, load from metadata
            if status and request.args.get('include_results') == 'true':
                try:
                    import json
                    metadata_file = Path(status.output_dir) / "batch_metadata.json"
                    if metadata_file.exists():
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        
                        # Load results from metadata
                        if 'results' in metadata:
                            from backend.services.batch_image_generator import BatchImageResult
                            status.results = [
                                BatchImageResult(
                                    prompt_id=r.get("prompt_id", ""),
                                    success=r.get("success", False),
                                    image_path=r.get("image_path"),
                                    thumbnail_path=r.get("thumbnail_path"),
                                    generation_time=r.get("generation_time", 0.0),
                                    error=r.get("error"),
                                    metadata=r.get("metadata", {})
                                )
                                for r in metadata.get("results", [])
                            ]
                except Exception as e:
                    logger.warning(f"Failed to load results from metadata for batch {batch_id}: {e}")
        
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
            "retry_data": getattr(status, "retry_data", None),
            "progress_percentage": int((status.completed_images / status.total_images) * 100) if status.total_images > 0 else 0
        }

        # Include results if requested
        if request.args.get('include_results') == 'true':
            status_data['results'] = [
                {
                    "prompt_id": r.prompt_id,
                    "success": r.success,
                    "image_path": r.image_path,
                    "thumbnail_path": r.thumbnail_path,
                    "generation_time": r.generation_time,
                    "error": r.error,
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
                    "status": batch.status,
                    "total_images": batch.total_images,
                    "completed_images": batch.completed_images,
                    "failed_images": batch.failed_images,
                    "start_time": batch.start_time.isoformat() if batch.start_time else None,
                    "end_time": batch.end_time.isoformat() if batch.end_time else None,
                    "retry_data": getattr(batch, "retry_data", None),
                    "can_retry": bool(getattr(batch, "retry_data", None)),
                    "progress_percentage": int((batch.completed_images / batch.total_images) * 100) if batch.total_images > 0 else 0
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

        status = get_batch_status(batch_id)
        if not status:
            return error_response("Batch not found", 404)

        if status.status not in ["completed", "cancelled"]:
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

            zip_filename = f"batch_{batch_id}_results.zip"

            def generate():
                with open(temp_zip.name, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
                os.unlink(temp_zip.name)

            return Response(
                stream_with_context(generate()),
                mimetype='application/zip',
                headers={'Content-Disposition': f'attachment; filename={zip_filename}'}
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
    status = generator.get_batch_status(batch_id)
    if status and status.output_dir:
        return Path(status.output_dir)

    safe_id = secure_filename(batch_id)
    if safe_id:
        direct = Path(generator.base_output_dir) / safe_id
        if (direct / "batch_metadata.json").exists():
            return direct

    match = next((b for b in generator.list_all_batches() if b.batch_id == batch_id), None)
    if match and match.output_dir:
        return Path(match.output_dir)
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
        status = generator.get_batch_status(batch_id)
        
        # If not in active batches, try to load from disk
        if not status:
            all_batches = generator.list_all_batches()
            status = next((b for b in all_batches if b.batch_id == batch_id), None)
        
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        output_dir = Path(status.output_dir)
        images_dir = output_dir / "images"
        thumbnails_dir = output_dir / "thumbnails"

        image_path = _contained_file(images_dir, image_name)
        thumbnail_path = _contained_file(thumbnails_dir, image_name)
        if image_path is None or thumbnail_path is None:
            return error_response("Invalid image name", 400)

        deleted_files = []
        errors = []

        if image_path.exists():
            try:
                image_path.unlink()
                deleted_files.append(str(image_path))
                logger.info(f"Deleted image: {image_path}")
            except Exception as e:
                errors.append(f"Failed to delete image: {str(e)}")

        if thumbnail_path.exists():
            try:
                thumbnail_path.unlink()
                deleted_files.append(str(thumbnail_path))
                logger.info(f"Deleted thumbnail: {thumbnail_path}")
            except Exception as e:
                errors.append(f"Failed to delete thumbnail: {str(e)}")

        if not deleted_files:
            return error_response(f"Image not found: {image_name}", 404)

        # Update batch metadata if it exists
        metadata_file = output_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                # Update results to remove deleted image
                if 'results' in metadata:
                    metadata['results'] = [
                        r for r in metadata['results']
                        if r.get('image_path') and Path(r['image_path']).name != image_name
                    ]
                
                # Update counts
                if 'completed_images' in metadata:
                    metadata['completed_images'] = max(0, metadata.get('completed_images', 0) - 1)
                
                metadata['updated_at'] = datetime.now().isoformat()
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not update metadata: {e}")

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
        status = generator.get_batch_status(batch_id)
        
        # If not in active batches, try to load from disk
        if not status:
            all_batches = generator.list_all_batches()
            status = next((b for b in all_batches if b.batch_id == batch_id), None)
        
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        output_dir = Path(status.output_dir)
        images_dir = output_dir / "images"
        thumbnails_dir = output_dir / "thumbnails"

        old_image_path = _contained_file(images_dir, image_name)
        old_thumbnail_path = _contained_file(thumbnails_dir, image_name)
        if old_image_path is None or old_thumbnail_path is None:
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

        if old_thumbnail_path.exists():
            new_thumbnail_path = thumbnails_dir / safe_new_name
            try:
                old_thumbnail_path.rename(new_thumbnail_path)
                logger.info(f"Renamed thumbnail: {old_thumbnail_path} -> {new_thumbnail_path}")
            except Exception as e:
                logger.warning(f"Failed to rename thumbnail: {e}")

        # Update batch metadata if it exists
        metadata_file = output_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                # Update results to reflect new filename
                if 'results' in metadata:
                    for result in metadata['results']:
                        if result.get('image_path') and Path(result['image_path']).name == image_name:
                            # Update image_path
                            old_path = result.get('image_path', '')
                            if old_path:
                                result['image_path'] = str(Path(old_path).parent / safe_new_name)
                            
                            # Update thumbnail_path if it exists
                            if result.get('thumbnail_path'):
                                old_thumb_path = result.get('thumbnail_path', '')
                                result['thumbnail_path'] = str(Path(old_thumb_path).parent / safe_new_name)
                
                metadata['updated_at'] = datetime.now().isoformat()
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not update metadata: {e}")

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
        status = generator.get_batch_status(batch_id)
        
        # If not in active batches, try to load from disk
        if not status:
            all_batches = generator.list_all_batches()
            status = next((b for b in all_batches if b.batch_id == batch_id), None)
        
        if not status:
            return error_response("Batch not found", 404)

        # Check if batch is still running
        if status.status == 'running':
            return error_response("Wait for generation to finish before deleting.", 400)

        # Delete the batch directory
        if status.output_dir and os.path.exists(status.output_dir):
            import shutil
            try:
                shutil.rmtree(status.output_dir)
                logger.info(f"Deleted batch directory: {status.output_dir}")
            except Exception as e:
                logger.error(f"Error deleting batch directory: {e}")
                return error_response(f"Failed to delete batch files: {str(e)}", 500)

        # Remove from active batches if present
        if batch_id in generator.active_batches:
            with generator.batch_lock:
                generator.active_batches.pop(batch_id, None)

        return success_response({
            "batch_id": batch_id,
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
        status = generator.get_batch_status(batch_id)
        
        # If not in active batches, try to load from disk
        if not status:
            all_batches = generator.list_all_batches()
            status = next((b for b in all_batches if b.batch_id == batch_id), None)
        
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

        # Update metadata file
        metadata_file = Path(status.output_dir) / "batch_metadata.json"
        if metadata_file.exists():
            try:
                import json
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
                import json
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
        status = generator.get_batch_status(batch_id)
        
        # If not in active batches, try to load from disk
        if not status:
            all_batches = generator.list_all_batches()
            status = next((b for b in all_batches if b.batch_id == batch_id), None)
        
        if not status or not status.output_dir:
            return error_response("Batch not found", 404)

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
                import json
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                metadata['folder'] = folder_name if folder_name else None
                metadata['updated_at'] = datetime.now().isoformat()
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not update metadata: {e}")

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
        # Create sample CSV template
        template_content = """prompt,negative_prompt,style,width,height,steps,guidance,seed
"A beautiful sunset over mountains",,"realistic",512,512,20,7.5,
"A cat sitting on a windowsill","blurry, low quality","artistic",512,512,25,8.0,42
"Abstract geometric patterns in blue","","artistic",768,768,30,7.0,
"Portrait of a wise old wizard","cartoon, anime","realistic",512,512,20,7.5,123
"""

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as temp_file:
            temp_file.write(template_content)
            temp_file_path = temp_file.name

        # Send file
        response = send_file(
            temp_file_path,
            as_attachment=True,
            download_name="batch_generation_template.csv",
            mimetype='text/csv'
        )

        # Clean up temp file after sending
        try:
            os.unlink(temp_file_path)
        except OSError:
            pass

        return response

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
        if not file.filename.endswith('.csv'):
            return error_response("Must be a CSV file", 400)

        csv_content = file.read().decode("UTF-8")
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