"""
Batch Video Generation API

Endpoints mirror the batch image generation API but for video workflows,
including frame-by-frame generation to reduce memory usage.
"""

import json
import logging
import os
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from flask import Blueprint, request, send_file
from werkzeug.utils import secure_filename

from backend.utils.response_utils import success_response, error_response
from backend.services.batch_video_generator import get_batch_video_generator
# Single source of truth for video-model file layout (download dst == install
# check == ComfyUI loader paths). See backend/services/video_model_registry.py.
from backend.services.video_model_registry import (
    VIDEO_MODEL_REGISTRY,
    DEFAULT_T2V_MODEL,
    DEFAULT_I2V_MODEL,
    preflight_video_model,
    classify_hf_download_error,
    model_capabilities,
    tier_defaults_for,
    resolve_active_video_model,
    clip_defaults_for,
)

# GPU Resource Coordinator for pre-flight availability check
try:
    from backend.services.gpu_resource_coordinator import get_gpu_coordinator
    gpu_coordinator_available = True
except ImportError:
    gpu_coordinator_available = False
    get_gpu_coordinator = None

logger = logging.getLogger(__name__)


def _gpu_queue_hint():
    """Non-blocking snapshot for enqueue responses — never rejects the job."""
    if not gpu_coordinator_available or not get_gpu_coordinator:
        return {"gpu_busy": False, "owner": None}
    try:
        status = get_gpu_coordinator().get_gpu_status()
        available = bool(status.get("available", True))
        return {
            "gpu_busy": not available,
            "owner": status.get("owner"),
            "message": (
                None if available
                else f"GPU currently in use by {status.get('owner', 'unknown')}; "
                     "your batch is queued and will start when the GPU is free."
            ),
        }
    except Exception as e:
        logger.warning(f"GPU queue hint failed: {e}")
        return {"gpu_busy": False, "owner": None}


batch_video_bp = Blueprint("batch_video", __name__, url_prefix="/api/batch-video")


import re as _re


@batch_video_bp.url_value_preprocessor
def _reject_unsafe_batch_id(endpoint, values):
    """Path-traversal guard for every route taking <batch_id>.

    Batch ids are flat tokens (e.g. ``VideoBatch_05-30-2026_003``). The routes
    build ``batch_dir = base_output_dir / batch_id`` from this URL segment, so a
    value like ``../../x`` would escape the output dir (the per-file ``video_name``
    check uses the *unresolved* batch_dir and does not catch this). Rejecting any
    non-token batch_id here closes the hole once for all routes.
    """
    if values and isinstance(values.get("batch_id"), str):
        if not _re.fullmatch(r"[A-Za-z0-9_\-]+", values["batch_id"]):
            from flask import abort
            abort(404)


def _parse_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            # Fallback: split by newlines/commas
            parts = [v.strip() for v in value.replace("\r", "").split("\n") if v.strip()]
            if not parts:
                parts = [v.strip() for v in value.split(",") if v.strip()]
            return parts
    return []


def _parse_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _resolve_request_model(data, role: str):
    """Explicit body model, else the active-video-model resolver."""
    explicit = (data.get("model") or "").strip() or None
    model_id, err = resolve_active_video_model(role, explicit)
    if err:
        return None, err
    return model_id, None


def _clip_params(data, model_id: str) -> dict:
    """Fill omitted fps/frames/steps/canvas from the model's native defaults."""
    defaults = clip_defaults_for(model_id)
    def _num(key, fallback):
        raw = data.get(key)
        if raw in (None, ""):
            return fallback
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback
    return {
        "duration_frames": _num("duration_frames", defaults["duration_frames"]),
        "fps": _num("fps", defaults["fps"]),
        "width": _num("width", defaults["width"]),
        "height": _num("height", defaults["height"]),
        "num_inference_steps": _num("num_inference_steps", defaults["num_inference_steps"]),
    }


