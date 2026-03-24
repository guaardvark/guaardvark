"""FastAPI application for the Upscaling plugin.

Endpoints are sync (run in uvicorn thread pool). Single worker process.
"""
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

import cv2
import numpy as np
import torch
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from service.auth import AUTH_TOKEN, verify_token
from service.config import UpscalingConfig, load_config
from service.health import get_health_status
from service.jobs import JobManager
from service.model_manager import ModelManager
from service.upscaler import upscale_image
from service.video_pipeline import get_video_info, process_video
from service.watcher import FolderWatcher

logger = logging.getLogger("upscaling.app")

# Re-export for test access
_auth_token = AUTH_TOKEN

# --- Globals (initialized on startup) ---
_config: Optional[UpscalingConfig] = None
_model_manager: Optional[ModelManager] = None
_job_manager: Optional[JobManager] = None
_watcher: Optional[FolderWatcher] = None
_cancel_flags: dict = {}  # job_id -> threading.Event


# --- Pydantic models ---
class ImageUpscaleRequest(BaseModel):
    input_path: str
    output_path: str
    model: Optional[str] = None
    scale: Optional[float] = None
    denoise_strength: Optional[float] = None


class VideoUpscaleRequest(BaseModel):
    input_path: str
    output_path: Optional[str] = None
    model: Optional[str] = None
    scale: Optional[float] = None
    denoise_strength: Optional[float] = None
    suffix: str = "upscaled"


class ModelDownloadRequest(BaseModel):
    model: str


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app):
    global _config, _model_manager, _job_manager, _watcher

    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _config = load_config(plugin_root)

    models_dir = os.path.join(plugin_root, "models")
    _model_manager = ModelManager(
        models_dir=models_dir,
        precision=_config.precision,
        compile_enabled=_config.compile_model,
    )
    _job_manager = JobManager(max_history=50)

    # Watch folder mode
    if _config.watch_folder_enabled and _config.watch_input_dir:
        _watcher = FolderWatcher(
            input_dir=_config.watch_input_dir,
            submit_fn=lambda path: _submit_watch_job(path),
        )
        _watcher.start()

    logger.info(f"Upscaling service started (port={_config.port}, precision={_config.precision})")

    yield

    if _watcher:
        _watcher.stop()
    if _model_manager:
        _model_manager.unload()
    logger.info("Upscaling service stopped")


