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
from urllib.parse import quote

from flask import Blueprint, request, send_file

from backend.utils.response_utils import success_response, error_response
from backend.utils.path_guard import PathEscapesRoot, contained
from backend.services.batch_video_generator import VideoRenameError, _derive_display_name, get_batch_video_generator
from backend.services.job_types import RenderErrorKind, batch_failure, describe_failure, failure_kind
from backend.services.video_consistency_metrics import NEEDS_REVIEW
# Single source of truth for video-model file layout (download dst == install
# check == ComfyUI loader paths). See backend/services/video_model_registry.py.
from backend.services.video_model_registry import (
    VIDEO_MODEL_REGISTRY,
    DEFAULT_T2V_MODEL,
    DEFAULT_I2V_MODEL,
    prepare_video_model,
    classify_hf_download_error,
    model_capabilities,
    tier_defaults_for,
    resolve_active_video_model,
    clip_defaults_for,
    LORA_STACK_TYPES,
    TEXT_ENCODER_SWAP_TYPES,
)
from backend.services.user_video_models import (
    preview_hf_url,
    add_user_model,
    remove_user_model,
    is_user_model_id,
    parse_hf_url,
)
from backend.services.user_model_families import DuplicateUserModel, hf_inspect_url

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


class _BadField(ValueError):
    """A request field that is not the number it has to be (answered as a 400)."""