@batch_video_bp.route("/generate/text", methods=["POST"])
def generate_text_to_video_batch():
    """
    Start a text-to-video batch generation.
    Body can be JSON or form-data.

    Returns immediately with status='queued'. The worker thread drains the queue
    one batch at a time, so concurrent submissions stack rather than collide.
    """
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        prompts = _parse_list(data.get("prompts"))
        # Storyboard mode: expand ONE concept into N shot clips. Create N placeholder
        # items here (the raw concept); the orchestrator rewrites them into connected
        # shots via the Storyboard agent in the background worker. Capped to keep a typo
        # from queuing hundreds of renders.
        storyboard_concept = (data.get("storyboard_concept") or "").strip()
        storyboard_shots = int(data.get("storyboard_shots") or 0)
        if storyboard_concept and storyboard_shots > 0:
            prompts = [storyboard_concept] * max(1, min(storyboard_shots, 50))
        if not prompts:
            return error_response("No prompts provided", 400)

        model_id, resolve_err = _resolve_request_model(data, "t2v")
        if resolve_err:
            return error_response(resolve_err, 400)
        ready, preflight_err = preflight_video_model(model_id)
        if not ready:
            return error_response(preflight_err, 400)
        # Per-prompt guides (audio or image anchors) on models that declare
        # audio_in: a list per prompt of {"kind", "path", "frame_idx", ...}.
        guides = data.get("guides") if isinstance(data.get("guides"), list) else []
        clip = _clip_params(data, model_id)

        params = {
            "model": model_id,
            "duration_frames": clip["duration_frames"],
            "fps": clip["fps"],
            "width": clip["width"],
            "height": clip["height"],
            "motion_strength": float(data.get("motion_strength", 1.0)),
            "num_inference_steps": clip["num_inference_steps"],
            "guidance_scale": float(data.get("guidance_scale", 7.5)),
            "seed": _parse_int(data.get("seed")),
            "generate_frames_only": str(data.get("generate_frames_only", "false")).lower() == "true",
            "frames_per_batch": int(data.get("frames_per_batch", 1)),
            "combine_frames": str(data.get("combine_frames", "false")).lower() == "true",
            "interpolation_multiplier": int(data.get("interpolation_multiplier", 2)),
            "prompt_style": data.get("prompt_style", "cinematic"),
            "enhance_prompt": str(data.get("enhance_prompt", "true")).lower() != "false",
            "fidelity_mode": str(data.get("fidelity_mode", data.get("preserve_text_fidelity", "false"))).lower() == "true",
            "wan_sampler_profile": (data.get("wan_sampler_profile") or None),
            "negative_prompt": data.get("negative_prompt", "") or "",
            "freeu": str(data.get("freeu", "false")).lower() == "true",
            "face_restore": str(data.get("face_restore", "false")).lower() == "true",
            "lora_name": data.get("lora_name"),
            "lora_strength": float(data.get("lora_strength", 1.0)),
            # Capability-contract knobs; the generator validates them against
            # what the model declares.
            "speed_profile": data.get("speed_profile") or None,
            "style_embedding": data.get("style_embedding") or None,
            # Quality pipeline (v2.6.2) — opt-in cinematic director + keyframe->I2V.
            "director_mode": str(data.get("director_mode", "false")).lower() == "true",
            "cinematic_keyframe": str(data.get("cinematic_keyframe", "false")).lower() == "true",
            "ui_config": data.get("ui_config"),
            "director_guidance": data.get("director_guidance") or None,
            # Trained cast members to lock into each clip. The video model can't apply
            # a LoRA, so each selected character's SDXL LoRA is baked into a cinematic
            # keyframe (which then seeds I2V) — selecting cast implies cinematic mode.
            "subject_ids": _parse_list(data.get("subject_ids")),
            "storyboard_concept": storyboard_concept or None,
            "metadata": {
                **(data.get("metadata") or {}),
                "upscale": str(data.get("upscale", "false")).lower() == "true",
                "teacache_threshold": float(data.get("teacache_threshold")) if data.get("teacache_threshold") else None,
                "feta_weight": float(data.get("feta_weight")) if data.get("feta_weight") else None,
                "high_consistency": str(data.get("high_consistency", "false")).lower() == "true",
                # A step count the person typed keeps priority over a model's
                # step floor; preset-driven values are raised to the floor.
                "steps_explicit": str(data.get("steps_explicit", "false")).lower() == "true",
            },
        }

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)

        gpu_hint = _gpu_queue_hint()
        status = generator.start_batch_from_prompts(prompts=prompts, guides=guides, **params)
        return success_response({
            "batch_id": status.batch_id,
            "status": status.status,
            "stage": getattr(status, "stage", "queued"),
            "gpu": gpu_hint,
        })
    except Exception as e:
        logger.error(f"Failed to start text-to-video batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/generate/image", methods=["POST"])
def generate_image_to_video_batch():
    """
    Start an image-to-video batch generation. Expects image paths or IDs provided by client.

    Same queueing behaviour as the text endpoint — returns immediately with
    status='queued' and stacks behind any in-flight batch.
    """
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        image_paths = _parse_list(data.get("image_paths") or data.get("image_ids"))
        if not image_paths:
            return error_response("No image_paths provided", 400)
        # Index-paired with image_paths on models that declare l2v/flf2v and
        # audio_in. guides is a list per item of {"kind", "path", "frame_idx", ...}.
        last_frame_paths = _parse_list(data.get("last_frame_paths"))
        guides = data.get("guides") if isinstance(data.get("guides"), list) else []

        model_id, resolve_err = _resolve_request_model(data, "i2v")
        if resolve_err:
            return error_response(resolve_err, 400)
        ready, preflight_err = preflight_video_model(model_id)
        if not ready:
            return error_response(preflight_err, 400)
        clip = _clip_params(data, model_id)

        params = {
            "prompt": data.get("prompt", ""),
            "model": model_id,
            "duration_frames": clip["duration_frames"],
            "fps": clip["fps"],
            "width": clip["width"],
            "height": clip["height"],
            "motion_strength": float(data.get("motion_strength", 1.0)),
            "num_inference_steps": clip["num_inference_steps"],
            "guidance_scale": float(data.get("guidance_scale", 7.5)),
            "seed": _parse_int(data.get("seed")),
            "generate_frames_only": str(data.get("generate_frames_only", "false")).lower() == "true",
            "frames_per_batch": int(data.get("frames_per_batch", 1)),
            "combine_frames": str(data.get("combine_frames", "false")).lower() == "true",
            "interpolation_multiplier": int(data.get("interpolation_multiplier", 2)),
            "prompt_style": data.get("prompt_style", "cinematic"),
            "enhance_prompt": str(data.get("enhance_prompt", "true")).lower() != "false",
            "fidelity_mode": str(data.get("fidelity_mode", data.get("preserve_text_fidelity", "false"))).lower() == "true",
            "wan_sampler_profile": (data.get("wan_sampler_profile") or None),
            "negative_prompt": data.get("negative_prompt", "") or "",
            "freeu": str(data.get("freeu", "false")).lower() == "true",
            "face_restore": str(data.get("face_restore", "false")).lower() == "true",
            "lora_name": data.get("lora_name"),
            "lora_strength": float(data.get("lora_strength", 1.0)),
            # Capability-contract knobs; the generator validates them against
            # what the model declares.
            "speed_profile": data.get("speed_profile") or None,
            "style_embedding": data.get("style_embedding") or None,
            # Quality pipeline (v2.6.2) — opt-in cinematic director + keyframe->I2V.
            "director_mode": str(data.get("director_mode", "false")).lower() == "true",
            "cinematic_keyframe": str(data.get("cinematic_keyframe", "false")).lower() == "true",
            "ui_config": data.get("ui_config"),
            "director_guidance": data.get("director_guidance") or None,
            "metadata": {
                **(data.get("metadata") or {}),
                "upscale": str(data.get("upscale", "false")).lower() == "true",
                "teacache_threshold": float(data.get("teacache_threshold")) if data.get("teacache_threshold") else None,
                "feta_weight": float(data.get("feta_weight")) if data.get("feta_weight") else None,
                "high_consistency": str(data.get("high_consistency", "false")).lower() == "true",
                # A step count the person typed keeps priority over a model's
                # step floor; preset-driven values are raised to the floor.
                "steps_explicit": str(data.get("steps_explicit", "false")).lower() == "true",
            },
        }

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)

        gpu_hint = _gpu_queue_hint()
        status = generator.start_batch_from_images(
            image_paths=image_paths, last_frame_paths=last_frame_paths, guides=guides, **params
        )
        return success_response({
            "batch_id": status.batch_id,
            "status": status.status,
            "stage": getattr(status, "stage", "queued"),
            "gpu": gpu_hint,
        })
    except Exception as e:
        logger.error(f"Failed to start image-to-video batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/enhance-preview", methods=["POST"])
