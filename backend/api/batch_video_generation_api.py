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
from typing import List

from flask import Blueprint, request, send_file
from werkzeug.utils import secure_filename

from backend.utils.response_utils import success_response, error_response
from backend.services.batch_video_generator import get_batch_video_generator

# GPU Resource Coordinator for pre-flight availability check
try:
    from backend.services.gpu_resource_coordinator import get_gpu_coordinator
    gpu_coordinator_available = True
except ImportError:
    gpu_coordinator_available = False
    get_gpu_coordinator = None

logger = logging.getLogger(__name__)


def _check_gpu_availability():
    """
    Pre-flight check for GPU availability before starting video generation.
    Returns (is_available, error_response_or_None).
    """
    if not gpu_coordinator_available or not get_gpu_coordinator:
        return True, None  # No coordinator, allow request to proceed

    try:
        coordinator = get_gpu_coordinator()
        status = coordinator.get_gpu_status()

        if not status.get("available"):
            owner = status.get("owner", "unknown")
            return False, error_response(
                f"GPU currently in use by {owner}. Please wait for current operation to complete or check /api/gpu/status.",
                409
            )
        return True, None
    except Exception as e:
        logger.warning(f"GPU availability check failed: {e}")
        return True, None  # Allow request on check failure

batch_video_bp = Blueprint("batch_video", __name__, url_prefix="/api/batch-video")


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


@batch_video_bp.route("/generate/text", methods=["POST"])
def generate_text_to_video_batch():
    """
    Start a text-to-video batch generation.
    Body can be JSON or form-data.
    """
    try:
        # Pre-flight GPU availability check
        gpu_available, gpu_error = _check_gpu_availability()
        if not gpu_available:
            return gpu_error

        data = request.get_json(silent=True) or request.form.to_dict()
        prompts = _parse_list(data.get("prompts"))
        if not prompts:
            return error_response("No prompts provided", 400)

        params = {
            "model": data.get("model", "svd"),
            "duration_frames": int(data.get("duration_frames", 25)),
            "fps": int(data.get("fps", 7)),
            "width": int(data.get("width", 512)),
            "height": int(data.get("height", 512)),
            "motion_strength": float(data.get("motion_strength", 1.0)),
            "num_inference_steps": int(data.get("num_inference_steps", 25)),
            "guidance_scale": float(data.get("guidance_scale", 7.5)),
            "seed": _parse_int(data.get("seed")),
            "generate_frames_only": str(data.get("generate_frames_only", "false")).lower() == "true",
            "frames_per_batch": int(data.get("frames_per_batch", 1)),
            "combine_frames": str(data.get("combine_frames", "false")).lower() == "true",
            "metadata": data.get("metadata") or {},
        }

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)

        status = generator.start_batch_from_prompts(prompts=prompts, **params)
        return success_response({"batch_id": status.batch_id, "status": status.status})
    except Exception as e:
        logger.error(f"Failed to start text-to-video batch: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/generate/image", methods=["POST"])
def generate_image_to_video_batch():
    """
    Start an image-to-video batch generation. Expects image paths or IDs provided by client.
    """
    try:
        # Pre-flight GPU availability check
        gpu_available, gpu_error = _check_gpu_availability()
        if not gpu_available:
            return gpu_error

        data = request.get_json(silent=True) or request.form.to_dict()
        image_paths = _parse_list(data.get("image_paths") or data.get("image_ids"))
        if not image_paths:
            return error_response("No image_paths provided", 400)

        params = {
            "model": data.get("model", "svd"),
            "duration_frames": int(data.get("duration_frames", 25)),
            "fps": int(data.get("fps", 7)),
            "width": int(data.get("width", 512)),
            "height": int(data.get("height", 512)),
            "motion_strength": float(data.get("motion_strength", 1.0)),
            "num_inference_steps": int(data.get("num_inference_steps", 25)),
            "guidance_scale": float(data.get("guidance_scale", 7.5)),
            "seed": _parse_int(data.get("seed")),
            "generate_frames_only": str(data.get("generate_frames_only", "false")).lower() == "true",
            "frames_per_batch": int(data.get("frames_per_batch", 1)),
            "combine_frames": str(data.get("combine_frames", "false")).lower() == "true",
            "metadata": data.get("metadata") or {},
        }

        generator = get_batch_video_generator()
        if not generator.service_available:
            return error_response("Video generation service not available", 503)

        status = generator.start_batch_from_images(image_paths=image_paths, **params)
        return success_response({"batch_id": status.batch_id, "status": status.status})
    except Exception as e:
        logger.error(f"Failed to start image-to-video batch: {e}")
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
                "total_videos": status.total_videos,
                "completed_videos": status.completed_videos,
                "failed_videos": status.failed_videos,
                "start_time": status.start_time.isoformat() if status.start_time else None,
                "end_time": status.end_time.isoformat() if status.end_time else None,
                "results": results,
                "metadata": status.metadata,
                "output_dir": status.output_dir,
            }
        )
    except Exception as e:
        logger.error(f"Failed to get batch status: {e}")
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

        mime_type = "video/mp4" if video_path.suffix.lower() == ".mp4" else "image/png"
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
        fps = int(data.get("fps", 7))
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


