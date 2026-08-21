"""
Upscaling API — proxy endpoints for the Upscaling plugin service.

Proxies requests to the upscaling service on port 8202.
Auth token is fetched from the plugin's /health endpoint and cached.
"""

import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import requests
from flask import Blueprint, request as flask_request, send_file
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename

from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

upscaling_bp = Blueprint("upscaling", __name__, url_prefix="/api/upscaling")

UPSCALING_URL = "http://localhost:8202"
UPSCALING_TIMEOUT = 10  # seconds for quick endpoints
# Model weights can be large; urlretrieve in the plugin holds the HTTP request open until done.
UPSCALING_DOWNLOAD_READ_TIMEOUT = 1800  # seconds

# Cached bearer token — fetched from plugin /health on first use
_cached_token: str | None = None


def _get_auth_token() -> str | None:
    """Fetch and cache the bearer token from the upscaling plugin."""
    global _cached_token
    if _cached_token:
        return _cached_token
    try:
        resp = requests.get(f"{UPSCALING_URL}/health", timeout=3)
        if resp.status_code == 200:
            _cached_token = resp.json().get("auth_token")
            return _cached_token
    except Exception:
        pass
    return None


def _auth_headers() -> dict:
    """Return Authorization header for upscaling service."""
    token = _get_auth_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _proxy_get(path: str, timeout: int = UPSCALING_TIMEOUT):
    """Proxy a GET request to the upscaling service."""
    try:
        resp = requests.get(f"{UPSCALING_URL}{path}", timeout=timeout)
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Upscaling service not running"}, 503
    except Exception as e:
        return {"error": str(e)}, 500


def _proxy_post(path: str, json_data: dict, timeout: int = UPSCALING_TIMEOUT):
    """Proxy a POST request to the upscaling service with auth."""
    try:
        resp = requests.post(
            f"{UPSCALING_URL}{path}",
            json=json_data,
            headers=_auth_headers(),
            timeout=timeout,
        )
        return resp.json(), resp.status_code
    except requests.ConnectionError:
        return {"error": "Upscaling service not running"}, 503
    except Exception as e:
        return {"error": str(e)}, 500


def _proxy_post_download(path: str, json_data: dict):
    """POST to upscaling plugin with auth; long read timeout for model downloads."""
    try:
        resp = requests.post(
            f"{UPSCALING_URL}{path}",
            json=json_data,
            headers=_auth_headers(),
            timeout=(10, UPSCALING_DOWNLOAD_READ_TIMEOUT),
        )
        try:
            data = resp.json()
        except Exception:
            data = {"error": resp.text or resp.reason}
        return data, resp.status_code
    except requests.ConnectionError:
        return {"error": "Upscaling service not running"}, 503
    except requests.Timeout:
        return {"error": "Model download timed out"}, 504
    except Exception as e:
        return {"error": str(e)}, 500


@upscaling_bp.route("/health", methods=["GET"])
def health():
    """Get upscaling service health status."""
    data, status = _proxy_get("/health")
    if status == 200:
        return success_response(data=data, message="Upscaling service healthy")
    return error_response(data.get("error", "Service unavailable"), status)


@upscaling_bp.route("/models", methods=["GET"])
def list_models():
    """List available upscaling models."""
    data, status = _proxy_get("/models")
    if status == 200:
        return success_response(data=data, message="Models retrieved")
    return error_response(data.get("error", "Failed to list models"), status)


@upscaling_bp.route("/models/download", methods=["POST"])
def download_model():
    """Download a registered upscaling model weight file into the plugin models dir."""
    body = flask_request.get_json() or {}
    model = body.get("model")
    if not model or not isinstance(model, str):
        return error_response("model is required", 400)

    data, status = _proxy_post_download("/models/download", {"model": model})
    if status == 200:
        return success_response(data=data, message="Model downloaded")
    err = None
    if isinstance(data, dict):
        err = data.get("error") or data.get("detail")
        if isinstance(err, list):
            err = "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                for item in err
            )
    if err is None:
        err = str(data) if data else "Download failed"
    elif isinstance(err, dict):
        err = err.get("message") or str(err)
    return error_response(str(err), status if status != 200 else 500)