def enhance_prompt_preview():
    """
    Lightweight preview of what the prompt enhancer will produce.

    Frontend calls this (or can call client-side in future) to show the user
    the final enhanced prompt + the default negative that will be used.

    Accepts the same fields used at generation time:
      prompt (or prompts[0] for batch), style, width, height, fidelity_mode, model, etc.
    Returns the strings that would actually be sent to the model.
    """
    try:
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        # Support both single prompt and the batch "prompts" list (take first for preview)
        prompt = data.get("prompt") or ""
        if not prompt:
            prompts = _parse_list(data.get("prompts"))
            prompt = prompts[0] if prompts else ""
        if not prompt:
            return error_response("No prompt provided for preview", 400)

        style = data.get("prompt_style", "cinematic")
        width = _parse_int(data.get("width")) or 0
        height = _parse_int(data.get("height")) or 0

        # fidelity_mode: UI "Exact text mode" / preserve fidelity toggle
        fidelity = str(data.get("fidelity_mode", data.get("preserve_text_fidelity", "false"))).lower() == "true"

        # model_family hint (frontend can send model or we infer)
        model = data.get("model", "")
        model_family = None
        if "ltx" in (model or "").lower():
            model_family = "ltx"
        elif "wan" in (model or "").lower():
            model_family = "wan"
        elif "cog" in (model or "").lower():
            model_family = "cogvideox"

        from backend.utils.prompt_enhancer import (
            enhance_video_prompt,
            get_default_negative_prompt,
            has_text_intent,
        )

        enhanced = enhance_video_prompt(
            prompt,
            style=style,
            width=width,
            height=height,
            fidelity_mode=fidelity,
            model_family=model_family,
        )

        # Default negative that the backend would inject if user left it blank
        default_neg = get_default_negative_prompt(style=style)

        return success_response({
            "original_prompt": prompt,
            "enhanced_prompt": enhanced,
            "default_negative_prompt": default_neg,
            "fidelity_mode": fidelity,
            "has_text_intent": has_text_intent(prompt),
            "model_family": model_family,
            "style": style,
        })
    except Exception as e:
        logger.error(f"Failed to generate prompt preview: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/status/<batch_id>", methods=["GET"])
def get_batch_status(batch_id: str):
    try:
        generator = get_batch_video_generator()
        status = generator.get_batch_status(batch_id)
        if not status:
            return error_response("Batch not found", 404)
        # serialize results
        results = [
            {
                "item_id": r.item_id,
                "success": r.success,
                "video_path": r.video_path,
                "frame_paths": r.frame_paths,
                "thumbnail_path": r.thumbnail_path,
                "error": r.error,
                "metadata": r.metadata,
            }
            for r in status.results
        ]
        return success_response(
            {
                "batch_id": status.batch_id,
                "status": status.status,
                "stage": getattr(status, "stage", None),
                "current_item": getattr(status, "current_item", None),
                "progress_pct": getattr(status, "progress_pct", None),
                "total_videos": status.total_videos,
                "completed_videos": status.completed_videos,
                "failed_videos": status.failed_videos,
                "start_time": status.start_time.isoformat() if status.start_time else None,
                "end_time": status.end_time.isoformat() if status.end_time else None,
                "results": results,
                "metadata": status.metadata,
                "output_dir": status.output_dir,
                "retry_data": getattr(status, "retry_data", None),
                "error": getattr(status, "error", None),
            }
        )
    except Exception as e:
        logger.error(f"Failed to get batch status: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/queue", methods=["GET"])
def get_queue():
    """
    Snapshot of the in-process batch queue for the UI panel.
    Returns batches in submission order with position numbers.
    """
    try:
        generator = get_batch_video_generator()
        return success_response({"queue": generator.list_queue()})
    except Exception as e:
        logger.error(f"Failed to get batch queue: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/list", methods=["GET"])
def list_batches():
    try:
        generator = get_batch_video_generator()
        batches = generator.list_batches()
        return success_response({"batches": batches})
    except Exception as e:
        logger.error(f"Failed to list video batches: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/video/<batch_id>/<path:video_name>", methods=["GET"])
