"""FastAPI application for the Upscaling plugin.

Endpoints are sync (run in uvicorn thread pool). Single worker process.
"""
import json
import logging
import os
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import cv2
import numpy as np
import torch
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from service.auth import TOKEN_NAME, auth_token, guard, verify_token
from service.config import UpscalingConfig, load_config, resolve_default_model
from service.health import get_health_status
from service.jobs import JobManager
from service.model_manager import FACE_ENHANCE_MODEL, ModelManager, face_enhance_status
from service.upscaler import (
    UnusableOutputError,
    face_enhance_supported,
    face_enhance_unavailable_message,
    fit_long_edge,
    upscale_image,
)
from service.video_pipeline import get_video_info, process_video
from service.watcher import FolderWatcher
from service.write_scope import OutsideWriteScope, confined_input, confined_output

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("upscaling.app")

# --- Globals (initialized on startup) ---
_config: Optional[UpscalingConfig] = None
_model_manager: Optional[ModelManager] = None
_job_manager: Optional[JobManager] = None
_watcher: Optional[FolderWatcher] = None
_cancel_flags: dict = {}  # job_id -> threading.Event
# Guards `_ensure_model` + `upscale_image` for sync image endpoints. Without
# this lock two concurrent /upscale/image calls with different model names
# could race: thread A finishes _ensure_model, thread B swaps the model out
# before A's upscale_image call gets the loaded model. Video jobs already
# serialise through `_job_queue`; this lock covers the sync image paths.
_image_upscale_lock = threading.Lock()
# The model a queued job is using, set while it runs (under the lock above). A
# sync request for another model is refused instead of swapping the model out
# from under the job, which left both models on the card.
_job_model: Optional[str] = None
# The job the worker is running, for /status and /evict.
_running_job_id: Optional[str] = None

# Single-worker queue. The GPU is one resource — running N jobs in parallel
# just means everyone fights for VRAM and the model swaps. So we serialize:
# every submitted job lands in PENDING, the worker pulls one at a time.
_job_queue: Optional["queue.Queue"] = None
_worker_thread: Optional[threading.Thread] = None


# --- Pydantic models ---

# ``target_width`` is the output's long edge in pixels (3840 for 4K). When set it
# wins over ``scale``: the model runs at its native factor, then a resize lands on it.
TARGET_WIDTH_MIN, TARGET_WIDTH_MAX = 16, 16384

class ImageUpscaleRequest(BaseModel):
    input_path: str
    output_path: str
    model: Optional[str] = None
    scale: Optional[float] = None
    target_width: Optional[int] = Field(None, ge=TARGET_WIDTH_MIN, le=TARGET_WIDTH_MAX)
    denoise_strength: Optional[float] = 0.0
    sharpen: Optional[float] = 0.3
    two_pass: Optional[bool] = False
    face_enhance: Optional[bool] = False


class ImageBatchUpscaleRequest(BaseModel):
    inputs: List[str]
    output_dir: str
    model: Optional[str] = None
    scale: Optional[float] = None
    target_width: Optional[int] = Field(None, ge=TARGET_WIDTH_MIN, le=TARGET_WIDTH_MAX)
    denoise_strength: Optional[float] = 0.0
    sharpen: Optional[float] = 0.3
    two_pass: Optional[bool] = False
    face_enhance: Optional[bool] = False
    suffix: str = "upscaled"


class VideoUpscaleRequest(BaseModel):
    input_path: str
    output_path: Optional[str] = None
    model: Optional[str] = None
    scale: Optional[float] = None
    target_width: Optional[int] = Field(None, ge=TARGET_WIDTH_MIN, le=TARGET_WIDTH_MAX)
    denoise_strength: Optional[float] = 0.0
    sharpen: Optional[float] = 0.3
    two_pass: Optional[bool] = False
    face_enhance: Optional[bool] = False
    double_fps: Optional[bool] = False
    suffix: str = "upscaled"


