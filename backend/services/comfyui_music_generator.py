"""MiniMax Music 3 through ComfyUI: caption plus tagged lyrics in, a song out.

The graph is the official "Text to Music (MiniMax Music 3)" template:
UNETLoader + CLIPLoader(type minimax) + VAELoader → MiniMaxMusic3TextEncode
(caption, lyrics, seed, max_duration, cfg 1.7, top_k 50; an autoregressive
plan whose second output is the song's length) → EmptyMiniMaxMusic3LatentAudio
sized from that length → KSampler (30 steps, cfg 1.7, euler/simple) →
VAEDecodeAudioTiled → SaveAudio. Queueing, waiting and downloading reuse the
video generator's ComfyUI plumbing; the GPU is claimed through gpu_session
like every other ComfyUI render.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MODEL_ID = "minimax-music3-int8"
CFG_SCALE = 1.7
TOP_K = 50
MAX_SECONDS = 300.0


def build_music_workflow(*, caption: str, lyrics: str = "", seconds: float = 60.0, seed: Optional[int] = None,
                         steps: int = 30, cfg: float = CFG_SCALE, top_k: int = TOP_K,
                         filename_prefix: str = "audio/minimax_music3", model_id: str = MODEL_ID) -> dict:
    from backend.services.video_model_registry import music_comfyui_map
    files = music_comfyui_map().get(model_id) or {}
    if not all(files.get(k) for k in ("unet", "clip", "vae")):
        raise ValueError(f"{model_id} has no complete ComfyUI loader map")
    if seed is None:
        seed = int(time.time() * 1000) % (2**31)
    seconds = max(1.0, min(MAX_SECONDS, float(seconds or 60.0)))
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": files["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "4": {
            "class_type": "MiniMaxMusic3TextEncode",
            "inputs": {
                "clip": ["2", 0],
                "caption": caption or "",
                "lyrics": lyrics or "",
                "seed": int(seed),
                "max_duration": seconds,
                "cfg_scale": float(cfg),
                "top_k": int(top_k),
            },
        },
        "5": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {"seconds": ["4", 1], "batch_size": 1}},
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["4", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
                "denoise": 1.0,
            },
        },
        "7": {"class_type": "VAEDecodeAudioTiled", "inputs": {"samples": ["6", 0], "vae": ["3", 0], "tile_size": 1536, "overlap": 64}},
        "8": {"class_type": "SaveAudio", "inputs": {"audio": ["7", 0], "filename_prefix": filename_prefix}},
    }


class ComfyUIMusicGenerator:
    """Runs the Music 3 graph on the shared ComfyUI and returns the song's path."""

    def __init__(self, video_generator=None):
        if video_generator is None:
            from backend.services.comfyui_video_generator import get_video_generator
            video_generator = get_video_generator()
        self.vg = video_generator

    def generate(self, *, caption: str, lyrics: str = "", seconds: float = 60.0, seed: Optional[int] = None,
                 steps: Optional[int] = None, output_dir: Optional[Path] = None,
                 model_id: str = MODEL_ID) -> Dict[str, Any]:
        from backend.services.video_model_registry import (
            model_capabilities, preflight_video_model, vram_mb_for_model,
        )
        ready, err = preflight_video_model(model_id)
        if not ready:
            return {"success": False, "error": err}
        caps = model_capabilities(model_id)
        floor = int(caps.get("min_steps") or 30)
        steps = max(int(steps or caps.get("default_steps") or 30), floor)
        job_id = uuid.uuid4().hex[:8]
        workflow = build_music_workflow(
            caption=caption, lyrics=lyrics, seconds=seconds, seed=seed, steps=steps,
            filename_prefix=f"audio/minimax_music3_{job_id}", model_id=model_id,
        )
        out_dir = Path(output_dir) if output_dir else Path(getattr(self.vg, "cache_dir", ".")) / "music"
        out_dir.mkdir(parents=True, exist_ok=True)

        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        # The slot name starts with "video" so the policy frees ComfyUI on release.
        with gpu_session(JobKind.VIDEO_RENDER, f"video:music3:{job_id}", on_busy="raise",
                         evict_ollama=True, free_comfyui=True, cross_process=True,
                         vram_estimate_mb=vram_mb_for_model(model_id), require_fit=True):
            prompt_id = self.vg._queue_prompt(workflow)
            if not prompt_id:
                return {"success": False, "error": getattr(self.vg, "_last_queue_error", None) or "ComfyUI refused the song"}
            # The plan stage is an 8B language model writing the whole song
            # before diffusion starts; budget on the song's length, not steps.
            timeout = int(max(600, float(seconds) * 12))
            outputs = self.vg._wait_for_completion(prompt_id, timeout=timeout, hard_ceiling_s=max(timeout * 2, 3600))
        if not outputs:
            return {"success": False, "error": "ComfyUI song generation timed out or failed"}
        paths = []
        for node_output in outputs.values():
            for item in node_output.get("audio", []):
                if item.get("filename"):
                    paths.extend(self.vg._download_file(item["filename"], out_dir, file_type=item.get("type", "output"),
                                                        subfolder=item.get("subfolder", "")))
        if not paths:
            return {"success": False, "error": "No audio was produced by ComfyUI"}
        return {
            "success": True, "path": paths[0], "seconds": seconds, "steps": steps, "seed": seed,
            "model": model_id, "attribution": "MiniMax-Music3",
        }


# ─── background jobs for the HTTP route ─────────────────────────────────────
# The song plan is an 8B language model writing the whole track before the
# diffusion pass, so a request runs for minutes. The route starts a job here
# and the client polls it, the same contract the audio sidecar offers.
import threading

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def start_job(**params) -> str:
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        _JOBS[job_id] = {"id": job_id, "status": "queued", "params": {k: v for k, v in params.items() if k != "app"}}

    def _run():
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "running"
        try:
            app = params.pop("app", None)
            result = ComfyUIMusicGenerator().generate(**params)
            if result.get("success") and app is not None:
                result["document_id"] = _register(app, result, params)
            with _JOBS_LOCK:
                _JOBS[job_id].update(result)
                _JOBS[job_id]["status"] = "done" if result.get("success") else "failed"
        except Exception as e:  # noqa: BLE001 — the job carries the failure
            logger.exception("MiniMax Music 3 job failed")
            with _JOBS_LOCK:
                _JOBS[job_id].update({"status": "failed", "error": str(e)})

    threading.Thread(target=_run, name=f"music3-{job_id[:8]}", daemon=True).start()
    return job_id


def job_status(job_id: str) -> Optional[Dict[str, Any]]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _register(app, result: Dict[str, Any], params: Dict[str, Any]) -> Optional[int]:
    """Register the song as a Document under Audio with the same metadata
    shape ACE-Step writes, so the music video director reads one format."""
    try:
        with app.app_context():
            from backend.services.output_registration import register_file
            doc = register_file(
                physical_path=result["path"],
                folder_name="Audio",
                file_metadata={
                    "source": "music_generation", "model": result.get("model"),
                    "attribution": result.get("attribution"), "caption": params.get("caption"),
                    "style_prompt": params.get("caption"), "lyrics": params.get("lyrics") or "",
                    "instrumental_only": not bool((params.get("lyrics") or "").strip()),
                    "duration_s": result.get("seconds"), "seed": result.get("seed"), "steps": result.get("steps"),
                },
            )
            return getattr(doc, "id", None)
    except Exception as e:  # noqa: BLE001 — the file exists even if the row does not
        logger.warning("Music 3 song was not registered: %s", e)
        return None