@upscaling_bp.route("/upscale/video", methods=["POST"])
def upscale_video():
    """Submit a video upscale job."""
    body = flask_request.get_json() or {}

    input_path = body.get("input_path")
    if not input_path:
        return error_response("input_path is required", 400)

    payload = {
        "input_path": input_path,
        "output_path": body.get("output_path"),
        "model": body.get("model"),
        "scale": body.get("scale"),
        "suffix": body.get("suffix", "upscaled"),
        "two_pass": body.get("two_pass", False),
    }
    # Remove None values (but keep two_pass even if False)
    payload = {k: v for k, v in payload.items() if v is not None}

    data, status = _proxy_post("/upscale/video", payload, timeout=30)
    if status in (200, 202):
        return success_response(data=data, message="Upscale job submitted")
    return error_response(data.get("error", "Failed to submit job"), status)


@upscaling_bp.route("/jobs", methods=["GET"])
def list_jobs():
    """List all upscale jobs."""
    data, status = _proxy_get("/jobs")
    if status == 200:
        return success_response(data=data, message="Jobs retrieved")
    return error_response(data.get("error", "Failed to list jobs"), status)


@upscaling_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    """Get upscale job status."""
    data, status = _proxy_get(f"/jobs/{job_id}")
    if status == 200:
        return success_response(data=data, message="Job status retrieved")
    return error_response(data.get("error", "Job not found"), status)


@upscaling_bp.route("/jobs/<job_id>", methods=["DELETE"])
def cancel_job(job_id):
    """Cancel an upscale job."""
    try:
        resp = requests.delete(
            f"{UPSCALING_URL}/jobs/{job_id}",
            headers=_auth_headers(),
            timeout=UPSCALING_TIMEOUT,
        )
        data = resp.json()
        if resp.status_code == 200:
            return success_response(data=data, message="Job cancelled")
        return error_response(data.get("error", "Failed to cancel"), resp.status_code)
    except requests.ConnectionError:
        return error_response("Upscaling service not running", 503)
    except Exception as e:
        return error_response(str(e), 500)


@upscaling_bp.route("/jobs", methods=["DELETE"])
def clear_finished_jobs():
    """Clear finished/failed/cancelled jobs from history. Active jobs untouched."""
    try:
        resp = requests.delete(
            f"{UPSCALING_URL}/jobs",
            headers=_auth_headers(),
            timeout=UPSCALING_TIMEOUT,
        )
        data = resp.json()
        if resp.status_code == 200:
            return success_response(data=data, message="Cleared finished jobs")
        return error_response(data.get("error", "Failed to clear"), resp.status_code)
    except requests.ConnectionError:
        return error_response("Upscaling service not running", 503)
    except Exception as e:
        return error_response(str(e), 500)


@upscaling_bp.route("/preview", methods=["POST"])
def preview_upscale():
    """Preview upscale by upscaling a single image (e.g. video frame)."""
    if "file" not in flask_request.files:
        return error_response("No file uploaded", 400)

    file = flask_request.files["file"]
    files = {"file": (file.filename, file.stream, file.mimetype)}
    
    data = {
        "model": flask_request.form.get("model"),
        "scale": flask_request.form.get("scale"),
        "sharpen": flask_request.form.get("sharpen"),
        "denoise_strength": flask_request.form.get("denoise_strength"),
        "two_pass": flask_request.form.get("two_pass"),
        "face_enhance": flask_request.form.get("face_enhance"),
    }
    
    try:
        resp = requests.post(
            f"{UPSCALING_URL}/upscale/image/upload",
            files=files,
            data={k: v for k, v in data.items() if v},
            headers=_auth_headers(),
            timeout=60,
        )
        if resp.status_code == 200:
            return send_file(
                io.BytesIO(resp.content),
                mimetype=resp.headers.get("content-type", "image/png"),
                as_attachment=False,
                download_name=f"preview_{file.filename}"
            )
        try:
            err_json = resp.json()
            return error_response(err_json.get("error", "Failed to upscale preview"), resp.status_code)
        except Exception:
            return error_response(f"Upscaling failed: {resp.text}", resp.status_code)
    except requests.ConnectionError:
        return error_response("Upscaling service not running", 503)
    except Exception as e:
        return error_response(str(e), 500)