def get_video(batch_id: str, video_name: str):
    try:
        generator = get_batch_video_generator()
        batch_dir = Path(generator.base_output_dir) / batch_id
        video_path = (batch_dir / video_name).resolve()
        try:
            video_path.relative_to(batch_dir)
        except ValueError:
            return error_response("Invalid video path", 400)

        if not video_path.exists():
            return error_response("Video not found", 404)

        ext = video_path.suffix.lower()
        mime_map = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")
        return send_file(str(video_path), mimetype=mime_type, as_attachment=False)
    except Exception as e:
        logger.error(f"Failed to serve video {video_name}: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/video/<batch_id>/<path:video_name>", methods=["DELETE"])
def delete_video(batch_id: str, video_name: str):
    try:
        generator = get_batch_video_generator()
        batch_dir = Path(generator.base_output_dir) / batch_id
        target_path = (batch_dir / video_name).resolve()
        try:
            target_path.relative_to(batch_dir)
        except ValueError:
            return error_response("Invalid video path", 400)

        if not target_path.exists():
            return error_response("Video not found", 404)

        target_path.unlink(missing_ok=True)

        # Update metadata if present
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                changed = False
                for res in data.get("results", []):
                    rel = res.get("video_path", "")
                    if rel and (rel == str(Path(video_name)) or rel.endswith(video_name)):
                        res["video_path"] = None
                        changed = True
                if changed:
                    with open(metadata_file, "w") as f:
                        json.dump(data, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to update metadata after delete: {e}")

        return success_response({"batch_id": batch_id, "deleted": video_name})
    except Exception as e:
        logger.error(f"Failed to delete video: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/video/<batch_id>/<path:video_name>/rename", methods=["PUT"])
def rename_video(batch_id: str, video_name: str):
    try:
        data = request.get_json(silent=True) or {}
        new_name = data.get("new_name", "").strip()
        if not new_name:
            return error_response("New name cannot be empty", 400)

        generator = get_batch_video_generator()
        batch_dir = Path(generator.base_output_dir) / batch_id
        src_path = (batch_dir / video_name).resolve()
        try:
            src_path.relative_to(batch_dir)
        except ValueError:
            return error_response("Invalid video path", 400)

        if not src_path.exists():
            return error_response("Video not found", 404)

        new_safe = secure_filename(new_name)
        dst_path = src_path.with_name(new_safe)
        if dst_path.exists():
            return error_response("A file with the new name already exists", 409)

        src_path.rename(dst_path)

        # Update metadata if present
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    meta = json.load(f)
                updated = False
                for res in meta.get("results", []):
                    rel = res.get("video_path", "")
                    if rel and (rel == str(Path(video_name)) or rel.endswith(video_name)):
                        res["video_path"] = str(dst_path.relative_to(batch_dir))
                        updated = True
                if updated:
                    with open(metadata_file, "w") as f:
                        json.dump(meta, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to update metadata after rename: {e}")

        return success_response({"batch_id": batch_id, "old_name": video_name, "new_name": new_safe})
    except Exception as e:
        logger.error(f"Failed to rename video: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/preview/<batch_id>", methods=["GET"])
def get_preview(batch_id: str):
    try:
        generator = get_batch_video_generator()
        thumb = generator.get_preview_thumbnail(batch_id)
        if not thumb or not thumb.exists():
            return error_response("Preview not found", 404)
        return send_file(str(thumb), mimetype="image/jpeg")
    except Exception as e:
        logger.error(f"Failed to get preview: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/batch/<batch_id>/cancel", methods=["POST"])
def cancel_batch(batch_id: str):
    """Cancel a running or stale batch."""
    try:
        generator = get_batch_video_generator()
        if generator.cancel_batch(batch_id):
            return success_response({"batch_id": batch_id, "message": "Batch cancelled"})
        return error_response("Batch not found or not in a cancellable state", 404)
    except Exception as e:
        logger.error(f"Failed to cancel batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/retry/<batch_id>", methods=["POST"])
def retry_batch(batch_id: str):
    """
    Retry a failed (or cancelled) batch using the exact original parameters
    that were persisted at submission time. This lets the UI offer a one-click
    "Retry" without the user having to re-type prompts, re-select images,
    re-choose model, steps, fidelity_mode, FreeU, LoRA, quality tier, etc.

    Returns the new batch_id (a fresh VideoBatch_... entry is created and queued).
    The original failed batch is left as-is in history.
    """
    try:
        generator = get_batch_video_generator()
        orig = generator.get_batch_status(batch_id)
        if not orig:
            return error_response("Batch not found", 404)
        rd = getattr(orig, "retry_data", None)
        if not rd:
            return error_response("This batch has no retry data (too old, or created before retry support)", 400)

        params = dict(rd.get("params") or {})
        mode = rd.get("mode")
        if mode == "text":
            prompts = rd.get("prompts") or []
            if not prompts:
                return error_response("Retry data is missing prompts", 400)
            # start_batch_from_prompts will rebuild items + enqueue
            new_status = generator.start_batch_from_prompts(prompts=prompts, **params)
        elif mode == "image":
            image_paths = rd.get("image_paths") or []
            if not image_paths:
                return error_response("Retry data is missing image_paths", 400)
            prompt = rd.get("prompt", "") or params.pop("prompt", "")
            if prompt:
                params["prompt"] = prompt
            new_status = generator.start_batch_from_images(image_paths=image_paths, **params)
        else:
            return error_response(f"Unknown retry mode: {mode}", 400)

        return success_response({
            "batch_id": new_status.batch_id,
            "status": new_status.status,
            "retried_from": batch_id,
        })
    except Exception as e:
        logger.error(f"Failed to retry batch {batch_id}: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/cancel-all", methods=["POST"])
def cancel_all_batches():
    """Cancel every queued/running video generation batch.

    Used by stop.sh and plugin shutdown so disabling ComfyUI or tearing down
    the stack actually kills in-flight VideoGen work instead of leaving the
    worker thread spinning against a dead backend.
    """
    try:
        generator = get_batch_video_generator()
        cancelled = generator.cancel_all_active(reason="Cancelled by shutdown")
        return success_response({
            "cancelled": cancelled,
            "count": len(cancelled),
            "message": f"Cancelled {len(cancelled)} batch(es)",
        })
    except Exception as e:
        logger.error(f"Failed to cancel all batches: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/delete/<batch_id>", methods=["DELETE"])
def delete_batch(batch_id: str):
    try:
        generator = get_batch_video_generator()
        if generator.delete_batch(batch_id):
            return success_response({"batch_id": batch_id, "message": "Batch deleted"})
        return error_response("Batch not found", 404)
    except Exception as e:
        logger.error(f"Failed to delete batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/rename/<batch_id>", methods=["PUT"])
def rename_batch(batch_id: str):
    try:
        data = request.get_json(silent=True) or {}
        new_name = data.get("name", "").strip()
        if not new_name:
            return error_response("Name cannot be empty", 400)
        generator = get_batch_video_generator()
        if generator.rename_batch(batch_id, new_name):
            return success_response({"batch_id": batch_id, "display_name": new_name})
        return error_response("Batch not found", 404)
    except Exception as e:
        logger.error(f"Failed to rename batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/download/<batch_id>", methods=["GET"])
def download_batch(batch_id: str):
    try:
        generator = get_batch_video_generator()
        batch_dir = Path(generator.base_output_dir) / batch_id
        if not batch_dir.exists():
            return error_response("Batch not found", 404)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(tmp_fd)
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in batch_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(batch_dir)
                    zipf.write(file_path, arcname)
        return send_file(tmp_path, as_attachment=True, download_name=f"{batch_id}.zip")
    except Exception as e:
        logger.error(f"Failed to download batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/combine-frames/<batch_id>", methods=["POST"])
def combine_frames(batch_id: str):
    try:
        data = request.get_json(silent=True) or {}
        fps = int(data.get("fps", 24))
        item_id = data.get("item_id")
        generator = get_batch_video_generator()
        combined = generator.combine_frames(batch_id, item_id=item_id, fps=fps)
        if not combined:
            return error_response("Failed to combine frames (missing frames?)", 400)
        return success_response({"batch_id": batch_id, "item_id": item_id, "video_path": combined})
    except Exception as e:
        logger.error(f"Failed to combine frames: {e}")
        return error_response(str(e), 500)


# ── Video Model Management ──────────────────────────────────────────────

# Model registry: maps model IDs to HuggingFace repos, local paths, and metadata
def _get_comfyui_models_dir():
    try:
        from backend.config import COMFYUI_DIR
    except ImportError:
        COMFYUI_DIR = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "plugins", "comfyui", "ComfyUI")
    return Path(COMFYUI_DIR) / "models"


# VIDEO_MODEL_REGISTRY is imported from backend.services.video_model_registry
# (the single source of truth). Don't redefine it here — that's exactly the
# three-copies-that-drift problem issue #36 fixed.

# ─── Download status tracking (hardened for issue #36) ───────────────────────
# The UI polls this dict to render the model-download modal, so it MUST always
# resolve to a terminal state (completed/failed) the UI can act on. A daemon
# download thread that dies, or a backend restart mid-download, used to leave it
# pinned at "downloading" forever — a spinner that never resolves and a 409 lock
# that never clears. Hardening:
#   - persisted to disk so a restart reconciles a stale "downloading" -> "failed"
#   - epoch-stamped so an orphaned thread can't clobber a newer download
#   - stall-detected by the monitor so a frozen HF pull fails instead of hanging
#   - a wedged lock is auto-taken-over once it goes stale (no restart needed)
_video_model_download_lock = threading.Lock()
_DOWNLOAD_STALL_SECONDS = 180  # no new bytes for this long => the pull is wedged
_DOWNLOAD_EPOCH = 0


def _download_status_path() -> Path:
    return Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "video_model_download_status.json"


def _idle_status() -> dict:
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
        "epoch": _DOWNLOAD_EPOCH,
    }