def _parse_float(value, field: str = "value"):
    """A number, or None for a field left out (the model's own value applies)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise _BadField(f"{field} must be a number, got {value!r}.")


def _float_field(data, key: str, default: float) -> float:
    value = _parse_float(data.get(key), key)
    return default if value is None else value


def _int_field(data, key: str, default: int) -> int:
    raw = data.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise _BadField(f"{key} must be a whole number, got {raw!r}.")


def _optional_positive_float(data, key: str):
    """A tuning value where 0 or blank means off (TeaCache threshold, FETA weight)."""
    value = _parse_float(data.get(key), key)
    return value if value else None


def _failure_response(message, default_kind=RenderErrorKind.INVALID_REQUEST, status_code: int = 400):
    """A refused request, with its failure record (job_types.describe_failure)
    under error.details so every caller reads the same kind."""
    kind = failure_kind(message, default_kind)
    return error_response(str(message), status_code, details={"failure": describe_failure(kind, message)})


def _withheld_style_error(params: dict):
    """400 when the request asks for a prompt style its model does not offer."""
    if not params.get("enhance_prompt"):
        return None
    from backend.services.video_render_limits import withheld_style
    why = withheld_style(params["model"], params.get("prompt_style"))
    return _failure_response(why) if why else None


def _resolve_request_model(data, role: str):
    """Explicit body model, else the active-video-model resolver."""
    explicit = (data.get("model") or "").strip() or None
    # Both routes run prepare_video_model next, which starts a stopped ComfyUI.
    model_id, err = resolve_active_video_model(role, explicit, comfyui_down_ok=True)
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


def _subject_ids(data) -> List[int]:
    """Cast member ids, as whole numbers (the batch stores them as ints)."""
    ids = []
    for raw in _parse_list(data.get("subject_ids")):
        if not str(raw).strip():
            continue
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            raise _BadField(f"subject_ids must be whole numbers, got {raw!r}.")
    return ids


def _render_params(data, model_id: str) -> dict:
    """The render settings every generate route reads the same way."""
    clip = _clip_params(data, model_id)
    return {
        "model": model_id,
        "duration_frames": clip["duration_frames"],
        "fps": clip["fps"],
        "width": clip["width"],
        "height": clip["height"],
        "motion_strength": _float_field(data, "motion_strength", 1.0),
        "num_inference_steps": clip["num_inference_steps"],
        "guidance_scale": _parse_float(data.get("guidance_scale"), "guidance_scale"),
        "seed": _parse_int(data.get("seed")),
        "generate_frames_only": str(data.get("generate_frames_only", "false")).lower() == "true",
        "frames_per_batch": _int_field(data, "frames_per_batch", 1),
        "combine_frames": str(data.get("combine_frames", "false")).lower() == "true",
        "interpolation_multiplier": _int_field(data, "interpolation_multiplier", 2),
        "prompt_style": data.get("prompt_style", "cinematic"),
        "enhance_prompt": str(data.get("enhance_prompt", "true")).lower() != "false",
        "fidelity_mode": str(data.get("fidelity_mode", data.get("preserve_text_fidelity", "false"))).lower() == "true",
        "wan_sampler_profile": (data.get("wan_sampler_profile") or None),
        "negative_prompt": data.get("negative_prompt", "") or "",
        # "freeu" is accepted and ignored: FreeU patches UNet skip connections
        # and every routed video model is a transformer, so it never applied.
        "face_restore": str(data.get("face_restore", "false")).lower() == "true",
        "lora_name": data.get("lora_name"),
        "lora_strength": _float_field(data, "lora_strength", 1.0),
        "adapters": data.get("adapters") if isinstance(data.get("adapters"), list) else [],
        "text_encoder": (data.get("text_encoder") or "").strip() or None,
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
            "teacache_threshold": _optional_positive_float(data, "teacache_threshold"),
            "feta_weight": _optional_positive_float(data, "feta_weight"),
            "high_consistency": str(data.get("high_consistency", "false")).lower() == "true",
            # A step count the person typed keeps priority over a model's
            # step floor; preset-driven values are raised to the floor.
            "steps_explicit": str(data.get("steps_explicit", "false")).lower() == "true",
        },
    }


def _saved_batch_preflight(params: dict):
    """The checks a new request gets, for a batch rebuilt from its saved
    settings (retry, re-render): the model is still installed and ComfyUI can
    load it, and the prompt style is still offered. None when it can run,
    else the refusal response."""
    model_id = params.get("model")
    if not model_id:
        return _failure_response("This batch did not save which model it used.")
    ready, preflight_err = prepare_video_model(model_id)
    if not ready:
        return _failure_response(preflight_err)
    return _withheld_style_error(params)


def _media_path(ref, label: str):
    """A Studio input (library document id or link, served URL, or a path
    inside the install, uploads or outputs) as (path, None) or (None, why)."""
    from backend.utils.media_inputs import document_file, resolve_media_ref
    found = resolve_media_ref(ref, mcp=False, label=label, document_path=document_file,
                              within_install=True)
    return (found.path, None) if found.path else (None, found.error or f"{label} not found: {ref}")


def _caller_is_this_machine() -> bool:
    """The request comes from the Guaardvark machine itself (the CLI, the
    video skill), through the local proxy or not."""
    from backend.utils.auth_guard import request_is_from_this_machine
    return request_is_from_this_machine()


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def _start_frames(refs):
    """Start images as files, or (None, why).

    Every reference goes through ``_media_path`` first: a library document
    id or link, as the Video page's uploads and the Files picker give, or a
    path inside the install, uploads or outputs. A path to a file anywhere
    else is honoured only for a caller on this machine, the documented form
    the CLI and the video skill send; the route needs no sign-in and the
    backend listens on every interface, so a caller elsewhere on the network
    gets no read of this machine's files. A file named like a credential is
    refused for everyone, and a frame has to be an image.
    """
    import os
    from backend.utils.path_safety import is_sensitive
    frames = []
    for i, ref in enumerate(refs, start=1):
        label = f"Start image {i}"
        text = str(ref or "").strip()
        if not text:
            return None, f"{label} is empty."
        path, err = _media_path(text, label)
        if err:
            if not os.path.isfile(text) or not _caller_is_this_machine():
                return None, err
            if is_sensitive(text) or is_sensitive(os.path.realpath(text)):
                return None, err
            path = text
        if not str(path).lower().endswith(_IMAGE_EXTENSIONS):
            return None, f"{label} is not an image: {os.path.basename(str(path))}."
        frames.append(path)
    return frames, None


def _last_frames(data, model_id: str):
    """Index-paired end frames, resolved, or (None, why) on a model without
    a last-frame mode or a frame that cannot be read."""
    raw = _parse_list(data.get("last_frame_paths"))
    if not any(str(r or "").strip() for r in raw):
        return [], None
    modes = model_capabilities(model_id).get("modes") or []
    if "l2v" not in modes and "flf2v" not in modes:
        name = (VIDEO_MODEL_REGISTRY.get(model_id) or {}).get("name") or model_id
        return None, f"{name} takes no end frame."
    frames = []
    for i, ref in enumerate(raw, start=1):
        if not str(ref or "").strip():
            frames.append(None)
            continue
        path, err = _media_path(ref, f"End frame {i}")
        if err:
            return None, err
        frames.append(path)
    return frames, None


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
        storyboard_shots = _int_field(data, "storyboard_shots", 0)
        if storyboard_concept and storyboard_shots > 0:
            prompts = [storyboard_concept] * max(1, min(storyboard_shots, 50))
        if not prompts:
            return error_response("No prompts provided", 400)

        model_id, resolve_err = _resolve_request_model(data, "t2v")
        if resolve_err:
            return _failure_response(resolve_err)
        ready, preflight_err = prepare_video_model(model_id)
        if not ready:
            return _failure_response(preflight_err)
        # Per-prompt guides (audio or image anchors) on models that declare
        # audio_in: a list per prompt of {"kind", "path", "frame_idx", ...}.
        guides = data.get("guides") if isinstance(data.get("guides"), list) else []
        # Per-prompt end frames on models that declare l2v: video that ends
        # on a picture, with no start image.
        last_frame_paths, frame_err = _last_frames(data, model_id)
        if frame_err:
            return _failure_response(frame_err)

        params = {
            **_render_params(data, model_id),
            # Trained cast members to lock into each clip. The video model can't apply
            # a LoRA, so each selected character's SDXL LoRA is baked into a cinematic
            # keyframe (which then seeds I2V) — selecting cast implies cinematic mode.
            "subject_ids": _subject_ids(data),
            "storyboard_concept": storyboard_concept or None,
        }

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)

        gpu_hint = _gpu_queue_hint()
        style_err = _withheld_style_error(params)
        if style_err:
            return style_err
        status = generator.start_batch_from_prompts(
            prompts=prompts, guides=guides, last_frame_paths=last_frame_paths, **params
        )
        return success_response({
            "batch_id": status.batch_id,
            "status": status.status,
            "stage": getattr(status, "stage", "queued"),
            "gpu": gpu_hint,
        })
    except _BadField as e:
        return _failure_response(str(e))
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
        image_refs = _parse_list(data.get("image_paths") or data.get("image_ids"))
        if not image_refs:
            return error_response("No image_paths provided", 400)
        image_paths, start_err = _start_frames(image_refs)
        if start_err:
            return _failure_response(start_err)
        # Index-paired with image_paths on models that declare l2v/flf2v and
        # audio_in. guides is a list per item of {"kind", "path", "frame_idx", ...}.
        guides = data.get("guides") if isinstance(data.get("guides"), list) else []

        model_id, resolve_err = _resolve_request_model(data, "i2v")
        if resolve_err:
            return _failure_response(resolve_err)
        last_frame_paths, frame_err = _last_frames(data, model_id)
        if frame_err:
            return _failure_response(frame_err)
        ready, preflight_err = prepare_video_model(model_id)
        if not ready:
            return _failure_response(preflight_err)

        params = {"prompt": data.get("prompt", ""), **_render_params(data, model_id)}

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)

        style_err = _withheld_style_error(params)
        if style_err:
            return style_err
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
    except _BadField as e:
        return _failure_response(str(e))
    except Exception as e:
        logger.error(f"Failed to start image-to-video batch: {e}")
        return error_response(str(e), 500)


# Extensions each reference slot takes; what ComfyUI's LoadImage, the video
# loader and LoadAudio read.
_REF_EXTENSIONS = {
    "image": (".png", ".jpg", ".jpeg", ".webp", ".bmp"),
    "video": (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"),
    "audio": (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"),
}
_REF_LABELS = {"image": "Picture", "video": "Clip", "audio": "Audio"}


def _default_clip_audio(role) -> str:
    """A clip's sound when the request names none: an edit or a continuation
    keeps the clip's soundtrack; a clip used for who is in it or for its motion
    goes in silent, since its own dialogue is a second voice beside any voice
    reference (heard as overlapping voices in the 2026-10-09 renders)."""
    return "own" if role in ("edit", "continue") else "none"


def _reference_file(ref, kind: str, index: int):
    """One reference board entry's file as (path, None) or (None, why)."""
    label = f"Reference {_REF_LABELS[kind].lower()} {index}"
    path, err = _media_path(ref, label)
    if err:
        return None, err
    if not path.lower().endswith(_REF_EXTENSIONS[kind]):
        return None, f"{label} is not a{'n' if kind in ('image', 'audio') else ''} {kind} file: {Path(path).name}."
    return path, None