class ModelDownloadRequest(BaseModel):
    model: str


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app):
    global _config, _model_manager, _job_manager, _watcher, _job_queue, _worker_thread

    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _config = load_config(plugin_root)

    # Enable TF32 tensor cores for fp32 matmul — ~3x faster on Ampere+ GPUs
    # with negligible quality impact (same fp32 storage, TF32 only for matmul)
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")

    models_dir = os.path.join(plugin_root, "models")
    _model_manager = ModelManager(
        models_dir=models_dir,
        precision=_config.precision,
        compile_enabled=_config.compile_model,
    )
    _job_manager = JobManager(max_history=50)
    # The token file exists before the first request, so the backend has a
    # token to send.
    auth_token()

    # Spin up the single worker that drains the job queue
    _job_queue = queue.Queue()
    _worker_thread = threading.Thread(target=_job_worker, daemon=True, name="upscale-worker")
    _worker_thread.start()

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
    # Tell the worker to wrap up — sentinel poison pill, then a short join.
    if _job_queue is not None:
        _job_queue.put(None)
    if _worker_thread is not None:
        _worker_thread.join(timeout=5)
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
# Every route but /health needs the token, the reads included: job lists
# name files on this machine, and only the backend (which sends the token)
# calls this service. The routes that act still check it themselves too.
app.add_middleware(guard.BearerTokenASGIMiddleware, name=TOKEN_NAME)
# Added last, so it runs first: a request addressed to a name that is not
# this machine's (a page re-pointed at 127.0.0.1) is refused before the token
# gate, CORS or any route sees it.
app.add_middleware(guard.HostCheckASGIMiddleware)


@app.exception_handler(OutsideWriteScope)
async def _outside_write_scope(_request: Request, exc: OutsideWriteScope):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(UnusableOutputError)
def _unusable_output(request: Request, exc: UnusableOutputError):
    """A still whose model output is not a picture: the caller gets the reason."""
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def _submit_watch_job(input_path: str):
    """Submit a job from the watch folder."""
    if not _config or not _job_manager:
        return
    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = _free_output_path(os.path.join(
        _config.watch_output_dir or os.path.dirname(input_path),
        f"{basename}_upscaled.mp4",
    ))
    _start_video_job(
        input_path=input_path,
        output_path=output_path,
        model_name=_default_model(),
        scale=None,
        denoise_strength=0.0,
        sharpen=0.3,
        two_pass=False,
        face_enhance=False,
        double_fps=False,
    )


# --- Helpers ---

def _atomic_imwrite(output_path: str, image) -> bool:
    """Write ``image`` to ``output_path`` via a sibling temp file.

    The temp name keeps the target extension: OpenCV chooses its encoder from
    the final extension and refuses to write one it does not recognise.
    """
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    root, ext = os.path.splitext(output_path)
    tmp_path = f"{root}.tmp{ext}"
    if not cv2.imwrite(tmp_path, image):
        return False
    os.replace(tmp_path, output_path)
    return True


def _free_output_path(path: str) -> str:
    """``path``, or the same name with a short token before the extension when
    a file is already there, so an earlier result is never replaced."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    return f"{root}_{uuid.uuid4().hex[:8]}{ext}"


def _target_size(img, target_width: Optional[int]):
    """The (width, height) an image's output is resized to, or None for scale."""
    if not target_width:
        return None
    return fit_long_edge(img.shape[1], img.shape[0], target_width)


def _default_model() -> str:
    """The model a request that names none uses (config.resolve_default_model)."""
    name = _config.default_model
    if _model_manager.is_downloaded(name):
        return name
    downloaded = _model_manager.list_models()["downloaded"]
    return resolve_default_model(name, [m["name"] for m in downloaded])


def _ensure_model(model_name: Optional[str] = None) -> str:
    """Ensure the requested (or default) model is loaded. Returns model name.

    Raises ModelNotInstalledError when its weights are not on disk; queued
    jobs record that message as their error.
    """
    name = model_name or _default_model()
    if _model_manager.current_model_name != name:
        _model_manager.load_model(name)
    return name


def _claim_job_model(model_name: Optional[str]):
    """Load a queued job's model and hold it until `_release_job_model`.

    Returns (model, scale). Sync requests for another model get 409 meanwhile.
    """
    global _job_model
    with _image_upscale_lock:
        name = _ensure_model(model_name)
        _job_model = name
        return _model_manager.get_model(), (_model_manager.scale or 4)


def _release_job_model() -> None:
    global _job_model
    with _image_upscale_lock:
        _job_model = None


def _free_cache_unless_job() -> None:
    """Return the allocator cache after a sync image. The queued path does this
    at the end of each job; a still kept its whole peak (2140 MiB with
    4x-UltraSharp on a 1024x1024 image, measured 2026-10-12) until the next one."""
    if torch.cuda.is_available() and _job_model is None:
        torch.cuda.empty_cache()