# --- Upload & Serve ---

def _get_upload_dir() -> Path:
    """Get the upload staging directory for upscaling."""
    project_root = Path(__file__).resolve().parent.parent.parent
    upload_dir = project_root / "data" / "outputs" / "upscaling"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _proxy_error(data, fallback: str) -> str:
    """Pull a message out of a plugin error body (FastAPI uses ``detail``)."""
    if isinstance(data, dict):
        message = data.get("error") or data.get("detail")
        if isinstance(message, list):
            return "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                for item in message
            )
        if message:
            return str(message)
    return fallback


def _contained_file(base_dir: Path, name: str) -> Optional[Path]:
    """Resolve ``name`` as a direct child of ``base_dir``.

    Returns None when the name is empty, carries a path separator, or resolves
    (through ``..`` or a symlink) anywhere other than directly inside ``base_dir``.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        return None
    base = base_dir.resolve()
    joined = safe_join(str(base), name)
    if joined is None:
        return None
    candidate = Path(joined).resolve()
    return candidate if candidate.parent == base else None


ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

# A single still is seconds of GPU work; the request is held open for the result.
UPSCALING_IMAGE_TIMEOUT = 300  # seconds

IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _image_input_dir() -> Path:
    """Staging directory for uploaded images awaiting upscale."""
    path = _get_upload_dir() / "input" / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _image_output_dir() -> Path:
    """Directory the plugin writes upscaled stills into."""
    path = _get_upload_dir() / "output" / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stage_image(file) -> Path:
    """Save one uploaded image into the staging directory.

    The stored name is derived from ``secure_filename`` plus a short random
    token, so nothing the caller sends reaches the filesystem as a path and two
    uploads of the same name never collide. Raises ValueError on a rejected file.
    """
    if not file.filename:
        raise ValueError("No filename")
    safe_name = secure_filename(file.filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image type: {ext or file.filename}")
    stem = os.path.splitext(safe_name)[0] or "image"
    staged = _image_input_dir() / f"{stem}_{uuid.uuid4().hex[:8]}{ext}"
    file.save(str(staged))
    return staged


def _image_options_from_form(form) -> dict:
    """Collect the shared upscale knobs out of a multipart form."""
    options = {}
    model = form.get("model")
    if model:
        options["model"] = model
    scale = form.get("scale")
    if scale:
        options["scale"] = float(scale)
    sharpen = form.get("sharpen")
    if sharpen is not None:
        options["sharpen"] = float(sharpen)
    denoise = form.get("denoise_strength")
    if denoise is not None:
        options["denoise_strength"] = float(denoise)
    for flag in ("two_pass", "face_enhance"):
        value = form.get(flag)
        if value and value.lower() in ("true", "1", "yes"):
            options[flag] = True
    return options


@upscaling_bp.route("/upscale/image", methods=["POST"])
def upscale_image():
    """Upload one image and upscale it synchronously.

    A still finishes in seconds, so this returns the finished file rather than
    a job id; batches go through ``/upscale/images`` and the job queue instead.
    """
    if "file" not in flask_request.files:
        return error_response("No file uploaded", 400)

    try:
        staged = _stage_image(flask_request.files["file"])
    except ValueError as exc:
        return error_response(str(exc), 400)

    output_path = _image_output_dir() / f"{staged.stem}_upscaled.png"
    payload = {
        "input_path": str(staged),
        "output_path": str(output_path),
        **_image_options_from_form(flask_request.form),
    }

    data, status = _proxy_post("/upscale/image", payload, timeout=UPSCALING_IMAGE_TIMEOUT)
    if status == 200:
        return success_response(
            data={
                "output_file": output_path.name,
                "url": f"/api/upscaling/output/image/{output_path.name}",
            },
            message="Image upscaled",
        )
    return error_response(_proxy_error(data, "Failed to upscale image"), status)


@upscaling_bp.route("/upscale/images", methods=["POST"])
def upscale_images():
    """Upload N images and queue them as one job on the plugin's GPU worker."""
    files = flask_request.files.getlist("files")
    if not files:
        return error_response("No files uploaded", 400)

    staged = []
    rejected = []
    for file in files:
        try:
            staged.append(_stage_image(file))
        except ValueError as exc:
            rejected.append(str(exc))

    if not staged:
        return error_response(rejected[0] if rejected else "No usable images", 400)

    payload = {
        "inputs": [str(p) for p in staged],
        "output_dir": str(_image_output_dir()),
        "suffix": "upscaled",
        **_image_options_from_form(flask_request.form),
    }

    data, status = _proxy_post("/upscale/images", payload, timeout=30)
    if status in (200, 202):
        return success_response(
            data={
                **(data if isinstance(data, dict) else {}),
                "queued": len(staged),
                "rejected": rejected,
            },
            message=f"Queued {len(staged)} image(s) for upscaling",
        )
    return error_response(_proxy_error(data, "Failed to submit image batch"), status)