app = FastAPI(title="Guaardvark Upscaling Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5175"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _submit_watch_job(input_path: str):
    """Submit a job from the watch folder."""
    if not _config or not _job_manager:
        return
    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(
        _config.watch_output_dir or os.path.dirname(input_path),
        f"{basename}_upscaled.mp4",
    )
    _start_video_job(
        input_path=input_path,
        output_path=output_path,
        model_name=_config.default_model,
        scale=None,
        denoise_strength=0.5,
    )


# --- Helpers ---

def _ensure_model(model_name: Optional[str] = None) -> str:
    """Ensure the requested (or default) model is loaded. Returns model name."""
    name = model_name or _config.default_model
    if _model_manager.current_model_name != name:
        _model_manager.load_model(name)
    return name


def _start_video_job(
    input_path: str,
    output_path: str,
    model_name: Optional[str],
    scale: Optional[float],
    denoise_strength: float,
) -> dict:
    """Create and start a video upscale job in a background thread."""
    name = model_name or _config.default_model
    job = _job_manager.create_job(
        input_path=input_path,
        output_path=output_path,
        model=name,
        scale=scale or 0,
        denoise_strength=denoise_strength,
    )
    cancel_event = threading.Event()
    _cancel_flags[job["job_id"]] = cancel_event

    thread = threading.Thread(
        target=_run_video_job,
        args=(job["job_id"], input_path, output_path, name, scale, denoise_strength, cancel_event),
        daemon=True,
    )
    thread.start()
    return job


def _run_video_job(
    job_id: str,
    input_path: str,
    output_path: str,
    model_name: str,
    scale: Optional[float],
    denoise_strength: float,
    cancel_event: threading.Event,
):
    """Background worker for video upscale job."""
    try:
        # Fix 7: VRAM pre-check
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            free_mb = free / (1024 * 1024)
            if free_mb < 500:
                raise RuntimeError(f"Insufficient VRAM: {free_mb:.0f}MB free, need ~500MB minimum")

        info = get_video_info(input_path)
        total_frames = info["nb_frames"]
        in_w, in_h = info["width"], info["height"]

        _ensure_model(model_name)
        model = _model_manager.get_model()
        model_scale = _model_manager.scale or 4

        outscale = scale if scale else float(model_scale)
        out_w = int(in_w * outscale)
        out_h = int(in_h * outscale)

        tile_size = 0
        if _config.max_tile_size != "auto":
            tile_size = int(_config.max_tile_size)

        _job_manager.start_job(job_id, total_frames=total_frames)

        # Fix 5: Send job_started callback
        _send_callback("upscale:job_started", {
            "job_id": job_id,
            "input_file": input_path,
            "model": model_name,
        })

        device = "cuda" if torch.cuda.is_available() else "cpu"
        frame_count = [0]
        start_time = time.time()
        last_callback_time = [start_time]

        def process_frame(frame):
            if cancel_event.is_set():
                raise InterruptedError("Job cancelled")

            # Fix 6: Job timeout enforcement
            elapsed = time.time() - start_time
            if _config and elapsed > _config.job_timeout_minutes * 60:
                raise TimeoutError(f"Job exceeded {_config.job_timeout_minutes} minute timeout")

            result = upscale_image(
                frame, model, model_scale,
                outscale=outscale, tile_size=tile_size,
                device=device, precision=_config.precision,
            )
            frame_count[0] += 1
            elapsed = time.time() - start_time
            fps = frame_count[0] / elapsed if elapsed > 0 else 0
            _job_manager.update_progress(job_id, frame_count[0], fps)

            # Fix 5: Throttled progress callbacks (every 2 seconds)
            now = time.time()
            if now - last_callback_time[0] >= 2.0:
                last_callback_time[0] = now
                progress = frame_count[0] / total_frames if total_frames > 0 else 0
                remaining = total_frames - frame_count[0]
                eta = round(remaining / fps) if fps > 0 else None
                _send_callback("upscale:job_progress", {
                    "job_id": job_id,
                    "progress": round(progress, 3),
                    "fps": round(fps, 1),
                    "eta_seconds": eta,
                })

            return result

        process_video(
            input_path=input_path,
            output_path=output_path,
            frame_processor=process_frame,
            out_width=out_w,
            out_height=out_h,
        )

        _job_manager.complete_job(job_id)
        logger.info(f"Job {job_id} completed: {output_path}")

        _send_callback("upscale:job_completed", {
            "job_id": job_id,
            "output_file": output_path,
            "duration_seconds": round(time.time() - start_time, 1),
        })

    except (InterruptedError, TimeoutError) as e:
        if isinstance(e, TimeoutError):
            _job_manager.fail_job(job_id, error=str(e))
            _send_callback("upscale:job_failed", {"job_id": job_id, "error": str(e)})
        else:
            _job_manager.cancel_job(job_id)
        logger.info(f"Job {job_id} stopped: {e}")
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        _job_manager.fail_job(job_id, error=str(e))
        _send_callback("upscale:job_failed", {"job_id": job_id, "error": str(e)})
    finally:
        _cancel_flags.pop(job_id, None)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _send_callback(event: str, payload: dict):
    """Send event callback to Guaardvark backend (best-effort)."""
    if not _config or not _config.callback_url:
        return
    try:
        import requests
        requests.post(
            _config.callback_url,
            json={"event": event, **payload},
            timeout=5,
        )
    except Exception as e:
        logger.debug(f"Callback failed: {e}")


# --- Endpoints ---

# Fix 2: Include auth_token in /health response
@app.get("/health")
def health():
    result = get_health_status(
        model_loaded=_model_manager.current_model_name if _model_manager else None,
        active_jobs=_job_manager.active_job_count if _job_manager else 0,
        compile_enabled=_config.compile_model if _config else False,
    )
    result["auth_token"] = _auth_token
    return result


@app.get("/models")
def list_models():
    if not _model_manager:
        raise HTTPException(503, "Service not initialized")
    return _model_manager.list_models()


@app.post("/models/download")
def download_model(req: ModelDownloadRequest, request: Request):
    verify_token(request)
    if not _model_manager:
        raise HTTPException(503, "Service not initialized")
    try:
        path = _model_manager.download_model(req.model)
        return {"status": "downloaded", "model": req.model, "path": path}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/upscale/image")
def upscale_image_endpoint(req: ImageUpscaleRequest, request: Request):
    verify_token(request)
    if not os.path.isfile(req.input_path):
        raise HTTPException(400, f"Input file not found: {req.input_path}")

    img = cv2.imread(req.input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise HTTPException(400, f"Could not read image: {req.input_path}")

    name = _ensure_model(req.model)
    model = _model_manager.get_model()
    model_scale = _model_manager.scale or 4

    tile_size = 0
    if _config.max_tile_size != "auto":
        tile_size = int(_config.max_tile_size)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    output = upscale_image(
        img, model, model_scale,
        outscale=req.scale, tile_size=tile_size,
        device=device, precision=_config.precision,
    )

    # Fix 8: Temp-file-then-rename for image upscale
    os.makedirs(os.path.dirname(req.output_path), exist_ok=True)
    tmp_path = req.output_path + ".tmp"
    cv2.imwrite(tmp_path, output)
    os.replace(tmp_path, req.output_path)
    return {"status": "completed", "output_path": req.output_path}


# Fix 4: Multipart image upload endpoint
@app.post("/upscale/image/upload")
async def upscale_image_upload(
    request: Request,
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    scale: Optional[float] = Form(None),
):
    verify_token(request)
    contents = await file.read()
    arr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise HTTPException(400, "Could not decode image")

    name = _ensure_model(model)
    mdl = _model_manager.get_model()
    model_scale = _model_manager.scale or 4

    tile_size = 0
    if _config.max_tile_size != "auto":
        tile_size = int(_config.max_tile_size)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    output = upscale_image(
        img, mdl, model_scale,
        outscale=scale, tile_size=tile_size,
        device=device, precision=_config.precision,
    )

    import tempfile
    tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp_file.name, output)
    return FileResponse(
        tmp_file.name,
        media_type="image/png",
        filename=f"upscaled_{file.filename}",
    )


@app.post("/upscale/video", status_code=202)
def upscale_video_endpoint(req: VideoUpscaleRequest, request: Request):
    verify_token(request)
    if not os.path.isfile(req.input_path):
        raise HTTPException(400, f"Input file not found: {req.input_path}")

    # Fix 7: VRAM pre-check before accepting job
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info(0)
        free_mb = free / (1024 * 1024)
        if free_mb < 500:
            raise HTTPException(503, f"Insufficient VRAM: {free_mb:.0f}MB free, need ~500MB minimum")

    output_path = req.output_path
    if not output_path:
        base = os.path.splitext(req.input_path)[0]
        output_path = f"{base}_{req.suffix}.mp4"

    job = _start_video_job(
        input_path=req.input_path,
        output_path=output_path,
        model_name=req.model,
        scale=req.scale,
        denoise_strength=req.denoise_strength or 0.5,
    )
    return job


@app.get("/jobs")
def list_jobs():
    if not _job_manager:
        return []
    return _job_manager.list_jobs()


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if not _job_manager:
        raise HTTPException(503, "Service not initialized")
    job = _job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job not found: {job_id}")
    return job


@app.delete("/jobs/{job_id}")
def cancel_job(job_id: str, request: Request):
    verify_token(request)
    cancel_event = _cancel_flags.get(job_id)
    if cancel_event:
        cancel_event.set()
        return {"status": "cancelling", "job_id": job_id}
    job = _job_manager.get_job(job_id) if _job_manager else None
    if not job:
        raise HTTPException(404, f"Job not found: {job_id}")
    return {"status": job["status"], "job_id": job_id}


@app.get("/config")
def get_config():
    if not _config:
        raise HTTPException(503, "Service not initialized")
    return {k: v for k, v in _config.__dict__.items()}


# Fix 3: Implement PUT /config properly
@app.put("/config")
async def update_config(request: Request):
    verify_token(request)
    body = await request.json()

    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugin_json_path = os.path.join(plugin_root, "plugin.json")
    with open(plugin_json_path) as f:
        manifest = json.load(f)

    manifest.setdefault("config", {}).update(body)
    with open(plugin_json_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Update in-memory config
    if _config:
        for key, value in body.items():
            if hasattr(_config, key):
                setattr(_config, key, value)

    return {"status": "updated", "config": {k: v for k, v in _config.__dict__.items()} if _config else {}}