def _sync_model(model_name: Optional[str]) -> str:
    """`_ensure_model` for a sync request; call with `_image_upscale_lock` held."""
    name = model_name or _default_model()
    if _job_model is not None and name != _job_model:
        raise HTTPException(
            409,
            f"A queued upscale job is using {_job_model}; this request asks for {name}. "
            "Try again when the job finishes.",
        )
    return _ensure_model(name)


def _require_installed(model_name: Optional[str]) -> str:
    """The model a request will use, refused with 409 when it is not installed.

    Checked before a job is queued or a model is loaded, so the caller hears
    which model is missing and where to install it.
    """
    name = model_name or _default_model()
    try:
        missing = _model_manager.missing_model_message(name)
    except ValueError as e:
        raise HTTPException(400, f"Invalid model name: {e}")
    if missing:
        raise HTTPException(409, missing)
    return name


def _require_face_enhance(face_enhance: Optional[bool]) -> None:
    """Refuse a Face Enhancement request with 409 when it cannot run here.

    Checked before a job is queued or a model is loaded, so the caller hears
    what to install (or that this install cannot run it) instead of getting
    an upscale without it.
    """
    if not face_enhance:
        return
    problem = face_enhance_unavailable_message()
    if problem:
        raise HTTPException(409, problem)


def _start_video_job(
    input_path: str,
    output_path: str,
    model_name: Optional[str],
    scale: Optional[float],
    denoise_strength: float,
    sharpen: float,
    two_pass: bool,
    face_enhance: bool,
    double_fps: bool,
    target_width: Optional[int] = None,
) -> dict:
    """Create a video upscale job and drop it on the queue. The worker will
    pick it up when the GPU is free."""
    name = model_name or _default_model()
    job = _job_manager.create_job(
        input_path=input_path,
        output_path=output_path,
        model=name,
        scale=scale or 0,
        denoise_strength=denoise_strength,
        sharpen=sharpen,
        two_pass=two_pass,
        face_enhance=face_enhance,
        double_fps=double_fps,
        target_width=target_width,
    )
    cancel_event = threading.Event()
    _cancel_flags[job["job_id"]] = cancel_event

    if _job_queue is None:
        # Should never happen post-startup, but bail safely if we get here.
        _job_manager.fail_job(job["job_id"], error="Job queue not initialized")
        return job

    _job_queue.put({
        "kind": "video",
        "job_id": job["job_id"],
        "input_path": input_path,
        "output_path": output_path,
        "model_name": name,
        "scale": scale,
        "denoise_strength": denoise_strength,
        "sharpen": sharpen,
        "two_pass": two_pass,
        "face_enhance": face_enhance,
        "double_fps": double_fps,
        "target_width": target_width,
        "cancel_event": cancel_event,
    })
    return job


def _start_image_batch_job(
    inputs: list,
    output_dir: str,
    model_name: Optional[str],
    scale: Optional[float],
    denoise_strength: float,
    sharpen: float,
    two_pass: bool,
    face_enhance: bool,
    suffix: str,
    target_width: Optional[int] = None,
) -> dict:
    """Create one job covering N images and drop it on the shared GPU queue.

    Image batches ride the same single-consumer queue as video so a folder of
    stills never competes with a running render for VRAM.
    """
    name = model_name or _default_model()
    job = _job_manager.create_job(
        input_path=inputs[0] if inputs else output_dir,
        output_path=output_dir,
        model=name,
        scale=scale or 0,
        denoise_strength=denoise_strength,
        sharpen=sharpen,
        two_pass=two_pass,
        face_enhance=face_enhance,
        double_fps=False,
        kind="image_batch",
        item_count=len(inputs),
        target_width=target_width,
    )
    cancel_event = threading.Event()
    _cancel_flags[job["job_id"]] = cancel_event

    if _job_queue is None:
        _job_manager.fail_job(job["job_id"], error="Job queue not initialized")
        return job

    _job_queue.put({
        "kind": "image_batch",
        "job_id": job["job_id"],
        "inputs": list(inputs),
        "output_dir": output_dir,
        "model_name": name,
        "scale": scale,
        "denoise_strength": denoise_strength,
        "sharpen": sharpen,
        "two_pass": two_pass,
        "face_enhance": face_enhance,
        "suffix": suffix,
        "target_width": target_width,
        "cancel_event": cancel_event,
    })
    return job