def _resolve_references(board, model_id: str):
    """The reference board as (render inputs, intent spec) or (None, why).

    ``board`` is ``{"images": [{ref, role, name, note}], "videos": [{ref,
    role, name, audio: "own"|"none"|{"ref"}}], "audios": [{ref, role,
    speaker, description}]}`` in tag order. The render inputs are the
    request's ref_images / ref_videos / ref_audios; the intent spec is what
    h3_prompt_compiler.intent_from_references reads, with each clip's
    ``soundtrack`` set from what is actually wired.
    """
    from backend.services.comfyui_video_generator import _media_has_audio, reference_count_error

    board = board if isinstance(board, dict) else {}
    images, videos, audios = ([e for e in (board.get(k) or []) if isinstance(e, dict)]
                              for k in ("images", "videos", "audios"))
    inputs = {"ref_images": [], "ref_videos": [], "ref_audios": []}
    spec = {"images": [], "videos": [], "audios": []}
    for i, entry in enumerate(images, start=1):
        path, err = _reference_file(entry.get("ref"), "image", i)
        if err:
            return None, err
        inputs["ref_images"].append(path)
        spec["images"].append({k: entry.get(k) for k in ("role", "name", "note")})
    for k, entry in enumerate(videos, start=1):
        path, err = _reference_file(entry.get("ref"), "video", k)
        if err:
            return None, err
        audio = entry.get("audio") or _default_clip_audio(entry.get("role"))
        if isinstance(audio, dict):
            track, err = _reference_file(audio.get("ref"), "audio", k)
            if err:
                return None, f"{err} (the separate soundtrack of clip {k})"
            video = {"path": path, "audio_path": track}
            soundtrack = True
        else:
            wants = audio != "none"
            video = {"path": path, "include_audio": wants}
            soundtrack = wants and _media_has_audio(path) is not False
        inputs["ref_videos"].append(video)
        spec["videos"].append({"role": entry.get("role"), "name": entry.get("name"), "soundtrack": soundtrack})
    for j, entry in enumerate(audios, start=1):
        path, err = _reference_file(entry.get("ref"), "audio", j)
        if err:
            return None, err
        inputs["ref_audios"].append(path)
        spec["audios"].append({k: entry.get(k) for k in ("role", "speaker", "description")})

    caps = model_capabilities(model_id)
    name = (VIDEO_MODEL_REGISTRY.get(model_id) or {}).get("name") or model_id
    count_err = reference_count_error(caps.get("ref_limits") or {}, name, inputs["ref_images"],
                                      inputs["ref_videos"], inputs["ref_audios"])
    if count_err:
        return None, count_err
    return (inputs, spec), None