VIDEO_MODEL_REGISTRY = {
    "cogvideox-2b": {
        "name": "CogVideoX 2B",
        "description": "Text-to-video, 6s clips. Good quality, fits in 12GB VRAM.",
        "hf_repo": "THUDM/CogVideoX-2b",
        "local_subdir": "CogVideo/CogVideoX-2b",
        "check_files": ["transformer/diffusion_pytorch_model.safetensors", "vae/diffusion_pytorch_model.safetensors"],
        "size_gb": 13.0,
        "vram_mb": 12000,
        "type": "cogvideox",
    },
    "cogvideox-5b": {
        "name": "CogVideoX 5B",
        "description": "Text-to-video, 6s clips. Best quality, needs ~16GB VRAM.",
        "hf_repo": "THUDM/CogVideoX-5b",
        "local_subdir": "CogVideo/CogVideoX-5b",
        "check_files": ["transformer/diffusion_pytorch_model-00001-of-00002.safetensors", "vae/diffusion_pytorch_model.safetensors"],
        "size_gb": 11.3,
        "vram_mb": 16000,
        "type": "cogvideox",
    },
    "cogvideox-5b-i2v": {
        "name": "CogVideoX 1.5 5B I2V (BF16)",
        "description": "Image-to-video, 6s clips. Full precision, best quality. Needs ~16GB VRAM.",
        "hf_repo": "Kijai/CogVideoX-comfy",
        "hf_filename": "CogVideoX_1_5_5b_I2V_bf16.safetensors",
        "local_subdir": "checkpoints",
        "check_files": ["CogVideoX_1_5_5b_I2V_bf16.safetensors"],
        "size_gb": 10.4,
        "vram_mb": 16000,
        "type": "cogvideox",
    },
    "svd-xt": {
        "name": "SVD-XT (Legacy)",
        "description": "Image-to-video, 3.5s clips, 512x512. Stable Video Diffusion.",
        "hf_repo": "stabilityai/stable-video-diffusion-img2vid-xt",
        "hf_filename": "svd_xt.safetensors",
        "local_subdir": "checkpoints",
        "check_files": ["svd_xt.safetensors"],
        "size_gb": 9.0,
        "vram_mb": 10000,
        "type": "svd",
    },
    "wan22-14b": {
        "name": "Wan 2.2 14B MoE (GGUF Q5_K)",
        "description": "State-of-the-art video gen. Two-expert MoE architecture, best quality on 16GB GPU. Requires both HighNoise + LowNoise experts.",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "local_subdir": "unet",
        "check_files": ["Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf", "Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf"],
        "size_gb": 21.0,
        "vram_mb": 11000,
        "type": "wan",
    },
    "wan-vae": {
        "name": "Wan 2.1/2.2 VAE",
        "description": "Required by all Wan video models. Shared between versions.",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "hf_filename": "VAE/Wan2.1_VAE.safetensors",
        "local_subdir": "vae",
        "check_files": ["wan_2.1_vae.safetensors"],
        "size_gb": 0.25,
        "vram_mb": 0,
        "type": "vae",
    },
    "wan-umt5": {
        "name": "UMT5-XXL Text Encoder (FP8)",
        "description": "Required by Wan 2.1/2.2 models for text encoding.",
        "hf_repo": "Osrivers/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "hf_filename": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "local_subdir": "text_encoders",
        "check_files": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
        "size_gb": 6.3,
        "vram_mb": 0,
        "type": "encoder",
    },
    "t5-encoder": {
        "name": "T5-XXL Text Encoder (FP8)",
        "description": "Required by CogVideoX models for text encoding.",
        "hf_repo": "comfyanonymous/flux_text_encoders",
        "hf_filename": "t5xxl_fp8_e4m3fn.safetensors",
        "local_subdir": "clip/t5",
        "check_files": ["t5xxl_fp8_e4m3fn.safetensors"],
        "size_gb": 4.6,
        "vram_mb": 0,
        "type": "encoder",
    },
}

# Download status tracking
_video_model_download_lock = threading.Lock()
_video_model_download_status = {
    "is_downloading": False,
    "current_model": None,
    "progress": 0,
    "status": "idle",
    "error": None,
    "speed_mbps": 0,
    "downloaded_gb": 0,
    "total_gb": 0,
}


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