def _persist_download_status() -> None:
    """Best-effort write of the current status to disk. Caller holds the lock."""
    try:
        p = _download_status_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(_video_model_download_status, f)
        tmp.replace(p)
    except Exception:
        pass


def _reconcile_download_status_on_load() -> None:
    """Heal a status the previous process left mid-flight (issue #36).

    A daemon download thread cannot survive a backend restart, so any persisted
    'downloading'/'starting' state is necessarily dead. Turn it into an honest
    terminal 'failed' so the UI shows an error + retry instead of a spinner that
    never resolves and a 409 lock that never releases.
    """
    global _video_model_download_status, _DOWNLOAD_EPOCH
    try:
        p = _download_status_path()
        persisted = json.loads(p.read_text()) if p.exists() else None
    except Exception:
        persisted = None
    if not persisted:
        _video_model_download_status = _idle_status()
        return
    _DOWNLOAD_EPOCH = int(persisted.get("epoch", 0))
    if persisted.get("is_downloading") or persisted.get("status") in ("starting", "downloading"):
        persisted.update({
            "is_downloading": False,
            "status": "failed",
            "error": "Download was interrupted by a backend restart. Click Install to retry.",
            "progress": 0,
            "updated_at": time.time(),
        })
        _video_model_download_status = persisted
        _persist_download_status()
    else:
        _video_model_download_status = persisted


