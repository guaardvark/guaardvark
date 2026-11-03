"""FastAPI application for the Vision Pipeline plugin.

All endpoints are sync def (not async def). Uvicorn runs them in its
default thread pool. Single worker process.
"""
import importlib.util
import os
import sys
import time
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from service.config_loader import load_config
from service.change_detector import ChangeDetector
from service.context_buffer import ContextBuffer
from service.model_tier import ModelTier, OllamaCapabilities
from service.frame_analyzer import FrameAnalyzer
from service.adaptive_throttle import AdaptiveThrottle
from service.stream_manager import StreamManager
from service.benchmarker import Benchmarker
from service.camera_capture import (
    CameraCapture, CameraError, CameraNotFoundError, CameraInUseError,
)

logger = logging.getLogger("vision_pipeline.app")

_GUARD_MODULE = "guaardvark_sidecar_guard"


def _load_guard():
    """backend/utils/sidecar_guard.py, loaded by path: this service runs
    outside the backend package."""
    loaded = sys.modules.get(_GUARD_MODULE)
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().parents[3] / "backend" / "utils" / "sidecar_guard.py"
    spec = importlib.util.spec_from_file_location(_GUARD_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[_GUARD_MODULE] = module
    return module


_guard = _load_guard()

app = FastAPI(title="Guaardvark Vision Pipeline", version="1.0.0")

# CORS — restrict to frontend origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5175"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Bearer token security ---
# Every route but /health needs the token: the camera, its frames and the
# scene context are behind this port, and only the backend calls it. The
# token is in data/.vision_pipeline_internal_secret, which the backend reads
# too (backend/utils/vision_context_utils.py); no reply carries it. POST
# /frame, POST /analyze and PUT /config still check it themselves too.
TOKEN_NAME = "vision_pipeline"
app.add_middleware(_guard.BearerTokenASGIMiddleware, name=TOKEN_NAME)
# Added last, so it runs first: a request addressed to a name that is not
# this machine's (a page re-pointed at 127.0.0.1) is refused before the token
# gate, CORS or any route sees it.
app.add_middleware(_guard.HostCheckASGIMiddleware)


def _auth_token() -> str:
    return _guard.internal_token(TOKEN_NAME)


def _verify_token(request: Request):
    """Check Authorization: Bearer <token> header."""
    if not _guard.bearer_matches(TOKEN_NAME, request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")

# --- Global pipeline components (initialized on startup) ---
_config = None
_change_detector = None
_context_buffer = None
_model_tier = None
_frame_analyzer = None
_adaptive_throttle = None
_stream_manager = None
_benchmarker = None
_camera_capture = None
_start_time = time.time()
_health_status = "starting"


@app.on_event("startup")
def startup():
    global _config, _change_detector, _context_buffer, _model_tier
    global _frame_analyzer, _adaptive_throttle, _stream_manager, _benchmarker
    global _camera_capture, _health_status, _start_time

    _start_time = time.time()
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _config = load_config(plugin_root)

    _change_detector = ChangeDetector(
        phash_threshold=_config.phash_threshold,
        periodic_refresh_seconds=_config.periodic_refresh_seconds,
    )
    _context_buffer = ContextBuffer(
        window_seconds=_config.context_window_seconds,
        max_entries=_config.max_entries,
        compression_interval=_config.compression_interval,
        max_context_tokens=_config.max_context_tokens,
        default_max_fps=_config.max_fps,
    )
    capabilities = OllamaCapabilities(_config.ollama_url)
    _model_tier = ModelTier(
        monitor_model=_config.monitor_model,
        escalation_model=_config.escalation_model,
        fallback_order=_config.fallback_order,
        ollama_url=_config.ollama_url,
        monitor_prompt=_config.monitor_prompt,
        escalation_prompt=_config.escalation_prompt,
        capabilities=capabilities,
    )
    _frame_analyzer = FrameAnalyzer(ollama_url=_config.ollama_url)
    _frame_analyzer.escalation_model = _config.escalation_model
    # /analyze without a model walks the same fallback chain as the loop.
    _frame_analyzer.model_resolver = lambda: _model_tier.select_model("user_query")[0]
    _frame_analyzer.supports_thinking = capabilities.supports_thinking
    _adaptive_throttle = AdaptiveThrottle(_config)
    _stream_manager = StreamManager(
        _config, _frame_analyzer, _change_detector,
        _context_buffer, _model_tier, _adaptive_throttle,
    )
    _benchmarker = Benchmarker(ollama_url=_config.ollama_url, capabilities=capabilities)

    _camera_capture = CameraCapture(_stream_manager, _config)
    # The token file exists before the first request, so the backend has a
    # token to send.
    _auth_token()

    # Check vision model availability
    if _model_tier.get_any_available_model():
        _health_status = "healthy"
        logger.info("Vision Pipeline started — vision models available")
    else:
        _health_status = "error"
        logger.warning("Vision Pipeline started — NO vision models available")


@app.on_event("shutdown")
def shutdown():
    if _camera_capture:
        _camera_capture.stop()
    logger.info("Vision Pipeline shutting down")


# --- Pydantic models for request bodies ---
class StreamStartRequest(BaseModel):
    stream_id: Optional[str] = None
    source_type: str = "camera"

class StreamStopRequest(BaseModel):
    stream_id: str

class FrameRequest(BaseModel):
    stream_id: str
    frame: str
    timestamp: float = 0

class AnalyzeRequest(BaseModel):
    frame: str
    prompt: str = "Describe this image in detail."
    model: Optional[str] = None

class ContentionRequest(BaseModel):
    source: str
    action: str

class BenchmarkRequest(BaseModel):
    models: Optional[list] = None
    frame_count: int = 5
    resolutions: Optional[list] = None


# --- Endpoints ---

@app.get("/health")
def health():
    """Status only, and the one route open without the token (start.sh and
    the Plugins page probe it); the backend relays this reply to browsers."""
    return {
        "status": _health_status,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "active_streams": len([s for s in (_stream_manager.streams.values() if _stream_manager else [])
                               if s.status == "active"]),
        "ollama_connected": _model_tier.get_any_available_model() is not None if _model_tier else False,
        "monitor_model_loaded": _model_tier.verify_model_available(_config.monitor_model) if _model_tier else False,
    }

@app.get("/status")
def status():
    return {
        "streams": _stream_manager.get_status() if _stream_manager else {},
        "gpu": {"utilization_pct": _adaptive_throttle._get_gpu_utilization() if _adaptive_throttle else 0},
        "throttle": {
            "current_fps": _adaptive_throttle.current_fps if _adaptive_throttle else 0,
            "is_paused": _adaptive_throttle.is_paused if _adaptive_throttle else False,
        },
        "context_buffer": {
            "entries_count": len(_context_buffer.entries) if _context_buffer else 0,
            "compressed_summary_length": len(_context_buffer.compressed_summary) if _context_buffer else 0,
        },
        "camera": _camera_capture.status() if _camera_capture else {"active": False},
    }

# --- Camera capture endpoints ---

class CameraStartRequest(BaseModel):
    device_index: int = 0

@app.post("/camera/start")
def camera_start(req: CameraStartRequest):
    """Start capturing from a local camera device."""
    try:
        return _camera_capture.start(device_index=req.device_index)
    except CameraNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except CameraInUseError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except CameraError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/camera/stop")
def camera_stop():
    """Stop the camera capture."""
    return _camera_capture.stop()

@app.get("/camera/status")
def camera_status():
    """Get camera capture status."""
    return _camera_capture.status()

# --- Stream endpoints ---

@app.post("/stream/start")
def stream_start(req: StreamStartRequest):
    try:
        stream = _stream_manager.start_stream(req.stream_id, req.source_type)
        return {"stream_id": stream.id, "status": stream.status,
                "initial_fps": _config.max_fps}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.post("/stream/stop")
def stream_stop(req: StreamStopRequest):
    return _stream_manager.stop_stream(req.stream_id)

@app.post("/stream/pause")
def stream_pause(req: StreamStopRequest):
    _stream_manager.pause_stream(req.stream_id)
    return {"stream_id": req.stream_id, "status": "paused"}

@app.post("/stream/resume")
def stream_resume(req: StreamStopRequest):
    _stream_manager.resume_stream(req.stream_id)
    return {"stream_id": req.stream_id, "status": "active"}

@app.post("/frame")
def submit_frame(req: FrameRequest, request: Request):
    _verify_token(request)
    return _stream_manager.submit_frame(req.stream_id, req.frame,
                                        timestamp=req.timestamp or None)

@app.get("/context")
def get_context(stream_id: Optional[str] = None):
    """Return current vision context. Called by main backend on every chat message."""
    ctx = _context_buffer.get_context(
        current_interval=_adaptive_throttle.get_interval() if _adaptive_throttle else None
    )
    return asdict(ctx) if hasattr(ctx, '__dataclass_fields__') else {
        "is_active": ctx.is_active, "current_scene": ctx.current_scene,
        "recent_changes": ctx.recent_changes, "summary": ctx.summary,
        "confidence": ctx.confidence, "last_update": ctx.last_update,
        "model_used": ctx.model_used,
    }

@app.get("/frame/latest")
def frame_latest():
    data = _stream_manager.get_latest_frame()
    if data:
        return data
    return {"frame": None, "timestamp": 0, "stream_id": None}

@app.post("/analyze")
def analyze(req: AnalyzeRequest, request: Request):
    _verify_token(request)
    if req.model:
        result = _frame_analyzer.analyze(req.frame, req.model, req.prompt)
    else:
        result = _frame_analyzer.analyze_direct(req.frame, req.prompt)
    return {"description": result.description, "model_used": result.model_used,
            "inference_ms": result.inference_ms}

@app.post("/gpu/contention")
def gpu_contention(req: ContentionRequest):
    prev_fps = _adaptive_throttle.current_fps
    if req.action == "start":
        _adaptive_throttle.notify_gpu_contention()
    else:
        _adaptive_throttle.notify_gpu_available()
    return {"throttle_state": "paused" if _adaptive_throttle.is_paused else "active",
            "previous_fps": prev_fps}

def _benchmark_results_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmark_results.json"
    )

@app.post("/benchmark")
def run_benchmark(req: BenchmarkRequest):
    """Stream NDJSON benchmark results, then save the run for GET /benchmark/results."""
    resolutions = [tuple(r) for r in req.resolutions] if req.resolutions else None
    def generate():
        _benchmarker.results = []
        for result in _benchmarker.run(req.models, req.frame_count, resolutions):
            yield json.dumps(asdict(result)) + "\n"
        try:
            _benchmarker.save_results(_benchmark_results_path())
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not save benchmark results: %s", e)
    return StreamingResponse(generate(), media_type="application/x-ndjson")

@app.get("/benchmark/results")
def benchmark_results():
    results_path = _benchmark_results_path()
    if os.path.exists(results_path):
        with open(results_path) as f:
            return json.load(f)
    return {"last_run": None, "results": []}

@app.get("/config")
def get_config():
    return asdict(_config) if hasattr(_config, '__dataclass_fields__') else {}

@app.put("/config")
def update_config(updates: dict, request: Request):
    _verify_token(request)
    restart_required = False
    for key, value in updates.items():
        if hasattr(_config, key):
            if key in ("monitor_model", "escalation_model", "fallback_order"):
                # The tier reads its own copies; swap them in place.
                setattr(_model_tier, key, value)
            elif key == "phash_threshold":
                _change_detector.phash_threshold = int(value)
            elif key == "periodic_refresh_seconds":
                _change_detector.periodic_refresh_seconds = value
            setattr(_config, key, value)
    # max_fps, min_fps and the GPU thresholds are read live by the throttle.
    return {"updated": True, "restart_required": restart_required}