def _reference_prompt(prompt: str, spec: dict, params: dict, language: str) -> str:
    """The prompt the reference build gets for this board.

    Compiled here, as Film Crew compiles its scenes, not by the render's
    enhancer: the roles and names are the person's own choices and must take
    effect with Verbatim Prompts on too, which drops only the style opening.
    Deterministic; no model is called.
    """
    from backend.services import h3_prompt_compiler as h3
    from backend.services.media_director import verbatim_prompts_enabled
    plain = params.get("fidelity_mode") or verbatim_prompts_enabled()
    intent = h3.intent_from_references(
        prompt, params["duration_frames"] / float(params["fps"] or 24), spec,
        style=None if plain else params.get("prompt_style"),
        language=language, motion_strength=params.get("motion_strength"),
    )
    return h3.compile(intent)[0]


@batch_video_bp.route("/generate/references", methods=["POST"])
def generate_reference_video_batch():
    """Start a reference-to-video clip: one prompt and the reference board
    (pictures, clips, audio) on a model that declares ref2v.

    With prompt enhancement on, the board's roles and names compile into the
    model's reference sections (subject definitions, retention) before the
    batch is queued; with it off, the prompt is sent as typed, its
    <Picture N> / <Video N> / <Audio N> tags numbered in board order.
    """
    try:
        data = request.get_json(silent=True) or {}
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return error_response("No prompt provided", 400)
        model_id, resolve_err = _resolve_request_model(data, "ref2v")
        if resolve_err:
            return _failure_response(resolve_err)
        # Inputs resolve before the model preflight, which may start ComfyUI.
        resolved, ref_err = _resolve_references(data.get("references"), model_id)
        if ref_err:
            return _failure_response(ref_err)
        inputs, spec = resolved
        ready, preflight_err = prepare_video_model(model_id)
        if not ready:
            return _failure_response(preflight_err)

        # Identity comes from the references: no keyframe still, no rewrite
        # of the prompt that names them.
        params = {**_render_params(data, model_id), "director_mode": False, "cinematic_keyframe": False}
        language = (data.get("language") or "").strip() or "English"
        sent = prompt
        if params["enhance_prompt"]:
            sent = _reference_prompt(prompt, spec, params, language)
            params["enhance_prompt"] = False  # compiled; the render must not enhance it again
        params["metadata"] = {"display_name": _derive_display_name(prompt), **params["metadata"]}

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)
        style_err = _withheld_style_error(params)
        if style_err:
            return style_err
        gpu_hint = _gpu_queue_hint()
        status = generator.start_batch_from_prompts(prompts=[sent], item_metadata=inputs, **params)
        return success_response({
            "batch_id": status.batch_id,
            "status": status.status,
            "stage": getattr(status, "stage", "queued"),
            "gpu": gpu_hint,
        })
    except _BadField as e:
        return _failure_response(str(e))
    except Exception as e:
        logger.error(f"Failed to start reference-to-video batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/references/probe", methods=["POST"])