def _job_worker():
    """Single consumer for the job queue. Runs one job at a time so the GPU
    isn't fighting itself."""
    global _running_job_id
    while True:
        task = _job_queue.get()
        if task is None:
            break
        job_id = task["job_id"]
        _running_job_id = job_id
        try:
            if task["kind"] == "image_batch":
                _run_image_batch_job(
                    job_id, task["inputs"], task["output_dir"], task["model_name"],
                    task["scale"], task["denoise_strength"], task["sharpen"],
                    task["two_pass"], task["face_enhance"], task["suffix"],
                    task["cancel_event"], target_width=task.get("target_width"),
                )
            else:
                _run_video_job(
                    job_id, task["input_path"], task["output_path"], task["model_name"],
                    task["scale"], task["denoise_strength"], task["sharpen"],
                    task["two_pass"], task["face_enhance"], task["double_fps"],
                    task["cancel_event"], target_width=task.get("target_width"),
                )
        except Exception as e:
            # The runners already record failure into the job manager; this is a
            # last-resort log so a worker crash doesn't kill the loop.
            logger.exception(f"Worker crashed processing {job_id}: {e}")
        finally:
            _running_job_id = None


def _run_video_job(
    job_id: str,
    input_path: str,
    output_path: str,
    model_name: str,
    scale: Optional[float],
    denoise_strength: float,
    sharpen: float,
    two_pass: bool,
    face_enhance: bool,
    double_fps: bool,
    cancel_event: threading.Event,
    target_width: Optional[int] = None,
):
    """Background worker for video upscale job."""
    try:
        # User cancelled while we were sitting in the queue — bail before VRAM/model load
        if cancel_event.is_set():
            _job_manager.cancel_job(job_id)
            logger.info(f"Job {job_id} cancelled before start")
            return

        # Fix 7: VRAM pre-check
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            free_mb = free / (1024 * 1024)
            if free_mb < 500:
                raise RuntimeError(f"Insufficient VRAM: {free_mb:.0f}MB free, need ~500MB minimum")

        info = get_video_info(input_path)
        total_frames = info["nb_frames"]
        in_w, in_h = info["width"], info["height"]

        model, model_scale = _claim_job_model(model_name)

        outscale = scale if scale else float(model_scale)
        if target_width:
            out_w, out_h = fit_long_edge(in_w, in_h, target_width)
        else:
            out_w = int(in_w * outscale)
            out_h = int(in_h * outscale)
        # NVENC / H.264 require even dimensions
        if out_w % 2:
            out_w += 1
        if out_h % 2:
            out_h += 1
        frame_size = (out_w, out_h) if target_width else None

        tile_size = 400
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

            # Timeout check removed as requested
            pass

            result = upscale_image(
                frame, model, model_scale,
                outscale=outscale, tile_size=tile_size,
                device=device, precision=_model_manager.current_precision,
                sharpen=sharpen, denoise_strength=denoise_strength, two_pass=two_pass,
                face_enhance=face_enhance, target_size=frame_size,
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
            double_fps=double_fps,
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
        _release_job_model()
        _cancel_flags.pop(job_id, None)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _run_image_batch_job(
    job_id: str,
    inputs: list,
    output_dir: str,
    model_name: str,
    scale: Optional[float],
    denoise_strength: float,
    sharpen: float,
    two_pass: bool,
    face_enhance: bool,
    suffix: str,
    cancel_event: threading.Event,
    target_width: Optional[int] = None,
):
    """Background worker for an image-batch upscale job.

    Frame counters count images. A file that fails to decode or write is
    recorded in ``error`` and skipped; the batch only fails outright when no
    image succeeded.
    """
    start_time = time.time()
    try:
        if cancel_event.is_set():
            _job_manager.cancel_job(job_id)
            logger.info(f"Job {job_id} cancelled before start")
            return

        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info(0)
            free_mb = free / (1024 * 1024)
            if free_mb < 500:
                raise RuntimeError(f"Insufficient VRAM: {free_mb:.0f}MB free, need ~500MB minimum")

        model, model_scale = _claim_job_model(model_name)

        tile_size = 400
        if _config.max_tile_size != "auto":
            tile_size = int(_config.max_tile_size)

        os.makedirs(output_dir, exist_ok=True)
        _job_manager.start_job(job_id, total_frames=len(inputs))
        _send_callback("upscale:job_started", {
            "job_id": job_id,
            "input_file": inputs[0] if inputs else output_dir,
            "model": model_name,
        })

        device = "cuda" if torch.cuda.is_available() else "cpu"
        done = 0
        failures = []

        for input_path in inputs:
            if cancel_event.is_set():
                raise InterruptedError("Job cancelled")

            img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                failures.append(f"{os.path.basename(input_path)}: unreadable")
                done += 1
                _job_manager.update_progress(job_id, done, 0.0)
                continue

            # Same lock the sync image endpoints take — model swap and upscale
            # must not interleave with a concurrent request.
            with _image_upscale_lock:
                output = upscale_image(
                    img, model, model_scale,
                    outscale=scale, tile_size=tile_size,
                    device=device, precision=_model_manager.current_precision,
                    sharpen=sharpen, denoise_strength=denoise_strength,
                    two_pass=two_pass, face_enhance=face_enhance,
                    target_size=_target_size(img, target_width),
                )

            stem = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(output_dir, f"{stem}_{suffix}.png")
            if not _atomic_imwrite(output_path, output):
                failures.append(f"{os.path.basename(input_path)}: write failed")
                done += 1
                _job_manager.update_progress(job_id, done, 0.0)
                continue
            _job_manager.add_output(job_id, output_path)

            done += 1
            elapsed = time.time() - start_time
            _job_manager.update_progress(job_id, done, done / elapsed if elapsed > 0 else 0.0)

        if failures and len(failures) == len(inputs):
            raise RuntimeError("; ".join(failures[:5]))
        if failures:
            _job_manager.set_error(job_id, f"{len(failures)} skipped: " + "; ".join(failures[:5]))
        _job_manager.complete_job(job_id)
        logger.info(f"Job {job_id} completed: {done - len(failures)}/{len(inputs)} images -> {output_dir}")

        _send_callback("upscale:job_completed", {
            "job_id": job_id,
            "output_file": output_dir,
            "duration_seconds": round(time.time() - start_time, 1),
        })

    except InterruptedError as e:
        _job_manager.cancel_job(job_id)
        logger.info(f"Job {job_id} stopped: {e}")
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        _job_manager.fail_job(job_id, error=str(e))
        _send_callback("upscale:job_failed", {"job_id": job_id, "error": str(e)})
    finally:
        _release_job_model()
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

# Status only, and the one route open without the token (start.sh and the
# Plugins page probe it). The token is never in a reply: the backend reads it
# from data/.upscaling_internal_secret (service/auth.py), and the backend
# relays this reply to browsers.
@app.get("/health")
def health():
    return get_health_status(
        model_loaded=_model_manager.current_model_name if _model_manager else None,
        active_jobs=_job_manager.active_job_count if _job_manager else 0,
        compile_enabled=_config.compile_model if _config else False,
    )


@app.get("/models")
def list_models():
    if not _model_manager:
        raise HTTPException(503, "Service not initialized")
    # default_model: the configured default, so the page can offer Install for
    # it when nothing is installed. request_default: the installed model a
    # request without a model runs on, which the page preselects and the
    # backend books VRAM for; None when nothing is installed.
    listing = _model_manager.list_models()
    configured = _config.default_model if _config else None
    installed = [m["name"] for m in listing["downloaded"]]
    request_default = resolve_default_model(configured, installed) if installed else None
    return {
        **listing,
        "default_model": configured,
        "request_default": request_default,
        # Whether Face Enhancement can run here and what its Install still fetches.
        "face_enhance": face_enhance_status(face_enhance_supported()),
    }


@app.post("/models/download")
def download_model(req: ModelDownloadRequest, request: Request):
    verify_token(request)
    if not _model_manager:
        raise HTTPException(503, "Service not initialized")
    if req.model == FACE_ENHANCE_MODEL and not face_enhance_supported():
        # Its weights would sit unused.
        raise HTTPException(409, face_enhance_unavailable_message())
    try:
        path = _model_manager.download_model(req.model)
        return {"status": "downloaded", "model": req.model, "path": path}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/upscale/image")
def upscale_image_endpoint(req: ImageUpscaleRequest, request: Request):
    verify_token(request)
    output_path = confined_output(req.output_path)
    input_path = confined_input(req.input_path)
    if not os.path.isfile(input_path):
        raise HTTPException(400, f"Input file not found: {req.input_path}")

    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise HTTPException(400, f"Could not read image: {req.input_path}")
    _require_installed(req.model)
    _require_face_enhance(req.face_enhance)

    # Serialise model swap + upscale together — see `_image_upscale_lock`
    # comment near the globals for the race we're avoiding.
    with _image_upscale_lock:
        name = _sync_model(req.model)
        model = _model_manager.get_model()
        model_scale = _model_manager.scale or 4

        tile_size = 400
        if _config.max_tile_size != "auto":
            tile_size = int(_config.max_tile_size)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        output = upscale_image(
            img, model, model_scale,
            outscale=req.scale, tile_size=tile_size,
            device=device, precision=_model_manager.current_precision,
            sharpen=req.sharpen if req.sharpen is not None else 0.3,
            denoise_strength=req.denoise_strength if req.denoise_strength is not None else 0.0,
            two_pass=req.two_pass if req.two_pass is not None else False,
            face_enhance=req.face_enhance if req.face_enhance is not None else False,
            target_size=_target_size(img, req.target_width),
        )
    _free_cache_unless_job()

    if not _atomic_imwrite(output_path, output):
        raise HTTPException(500, f"Could not write image: {req.output_path}")
    return {"status": "completed", "output_path": output_path}


# Fix 4: Multipart image upload endpoint
@app.post("/upscale/image/upload")
async def upscale_image_upload(
    request: Request,
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    scale: Optional[float] = Form(None),
    sharpen: Optional[float] = Form(0.3),
    denoise_strength: Optional[float] = Form(0.0),
    two_pass: Optional[bool] = Form(False),
    face_enhance: Optional[bool] = Form(False),
):
    verify_token(request)
    contents = await file.read()
    arr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise HTTPException(400, "Could not decode image")
    _require_installed(model)
    _require_face_enhance(face_enhance)

    # See `_image_upscale_lock` comment — same race as /upscale/image.
    with _image_upscale_lock:
        name = _sync_model(model)
        mdl = _model_manager.get_model()
        model_scale = _model_manager.scale or 4

        tile_size = 400
        if _config.max_tile_size != "auto":
            tile_size = int(_config.max_tile_size)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        output = upscale_image(
            img, mdl, model_scale,
            outscale=scale, tile_size=tile_size,
            device=device, precision=_model_manager.current_precision,
            sharpen=sharpen, denoise_strength=denoise_strength, two_pass=two_pass,
            face_enhance=face_enhance,
        )
    _free_cache_unless_job()

    import tempfile
    tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp_file.name, output)
    # FileResponse needs the file alive until the client finishes reading,
    # so delete=False is required. The BackgroundTask ensures we still
    # reap it after the response stream closes — no orphaned temp PNGs.
    return FileResponse(
        tmp_file.name,
        media_type="image/png",
        filename=f"upscaled_{file.filename}",
        background=BackgroundTask(os.unlink, tmp_file.name),
    )