_video_model_download_status = _idle_status()
_reconcile_download_status_on_load()


def _check_model_downloaded(model_id: str) -> bool:
    """Check if a video model's files exist and are non-empty."""
    model_info = VIDEO_MODEL_REGISTRY.get(model_id)
    if not model_info:
        return False
    models_dir = _get_comfyui_models_dir()
    base = models_dir / model_info["local_subdir"]
    for check_file in model_info["check_files"]:
        fpath = base / check_file
        if not fpath.exists() or fpath.stat().st_size == 0:
            return False
    return True


def _missing_check_files(model_id: str) -> List[str]:
    """Return the check_files (model + companions) that are absent on disk — the
    precise reason a model isn't 'ready'. Surfaces the issue #36 case where a dir
    holds GBs of the wrong quant but the one required file is missing, instead of
    leaving the UI with an unexplained is_ready=False.
    """
    models_dir = _get_comfyui_models_dir()
    missing: List[str] = []
    for eid in _resolve_download_plan(model_id):
        info = VIDEO_MODEL_REGISTRY.get(eid, {})
        base = models_dir / info.get("local_subdir", "")
        for cf in info.get("check_files", []):
            fp = base / cf
            if not fp.exists() or fp.stat().st_size == 0:
                missing.append(f"{eid}:{cf}")
    return missing


def _resolve_download_plan(model_id: str) -> List[str]:
    """Expand a model id into [model + required companions], order-preserving.

    A WAN unet is dead weight without its VAE + text encoder; CogVideoX needs
    its T5 encoder. `requires` in the registry declares those companions so one
    Install click yields a render-ready set instead of a half-installed model.
    """
    plan: List[str] = []

    def _add(mid: str):
        if mid in plan or mid not in VIDEO_MODEL_REGISTRY:
            return
        plan.append(mid)
        for dep in VIDEO_MODEL_REGISTRY[mid].get("requires", []):
            _add(dep)

    _add(model_id)
    return plan


def _detected_total_vram_mb() -> int:
    """Total VRAM of the primary card, 0 when there is none or the probe
    fails. Read once per listing so the tier lookup does not probe per model."""
    try:
        from backend.services.gpu_resource_coordinator import get_available_vram
        return int((get_available_vram() or {}).get("total_mb") or 0)
    except Exception:
        return 0


@batch_video_bp.route("/models", methods=["GET"])
def list_video_models():
    """List all video models and their installation status."""
    try:
        models = []
        total_vram_mb = _detected_total_vram_mb()
        active_t2v, _ = resolve_active_video_model("t2v")
        active_i2v, _ = resolve_active_video_model("i2v")
        for model_id, info in VIDEO_MODEL_REGISTRY.items():
            plan = _resolve_download_plan(model_id)
            requires = info.get("requires", [])
            caps = model_capabilities(model_id)
            models.append({
                "id": model_id,
                "name": info["name"],
                "description": info["description"],
                "type": info["type"],
                "size_gb": info["size_gb"],
                "vram_mb": info["vram_mb"],
                # UI/SSOT dimension helpers (mirror videoGeneratorPresets MODEL_OPTIONS).
                "dimension_alignment": info.get("dimension_alignment"),
                "max_pixel_area": info.get("max_pixel_area"),
                # is_downloaded = this model's own files present.
                # is_ready = model + every required companion present (truly usable).
                "is_downloaded": _check_model_downloaded(model_id),
                "is_ready": all(_check_model_downloaded(e) for e in plan),
                # The exact files still missing (model + companions) — empty when
                # ready. Makes a partial/wrong-quant install diagnosable (issue #36).
                "missing_files": _missing_check_files(model_id),
                "requires": requires,
                # Total bytes an Install click will fetch (model + missing deps).
                "install_size_gb": round(
                    sum(VIDEO_MODEL_REGISTRY[e]["size_gb"]
                        for e in plan if not _check_model_downloaded(e)),
                    2,
                ),
                # Capability contract (registry SSOT): empty for companions.
                # tier_defaults is already resolved for this card's VRAM class
                # so the page seeds its controls without knowing the tiers.
                "capabilities": caps,
                "tier_defaults": tier_defaults_for(model_id, total_vram_mb) if caps else {},
                "license": info.get("license"),
                # LoRA companions name the generation entries they apply to.
                "applies_to": info.get("applies_to", []),
                "active": model_id in {active_t2v, active_i2v} and bool(model_id),
            })
        return success_response({"models": models, "active_t2v": active_t2v, "active_i2v": active_i2v})
    except Exception as e:
        logger.error(f"Error listing video models: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/prompt-presets", methods=["GET"])