def probe_reference():
    """Length and sound of one reference file, so the board can number its
    <Audio N> tags the way the graph wires them and flag a clip that is
    too short before anything is queued."""
    try:
        from backend.services.comfyui_video_generator import _media_has_audio, _media_seconds

        data = request.get_json(silent=True) or {}
        kind = data.get("kind") if data.get("kind") in _REF_EXTENSIONS else "video"
        path, err = _reference_file(data.get("ref"), kind, 1)
        if err:
            return _failure_response(err)
        return success_response({
            "name": Path(path).name,
            "kind": kind,
            "duration_s": _media_seconds(path) if kind != "image" else None,
            "has_audio": _media_has_audio(path) if kind != "image" else False,
        })
    except Exception as e:
        logger.error(f"Failed to probe reference: {e}")
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

        # Family comes from the registry entry, not a substring. Hunyuan used
        # to miss the "cog"/"wan"/"ltx" tests and get the generic path; a
        # default of cogvideox would have been a silent mis-label.
        model = data.get("model", "")
        model_family = None
        if model:
            from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
            model_family = (VIDEO_MODEL_REGISTRY.get(model) or {}).get("type")

        from backend.services import video_render_limits as render_limits
        from backend.services.media_director import verbatim_prompts_enabled
        from backend.utils.prompt_enhancer import enhance_video_prompt, has_text_intent

        # Settings → Verbatim Prompts: the render skips the enhancer and sends
        # the typed text (comfyui_video_generator); the reference compiler keeps
        # the board's roles and drops only the style opening (_reference_prompt).
        verbatim = bool(verbatim_prompts_enabled())
        # With Enhance prompt off the render sends the typed prompt on every
        # path (comfyui_video_generator gates on request.enhance_prompt; the
        # references route only compiles the board when it is on). Parsed as
        # _render_params parses it.
        enhance = str(data.get("enhance_prompt", "true")).lower() != "false"

        reference_prompt = None
        if enhance and isinstance(data.get("references"), dict) and model:
            # No files are read here: the board's own soundtrack flags stand in
            # for what the render wires.
            board = data["references"]
            spec = {
                "images": [e for e in board.get("images") or [] if isinstance(e, dict)],
                "videos": [{**e, "soundtrack": bool(e.get("soundtrack"))}
                           for e in board.get("videos") or [] if isinstance(e, dict)],
                "audios": [e for e in board.get("audios") or [] if isinstance(e, dict)],
            }
            clip = _clip_params(data, model)
            reference_prompt = _reference_prompt(prompt, spec, {
                "duration_frames": clip["duration_frames"], "fps": clip["fps"],
                "prompt_style": style, "fidelity_mode": fidelity,
                "motion_strength": _parse_float(data.get("motion_strength"), "motion_strength"),
            }, (data.get("language") or "").strip() or "English")

        if reference_prompt:
            enhanced = reference_prompt
        elif verbatim or not enhance:
            enhanced = prompt
        else:
            enhanced = enhance_video_prompt(
                prompt,
                style=style,
                width=width,
                height=height,
                fidelity_mode=fidelity,
                model_family=model_family,
                motion_strength=data.get("motion_strength"),
            )

        # Default negative that the backend would inject if user left it blank
        character = bool(_parse_list(data.get("subject_ids")) or data.get("lora_name") or data.get("adapters"))
        default_neg = render_limits.default_negative(
            model, style, enhanced=True, character=character, family=model_family)
        unset_cfg = render_limits.cfg_when_unset(model, model_family) if model else None

        return success_response({
            "original_prompt": prompt,
            "enhanced_prompt": enhanced,
            "default_negative_prompt": default_neg,
            # Guidance a request that names none renders with.
            "cfg_when_unset": unset_cfg if unset_cfg is not None else render_limits.LEGACY_CFG,
            "reference_defaults": render_limits.reference_defaults_enabled(),
            "fidelity_mode": fidelity,
            "verbatim": verbatim,
            "enhance_prompt": enhance,
            "has_text_intent": has_text_intent(prompt),
            "model_family": model_family,
            "style": style,
        })
    except _BadField as e:
        return _failure_response(str(e))
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
                "error_kind": r.error_kind,
                "failure": None if r.success else describe_failure(r.error_kind, r.error),
                "metadata": r.metadata,
                "review": getattr(r, "review", None),
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
                # Finished but not usable as rendered: the post-render quality flags.
                "flagged_videos": sum(
                    1 for r in status.results
                    if r.success and ((r.metadata or {}).get("quality") or {}).get("flagged")
                ),
                # Held until a person approves or re-renders them.
                "needs_review_videos": sum(
                    1 for r in status.results
                    if (getattr(r, "review", None) or {}).get("state") == NEEDS_REVIEW
                ),
                "start_time": status.start_time.isoformat() if status.start_time else None,
                "end_time": status.end_time.isoformat() if status.end_time else None,
                "results": results,
                "metadata": status.metadata,
                "output_dir": status.output_dir,
                "retry_data": getattr(status, "retry_data", None),
                "error": getattr(status, "error", None),
                # Why it stopped, the same record for the batch and each clip.
                "failure": batch_failure(status),
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