@app.post("/upscale/images", status_code=202)
def upscale_images_endpoint(req: ImageBatchUpscaleRequest, request: Request):
    """Queue a batch of images as a single job on the shared GPU worker."""
    verify_token(request)
    if not req.inputs:
        raise HTTPException(400, "inputs must not be empty")
    output_dir = confined_output(req.output_dir)
    # The suffix becomes part of each file name inside that folder.
    if "/" in req.suffix or "\\" in req.suffix or "\x00" in req.suffix:
        raise HTTPException(400, "suffix must not contain a path separator")

    inputs = [confined_input(p) for p in req.inputs]
    missing = [p for p in inputs if not os.path.isfile(p)]
    if missing:
        raise HTTPException(400, f"Input file not found: {missing[0]}")
    _require_installed(req.model)
    _require_face_enhance(req.face_enhance)

    job = _start_image_batch_job(
        inputs=inputs,
        output_dir=output_dir,
        model_name=req.model,
        scale=req.scale,
        denoise_strength=req.denoise_strength if req.denoise_strength is not None else 0.0,
        sharpen=req.sharpen if req.sharpen is not None else 0.3,
        two_pass=req.two_pass if req.two_pass is not None else False,
        face_enhance=req.face_enhance if req.face_enhance is not None else False,
        suffix=req.suffix,
        target_width=req.target_width,
    )
    return job