@batch_video_bp.route("/models", methods=["GET"])
def list_video_models():
    """List all video models and their installation status."""
    try:
        models = []
        for model_id, info in VIDEO_MODEL_REGISTRY.items():
            models.append({
                "id": model_id,
                "name": info["name"],
                "description": info["description"],
                "type": info["type"],
                "size_gb": info["size_gb"],
                "vram_mb": info["vram_mb"],
                "is_downloaded": _check_model_downloaded(model_id),
            })
        return success_response({"models": models})
    except Exception as e:
        logger.error(f"Error listing video models: {e}")
        return error_response(str(e), 500)


@batch_video_bp.route("/models/download", methods=["POST"])
def download_video_model():
    """Start downloading a video model from HuggingFace."""
    global _video_model_download_status
    try:
        data = request.get_json()
        if not data or "model_id" not in data:
            return error_response("No model_id provided", 400)

        model_id = data["model_id"]
        if model_id not in VIDEO_MODEL_REGISTRY:
            return error_response(f"Unknown model: {model_id}", 400)

        if _check_model_downloaded(model_id):
            return success_response({"message": f"{model_id} is already installed"})

        with _video_model_download_lock:
            if _video_model_download_status["is_downloading"]:
                return error_response(
                    f"Already downloading: {_video_model_download_status['current_model']}", 409
                )
            model_info = VIDEO_MODEL_REGISTRY[model_id]
            _video_model_download_status = {
                "is_downloading": True,
                "current_model": model_id,
                "progress": 0,
                "status": "starting",
                "error": None,
                "speed_mbps": 0,
                "downloaded_gb": 0,
                "total_gb": model_info["size_gb"],
            }

        def _download_task(mid, minfo):
            global _video_model_download_status
            _start_time = time.time()
            total_bytes = int(minfo["size_gb"] * 1024**3)

            try:
                from huggingface_hub import hf_hub_download, snapshot_download

                models_dir = _get_comfyui_models_dir()
                local_dir = models_dir / minfo["local_subdir"]
                local_dir.mkdir(parents=True, exist_ok=True)

                with _video_model_download_lock:
                    _video_model_download_status["status"] = "downloading"

                # Monitor download progress by watching file sizes on disk
                stop_monitor = threading.Event()

                def _monitor_progress():
                    while not stop_monitor.is_set():
                        try:
                            # Sum up all .incomplete and final files in the HF cache + local_dir
                            downloaded = 0
                            # Check HF cache (downloads go here first)
                            cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
                            if cache_dir.exists():
                                for f in cache_dir.rglob("*.incomplete"):
                                    downloaded += f.stat().st_size
                            # Check target files
                            for cf in minfo["check_files"]:
                                target = local_dir / cf
                                if target.exists():
                                    downloaded += target.stat().st_size

                            elapsed = time.time() - _start_time
                            speed = (downloaded / (1024 * 1024)) / max(elapsed, 0.1)
                            pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)

                            with _video_model_download_lock:
                                _video_model_download_status.update({
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
                    if "hf_filename" in minfo:
                        hf_hub_download(
                            repo_id=minfo["hf_repo"],
                            filename=minfo["hf_filename"],
                            local_dir=str(local_dir),
                        )
                    else:
                        snapshot_download(
                            repo_id=minfo["hf_repo"],
                            local_dir=str(local_dir),
                        )
                finally:
                    stop_monitor.set()
                    monitor_thread.join(timeout=2)

                with _video_model_download_lock:
                    _video_model_download_status.update({
                        "progress": 100,
                        "downloaded_gb": minfo["size_gb"],
                        "total_gb": minfo["size_gb"],
                    })

                # Rename file if check_files expects a different name
                if "hf_filename" in minfo and minfo["check_files"][0] != minfo["hf_filename"]:
                    src = local_dir / minfo["hf_filename"]
                    dst = local_dir / minfo["check_files"][0]
                    if src.exists() and not dst.exists():
                        import shutil
                        shutil.copy2(str(src), str(dst))

                with _video_model_download_lock:
                    _video_model_download_status["status"] = "completed"
                logger.info(f"Video model downloaded: {mid}")

            except Exception as e:
                logger.error(f"Video model download failed: {e}", exc_info=True)
                with _video_model_download_lock:
                    _video_model_download_status.update({
                        "status": "failed",
                        "error": str(e),
                        "progress": 0,
                    })
            finally:
                with _video_model_download_lock:
                    _video_model_download_status["is_downloading"] = False

        thread = threading.Thread(
            target=_download_task, args=(model_id, VIDEO_MODEL_REGISTRY[model_id])
        )
        thread.daemon = True
        thread.start()

        return success_response({
            "message": f"Started downloading {model_id}",
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