@upscaling_bp.route("/output/image/<path:filename>", methods=["GET"])
def serve_image_output(filename):
    """Serve an upscaled still out of the plugin's image output directory."""
    output_dir = _image_output_dir()
    file_path = _contained_file(output_dir, filename)
    if file_path is None:
        return error_response("Invalid path", 400)
    if not file_path.exists():
        return error_response("File not found", 404)

    mimetype = IMAGE_MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return send_file(str(file_path), mimetype=mimetype)



@upscaling_bp.route("/upload", methods=["POST"])
def upload_and_upscale():
    """Upload a video file and submit it for upscaling."""
    if "file" not in flask_request.files:
        return error_response("No file uploaded", 400)

    file = flask_request.files["file"]
    if not file.filename:
        return error_response("No filename", 400)

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return error_response(f"Unsupported file type: {ext}", 400)

    upload_dir = _get_upload_dir()
    input_path = upload_dir / "input" / filename
    input_path.parent.mkdir(parents=True, exist_ok=True)

    # Save uploaded file
    file.save(str(input_path))
    logger.info(f"Uploaded video saved: {input_path}")

    # Build output path
    base_name = os.path.splitext(filename)[0]
    model = flask_request.form.get("model")
    scale = flask_request.form.get("scale")
    target_width = flask_request.form.get("target_width")
    suffix = "4k" if target_width and int(target_width) >= 3840 else "upscaled"
    output_path = upload_dir / "output" / f"{base_name}_{suffix}{ext}"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Submit to upscaling service
    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "suffix": suffix,
    }
    if model:
        payload["model"] = model
    if scale:
        payload["scale"] = float(scale)
        
    sharpen = flask_request.form.get("sharpen")
    if sharpen is not None:
        payload["sharpen"] = float(sharpen)
        
    denoise = flask_request.form.get("denoise_strength")
    if denoise is not None:
        payload["denoise_strength"] = float(denoise)
        
    two_pass = flask_request.form.get("two_pass")
    if two_pass and two_pass.lower() in ("true", "1", "yes"):
        payload["two_pass"] = True
        
    face_enhance = flask_request.form.get("face_enhance")
    if face_enhance and face_enhance.lower() in ("true", "1", "yes"):
        payload["face_enhance"] = True
        
    double_fps = flask_request.form.get("double_fps")
    if double_fps and double_fps.lower() in ("true", "1", "yes"):
        payload["double_fps"] = True

    data, status = _proxy_post("/upscale/video", payload, timeout=30)
    if status in (200, 202):
        return success_response(
            data={**(data if isinstance(data, dict) else {}), "output_path": str(output_path)},
            message="Upload received, upscale job submitted",
        )
    return error_response(data.get("error", "Failed to submit upscale job"), status)


@upscaling_bp.route("/output/<path:filename>", methods=["GET"])
def serve_output(filename):
    """Serve an upscaled output video."""
    output_dir = _get_upload_dir() / "output"
    file_path = _contained_file(output_dir, filename)
    if file_path is None:
        return error_response("Invalid path", 400)
    if not file_path.exists():
        return error_response("File not found", 404)

    ext = file_path.suffix.lower()
    mime_map = {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }
    return send_file(str(file_path), mimetype=mime_map.get(ext, "application/octet-stream"))
