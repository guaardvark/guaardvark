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

from service.config_loader import load_config
from service.jobs import Job, JobTable
from service.registration import register_output

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

_config = load_config()
_runtime = _config["runtime"]
_paths = _config["paths"]

_jobs = JobTable(
    max_entries=_runtime.get("jobs", {}).get("max_entries", 200),
    worker_threads=_runtime.get("jobs", {}).get("worker_threads", 2),
)

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


class ShotcutComposeRequest(BaseModel):
    """Placeholder for M3 — generic timeline JSON → .mlt."""

    timeline: dict[str, Any]
    fps_num: int = 30
    fps_den: int = 1
    width: int = 1920
    height: int = 1080


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
    """M3 placeholder. Accepts a timeline JSON, emits .mlt — not yet wired."""
    raise HTTPException(
        status_code=501,
        detail="/shotcut/compose lands in M3. Use /beat-sync/render for now.",
    )


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
