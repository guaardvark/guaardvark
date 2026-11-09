"""Style analysis: measure a video's style, read the result, keep it as a recipe.

POST /api/video-style/analyze     start an analysis (video_document_id or path)
GET  /api/video-style             recent analyses
GET  /api/video-style/recipes     style recipes on disk (hand-written and measured)
GET  /api/video-style/<job>       job record plus the profile when complete
GET  /api/video-style/<job>/shots per-shot measurements with keyframe URLs
POST /api/video-style/<job>/recipe  save the profile as data/agent/style_recipes/<slug>.json
POST /api/video-style/<job>/cancel
POST /api/video-style/compare     distance between two jobs' or recipes' profiles

A video is named the way every other media input is named (a library
document id, an /api/outputs URL, a guaardvark://outputs URI, or a path under
the uploads, outputs or install folder); the shared media-input rules refuse
anything else.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from backend.services.video_style import analyze, jobs, recipe as recipes

logger = logging.getLogger(__name__)

bp = Blueprint("video_style_api", __name__, url_prefix="/api/video-style")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts"}


def _resolve_video(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """(path, document_id, error) for the video a request names."""
    from backend.utils.media_inputs import accepted_forms, document_file, document_id_from_ref, resolve_media_ref

    ref = body.get("video_document_id") or body.get("video") or body.get("path") or ""
    ref = str(ref).strip()
    if not ref:
        return None, None, "video_document_id or path is required"
    doc_id = document_id_from_ref(ref)
    found = resolve_media_ref(
        ref, mcp=False, label="video", document_path=document_file, within_install=True,
        accepted=accepted_forms(mcp=False, documents=True, within_install=True),
    )
    if not found.path:
        return None, None, found.error or "video not found"
    from pathlib import Path

    if Path(found.path).suffix.lower() not in VIDEO_EXT:
        return None, None, f"not a video file: {Path(found.path).name}"
    return found.path, doc_id, None


def _settings(body: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key in ("scdet_threshold", "min_shot_s"):
        if body.get(key) is not None:
            try:
                out[key] = float(body[key])
            except (TypeError, ValueError):
                pass
    return out


def _job_view(job: Dict[str, Any], *, with_profile: bool = True) -> Dict[str, Any]:
    view = {k: v for k, v in job.items() if k not in ("dir",)}
    if with_profile and job.get("status") == "complete":
        analysis = analyze.load_analysis(job["dir"])
        if analysis:
            view["profile"] = analysis.get("profile")
            view["source"] = analysis.get("source")
            view["audio"] = {k: v for k, v in (analysis.get("audio") or {}).items()
                             if k not in ("beat_times", "cut_beat_offsets_ms")} or None
            view["shot_count"] = len(analysis.get("shots") or [])
            view["provenance"] = analysis.get("provenance")
            view["style_clause"] = recipes.style_clause(recipes.build_recipe(analysis, job["name"]))
    return view


@bp.post("/analyze")
def start_analysis():
    body = request.get_json(silent=True) or {}
    path, doc_id, err = _resolve_video(body)
    if err:
        return jsonify({"error": err}), 400
    name = (body.get("name") or "").strip() or None
    job = jobs.new_job(path, name=name, settings=_settings(body), document_id=doc_id)
    if body.get("sync"):
        # Inline for callers that wait (the CLI's --wait, tests); the worker path is the default.
        from backend.tasks.video_style_tasks import run_stage

        stage: Optional[str] = "probing"
        while stage:
            stage = run_stage(job["id"], stage)
        return jsonify(_job_view(jobs.load_job(job["id"]) or job)), 200
    try:
        from backend.tasks.video_style_tasks import dispatch

        dispatch(job["id"])
        dispatched, warning = True, None
    except Exception as e:  # noqa: BLE001 - the job stays queued; reported to the caller
        why = getattr(e, "why", None) or str(e) or type(e).__name__
        dispatched, warning = False, f"The analysis was not started: {why}."
        jobs.update(job["id"], message=warning)
    view = _job_view(jobs.load_job(job["id"]) or job)
    view["dispatched"] = dispatched
    if warning:
        view["warning"] = warning
    return jsonify(view), 202


@bp.get("")
def list_analyses():
    limit = min(200, max(1, request.args.get("limit", 50, type=int)))
    return jsonify({"jobs": [_job_view(j, with_profile=False) for j in jobs.list_jobs(limit=limit)]})


@bp.get("/recipes")
def list_style_recipes():
    return jsonify({"recipes": recipes.list_recipes()})


@bp.post("/compare")
def compare():
    body = request.get_json(silent=True) or {}
    a, err_a = _profile_from_ref(body.get("a"))
    b, err_b = _profile_from_ref(body.get("b"))
    if err_a or err_b:
        return jsonify({"error": err_a or err_b}), 400
    preset = body.get("preset") or "all"
    return jsonify(recipes.distance(a, b, preset=preset, weights=body.get("weights")))


def _profile_from_ref(ref: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """A profile from a job id or a measured recipe's name."""
    text = str(ref or "").strip()
    if not text:
        return None, "a and b are required (job ids or recipe names)"
    job = jobs.load_job(text)
    if job:
        analysis = analyze.load_analysis(job["dir"]) if job.get("status") == "complete" else None
        if not analysis or not analysis.get("profile"):
            return None, f"job {text} has no finished profile"
        return analysis["profile"], None
    rec = recipes.load_recipe(text)
    if rec and rec.get("measured"):
        return _recipe_profile(rec), None
    return None, f"{text}: no job or measured recipe by that name"