@batch_video_bp.route("/queue/clear-finished", methods=["POST"])
def clear_finished_queue():
    """Remove completed, failed and cancelled batches from the queue
    snapshot for every browser. Output files and history are kept."""
    try:
        generator = get_batch_video_generator()
        removed = generator.clear_finished_queue()
        return success_response({"removed": removed, "queue": generator.list_queue()})
    except Exception as e:
        logger.error(f"Failed to clear finished batches from the queue: {e}")
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
        try:
            batch_dir = contained(generator.base_output_dir, batch_id)
            video_path = contained(batch_dir, video_name)
        except PathEscapesRoot:
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
        try:
            deleted = generator.delete_video(batch_id, video_name)
        except PathEscapesRoot:
            return error_response("Invalid video path", 400)
        if not deleted:
            return error_response("Video not found", 404)
        return success_response({"batch_id": batch_id, "deleted": video_name})
    except Exception as e:
        logger.error(f"Failed to delete video: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/video/<batch_id>/<path:video_name>/rename", methods=["PUT"])
def rename_video(batch_id: str, video_name: str):
    """Rename one clip. Body {"new_name": "<file name>"}; the extension is kept.

    Answers with the clip's new paths (relative to the batch) and the URL that
    serves it, so a page can repoint its player without reloading the batch.
    """
    try:
        data = request.get_json(silent=True) or {}
        new_name = str(data.get("new_name") or "")
        generator = get_batch_video_generator()
        try:
            renamed = generator.rename_video(batch_id, video_name, new_name)
        except PathEscapesRoot:
            return error_response("Invalid video path", 400)
        except VideoRenameError as e:
            return error_response(str(e), e.status)

        video_path = renamed["video_path"]
        return success_response({
            "batch_id": batch_id,
            "old_name": video_name,
            "new_name": Path(video_path).name,
            "old_video_path": renamed["old_video_path"],
            "video_path": video_path,
            "thumbnail_path": renamed["thumbnail_path"],
            "url": f"{batch_video_bp.url_prefix}/video/{quote(batch_id)}/{quote(Path(video_path).as_posix())}",
        })
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
    re-choose model, steps, fidelity_mode, LoRA, quality tier, etc.

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
        refused = _saved_batch_preflight(params)
        if refused:
            return refused
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


@batch_video_bp.route("/review/<batch_id>/<item_id>/approve", methods=["POST"])
def approve_held_clip(batch_id: str, item_id: str):
    """Keep a clip a quality check held for review; it is registered into Documents."""
    try:
        result = get_batch_video_generator().approve_item(batch_id, item_id)
        if result is None:
            return error_response("Clip not found", 404)
        return success_response({"batch_id": batch_id, "item_id": item_id, "review": result.review})
    except Exception as e:
        logger.error(f"Failed to approve clip {batch_id}/{item_id}: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/review/<batch_id>/<item_id>/rerender", methods=["POST"])