@app.post("/upscale/video", status_code=202)
def upscale_video_endpoint(req: VideoUpscaleRequest, request: Request):
    verify_token(request)
    input_path = confined_input(req.input_path)
    if not os.path.isfile(input_path):
        raise HTTPException(400, f"Input file not found: {req.input_path}")
    if req.output_path:
        output_path = confined_output(req.output_path)
    else:
        base = os.path.splitext(input_path)[0]
        output_path = _free_output_path(confined_output(f"{base}_{req.suffix}.mp4"))
    _require_installed(req.model)
    _require_face_enhance(req.face_enhance)

    # Note: VRAM is checked at job-start time (inside the worker), not here.
    # When jobs are queued, free VRAM at submission time is irrelevant — by
    # the time this job's turn comes up, the previous one has released it.

    job = _start_video_job(
        input_path=input_path,
        output_path=output_path,
        model_name=req.model,
        scale=req.scale,
        denoise_strength=req.denoise_strength if req.denoise_strength is not None else 0.0,
        sharpen=req.sharpen if req.sharpen is not None else 0.3,
        two_pass=req.two_pass if req.two_pass is not None else False,
        face_enhance=req.face_enhance if req.face_enhance is not None else False,
        double_fps=req.double_fps if req.double_fps is not None else False,
        target_width=req.target_width,
    )
    return job