def _recipe_profile(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The profile blocks a measured recipe carries, in profile shape for distance()."""
    s = rec["measured"]
    pacing, mixes, look = s.get("pacing") or {}, s.get("mixes") or {}, s.get("look_targets") or {}
    cut = pacing.get("cut_on_beat") or {}
    return {
        "cutting": {
            "shot_len_hist_log2": pacing.get("shot_len_hist_log2"),
            "shot_len_1f_alpha": pacing.get("shot_len_1f_alpha"),
            "transition_mix": mixes.get("transition_mix"),
            "cut_rate_curve": pacing.get("cut_rate_curve"),
        },
        "scale_mix": mixes.get("scale_mix"),
        "scale_transition_matrix": mixes.get("scale_transition_matrix"),
        "motion_mix": mixes.get("motion_mix"),
        "look": {
            "key_mix": mixes.get("key_mix"),
            "global_palette_lab": look.get("palette_lab"),
            "tone_curve_p": look.get("tone_curve_p"),
        },
        "energy_curve": pacing.get("energy_curve"),
        "beat_sync": {"frac_cuts_on_beat": cut.get("prob")} if cut else None,
    }


@bp.get("/<job_id>")
def get_analysis(job_id: str):
    job = jobs.load_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(_job_view(job))


@bp.get("/<job_id>/shots")
def get_shots(job_id: str):
    job = jobs.load_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    analysis = analyze.load_analysis(job["dir"]) or {}
    shots = []
    for s in analysis.get("shots") or []:
        row = dict(s)
        row["keyframes"] = [{"t": k["t"], "url": jobs.public_frame_url(k["path"])} for k in s.get("keyframes") or []]
        shots.append(row)
    return jsonify({"job_id": job_id, "status": job.get("status"), "shots": shots,
                    "cuts": analysis.get("cuts"), "black_intervals": analysis.get("black_intervals")})


@bp.post("/<job_id>/recipe")
def save_style_recipe(job_id: str):
    job = jobs.load_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    if job.get("status") != "complete":
        return jsonify({"error": "analysis is not complete"}), 409
    analysis = analyze.load_analysis(job["dir"])
    if not analysis or not analysis.get("profile"):
        return jsonify({"error": "no profile on disk"}), 409
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip() or job["name"]
    rec = recipes.build_recipe(analysis, name, description=(body.get("description") or "").strip() or None)
    try:
        path = recipes.save_recipe(rec, overwrite=bool(body.get("overwrite")))
    except FileExistsError as e:
        return jsonify({"error": str(e)}), 409
    jobs.update(job_id, recipe_slug=path.stem)
    return jsonify({"saved": str(path), "slug": path.stem, "recipe": rec, "style_clause": recipes.style_clause(rec)}), 201


@bp.post("/<job_id>/cancel")
def cancel(job_id: str):
    if not jobs.request_cancel(job_id):
        return jsonify({"error": "not running"}), 409
    return jsonify({"cancelled": True, "job_id": job_id})