def rerender_held_clip(batch_id: str, item_id: str):
    """Render one clip again as a new batch with the same settings and a fresh seed."""
    try:
        generator = get_batch_video_generator()
        orig = generator.get_batch_status(batch_id)
        if not orig:
            return error_response("Clip not found", 404)
        saved = dict((getattr(orig, "retry_data", None) or {}).get("params") or {})
        # A batch that kept no settings gets rerender_item's own 400 below.
        refused = _saved_batch_preflight(saved) if saved else None
        if refused:
            return refused
        new_status = generator.rerender_item(batch_id, item_id)
        if new_status is None:
            return error_response("Clip not found", 404)
        return success_response({
            "batch_id": new_status.batch_id,
            "status": new_status.status,
            "rerender_of": {"batch_id": batch_id, "item_id": item_id},
        })
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        logger.error(f"Failed to re-render clip {batch_id}/{item_id}: {e}")
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
        try:
            batch_dir = contained(generator.base_output_dir, batch_id)
        except PathEscapesRoot:
            return error_response("Batch not found", 404)
        if not batch_dir.exists():
            return error_response("Batch not found", 404)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(tmp_fd)
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for file_path in batch_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(batch_dir)
                        zipf.write(file_path, arcname)
            # The open handle keeps the data readable after the name is gone,
            # so every download leaves nothing behind in the temp directory
            # (tmpfs on many systems), whether or not the client reads it all.
            archive = open(tmp_path, "rb")
        finally:
            os.unlink(tmp_path)
        return send_file(archive, as_attachment=True, download_name=f"{batch_id}.zip",
                         mimetype="application/zip")
    except Exception as e:
        logger.error(f"Failed to download batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/combine-frames/<batch_id>", methods=["POST"])
def combine_frames(batch_id: str):
    try:
        data = request.get_json(silent=True) or {}
        try:
            fps = _int_field(data, "fps", 24)
        except _BadField as e:
            return _failure_response(str(e))
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
    """Check if a video model's files exist and are non-empty (wherever the
    entry's `dest` says they live; see video_model_registry.resolve_entry_dir)."""
    from backend.services.video_model_registry import entry_files_present
    return entry_files_present(VIDEO_MODEL_REGISTRY.get(model_id))


def _missing_check_files(model_id: str) -> List[str]:
    """The check_files (model + companions) absent on disk: the precise reason a
    model is not ready (video_model_registry.missing_plan_files)."""
    from backend.services.video_model_registry import missing_plan_files
    return missing_plan_files(model_id)


def _resolve_download_plan(model_id: str) -> List[str]:
    """Expand a model id into [model + required companions], order-preserving
    (video_model_registry.install_plan)."""
    from backend.services.video_model_registry import install_plan
    return install_plan(model_id)


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
        active_t2v, _ = resolve_active_video_model("t2v", comfyui_down_ok=True)
        active_i2v, _ = resolve_active_video_model("i2v", comfyui_down_ok=True)
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
                # LoRA companions name the generation entries they apply to;
                # adapter False marks one no picker may offer.
                "applies_to": info.get("applies_to", []),
                "adapter": info.get("adapter"),
                "active": model_id in {active_t2v, active_i2v} and bool(model_id),
                "user": bool(info.get("user")) or is_user_model_id(model_id),
                "like": info.get("like"),
                # User text encoders name the shipped companion they stand in for;
                # generation rows say whether their graph accepts one.
                "replaces": info.get("replaces"),
                "encoder_swap": info.get("type") in TEXT_ENCODER_SWAP_TYPES,
                "lora_stack": info.get("type") in LORA_STACK_TYPES,
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
    data = request.get_json(silent=True)
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

            class _Superseded(Exception):
                """A newer download took over (stale-lock takeover, or the stall
                monitor failed this one): stop before starting another file, so
                two pulls never write the same directory."""

            def _still_owner():
                if stalled.is_set() or not _is_current():
                    raise _Superseded()

            def _pull_one(einfo, local_dir):
                """Pull a single registry entry's files into local_dir."""
                download_entry_files(einfo, local_dir, check=_still_owner)

            last_repo = None
            try:
                from backend.services.video_model_registry import download_entry_files

                # Resolve each entry's target dir up front and snapshot a baseline
                # so progress reflects ONLY the bytes this run adds (dirs like
                # unet/ may already hold other models). Unique dirs only, so a
                # shared dir isn't double-counted.
                entries = []
                from backend.services.video_model_registry import resolve_entry_dir
                for eid in plan_ids:
                    einfo = VIDEO_MODEL_REGISTRY[eid]
                    ldir = resolve_entry_dir(einfo)
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
                        if stalled.is_set() or not _is_current():
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

                if stalled.is_set() or not _is_current():
                    return  # the monitor or a newer download owns the status

                _update_status(
                    progress=100,
                    downloaded_gb=_video_model_download_status.get("total_gb", 0),
                    status="completed",
                    is_downloading=False,
                )
                logger.info(f"Video model(s) downloaded: {', '.join(plan_ids)}")

            except _Superseded:
                logger.info("Video model download (epoch %s) stopped: superseded", my_epoch)
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


