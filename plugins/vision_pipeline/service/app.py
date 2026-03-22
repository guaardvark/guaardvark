"""FastAPI application for the Vision Pipeline plugin.

All endpoints are sync def (not async def). Uvicorn runs them in its
default thread pool. Single worker process.
"""
import os
import time
import json
import secrets
import logging
from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from service.config_loader import load_config
from service.change_detector import ChangeDetector
from service.context_buffer import ContextBuffer
from service.model_tier import ModelTier
from service.frame_analyzer import FrameAnalyzer
from service.adaptive_throttle import AdaptiveThrottle
from service.stream_manager import StreamManager
from service.benchmarker import Benchmarker

logger = logging.getLogger("vision_pipeline.app")

app = FastAPI(title="Guaardvark Vision Pipeline", version="1.0.0")

# CORS — restrict to frontend origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5175"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Bearer token security ---
# Generated at startup. Shared with main backend via /health response.
# Required on POST /frame, POST /analyze, PUT /config.
_auth_token = secrets.token_urlsafe(32)

def _verify_token(request: Request):
    """Check Authorization: Bearer <token> header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != _auth_token:
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
_start_time = time.time()
_health_status = "starting"


@app.on_event("startup")
def startup():
    global _config, _change_detector, _context_buffer, _model_tier
    global _frame_analyzer, _adaptive_throttle, _stream_manager, _benchmarker
    global _health_status, _start_time

    _start_time = time.time()
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _config = load_config(plugin_root)

    _change_detector = ChangeDetector(
        semantic_threshold=_config.change_threshold,
        periodic_refresh_seconds=_config.periodic_refresh_seconds,
    )
    _context_buffer = ContextBuffer(
        window_seconds=_config.context_window_seconds,
        max_entries=_config.max_entries,
        compression_interval=_config.compression_interval,
        max_context_tokens=_config.max_context_tokens,
        default_max_fps=_config.max_fps,
    )
    _model_tier = ModelTier(
        monitor_model=_config.monitor_model,
        escalation_model=_config.escalation_model,
        fallback_order=_config.fallback_order,
        ollama_url=_config.ollama_url,
        monitor_prompt=_config.monitor_prompt,
        escalation_prompt=_config.escalation_prompt,
        change_detected_prompt=_config.change_detected_prompt,
    )
    _frame_analyzer = FrameAnalyzer(
        ollama_url=_config.ollama_url,
        max_parallel=_config.max_parallel
    )
    _frame_analyzer.escalation_model = _config.escalation_model
    _adaptive_throttle = AdaptiveThrottle(_config)
    _stream_manager = StreamManager(
        _config, _frame_analyzer, _change_detector,
        _context_buffer, _model_tier, _adaptive_throttle,
    )
    _benchmarker = Benchmarker(ollama_url=_config.ollama_url)

    # Check vision model availability
    if _model_tier.get_any_available_model():
        _health_status = "healthy"
        logger.info("Vision Pipeline started — vision models available")
    else:
        _health_status = "error"
        logger.warning("Vision Pipeline started — NO vision models available")


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
def health(request: Request):
    """Health check. Returns auth token on first call (for main backend handshake)."""
    resp = {
        "status": _health_status,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "active_streams": len([s for s in (_stream_manager.streams.values() if _stream_manager else [])
                               if s.status == "active"]),
        "ollama_connected": _model_tier.get_any_available_model() is not None if _model_tier else False,
        "monitor_model_loaded": _model_tier.verify_model_available(_config.monitor_model) if _model_tier else False,
    }
    # Include token for initial handshake (no auth header = first contact)
    if not request.headers.get("Authorization"):
        resp["token"] = _auth_token
    return resp

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
    }

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
    return _stream_manager.submit_frame(req.stream_id, req.frame)

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
    import io
    import base64
    from PIL import Image
    try:
        img_bytes = base64.b64decode(req.frame)
        img = Image.open(io.BytesIO(img_bytes))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid frame data: {e}")

    if req.model:
        result = _frame_analyzer.analyze(img, req.model, req.prompt)
    else:
        result = _frame_analyzer.analyze_direct(img, req.prompt)
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

@app.post("/benchmark")
def run_benchmark(req: BenchmarkRequest):
    """Stream NDJSON benchmark results."""
    resolutions = [tuple(r) for r in req.resolutions] if req.resolutions else None
    def generate():
        for result in _benchmarker.run(req.models, req.frame_count, resolutions):
            yield json.dumps(asdict(result)) + "\n"
    return StreamingResponse(generate(), media_type="application/x-ndjson")

@app.get("/benchmark/results")
def benchmark_results():
    results_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmark_results.json"
    )
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
            if key in ("monitor_model", "escalation_model"):
                restart_required = True
            setattr(_config, key, value)
    return {"updated": True, "restart_required": restart_required}
