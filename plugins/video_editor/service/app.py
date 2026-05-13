"""FastAPI entrypoint for the Video Editor plugin.

Routes:
  GET  /health                  — liveness
  GET  /status                  — full service snapshot
  GET  /config                  — merged manifest + runtime config
  POST /beat-sync/render        — beat-sync a soundtrack against video pool
  POST /auto-editor/trim        — silence-removal trim via auto-editor CLI
  POST /shotcut/compose         — emit .mlt from a generic timeline JSON (M3)
  GET  /jobs                    — list recent jobs
  GET  /jobs/{job_id}           — poll one job

Heavy work runs on the JobTable's thread pool; HTTP handlers return job_ids
immediately.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from mlt.auto_editor_runner import run_auto_editor
from mlt.beat_detector import BeatFilterParams, detect_beats
from mlt.frame_math import FrameRate
from mlt.mlt_parser import MediaAsset, ProjectProfile
from mlt.mlt_writer import plan_cuts_from_beats, write_project
from mlt.render import MeltNotFound, render_mlt
from mlt.timeline_compose import compose_timeline, timeline_from_payload

from service.config_loader import load_config
from service.crew_interface import LocalArtDirector
from service.jobs import Job, JobTable
from service.jobs_pipeline import BinClip, PlanRequest, run_plan
from service.registration import register_output
from service.style_recipe_loader import list_recipes, load_recipe

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

_config = load_config()
_runtime = _config["runtime"]
_paths = _config["paths"]

_jobs = JobTable(
    max_entries=_runtime.get("jobs", {}).get("max_entries", 200),
    worker_threads=_runtime.get("jobs", {}).get("worker_threads", 2),
)

# v1 Crew implementation. Swap to FilmCrewClient when plugins/film_crew/ lands.
_crew = LocalArtDirector(ollama_url="http://localhost:11434")

app = FastAPI(
    title="Video Editor",
    version=_config["manifest"].get("version", "0.1.0"),
    description="MLT/Shotcut + auto-editor backend for Guaardvark Video Editor.",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


# ---------- request models ---------------------------------------------------

class BeatSyncRequest(BaseModel):
    audio_path: str = Field(..., description="Absolute path to soundtrack file.")
    video_paths: list[str] = Field(..., min_length=1, description="Source video pool.")
    fps_num: int = Field(30)
    fps_den: int = Field(1)
    width: int = Field(1920)
    height: int = Field(1080)
    subdivision: int = Field(2, ge=1)
    min_clip_seconds: float = Field(1.2, ge=0.0)
    tightness: int = Field(100, ge=1)
    use_onsets: bool = Field(False)
    seed: Optional[int] = None
    render_mp4: bool = Field(False, description="If true, also encode final MP4 via melt.")
    register: bool = Field(True, description="POST outputs to backend as Documents.")


class AutoEditorRequest(BaseModel):
    input_path: str
    threshold: float = Field(0.04, ge=0.0, le=1.0)
    margin: str = Field("0.2sec")
    mode: str = Field("mp4", pattern="^(mp4|kdenlive)$")
    register: bool = Field(False)


class PlanBinClip(BaseModel):
    clip_id: str
    source_path: str
    document_id: Optional[int] = None


class PlanBody(BaseModel):
    bin_clips: list[PlanBinClip] = Field(..., min_length=1)
    song_path: str
    scan_mode: str = Field("both-and", pattern="^(audio|motion|both-or|both-and)$")
    audio_threshold: float = Field(0.04, ge=0.0, le=1.0)
    motion_threshold: float = Field(0.02, ge=0.0, le=1.0)
    margin: str = "0.2sec"
    style_recipe_name: str = "default"
    seed: int = 0


class OpenInShotcutBody(BaseModel):
    mlt_path: str


class VisionScanBody(BaseModel):
    """A3 will run real qwen3-vl here; A1 stub returns neutral defaults."""

    clip_paths: list[str] = Field(..., min_length=1)


class ShotcutComposeRequest(BaseModel):
    """Generic VideoEditorPage-style timeline → .mlt (and optional .mp4).

    Mirrors the shape the existing Flask /api/video-overlay/render-timeline
    endpoint already consumes; paths are absolute (Flask proxy resolves
    document_id → path before forwarding).
    """

    video_path: str
    audio_path: Optional[str] = None
    video_trim_start: float = 0.0
    video_trim_end: Optional[float] = None
    audio_volume: float = 1.0
    text_elements: list[dict[str, Any]] = Field(default_factory=list)

    fps_num: int = 30
    fps_den: int = 1
    width: int = 1920
    height: int = 1080

    video_source_duration_seconds: Optional[float] = Field(
        None, description="Hint: video source duration so trim_end=None falls back here."
    )
    render_mp4: bool = Field(False)
    register: bool = Field(True)


# ---------- read endpoints ---------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "video_editor"}


@app.get("/status")
def status() -> dict[str, Any]:
    return {
        "service": "video_editor",
        "version": _config["manifest"].get("version", "0.0.0"),
        "port": _config["manifest"].get("port"),
        "melt_resolved_path": _runtime.get("melt", {}).get("resolved_path", ""),
        "paths": _paths,
        "jobs": {
            "total": len(_jobs.list(limit=10000)),
            "recent": [j.to_dict() for j in _jobs.list(limit=5)],
        },
    }


@app.get("/config")
def get_config() -> dict[str, Any]:
    return _config


@app.get("/jobs")
def list_jobs(limit: int = 50) -> dict[str, Any]:
    return {"jobs": [j.to_dict() for j in _jobs.list(limit=limit)]}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job.to_dict()


# ---------- write endpoints --------------------------------------------------

@app.post("/beat-sync/render")
def beat_sync_render(req: BeatSyncRequest) -> dict[str, Any]:
    """Schedule a beat-sync render job; return job_id immediately."""
    _require_paths(req.audio_path, *req.video_paths)

    def task(job: Job) -> dict[str, Any]:
        return _do_beat_sync_render(job, req)

    job = _jobs.submit("beat_sync_render", task)
    return {"job_id": job.id, "status": job.status}


@app.post("/auto-editor/trim")
def auto_editor_trim(req: AutoEditorRequest) -> dict[str, Any]:
    """Run auto-editor in JSON export mode and return the cut list inline.

    Synchronous because auto-editor on a short clip is fast and the caller
    usually wants the cut list before continuing.
    """
    _require_paths(req.input_path)

    out_dir = Path(_paths["mlt_projects"])
    out_dir.mkdir(parents=True, exist_ok=True)

    result = run_auto_editor(
        req.input_path,
        output_dir=out_dir,
        auto_editor_path=_runtime.get("auto_editor", {}).get("path", "auto-editor"),
        threshold=req.threshold,
        margin=req.margin,
        mode=req.mode,
    )
    response: dict[str, Any] = {
        "source_path": str(result.source_path),
        "output_path": str(result.output_path),
        "mode": result.mode,
        "threshold": result.threshold,
        "clips": [{"start": c.start, "end": c.end} for c in result.clips],
        "documents": [],
    }
    if req.register:
        doc = register_output(
            result.output_path,
            backend_url=_runtime.get("registration", {}).get("backend_url", "http://localhost:5002"),
            folder=_runtime.get("registration", {}).get("folder", "Videos"),
            file_metadata={"kind": f"auto_editor_{result.mode}", "threshold": result.threshold},
        )
        if doc:
            response["documents"].append(doc)
    return response


@app.post("/shotcut/compose")
def shotcut_compose(req: ShotcutComposeRequest) -> dict[str, Any]:
    """Compose a VideoEditorPage timeline into a .mlt; optionally render MP4."""
    _require_paths(req.video_path)
    if req.audio_path:
        _require_paths(req.audio_path)

    profile = ProjectProfile(
        frame_rate=FrameRate(req.fps_num, req.fps_den),
        width=req.width,
        height=req.height,
    )

    timeline = timeline_from_payload(req.model_dump())
    mlt_dir = Path(_paths["mlt_projects"])
    mlt_dir.mkdir(parents=True, exist_ok=True)
    mlt_path = mlt_dir / f"timeline_{uuid.uuid4().hex[:12]}.mlt"
    compose_timeline(
        timeline,
        mlt_path,
        profile,
        video_source_duration_seconds=req.video_source_duration_seconds,
    )

    response: dict[str, Any] = {
        "mlt_path": str(mlt_path),
        "text_overlay_count": len(timeline.text_elements),
        "rendered_mp4": None,
        "documents": [],
    }

    if req.register:
        doc = register_output(
            mlt_path,
            backend_url=_runtime.get("registration", {}).get("backend_url", "http://localhost:5002"),
            folder=_runtime.get("registration", {}).get("folder", "Videos"),
            file_metadata={"kind": "mlt_timeline", "text_overlays": len(timeline.text_elements)},
        )
        if doc:
            response["documents"].append(doc)

    if req.render_mp4:
        melt_path = _runtime.get("melt", {}).get("resolved_path", "") or "melt"
        renders_dir = Path(_paths["renders"])
        renders_dir.mkdir(parents=True, exist_ok=True)
        mp4_path = renders_dir / (mlt_path.stem + ".mp4")
        try:
            render = render_mlt(
                mlt_path,
                mp4_path,
                melt_path=melt_path,
                vcodec=_runtime.get("melt", {}).get("default_vcodec", "libx264"),
                acodec=_runtime.get("melt", {}).get("default_acodec", "aac"),
            )
        except MeltNotFound as e:
            raise HTTPException(status_code=500, detail=f"melt unavailable: {e}") from e
        response["rendered_mp4"] = str(render.output_path)
        if req.register:
            doc = register_output(
                render.output_path,
                backend_url=_runtime.get("registration", {}).get("backend_url", "http://localhost:5002"),
                folder=_runtime.get("registration", {}).get("folder", "Videos"),
                file_metadata={"kind": "mlt_render", "text_overlays": len(timeline.text_elements)},
            )
            if doc:
                response["documents"].append(doc)

    return response


# ---------- internals --------------------------------------------------------

def _require_paths(*paths: str) -> None:
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        raise HTTPException(status_code=400, detail=f"file(s) not found: {missing}")


def _do_beat_sync_render(job: Job, req: BeatSyncRequest) -> dict[str, Any]:
    profile = ProjectProfile(
        frame_rate=FrameRate(req.fps_num, req.fps_den),
        width=req.width,
        height=req.height,
    )

    job.progress = 0.1
    analysis = detect_beats(
        req.audio_path,
        BeatFilterParams(
            subdivision=req.subdivision,
            min_clip_seconds=req.min_clip_seconds,
            tightness=req.tightness,
            use_onset_envelope=req.use_onsets,
        ),
    )
    logger.info(
        "beat-sync: tempo=%.2f beats=%d duration=%.2fs",
        analysis.tempo_bpm, len(analysis.beat_times), analysis.duration_seconds,
    )

    assets = [MediaAsset(producer_id=f"src{i}", resource_path=p) for i, p in enumerate(req.video_paths)]
    cuts = plan_cuts_from_beats(analysis.beat_times, assets, profile, seed=req.seed)
    job.progress = 0.4

    mlt_dir = Path(_paths["mlt_projects"])
    mlt_dir.mkdir(parents=True, exist_ok=True)
    mlt_path = mlt_dir / f"beat_sync_{uuid.uuid4().hex[:12]}.mlt"
    write_project(mlt_path, cuts, req.audio_path, profile, audio_out_seconds=analysis.duration_seconds)
    job.progress = 0.5

    result: dict[str, Any] = {
        "mlt_path": str(mlt_path),
        "tempo_bpm": analysis.tempo_bpm,
        "beat_count": len(analysis.beat_times),
        "cut_count": len(cuts),
        "duration_seconds": analysis.duration_seconds,
        "rendered_mp4": None,
        "documents": [],
    }

    if req.register:
        doc = register_output(
            mlt_path,
            backend_url=_runtime.get("registration", {}).get("backend_url", "http://localhost:5002"),
            folder=_runtime.get("registration", {}).get("folder", "Videos"),
            file_metadata={"kind": "mlt_project", "cut_count": len(cuts)},
        )
        if doc:
            result["documents"].append(doc)

    if req.render_mp4:
        job.progress = 0.6
        melt_path = _runtime.get("melt", {}).get("resolved_path", "") or "melt"
        renders_dir = Path(_paths["renders"])
        renders_dir.mkdir(parents=True, exist_ok=True)
        mp4_path = renders_dir / (mlt_path.stem + ".mp4")
        try:
            render = render_mlt(
                mlt_path,
                mp4_path,
                melt_path=melt_path,
                vcodec=_runtime.get("melt", {}).get("default_vcodec", "libx264"),
                acodec=_runtime.get("melt", {}).get("default_acodec", "aac"),
            )
        except MeltNotFound as e:
            raise RuntimeError(f"melt unavailable, set melt.path in config.yaml: {e}") from e
        result["rendered_mp4"] = str(render.output_path)
        job.progress = 0.9

        if req.register:
            doc = register_output(
                render.output_path,
                backend_url=_runtime.get("registration", {}).get("backend_url", "http://localhost:5002"),
                folder=_runtime.get("registration", {}).get("folder", "Videos"),
                file_metadata={
                    "kind": "beat_sync_render",
                    "tempo_bpm": analysis.tempo_bpm,
                    "duration_seconds": render.duration_seconds,
                },
            )
            if doc:
                result["documents"].append(doc)

    return result


# ---------- Plan pipeline ---------------------------------------------------


@app.get("/recipes")
def list_style_recipes() -> dict[str, Any]:
    """List available Style Recipes (data/agent/style_recipes/*.json)."""
    return {"recipes": [r.to_dict() for r in list_recipes()]}


@app.post("/plan")
def submit_plan(body: PlanBody) -> dict[str, Any]:
    """Submit a Plan job: bin + song + scan_mode → arrangement.json (async)."""
    _require_paths(body.song_path, *[c.source_path for c in body.bin_clips])

    recipe = load_recipe(body.style_recipe_name)
    recipe_dict = recipe.to_dict() if recipe else None

    plan_req = PlanRequest(
        bin_clips=[BinClip(clip_id=c.clip_id, source_path=c.source_path, document_id=c.document_id)
                   for c in body.bin_clips],
        song_path=body.song_path,
        scan_mode=body.scan_mode,
        audio_threshold=body.audio_threshold,
        motion_threshold=body.motion_threshold,
        margin=body.margin,
        style_recipe=recipe_dict,
        seed=body.seed,
    )

    analyze_dir = Path(_paths["mlt_projects"]).parent / "auto-editor-scans"
    vision_cache_dir = Path(_paths["mlt_projects"]).parent / "clip-scans"

    def task(job: Job) -> dict[str, Any]:
        def progress(pct: float, message: str) -> None:
            job.progress = pct
            logger.info("plan job %s: %.0f%% %s", job.id, pct * 100, message)

        result = run_plan(
            plan_req,
            crew=_crew,
            analyze_out_dir=analyze_dir,
            vision_cache_dir=vision_cache_dir,
            progress_cb=progress,
        )
        return result.to_dict()

    job = _jobs.submit("plan", task)
    return {"job_id": job.id, "status": job.status}


@app.post("/vision/scan-clips")
def vision_scan_clips(body: VisionScanBody) -> dict[str, Any]:
    """Per-clip vision analysis. A1: neutral defaults via LocalArtDirector. A3: qwen3-vl."""
    _require_paths(*body.clip_paths)
    out: list[dict[str, Any]] = []
    for p in body.clip_paths:
        analysis = _crew.analyze_clip(frames=[], clip_id=Path(p).stem, source_path=p)
        out.append(analysis.to_dict())
    return {"analyses": out}


@app.post("/open-in-shotcut")
def open_in_shotcut(body: OpenInShotcutBody) -> dict[str, Any]:
    """Spawn Shotcut on a .mlt file. Path must live under the configured mlt_projects dir."""
    import shutil
    import subprocess

    mlt_path = Path(body.mlt_path).resolve()
    if not mlt_path.exists():
        raise HTTPException(status_code=404, detail=f"mlt not found: {mlt_path}")

    allowed_root = Path(_paths["mlt_projects"]).resolve()
    try:
        mlt_path.relative_to(allowed_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"mlt path must be under {allowed_root}",
        )

    shotcut_bin = shutil.which("shotcut")
    if not shotcut_bin:
        raise HTTPException(status_code=500, detail="shotcut binary not on PATH")

    # Detached spawn — we don't track it.
    subprocess.Popen([shotcut_bin, str(mlt_path)], start_new_session=True)
    return {"launched": str(mlt_path)}