@batch_video_bp.route("/models/from-hf", methods=["POST"])
def preview_hf_video_model():
    """Parse a Hugging Face paste and list weight files. Does not download."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return error_response("Paste a Hugging Face URL or org/repo.", 400)
    repo_id = None
    try:
        repo_id = parse_hf_url(url)["hf_repo"]
        preview = preview_hf_url(url)
        return success_response(preview)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        logger.error("HF preview failed: %s", e)
        return error_response(classify_hf_download_error(e, repo_id=repo_id), 400)


@batch_video_bp.route("/models/user", methods=["POST"])
def add_user_video_model():
    """Register a user model (same shape as the shipped catalog) and optionally Install."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    hf_repo = (data.get("hf_repo") or "").strip()
    revision = (data.get("revision") or "main").strip() or "main"
    files = data.get("files") if isinstance(data.get("files"), list) else []
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
            logger.error("HF re-inspect failed: %s", e)
            return error_response(classify_hf_download_error(e, repo_id=hf_repo or None), 400)
        hf_repo = inspected["hf_repo"]
        revision = inspected.get("revision") or revision
        if inspected.get("unwired"):
            return error_response(inspected["unwired"].get("reason") or "That architecture is not wired yet.", 400)
        if inspected.get("src") and not files:
            files = [{"src": inspected["src"]}]
    else:
        return error_response("Paste a Hugging Face URL or org/repo.", 400)
    if not files:
        return error_response(
            "Pick at least one weight file. Pasting a repo root does not snapshot the whole repo.",
            400,
        )
    try:
        mid, entry, problems = add_user_model(
            role=(data.get("role") or "").strip(),
            like_id=(data.get("like") or "").strip(),
            hf_repo=hf_repo,
            files=files,
            name=(data.get("name") or "").strip() or None,
            model_id=(data.get("id") or "").strip() or None,
            description=(data.get("description") or "").strip() or None,
            revision=revision,
            known_files=inspected["files"] if inspected else None,
        )
    except DuplicateUserModel as e:
        return error_response(str(e), 409, data={"id": e.model_id})
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        logger.error("add user video model failed: %s", e)
        return error_response(str(e), 500)
    install = str(data.get("install", "true")).lower() != "false"
    download_payload = None
    if install:
        dl = start_video_model_download(mid)
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
        "entry": {
            "id": mid,
            "name": entry.get("name"),
            "type": entry.get("type"),
            "like": entry.get("like"),
            "applies_to": entry.get("applies_to") or [],
            "files": entry.get("files"),
            "hf_repo": entry.get("hf_repo"),
            "size_gb": entry.get("size_gb"),
        },
        "verify": problems,
        "download": download_payload,
    })


@batch_video_bp.route("/models/user/<model_id>", methods=["DELETE"])
def delete_user_video_model(model_id):
    """Remove a user-added catalog entry. Shipped ids are refused."""
    delete_files = str(request.args.get("delete_files") or (request.get_json(silent=True) or {}).get("delete_files") or "").lower() in (
        "1", "true", "yes",
    )
    try:
        result = remove_user_model(model_id, delete_files=delete_files)
    except ValueError as e:
        return error_response(str(e), 403)
    except KeyError as e:
        return error_response(str(e), 404)
    except Exception as e:
        logger.error("remove user video model failed: %s", e)
        return error_response(str(e), 500)
    return success_response(result)


@batch_video_bp.route("/models/download-status", methods=["GET"])
def get_video_model_download_status():
    """Get current video model download progress."""
    try:
        with _video_model_download_lock:
            return success_response(_video_model_download_status.copy())
    except Exception as e:
        logger.error(f"Error getting download status: {e}")
        return error_response(str(e), 500)