@app.get("/status")
def status():
    """What this service holds on the GPU and whether it is working. The
    backend reads it before asking /evict for the memory."""
    loaded = _model_manager.current_model_name if _model_manager and _model_manager.is_loaded else None
    return {
        "model_loaded": loaded,
        "busy": _running_job_id is not None or _image_upscale_lock.locked(),
        "running_job": _running_job_id,
        "queued": _job_queue.qsize() if _job_queue is not None else 0,
        "reserved_mb": int(torch.cuda.memory_reserved() // (1024 * 1024)) if torch.cuda.is_available() else 0,
    }


@app.post("/evict")
def evict(request: Request):
    """Unload the model and return its memory, unless a job runs or waits.

    The backend calls this when another GPU job is short of memory. A busy
    service answers unloaded: false, so a running upscale is never cut off.
    """
    verify_token(request)
    if _running_job_id is not None or (_job_queue is not None and _job_queue.qsize()):
        return {"unloaded": False, "reason": "a job is running or queued"}
    if not _image_upscale_lock.acquire(blocking=False):
        return {"unloaded": False, "reason": "an image is being upscaled"}
    try:
        name = _model_manager.current_model_name if _model_manager.is_loaded else None
        if name is None:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            return {"unloaded": False, "reason": "no model loaded"}
        _model_manager.unload()
        logger.info(f"Evicted model {name} on request")
        return {"unloaded": True, "model": name}
    finally:
        _image_upscale_lock.release()


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


@app.delete("/jobs")
def clear_finished_jobs(request: Request):
    """Remove all completed/failed/cancelled jobs from history. Active jobs stay."""
    verify_token(request)
    if not _job_manager:
        raise HTTPException(503, "Service not initialized")
    removed = _job_manager.clear_finished()
    return {"removed": removed}


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


@app.put("/config")
async def update_config(request: Request):
    # Per-machine runtime config lives in data/, NOT plugin.json. Mutating the
    # manifest from running code makes it drift between machines and the
    # state-sync system flags it forever (see static-manifest refactor 2026-04-30).
    verify_token(request)
    body = await request.json()

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    runtime_config_dir = os.environ.get(
        "GUAARDVARK_PLUGIN_CONFIG_DIR",
        os.path.join(project_root, "data", "plugin_config"),
    )
    os.makedirs(runtime_config_dir, exist_ok=True)
    runtime_config_path = os.path.join(runtime_config_dir, "upscaling.json")

    try:
        with open(runtime_config_path) as f:
            existing = json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    existing.update(body)
    tmp_path = runtime_config_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp_path, runtime_config_path)

    if _config:
        for key, value in body.items():
            if hasattr(_config, key):
                setattr(_config, key, value)

    return {"status": "updated", "config": {k: v for k, v in _config.__dict__.items()} if _config else {}}