def list_prompt_presets():
    """Prompt presets for a model family, read from plugins/comfyui/scripts/prompt_bundles.

    Each preset carries its structured intent and the prompt the compiler
    renders from it, plus format hints (duration, ratio, mode). The community
    gallery is returned as attributed links, never as copied prompt text
    (see the bundle's NOTICE.md).
    """
    try:
        from backend.services import h3_prompt_compiler as h3
        model_id = (request.args.get("model") or "").strip()
        family = (VIDEO_MODEL_REGISTRY.get(model_id) or {}).get("type") or request.args.get("family") or ""
        if family != "minimax":
            return success_response({"family": family, "presets": [], "gallery": None})
        bundle = json.loads((h3.BUNDLE_DIR / "presets.json").read_text(encoding="utf-8"))
        presets = []
        for preset in bundle.get("presets", []):
            intent = h3.intent_from_dict({**preset["intent"], "duration_s": preset.get("duration_s", 5),
                                          "mode": preset.get("mode", "t2va")})
            prompt, diag = h3.compile(intent)
            presets.append({
                "slug": preset["slug"], "title": preset["title"], "category": preset.get("category"),
                "duration_s": preset.get("duration_s"), "ratio": preset.get("ratio"),
                "mode": preset.get("mode", "t2va"), "style": preset.get("style"),
                "prompt": prompt, "intent": preset["intent"], "frames": diag["frames"],
            })
        gallery = None
        gallery_path = h3.BUNDLE_DIR / "gallery_index.json"
        if gallery_path.exists():
            gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
        return success_response({"family": family, "presets": presets, "gallery": gallery})
    except Exception as e:
        logger.error(f"Error listing prompt presets: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/models/download", methods=["POST"])
def download_video_model():
    """Start downloading a video model from HuggingFace."""
    data = request.get_json()
    if not data or "model_id" not in data:
        return error_response("No model_id provided", 400)
    return start_video_model_download(data["model_id"])


def start_video_model_download(model_id):
    """Start the registry download plan for model_id (+ required companions).

    Split from the route so the image-models download endpoint can delegate
    Comfy-only catalog entries (sentinel "comfy:" ids, e.g. flux-dev) here —
    their assets live under ComfyUI/models/, not the diffusers snapshot dir,
    and this downloader knows the gated repo + companion files. Returns a
    Flask response.
    """
    global _video_model_download_status, _DOWNLOAD_EPOCH
    try:
        if model_id not in VIDEO_MODEL_REGISTRY:
            return error_response(f"Unknown model: {model_id}", 400)

        # Build the install plan: the model itself plus any required companion
        # models (a WAN unet needs its VAE + text encoder, etc.). Dedupe while
        # preserving order. Only entries that aren't already on disk get pulled.
        plan = _resolve_download_plan(model_id)
        pending = [eid for eid in plan if not _check_model_downloaded(eid)]
        if not pending:
            return success_response({"message": f"{model_id} is already installed"})

        with _video_model_download_lock:
            now = time.time()
            st = _video_model_download_status
            # Self-heal a wedged lock: a prior download that claims to be running
            # but hasn't progressed within the stall window has a dead/stuck thread
            # behind it — take it over instead of returning 409 forever (issue #36).
            is_stale = (now - st.get("updated_at", 0)) > _DOWNLOAD_STALL_SECONDS
            if st.get("is_downloading") and not is_stale:
                return error_response(
                    f"Already downloading: {st.get('current_model')}", 409
                )
            _DOWNLOAD_EPOCH += 1
            my_epoch = _DOWNLOAD_EPOCH
            plan_total_gb = round(sum(VIDEO_MODEL_REGISTRY[e]["size_gb"] for e in pending), 2)
            _video_model_download_status = {
                "is_downloading": True,
                "current_model": model_id,
                "progress": 0,
                "status": "starting",
                "error": None,
                "speed_mbps": 0,
                "downloaded_gb": 0,
                "total_gb": plan_total_gb,
                "updated_at": now,
                "epoch": my_epoch,
            }
            _persist_download_status()

        def _download_task(plan_ids, my_epoch):
            global _video_model_download_status
            import shutil
            _start_time = time.time()
            stalled = threading.Event()

            def _is_current() -> bool:
                return _video_model_download_status.get("epoch") == my_epoch

            def _update_status(**kw) -> bool:
                # Only the owning epoch may write — a stale orphaned thread must
                # never clobber a newer download's status (issue #36).
                with _video_model_download_lock:
                    if not _is_current():
                        return False
                    _video_model_download_status.update(kw)
                    _video_model_download_status["updated_at"] = time.time()
                    _persist_download_status()
                    return True

            def _dir_bytes(d: Path) -> int:
                total = 0
                if d.exists():
                    for f in d.rglob("*"):
                        try:
                            if f.is_file():
                                total += f.stat().st_size
                        except OSError:
                            pass
                return total

            def _pull_one(einfo, local_dir):
                """Pull a single registry entry's files into local_dir."""
                if "direct_urls" in einfo:
                    import urllib.request
                    for spec in einfo["direct_urls"]:
                        dst = local_dir / spec["dst"]
                        if dst.exists() and dst.stat().st_size > 0:
                            continue
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        urllib.request.urlretrieve(spec["url"], str(dst))
                elif "files" in einfo:
                    # Explicit per-file pulls: only the bytes we actually need,
                    # placed at the exact name ComfyUI's loaders expect.
                    for spec in einfo["files"]:
                        dst = local_dir / spec["dst"]
                        if not (dst.exists() and dst.stat().st_size > 0):
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            got = Path(hf_hub_download(
                                repo_id=einfo["hf_repo"],
                                filename=spec["src"],
                                local_dir=str(local_dir),
                            ))
                            if got.resolve() != dst.resolve():
                                shutil.move(str(got), str(dst))
                        # Some ComfyUI loaders enumerate a DIFFERENT directory
                        # than where the file canonically lives (e.g.
                        # LTXVAudioVAELoader reads checkpoints/, audio VAEs
                        # live in vae/). also_link hardlinks the file there —
                        # previously a by-hand step that fresh installs missed,
                        # failing workflow validation ('Value not in list').
                        extra = spec.get("also_link")
                        if extra and dst.exists():
                            link_dst = models_dir / extra / dst.name
                            if not link_dst.exists():
                                link_dst.parent.mkdir(parents=True,
                                                      exist_ok=True)
                                try:
                                    os.link(str(dst), str(link_dst))
                                except OSError:
                                    shutil.copy2(str(dst), str(link_dst))
                elif "hf_filename" in einfo:
                    hf_hub_download(
                        repo_id=einfo["hf_repo"],
                        filename=einfo["hf_filename"],
                        local_dir=str(local_dir),
                    )
                    if einfo["check_files"][0] != einfo["hf_filename"]:
                        src = local_dir / einfo["hf_filename"]
                        dst = local_dir / einfo["check_files"][0]
                        if src.exists() and not dst.exists():
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(str(src), str(dst))
                else:
                    snapshot_download(
                        repo_id=einfo["hf_repo"],
                        local_dir=str(local_dir),
                    )

            last_repo = None
            try:
                from huggingface_hub import hf_hub_download, snapshot_download

                models_dir = _get_comfyui_models_dir()
                # Resolve each entry's target dir up front and snapshot a baseline
                # so progress reflects ONLY the bytes this run adds (dirs like
                # unet/ may already hold other models). Unique dirs only, so a
                # shared dir isn't double-counted.
                entries = []
                for eid in plan_ids:
                    einfo = VIDEO_MODEL_REGISTRY[eid]
                    ldir = models_dir / einfo["local_subdir"]
                    ldir.mkdir(parents=True, exist_ok=True)
                    entries.append((eid, einfo, ldir))
                uniq_dirs = list({str(ldir): ldir for (_, _, ldir) in entries}.values())
                total_bytes = int(sum(e[1]["size_gb"] for e in entries) * 1024**3)
                baselines = {str(d): _dir_bytes(d) for d in uniq_dirs}

                _update_status(status="downloading")

                # Progress = (current bytes across target dirs) − baseline. This
                # tracks the real .incomplete staging that hf_hub_download writes
                # under local_dir; the old monitor watched ~/.cache/huggingface/hub
                # (the wrong place when local_dir is set) and sat frozen at 0%.
                stop_monitor = threading.Event()

                def _monitor_progress():
                    last_bytes = -1
                    last_change = time.time()
                    while not stop_monitor.is_set():
                        try:
                            downloaded = sum(
                                max(0, _dir_bytes(d) - baselines[str(d)]) for d in uniq_dirs
                            )
                            now = time.time()
                            if downloaded != last_bytes:
                                last_bytes = downloaded
                                last_change = now
                            elif (now - last_change) > _DOWNLOAD_STALL_SECONDS:
                                # No new bytes for the whole stall window: the HF
                                # pull is wedged (dead socket, throttled to zero).
                                # Fail honestly so the UI can retry instead of
                                # spinning forever at a frozen % (issue #36).
                                stalled.set()
                                _update_status(
                                    status="failed",
                                    is_downloading=False,
                                    progress=0,
                                    error=(f"Download stalled — no progress for "
                                           f"{_DOWNLOAD_STALL_SECONDS}s. Check your "
                                           f"network and click Install to retry."),
                                )
                                stop_monitor.set()
                                break
                            elapsed = now - _start_time
                            speed = (downloaded / (1024 * 1024)) / max(elapsed, 0.1)
                            pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)
                            _update_status(
                                progress=pct,
                                speed_mbps=round(speed, 1),
                                downloaded_gb=round(downloaded / 1024**3, 2),
                            )
                        except Exception:
                            pass
                        stop_monitor.wait(1.0)

                monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                monitor_thread.start()

                try:
                    for eid, einfo, ldir in entries:
                        if stalled.is_set():
                            break
                        last_repo = einfo.get("hf_repo")
                        _update_status(current_model=eid)
                        _pull_one(einfo, ldir)
                        # Verify each entry's files actually landed — no placebo
                        # "completed" when the check paths are still empty.
                        if not _check_model_downloaded(eid):
                            raise RuntimeError(
                                f"Download of {eid} finished but expected files are "
                                f"missing under {ldir}"
                            )
                finally:
                    stop_monitor.set()
                    monitor_thread.join(timeout=2)

                if stalled.is_set():
                    return  # monitor already wrote the terminal failed state

                _update_status(
                    progress=100,
                    downloaded_gb=_video_model_download_status.get("total_gb", 0),
                    status="completed",
                    is_downloading=False,
                )
                logger.info(f"Video model(s) downloaded: {', '.join(plan_ids)}")

            except Exception as e:
                logger.error(f"Video model download failed: {e}", exc_info=True)
                if not stalled.is_set():
                    _update_status(
                        status="failed",
                        error=classify_hf_download_error(e, repo_id=last_repo),
                        progress=0,
                        is_downloading=False,
                    )
            finally:
                # Belt-and-suspenders: release the lock for THIS epoch even if an
                # early return skipped the terminal write.
                with _video_model_download_lock:
                    if (_video_model_download_status.get("epoch") == my_epoch
                            and _video_model_download_status.get("is_downloading")):
                        _video_model_download_status["is_downloading"] = False
                        _video_model_download_status["updated_at"] = time.time()
                        _persist_download_status()

        thread = threading.Thread(target=_download_task, args=(pending, my_epoch))
        thread.daemon = True
        thread.start()

        return success_response({
            "message": f"Started downloading {', '.join(pending)}",
            "status": "downloading",
        })
    except Exception as e:
        logger.error(f"Error starting video model download: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/models/download-status", methods=["GET"])
def get_video_model_download_status():
    """Get current video model download progress."""
    try:
        with _video_model_download_lock:
            return success_response(_video_model_download_status.copy())
    except Exception as e:
        logger.error(f"Error getting download status: {e}")
        return error_response(str(e), 500)

