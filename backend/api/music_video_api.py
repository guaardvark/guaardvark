"""Music-video pipeline REST API. Drive the MusicVideo state machine.

Front door for: create (→ analyze), inspect (stage + cut/clip counts + the GPU
cost estimate shown before approval), and the approval gate that releases the
expensive per-clip generation.
"""
import logging
import os
import time
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

from backend.models import db, MusicVideo, MusicVideoRevision, Project, Document
from backend.services.music_video_service import (
    MAX_REVISIONS_PER_VIDEO,
    MusicVideoService,
    clean_settings,
    clip_needs_render,
    held_cuts,
    restored_clips,
    snapshot_of,
)
from backend.services.pipeline_service import dispatch_report
from backend.services.gpu_resource_policy import gpu_session, free_comfyui_vram
from backend.services.job_types import JobKind
from backend.services.job_operation_gate import GpuBusyError
from backend.services.plugin_bridge import ensure_plugin_running
from backend.services.comfyui_video_generator import get_video_generator
from backend.services.music_video_director import _is_embedding_model

bp = Blueprint("music_video_api", __name__, url_prefix="/api/music-video")
log = logging.getLogger(__name__)

# A storyboard_generating flag older than this is treated as left behind by a dead worker.
STORYBOARD_FLAG_TTL_S = 1800

# Longest cut prompt accepted from an edit. The structured prompts the Director
# writes for native-audio models run to a few thousand characters.
MAX_PROMPT_CHARS = 8000

# Rough per-clip wall-clock for the approval-gate estimate: FLUX still (~20s) +
# WAN i2v (~45s) + gate cooldown (~8s) + ffmpeg fill (~2s). Display-only.
_SECONDS_PER_CLIP = 75


def _resolve_song(song_document_id) -> str | None:
    """Absolute on-disk path for a song Document id, or None if unresolvable.

    Uploaded Documents store a path RELATIVE TO UPLOAD_DIR (data/uploads), not
    cwd — same as backend/utils/uploaded_file_resolver. Try the upload-relative
    location first, then absolute, then cwd-relative as a fallback.
    """
    if not song_document_id:
        return None
    doc = db.session.get(Document, song_document_id)
    if not doc:
        return None
    path = getattr(doc, "file_path", None) or doc.path or doc.filename
    if not path:
        return None
    from backend.config import UPLOAD_DIR
    p = Path(path)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(Path(UPLOAD_DIR) / p)
        candidates.append(Path.cwd() / p)
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    return None


def _live_clip_job(mv: MusicVideo) -> dict | None:
    """The unified-progress record of the clip being rendered right now (sampler
    step/steps from the ComfyUI bridge), or None. Works across processes: the
    worker's job is read from its metadata record when not in this process."""
    detail = (mv.settings_json or {}).get("progress") or {}
    if mv.current_stage != "generating" or detail.get("stage") != "generating":
        return None
    idx = detail.get("clip_index")
    if idx is None:
        return None
    try:
        from backend.utils.unified_progress_system import get_unified_progress
        return get_unified_progress().get_job_status(f"mv_{mv.id}_{int(idx)}")
    except Exception:  # noqa: BLE001 — live detail is optional
        return None


def _progress_for(mv: MusicVideo) -> dict:
    from backend.services.music_video_progress import compute_progress
    try:
        return compute_progress(
            current_stage=mv.current_stage,
            status=mv.status,
            clips=mv.clips,
            settings=mv.settings_json,
            live=_live_clip_job(mv),
            seconds_per_clip=_SECONDS_PER_CLIP,
        )
    except Exception as e:  # noqa: BLE001 — never break the detail view over the meter
        log.warning("music_video %s: progress unavailable: %s", mv.id, e)
        return {"overall_pct": None, "stages": [], "error": str(e)}


def _mv_dict(mv: MusicVideo) -> dict:
    cut_count = len(mv.cut_plan or [])
    clips = mv.clips or []
    clips_done = sum(1 for c in clips if c.get("status") == "done")
    out = {
        "id": mv.id,
        "name": mv.name,
        "status": mv.status,
        "current_stage": mv.current_stage,
        "project_id": mv.project_id,
        "project_name": mv.project.name if mv.project_id and mv.project else None,
        "song_document_id": mv.song_document_id,
        "style_prompt": mv.style_prompt,
        "cut_count": cut_count,
        "clip_count": len(clips),
        "clips_done": clips_done,
        # Rendered cuts a quality check held; the video waits for a person.
        "clips_needing_review": len(held_cuts(clips)),
        "output_document_id": mv.output_document_id,
        "error_blob": mv.error_blob,
        "created_at": mv.created_at.isoformat() if mv.created_at else None,
        # Full plan data for the UI (cut list + per-cut prompts from the Director).
        # Only present after the analyzer has run. Safe to send for both list and detail;
        # the sidebar list ignores these fields.
        "cut_plan": mv.cut_plan or [],
        "clips": clips,
    }
    # Cuts approval would render: not rendered yet, file gone, or inputs changed
    # since (revise / restore keep the rest). Equals cut_count on a fresh plan.
    raw_settings = mv.settings_json or {}
    to_render = sum(
        1 for c in clips if clip_needs_render(c, raw_settings, mv.style_prompt)
    ) if clips else cut_count
    out["cuts_to_render"] = to_render
    # Surface the cost estimate once the plan exists (i.e. at the approval gate).
    if cut_count:
        est = to_render * _SECONDS_PER_CLIP
        out["estimate"] = {
            "clips_to_generate": to_render,
            "seconds_per_clip": _SECONDS_PER_CLIP,
            "estimated_seconds": est,
            "estimated_human": f"~{est // 3600}h {(est % 3600) // 60}m" if est >= 3600 else f"~{est // 60}m",
        }
    # Also surface pipeline / model settings so the UI (Plan viewer) can show exactly what will be / was used.
    s = mv.settings_json or {}
    out["director_enabled"] = s.get("director_enabled", True)
    if s.get("planning_mode"):
        out["planning_mode"] = s.get("planning_mode")
    # Dedicated (usually small/fast) model used for the Director when generating per-cut prompts + treatment.
    # Defaults to a lightweight gemma suitable for structured JSON + visual storytelling (not necessarily the user's main chat/brain model).
    dm = s.get("director_model") or "gemma4:e4b"
    if _is_embedding_model(dm):  # avoid showing bad persisted values in UI
        dm = "gemma4:e4b"
    out["director_model"] = dm
    out["use_lora_consistency"] = s.get("use_lora_consistency", False)
    out["keyframe_model"] = s.get("keyframe_model", "flux-schnell")
    from backend.services.video_model_registry import DEFAULT_I2V_MODEL
    out["i2v_model"] = s.get("i2v_model") or (
        "cogvideox-5b-i2v" if s.get("i2v_engine") == "cogvideox" else DEFAULT_I2V_MODEL
    )
    try:
        from backend.services.video_model_registry import model_capabilities
        out["i2v_native_audio"] = bool((model_capabilities(out["i2v_model"]) or {}).get("audio_out"))
    except Exception:  # noqa: BLE001 — informational only
        out["i2v_native_audio"] = False
    # The rich visual treatment / short story the Director (acting as screenwriter) invented.
    # This is the creative foundation — per-cut prompts should advance this treatment.
    # This is the main defense against "every scene uses the identical repeated global prompt".
    treatment = s.get("director_treatment") or s.get("director_storyline")
    if treatment:
        out["director_treatment"] = treatment
    # The story the person wrote, shown as written next to the Director's version.
    if s.get("user_treatment"):
        out["user_treatment"] = s.get("user_treatment")
    # P0 diagnostics (story-arc plan): surface why we may have fallen back to global/identical
    # prompts (LLM unavailable, parse failure, recovery used, low distinctness after guard, etc.).
    # The UI (PlanViewer) can show a warning badge so the operator knows the arc is not fully active.
    if isinstance(s, dict) and s.get("director_diagnostics"):
        out["director_diagnostics"] = s.get("director_diagnostics")
    if s.get("song_analysis"):
        out["song_analysis"] = s.get("song_analysis")
    out["notes"] = s.get("notes") or ""
    out["tags"] = s.get("tags") or []
    out["updated_at"] = mv.updated_at.isoformat() if mv.updated_at else None
    out["progress"] = _progress_for(mv)
    out["storyboard_generating"] = bool(s.get("storyboard_generating"))
    if s.get("storyboard_error"):
        out["storyboard_error"] = s.get("storyboard_error")
    return out


@bp.post("")
def create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    song_document_id = body.get("song_document_id")
    style_prompt = (body.get("style_prompt") or "").strip()
    project_id = body.get("project_id")
    settings = body.get("settings") or {}
    # User-provided treatment / short story takes precedence as the creative source
    if body.get("user_treatment"):
        settings["user_treatment"] = body.get("user_treatment").strip()
        settings["director_treatment"] = body.get("user_treatment").strip()  # seed the visible treatment

    if not name or not style_prompt or not song_document_id:
        return jsonify({"error": "name, song_document_id and style_prompt are required"}), 400

    from backend.services.video_model_registry import resolve_active_video_model
    explicit_i2v = (settings.get("i2v_model") or "").strip() or None
    # Analysis needs no GPU; the storyboard and render stages start ComfyUI themselves.
    picked, resolve_err = resolve_active_video_model(
        "i2v", explicit_i2v, surface="music-video", comfyui_down_ok=True,
    )
    if resolve_err:
        return jsonify({"error": resolve_err}), 400
    settings["i2v_model"] = picked

    if project_id in ("", None):
        project_id = None
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return jsonify({"error": "project_id must be an integer"}), 400
        if db.session.get(Project, project_id) is None:
            return jsonify({"error": f"project_id {project_id} not found"}), 400

    song_path = _resolve_song(song_document_id)
    if not song_path:
        return jsonify({"error": f"song_document_id {song_document_id} not found on disk"}), 400

    svc = MusicVideoService(db.session)
    mv = svc.create(
        name=name, song_document_id=song_document_id, song_path=song_path,
        style_prompt=style_prompt, project_id=project_id, settings=settings,
    )

    # Kick the pipeline: draft → analyzing, then dispatch the analyzer. A dispatch
    # failure is reported, not fatal: the row exists and boot resume_all picks it up.
    dispatch = {}
    if svc.advance_if_predecessor(mv.id, expected_predecessor="draft"):
        dispatch = dispatch_report(svc.try_dispatch(mv.id, "analyzer"))
        db.session.refresh(mv)

    return jsonify({**_mv_dict(mv), **dispatch}), 201


@bp.get("")
def list_music_videos():
    q = MusicVideo.query
    pid = request.args.get("project_id")
    if pid is not None:
        if pid in ("", "none", "null"):
            q = q.filter(MusicVideo.project_id.is_(None))
        else:
            try:
                q = q.filter(MusicVideo.project_id == int(pid))
            except ValueError:
                return jsonify({"error": "project_id must be an integer or 'none'"}), 400
    rows = q.order_by(MusicVideo.created_at.desc()).all()
    return jsonify({"music_videos": [_mv_dict(mv) for mv in rows]})


@bp.get("/<int:mv_id>")
def get_music_video(mv_id):
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_mv_dict(mv))


@bp.delete("/<int:mv_id>")
def delete_music_video(mv_id):
    """Clear one music video from the log.

    Removes the MusicVideo row only — the rendered output Document (if any) and
    any uploaded song are left intact; this just clears the generation entry the
    operator sees in the list.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(mv)
    db.session.commit()
    return jsonify({"deleted": mv_id})


@bp.delete("")
def clear_music_videos():
    """Bulk-clear music videos from the log.

    Default: only terminal entries (``complete`` or ``failed*``) — the safe
    "clear finished" sweep. Pass ``?all=true`` to clear every entry regardless
    of stage (still leaves output Documents on disk).
    """
    clear_all = (request.args.get("all") or "").lower() in ("1", "true", "yes")
    rows = MusicVideo.query.all()

    def _is_terminal(mv: MusicVideo) -> bool:
        status = (mv.status or "")
        return (
            mv.current_stage == "complete"
            or status.startswith("failed")
            or status.startswith("cancelled")
            or mv.current_stage == "cancelled"
        )

    targets = rows if clear_all else [mv for mv in rows if _is_terminal(mv)]
    deleted = [mv.id for mv in targets]
    for mv in targets:
        db.session.delete(mv)
    db.session.commit()
    return jsonify({"deleted": deleted, "count": len(deleted)})


@bp.post("/<int:mv_id>/approve")
def approve(mv_id):
    """Cost gate: release per-clip generation only on explicit operator approval."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"music_video is at stage '{mv.current_stage}', not awaiting_approval"
        }), 409
    if not (mv.cut_plan and len(mv.cut_plan) > 0):
        return jsonify({
            "error": "Song analysis is not complete yet — use Continue & Analyze first."
        }), 409

    # LoRA/Comfy preflight+clamp resumed (P1-5/P3-12 media items from team audit; was WIP
    # after phase-map/flux wiring). Now consistent with clip path + generator helper.
    try:
        vg = get_video_generator()
        if not getattr(vg, "service_available", True):
            vg.service_available = vg._check_comfyui_connection() if hasattr(vg, "_check_comfyui_connection") else False
        if not getattr(vg, "service_available", True):
            return jsonify({"error": "ComfyUI not reachable; cannot release music-video generation. Start ComfyUI."}), 503
    except Exception as _pf:
        log.warning(f"Music-video preflight soft-failed (proceeding): {_pf}")

    svc = MusicVideoService(db.session)
    dispatch = {}
    snapshot = MusicVideoRevision(music_video_id=mv.id, reason="approve", label="Approved plan",
                                  snapshot=snapshot_of(mv))
    if svc.advance_if_predecessor(mv_id, expected_predecessor="awaiting_approval"):
        # Saved only by the request that moved the video on, so a double click
        # keeps one version.
        _store_revision(mv, snapshot)
        _queue_changed_cuts(mv)
        dispatch = dispatch_report(svc.try_dispatch(mv_id, "clip_generator"))
        db.session.refresh(mv)

    return jsonify({**_mv_dict(mv), **dispatch})


# --- Cuts a quality check held -----------------------------------------------
# A flagged cut waits for a person (video_consistency_metrics.QUALITY_FLAG_OUTCOME):
# keep it, or render it again. Nothing re-renders on its own.

def _held_cut(mv_id: int, idx: int):
    """(music video, a copy of its clips, the held cut, None), or Nones and an
    error response when there is no such held cut."""
    import copy
    from backend.services.video_consistency_metrics import NEEDS_REVIEW
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return None, None, None, (jsonify({"error": "not_found"}), 404)
    clips = copy.deepcopy(mv.clips or [])
    cut = next((c for c in clips if c.get("index") == idx), None)
    if cut is None:
        return None, None, None, (jsonify({"error": f"cut {idx} not found"}), 404)
    if (cut.get("review") or {}).get("state") != NEEDS_REVIEW:
        return None, None, None, (jsonify({"error": f"cut {idx} is not held for review"}), 409)
    return mv, clips, cut, None


def _generator_idle(mv: MusicVideo) -> bool:
    """No cut is waiting to render, so no clip-generator chain is running: the
    generator stops when nothing is pending (held cuts wait for a person). A
    chain that died with work pending is picked up by the boot-time resume."""
    s = mv.settings_json or {}
    return not any(clip_needs_render(c, s, mv.style_prompt) for c in (mv.clips or []))


def _continue_generation(mv: MusicVideo, was_idle: bool) -> dict:
    """Let the clip generator pick up again: it renders what is pending and
    assembles once no cut is pending or held. Only when it had stopped
    (``was_idle``, read before the change): a running chain reaches the change
    itself, and a second chain would render beside it."""
    if mv.current_stage != "generating" or not was_idle:
        return {}
    return dispatch_report(MusicVideoService(db.session).try_dispatch(mv.id, "clip_generator"))


def _queue_changed_cuts(mv: MusicVideo) -> None:
    """Send done cuts whose inputs changed back to pending, so what the meter and
    the list show as rendered is what the video will use."""
    import copy
    s = mv.settings_json or {}
    clips = copy.deepcopy(mv.clips or [])
    changed = False
    for c in clips:
        if c.get("status") == "done" and clip_needs_render(c, s, mv.style_prompt):
            c.update({"status": "pending", "quality": None, "review": None})
            changed = True
    if changed:
        mv.clips = clips
        db.session.commit()


@bp.post("/<int:mv_id>/cut/<int:idx>/approve")
def approve_held_cut(mv_id, idx):
    """Keep a held cut as rendered; the video is assembled once none is held."""
    from datetime import datetime
    mv, clips, cut, err = _held_cut(mv_id, idx)
    if err:
        return err
    was_idle = _generator_idle(mv)
    cut["review"] = {**cut["review"], "state": "approved", "approved_at": datetime.now().isoformat()}
    mv.clips = clips
    db.session.commit()
    dispatch = _continue_generation(mv, was_idle)
    db.session.refresh(mv)
    return jsonify({**_mv_dict(mv), **dispatch})


@bp.post("/<int:mv_id>/cut/<int:idx>/rerender")
def rerender_held_cut(mv_id, idx):
    """Render a held cut again; the new render is checked like the first."""
    mv, clips, cut, err = _held_cut(mv_id, idx)
    if err:
        return err
    if mv.current_stage != "generating":
        return jsonify({"error": f"music_video is at stage '{mv.current_stage}', not generating"}), 409
    was_idle = _generator_idle(mv)
    cut.update({
        "status": "pending", "clip_path": None, "quality": None, "review": None,
        "rerenders": int(cut.get("rerenders") or 0) + 1,
    })
    mv.clips = clips
    db.session.commit()
    dispatch = _continue_generation(mv, was_idle)
    db.session.refresh(mv)
    return jsonify({**_mv_dict(mv), **dispatch})


@bp.post("/<int:mv_id>/reassemble")
def reassemble(mv_id):
    """Assemble the final video again from the clips already rendered.

    For a video whose assembly failed, or that finished without a playable
    output; no clip is rendered again."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if not (mv.current_stage == "complete" or (mv.status or "") == "failed_assembling"):
        return jsonify({"error": f"music_video is at stage '{mv.current_stage}', not complete or failed in assembly"}), 409
    clips = [c for c in (mv.clips or []) if c.get("status") != "cancelled"]
    missing = [c.get("index") for c in clips
               if not (c.get("status") == "done" and c.get("clip_path") and os.path.exists(c["clip_path"]))]
    if missing or not clips:
        return jsonify({"error": f"cuts {missing} have no rendered clip on disk; re-plan to render them"}), 409
    if held_cuts(clips):
        return jsonify({"error": "a cut is held for review"}), 409
    _save_revision(mv, reason="reassemble", label="Before re-assembly")
    mv.current_stage = "assembling"
    mv.status = "assembling"
    mv.output_document_id = None
    mv.error_blob = None
    db.session.commit()
    MusicVideoService(db.session).stamp_stage(mv_id, "assembling")
    warning = MusicVideoService(db.session).try_dispatch(mv_id, "assembler")
    db.session.refresh(mv)
    return jsonify({**_mv_dict(mv), **dispatch_report(warning)})


# --- Pre-approval plan inspection & editing -------------------------------

@bp.post("/<int:mv_id>/plan")
def update_plan(mv_id):
    """Accept operator edits to the per-cut prompts (and optionally global style).

    Body: { "prompts": { "<index>": "new visual prompt text", ... }, "style_prompt"?: "..." }
    Only allowed while the video is parked at awaiting_approval (before any GPU work).
    The edited prompts will be used when generation starts.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"plan edits only allowed at 'awaiting_approval', currently '{mv.current_stage}'"
        }), 409

    body = request.get_json(silent=True) or {}
    svc = MusicVideoService(db.session)

    # Optional global style tweak
    new_style = body.get("style_prompt")
    if isinstance(new_style, str) and new_style.strip():
        mv.style_prompt = new_style.strip()

    # Per-clip prompt patches: support both {prompts: {0: ".." }} and {clips: [{index, prompt}, ...]}
    prompt_updates: dict[int, str] = {}
    if isinstance(body.get("prompts"), dict):
        for k, v in body["prompts"].items():
            if len(str(v or "")) > MAX_PROMPT_CHARS:
                return jsonify({"error": f"cut {k}: a prompt can be at most {MAX_PROMPT_CHARS} characters"}), 400
            try:
                prompt_updates[int(k)] = str(v or "")
            except (ValueError, TypeError):
                pass
    if isinstance(body.get("clips"), list):
        for item in body["clips"]:
            if isinstance(item, dict):
                try:
                    idx = int(item.get("index"))
                    prompt_updates[idx] = str(item.get("prompt") or "")
                except (ValueError, TypeError):
                    pass

    updated = svc.update_clip_prompts(mv_id, prompt_updates)

    # Support editing the treatment / story directly (user pasted or refined the creative treatment)
    if "treatment" in body and isinstance(body["treatment"], str):
        s = dict(mv.settings_json or {})
        s["user_treatment"] = body["treatment"].strip()
        # Also update the active director_treatment so the plan shows the user version
        s["director_treatment"] = body["treatment"].strip()
        mv.settings_json = s

    if updated is None:
        # Shouldn't happen because we checked stage above, but be defensive.
        updated = mv
    # The style and treatment edits above are only on the row so far; a refresh
    # without this commit reloads the old values and drops them.
    db.session.commit()
    db.session.refresh(updated)
    return jsonify(_mv_dict(updated))


def _restart_analysis(mv_id: int) -> tuple[MusicVideo | None, str | None]:
    """Move a music video back to analyzing and dispatch the analyzer agent.
    Returns the row and, when the analyzer was not queued, why."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return None, None
    mv.current_stage = "analyzing"
    mv.status = "analyzing"
    mv.error_blob = None
    if not (mv.cut_plan and len(mv.cut_plan) > 0):
        mv.cut_plan = None
        mv.clips = []
    db.session.commit()
    MusicVideoService(db.session).stamp_stage(mv_id, "analyzing")
    warning = MusicVideoService(db.session).try_dispatch(mv_id, "analyzer")
    db.session.refresh(mv)
    return mv, warning


@bp.post("/<int:mv_id>/analyze")
def start_analysis(mv_id):
    """(Re)start song analysis on an existing music video job.

    Use when a job was created but analysis never finished (e.g. plugin was
    disabled), failed during analyzing, or is stuck at awaiting_approval with
    no cut plan. Preserves name, song, style, and settings_json.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404

    stage = mv.current_stage or ""
    status = mv.status or ""
    has_plan = bool(mv.cut_plan and len(mv.cut_plan) > 0)

    if stage in ("generating", "assembling"):
        return jsonify({"error": f"Cannot analyze while at stage '{stage}'"}), 409

    if stage == "analyzing" and status == "analyzing" and not has_plan:
        warning = MusicVideoService(db.session).try_dispatch(mv_id, "analyzer")
        db.session.refresh(mv)
        return jsonify({**_mv_dict(mv), **dispatch_report(warning)})

    needs_analysis = (
        not has_plan
        or stage == "draft"
        or status.startswith("failed")
        or status == "cancelled"
        or stage == "cancelled"
    )
    if not needs_analysis:
        return jsonify({
            "error": "Analysis already complete — approve the plan or use re-plan to re-render."
        }), 409

    mv, warning = _restart_analysis(mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({**_mv_dict(mv), **dispatch_report(warning)})


@bp.post("/<int:mv_id>/replan")
def replan(mv_id):
    """Reset a terminal music video (complete or failed) back to awaiting_approval
    with its existing plan, treatment, and settings intact. This lets the user
    re-edit the treatment/prompts or just re-render (e.g. different seeds or model choices)
    without losing the creative work.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404

    has_plan = bool(mv.cut_plan and len(mv.cut_plan) > 0)
    if not has_plan:
        mv, warning = _restart_analysis(mv_id)
        if mv is None:
            return jsonify({"error": "not_found"}), 404
        return jsonify({**_mv_dict(mv), **dispatch_report(warning)})

    if not (mv.current_stage in ("complete",) or (mv.status or "").startswith("failed") or (mv.status or "") == "cancelled" or mv.current_stage == "cancelled"):
        return jsonify({
            "error": f"replan only allowed on complete, failed, or cancelled videos, current stage is '{mv.current_stage}'"
        }), 409

    # Reset generation state but keep the plan + treatment + settings
    mv.current_stage = "awaiting_approval"
    mv.status = "awaiting_approval"
    mv.output_document_id = None
    mv.error_blob = None

    # deepcopy: clips is a plain db.JSON column (not MutableList); mutating the
    # same list object in place is not seen as dirty and won't persist on commit.
    import copy
    clips = copy.deepcopy(mv.clips or [])
    for c in clips:
        c["clip_path"] = None
        c["status"] = "pending"
        # Keep storyboard_path if present (for the storyboard-first review flow)
    mv.clips = clips

    db.session.commit()
    MusicVideoService(db.session).stamp_stage(mv_id, "awaiting_approval")
    db.session.refresh(mv)
    return jsonify(_mv_dict(mv))


@bp.post("/<int:mv_id>/cancel")
def cancel_music_video(mv_id):
    """Cancel a music video that is generating, assembling, or still awaiting approval.
    Marks it cancelled so the clip generator loop will stop re-queuing further clips.
    Current clip may finish (i2v is hard to interrupt mid-frame), but no more work after.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404

    cancellable = ("generating", "assembling", "awaiting_approval", "analyzing")
    if mv.current_stage not in cancellable:
        return jsonify({"error": f"Cannot cancel at stage '{mv.current_stage}'"}), 409

    was_generating = mv.current_stage == "generating"

    mv.status = "cancelled"
    # Also advance the stage to a terminal "cancelled" value so UI (which keys
    # heavily on current_stage for progress/labels/active filters) and resume
    # logic immediately see it as done. The per-clip guards already short-circuit
    # on status.startswith("cancelled"), but making the stage explicit prevents
    # "still shows as generating" after cancel and stops resume dispatches.
    mv.current_stage = "cancelled"
    # deepcopy so the reassignment is a new object SQLAlchemy detects as dirty —
    # clips is a plain db.JSON column (not MutableList), so an in-place mutation
    # of the same list is NOT persisted and would silently revert on commit.
    import copy
    clips = copy.deepcopy(mv.clips or [])
    for c in clips:
        if c.get("status") == "pending":
            c["status"] = "cancelled"
    mv.clips = clips
    db.session.commit()

    if was_generating:
        try:
            from backend.services.video_generation_router import get_video_router
            interrupted = get_video_router().interrupt()
            log.info(
                "Music video %s cancel: ComfyUI interrupt sent (ack=%s)",
                mv_id,
                interrupted,
            )
        except Exception:
            log.warning(
                "Music video %s cancel: ComfyUI interrupt failed",
                mv_id,
                exc_info=True,
            )

    log.info(f"Music video {mv_id} cancelled by user at stage {mv.current_stage}")
    return jsonify(_mv_dict(mv))


@bp.get("/<int:mv_id>/storyboard/<int:idx>")
def get_mv_storyboard(mv_id, idx):
    """Serve a generated storyboard still for a cut (used in the plan review UI for the storyboard-first flow)."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or not mv.clips:
        return jsonify({"error": "not found"}), 404
    for c in (mv.clips or []):
        if int(c.get("index", -1)) == int(idx):
            sp = c.get("storyboard_path")
            if sp and os.path.exists(sp):
                return send_file(sp, mimetype="image/png")
    return jsonify({"error": "storyboard not found for this cut"}), 404


@bp.get("/<int:mv_id>/clip/<int:idx>")
def get_mv_clip(mv_id, idx):
    """Serve a cut's rendered clip, so a cut held for review can be watched before it is
    approved or rendered again."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or not mv.clips:
        return jsonify({"error": "not found"}), 404
    for c in (mv.clips or []):
        if int(c.get("index", -1)) == int(idx):
            cp = c.get("clip_path")
            if cp and os.path.exists(cp):
                return send_file(cp, mimetype="video/mp4", conditional=True)
    return jsonify({"error": "no rendered clip for this cut"}), 404


@bp.post("/<int:mv_id>/generate-storyboards")
def generate_storyboards(mv_id):
    """Dispatch async storyboard keyframe generation for all cuts (thumbnails-first review)."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({"error": f"storyboard generation only from awaiting_approval (plan approved), current={mv.current_stage}"}), 409

    body = request.get_json(silent=True) or {}
    force = (
        request.args.get("force", "").lower() in ("1", "true", "yes")
        or bool(body.get("force"))
    )

    settings = dict(mv.settings_json or {})
    if settings.get("storyboard_generating"):
        # A worker that died mid-storyboard never clears the flag; treat a
        # flag older than the TTL as stale rather than locking the video forever.
        started = float(settings.get("storyboard_started_at") or 0)
        if started and (time.time() - started) < STORYBOARD_FLAG_TTL_S:
            return jsonify({"error": "storyboard generation already in progress"}), 409
        log.warning("music_video %s: stale storyboard_generating flag ignored", mv_id)

    settings["storyboard_generating"] = True
    settings["storyboard_started_at"] = time.time()
    settings.pop("storyboard_error", None)
    mv.settings_json = settings
    db.session.commit()

    from backend.celery_app import celery
    from backend.celery_dispatch import TaskNotStarted
    try:
        task = celery.send_task("music_video.run_storyboard_generator", args=[mv_id, force])
    except TaskNotStarted as e:
        # Nothing will clear the flag, which would lock the button for the TTL.
        settings = dict(mv.settings_json or {})
        settings["storyboard_generating"] = False
        settings["storyboard_error"] = str(e)
        mv.settings_json = settings
        db.session.commit()
        raise
    return jsonify({
        "task_id": task.id,
        "mv_id": mv_id,
        "status": "dispatched",
        **_mv_dict(mv),
    }), 202


@bp.post("/<int:mv_id>/regen-storyboard/<int:idx>")
def regen_mv_storyboard(mv_id, idx):
    """Re-generate a single storyboard still (for the review step before full video render).
    Optional body: { "prompt": "override prompt for this cut" }
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None or mv.current_stage != "awaiting_approval":
        return jsonify({"error": "only allowed during plan/storyboard review"}), 409

    from backend.services.plugin_bridge import ensure_plugins_for_stage
    ensure_plugins_for_stage("music-video", "storyboard")

    # Signal the GPU/memory orchestrator (see comment in generate_storyboards).
    try:
        from backend.services.plugin_bridge import prepare_plugins_for_route
        prepare_plugins_for_route("/music-video/storyboard")
    except Exception:
        log.warning("Failed to prepare plugins for music-video storyboard regen (non-fatal)", exc_info=True)  # noqa: BLE001

    body = request.get_json(silent=True) or {}
    prompt_override = body.get("prompt")

    s = _settings_for_mv(mv)
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.tasks.music_video_tasks import (
        _clip_dir, _keyframe_cast_context, _keyframe_lora_strength, _zimage_keyframes,
    )
    import copy

    clips = copy.deepcopy(mv.clips or [])
    target = None
    for c in clips:
        if int(c.get("index", -1)) == int(idx):
            target = c
            break
    if not target:
        return jsonify({"error": "cut not found"}), 404

    prompt = prompt_override or target.get("prompt") or mv.style_prompt
    try:
        out_dir = _clip_dir(mv.id)
    except Exception:
        from pathlib import Path
        out_dir = Path("data/outputs/videos") / f"music_video_{mv_id}" / "clips"  # fallback; non-fatal per bare-excepts audit (infra/security)
        out_dir.mkdir(parents=True, exist_ok=True)

    # Support optional "variation" for different outputs with the same prompt.
    # If provided, we add it to the base seed so the user (or UI) can request
    # variations without editing the prompt text itself.
    variation = body.get("variation", 0) or 0
    try:
        variation = int(variation)
    except (TypeError, ValueError):
        variation = 0

    # Cast: raw scene prompt + subject_ids; identity core applied in render_character_still.
    kf_lora_strength = _keyframe_lora_strength(s)
    kf_loras, kf_sids, kf_prompt = _keyframe_cast_context(mv, s, prompt, cut=target)

    still_path = str(out_dir / f"storyboard_{idx}.png")
    try:
        with gpu_session(JobKind.VIDEO_RENDER, f"mv_storyboard_{mv_id}_{idx}", evict_ollama=True,
                         free_comfyui=True, vram_estimate_mb=11000, require_fit=True):
            if kf_loras or _zimage_keyframes(s):
                from backend.services.character_still_pipeline import render_character_still
                still = render_character_still(
                    kf_prompt,
                    subject_ids=kf_sids or None,
                    lora_paths=kf_loras or None,
                    include_bible=True,
                    source="musicvideo",
                    width=s.get("still_width", 1024),
                    height=s.get("still_height", 576),
                    seed=3000 + int(idx) + variation,
                    output_path=still_path,
                    lora_strength=kf_lora_strength,
                    keep_pipeline=False,
                )
                if not still.success:
                    raise RuntimeError(still.error or "storyboard regen failed")
            else:
                ComfyUIImageGenerator(
                    lora_strength=kf_lora_strength,
                    flux_unet=s.get("flux_unet"),
                    flux_t5=s.get("flux_t5"),
                    flux_clip=s.get("flux_clip"),
                    flux_vae=s.get("flux_vae"),
                ).generate_image(
                    prompt=kf_prompt,
                    loras=kf_loras,
                    output_path=still_path,
                    width=s.get("still_width", 1024),
                    height=s.get("still_height", 576),
                    seed=3000 + int(idx) + variation,
                    steps=s.get("keyframe_steps") or 20,
                    model=s.get("keyframe_model"),
                )
            # Free ComfyUI VRAM after the still so any follow-up video work
            # (or other users) gets a clean card.
            free_comfyui_vram()
            target["storyboard_path"] = still_path
            # Optionally record the variation used for this cut (for transparency / future re-renders)
            target["storyboard_variation"] = variation
    except GpuBusyError as e:
        return jsonify({"error": f"GPU busy, cannot regenerate storyboard right now: {e}"}), 503
    except RuntimeError as e:
        # ComfyUI accepted the job but produced no usable image (workflow error,
        # missing model, node failure, etc.). Return a clean error with the
        # prompt_id so the operator can inspect it directly in the ComfyUI UI
        # (http://127.0.0.1:8188 or the comfyui plugin port) under the prompt history.
        log.error("regen-storyboard ComfyUI failure for mv %s cut %s: %s", mv_id, idx, e)
        # Try to surface any ComfyUI-side error details if the prompt_id is in the message.
        return jsonify({
            "error": "ComfyUI failed to produce a storyboard image for this cut",
            "details": str(e),
            "cut": idx,
            "suggestion": "Open the ComfyUI interface, look up the prompt_id in the queue/history, and check the node that errored. Common causes: workflow expects different models, custom nodes not loaded, or VRAM pressure after plugin toggle."
        }), 422
    except Exception as e:
        log.exception("Unexpected error in regen-storyboard for mv %s cut %s", mv_id, idx)
        return jsonify({"error": f"Storyboard regen failed: {e}"}), 500

    # Only this cut's storyboard goes onto the clips as they are now: the render
    # took a while, and edits made meanwhile (autosave, another tab) are kept.
    db.session.refresh(mv)
    fresh = copy.deepcopy(mv.clips or [])
    for c in fresh:
        if c.get("index") == idx:
            c["storyboard_path"] = target.get("storyboard_path")
            c["storyboard_variation"] = target.get("storyboard_variation")
    mv.clips = fresh
    db.session.commit()
    return jsonify(_mv_dict(mv))


def _settings_for_mv(mv):
    """Lightweight settings read for storyboard gen (mirrors the one in tasks)."""
    s = dict(mv.settings_json or {})
    s.setdefault("still_width", 1024)
    s.setdefault("still_height", 576)
    s.setdefault("keyframe_steps", 20)
    s.setdefault("keyframe_model", "flux-schnell")
    # Flux asset overrides (for storyboard/keyframe flux workflows). None = use
    # GUAARDVARK_FLUX_* env defaults (which match the documented infographic assets).
    for k in ("flux_unet", "flux_t5", "flux_clip", "flux_vae"):
        s.setdefault(k, None)
    return s


@bp.post("/<int:mv_id>/regenerate-plan")
def regenerate_plan(mv_id):
    """Re-run the Director (music_video_director) over the existing cut plan.

    Optional body:
      { "feedback": "free text guidance, e.g. 'abstract mood arc, more light and texture, slow and dreamy intro'",
        "planning_mode": "narrative" | "visual" | "mood_arc" }

    The chosen mode/guidance are persisted in settings_json and the new prompts replace
    the previous ones in the clips list. Only usable before generation.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"regenerate-plan only allowed at 'awaiting_approval', currently '{mv.current_stage}'"
        }), 409

    body = request.get_json(silent=True) or {}
    feedback = body.get("feedback")
    if isinstance(feedback, str):
        feedback = feedback.strip() or None
    mode = body.get("planning_mode")
    if not isinstance(mode, str):
        mode = None
    director_model = body.get("director_model")
    if not isinstance(director_model, str) or not director_model.strip():
        director_model = None

    svc = MusicVideoService(db.session)
    updated = svc.regenerate_director_prompts(mv_id, feedback=feedback, planning_mode=mode, director_model=director_model)
    if updated is None:
        updated = mv
    db.session.refresh(updated)
    return jsonify(_mv_dict(updated))


# --- Progress -----------------------------------------------------------------

@bp.get("/<int:mv_id>/progress")
def get_progress(mv_id):
    """The progress meter alone (stages, clip in flight, ETA): a cheap poll."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"id": mv.id, "current_stage": mv.current_stage, "status": mv.status,
                    "progress": _progress_for(mv)})


# --- Saving and editing a music video as a project ------------------------------

_BUSY_STAGES = ("analyzing", "generating", "assembling")


def _storyboards_rendering(mv: MusicVideo) -> bool:
    s = mv.settings_json or {}
    if not s.get("storyboard_generating"):
        return False
    started = float(s.get("storyboard_started_at") or 0)
    return bool(started) and (time.time() - started) < STORYBOARD_FLAG_TTL_S


def _is_busy(mv: MusicVideo) -> bool:
    status = mv.status or ""
    return (mv.current_stage in _BUSY_STAGES
            and not status.startswith("failed") and not status.startswith("cancelled"))


def _save_revision(mv: MusicVideo, *, reason: str, label: str | None = None) -> MusicVideoRevision | None:
    """Snapshot the video's creative state. Keeps the newest MAX_REVISIONS_PER_VIDEO.
    Best-effort for automatic snapshots: a failure is logged, never blocks the action."""
    return _store_revision(mv, MusicVideoRevision(
        music_video_id=mv.id, reason=reason, label=(label or "")[:255] or None,
        snapshot=snapshot_of(mv),
    ))


def _store_revision(mv: MusicVideo, rev: MusicVideoRevision) -> MusicVideoRevision | None:
    reason = rev.reason
    try:
        rev.label = (rev.label or "")[:255] or None
        db.session.add(rev)
        db.session.flush()
        stale = (
            MusicVideoRevision.query.filter_by(music_video_id=mv.id)
            .order_by(MusicVideoRevision.created_at.desc(), MusicVideoRevision.id.desc())
            .offset(MAX_REVISIONS_PER_VIDEO).all()
        )
        for old in stale:
            db.session.delete(old)
        db.session.commit()
        return rev
    except Exception as e:  # noqa: BLE001
        log.warning("music_video %s: could not save a %s version: %s", mv.id, reason, e)
        db.session.rollback()
        return None


def _parse_project_id(raw):
    """(project_id or None, error response or None)."""
    if raw in (None, "", "none", "null"):
        return None, None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({"error": "project_id must be an integer or null"}), 400)
    if db.session.get(Project, pid) is None:
        return None, (jsonify({"error": f"project_id {pid} not found"}), 400)
    return pid, None


def _refile_outputs(mv: MusicVideo) -> None:
    """Move the video's rendered outputs to its (new) project, or out of any."""
    from backend.services.music_video_service import attach_outputs_to_project
    ids = list((mv.settings_json or {}).get("project_document_ids") or [])
    if mv.output_document_id:
        ids.append(mv.output_document_id)
    if not ids:
        return
    if mv.project_id:
        attach_outputs_to_project(mv, ids)
    else:
        for doc in Document.query.filter(Document.id.in_(ids)).all():
            doc.project_id = None


@bp.patch("/<int:mv_id>")
def patch_music_video(mv_id):
    """Rename, file under a project (or none), and keep notes/tags. Any stage.

    Body (all optional): {name, project_id (int|null), notes, tags: [str]}
    Moving to a project also files the rendered video under it.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}

    if "name" in body:
        if not isinstance(body.get("name"), str):
            return jsonify({"error": "name must be text"}), 400
        name = body["name"].strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
        mv.name = name[:255]

    moved = False
    if "project_id" in body:
        pid, err = _parse_project_id(body.get("project_id"))
        if err:
            return err
        moved = pid != mv.project_id
        mv.project_id = pid

    if "notes" in body or "tags" in body:
        db.session.refresh(mv, ["settings_json"])
        s = dict(mv.settings_json or {})
        if "notes" in body:
            s["notes"] = str(body.get("notes") or "")[:20000]
        if "tags" in body:
            tags = body.get("tags") or []
            if not isinstance(tags, list):
                return jsonify({"error": "tags must be a list of strings"}), 400
            s["tags"] = sorted({str(t).strip()[:64] for t in tags if str(t).strip()})[:50]
        mv.settings_json = s

    if moved:
        db.session.flush()
        try:
            _refile_outputs(mv)
        except Exception as e:  # noqa: BLE001
            log.warning("music_video %s: re-filing outputs failed: %s", mv_id, e)
            db.session.rollback()
            return jsonify({"error": f"could not move the rendered video to that project: {e}"}), 500
    db.session.commit()
    db.session.refresh(mv)
    return jsonify(_mv_dict(mv))


def _copy_media_into(mv_id: int, clips: list) -> list:
    """Give a copied video its own storyboard and clip files, so a later render
    or delete of the original never changes the copy. Real copies that keep the
    timestamp, not hard links: a render that rewrites a file in place would
    change both."""
    import shutil
    from backend.tasks.music_video_tasks import _clip_dir
    out_dir = None
    for c in clips:
        for key in ("storyboard_path", "clip_path"):
            src_path = c.get(key)
            if not (src_path and os.path.exists(src_path)):
                continue
            try:
                out_dir = out_dir or _clip_dir(mv_id)
                dst = out_dir / os.path.basename(src_path)
                if not dst.exists():
                    shutil.copy2(src_path, dst)
                c[key] = str(dst)
            except OSError as e:
                log.warning("music_video %s: could not copy %s: %s", mv_id, src_path, e)
                c[key] = None
                if key == "clip_path":
                    c.update({"status": "pending", "quality": None, "review": None})
    return clips


@bp.post("/<int:mv_id>/duplicate")
def duplicate_music_video(mv_id):
    """Copy a music video's song, style, story, settings and cut plan into a new
    one parked at the approval gate (or re-analyzed when there is no plan).

    Body (optional): {name, project_id, keep_storyboards: bool=true, keep_clips: bool=false}
    keep_clips reuses the rendered clip files: only cuts you change re-render.
    """
    import copy
    src = db.session.get(MusicVideo, mv_id)
    if src is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    pid = src.project_id
    if "project_id" in body:
        pid, err = _parse_project_id(body.get("project_id"))
        if err:
            return err
    keep_sb = body.get("keep_storyboards", True) is not False
    keep_clips = bool(body.get("keep_clips", False))
    if "name" in body and body["name"] is not None and not isinstance(body["name"], str):
        return jsonify({"error": "name must be text"}), 400

    settings = clean_settings(src.settings_json)
    settings.pop("project_document_ids", None)
    settings.pop("director_diagnostics", None)
    clips = copy.deepcopy(src.clips or [])
    for c in clips:
        if not keep_sb:
            c["storyboard_path"] = None
            c.pop("storyboard_variation", None)
        if not keep_clips:
            c.update({"status": "pending", "clip_path": None, "quality": None, "review": None})
            c.pop("input_hash", None)
            c.pop("elapsed_s", None)
        c.pop("deferred_since", None)
        c.pop("error", None)
    if keep_clips:
        clips = restored_clips(clips)

    name = (body.get("name") or "").strip() or f"{src.name} (copy)"
    svc = MusicVideoService(db.session)
    new = svc.create(
        name=name[:255], song_document_id=src.song_document_id, song_path=src.song_path,
        style_prompt=src.style_prompt, project_id=pid, settings=settings,
    )
    has_plan = bool(src.cut_plan)
    dispatch = {}
    if has_plan:
        new.cut_plan = copy.deepcopy(src.cut_plan)
        new.clips = _copy_media_into(new.id, clips)
        new.current_stage = "awaiting_approval"
        new.status = "awaiting_approval"
        db.session.commit()
        svc.stamp_stage(new.id, "awaiting_approval")
    elif svc.advance_if_predecessor(new.id, expected_predecessor="draft"):
        dispatch = dispatch_report(svc.try_dispatch(new.id, "analyzer"))
    db.session.refresh(new)
    _save_revision(new, reason="manual", label=f"Copied from «{src.name}» (#{src.id})")
    db.session.refresh(new)
    return jsonify({**_mv_dict(new), **dispatch, "duplicated_from": src.id}), 201


@bp.post("/<int:mv_id>/revise")
def revise_music_video(mv_id):
    """Reopen a finished, failed or cancelled video for editing at the approval gate.

    Unlike /replan (which renders every cut again), finished clips are KEPT:
    after you edit, approving re-renders only the cuts whose prompt, seed or
    render settings changed (clips[i].input_hash), then re-assembles.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if not mv.cut_plan:
        return jsonify({"error": "no cut plan yet; analyze the song first"}), 409
    if _is_busy(mv) or mv.current_stage == "awaiting_approval":
        return jsonify({"error": f"music_video is at stage '{mv.current_stage}'; revise a finished, failed or cancelled video"}), 409
    _save_revision(mv, reason="revise", label="Before revision")
    mv.clips = restored_clips(mv.clips)
    mv.current_stage = "awaiting_approval"
    mv.status = "awaiting_approval"
    mv.error_blob = None
    # The old render stays in the library (and in the version just saved).
    db.session.commit()
    MusicVideoService(db.session).stamp_stage(mv_id, "awaiting_approval")
    db.session.refresh(mv)
    return jsonify(_mv_dict(mv))


@bp.post("/<int:mv_id>/cut/<int:idx>")
def edit_cut(mv_id, idx):
    """Edit one cut: {prompt?, seed? (int|null), locked? (bool), subject_ids? (list|null)}.

    ``subject_ids`` names the Cast subjects this cut's keyframe carries (null goes
    back to the video's own list).

    At the approval gate: changes the plan (a changed prompt clears that cut's
    storyboard). While generating, a changed prompt/seed sends just this cut
    back to render. Locked cuts keep their prompt when the Director re-plans.
    """
    import copy
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage not in ("awaiting_approval", "generating") or (mv.status or "").startswith(("failed", "cancelled")):
        return jsonify({"error": f"cuts can be edited at the approval gate or while generating; this video is at '{mv.current_stage}' (use revise first)"}), 409
    body = request.get_json(silent=True) or {}
    clips = copy.deepcopy(mv.clips or [])
    cut = next((c for c in clips if c.get("index") == idx), None)
    if cut is None:
        return jsonify({"error": f"cut {idx} not found"}), 404

    changed_render = False
    if "prompt" in body:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "prompt cannot be empty"}), 400
        if len(prompt) > MAX_PROMPT_CHARS:
            return jsonify({"error": f"a prompt can be at most {MAX_PROMPT_CHARS} characters"}), 400
        if prompt != (cut.get("prompt") or "").strip():
            cut["prompt"] = prompt
            cut["storyboard_path"] = None
            cut.pop("storyboard_variation", None)
            changed_render = True
    if "seed" in body:
        seed = body.get("seed")
        if seed in (None, ""):
            new_seed = None
        else:
            try:
                new_seed = int(seed)
            except (TypeError, ValueError):
                return jsonify({"error": "seed must be an integer or null"}), 400
            if not (0 <= new_seed < 2**31):
                return jsonify({"error": "seed must be between 0 and 2147483647"}), 400
        if new_seed != cut.get("seed"):
            if new_seed is None:
                cut.pop("seed", None)
            else:
                cut["seed"] = new_seed
            changed_render = True
    if "locked" in body:
        cut["locked"] = bool(body.get("locked"))
    if "subject_ids" in body:
        raw = body.get("subject_ids")
        if raw is None:
            new_ids = None
        elif isinstance(raw, list) and all(
            isinstance(x, int) and not isinstance(x, bool) for x in raw
        ):
            new_ids = list(dict.fromkeys(raw))
            from backend.models import Subject
            unknown = [x for x in new_ids if db.session.get(Subject, x) is None]
            if unknown:
                return jsonify({"error": f"no Cast subject with id {unknown[0]}"}), 400
        else:
            return jsonify({"error": "subject_ids must be a list of Cast subject ids or null"}), 400
        if new_ids != cut.get("subject_ids"):
            if new_ids is None:
                cut.pop("subject_ids", None)
            else:
                cut["subject_ids"] = new_ids
            cut["storyboard_path"] = None
            cut.pop("storyboard_variation", None)
            changed_render = True

    was_idle = _generator_idle(mv)
    if changed_render and cut.get("status") in ("done", "failed"):
        # Re-render this cut. Hash covers it for hashed clips; legacy ones need the reset.
        cut.update({"status": "pending", "clip_path": None, "quality": None, "review": None})
    mv.clips = clips
    db.session.commit()
    dispatch = _continue_generation(mv, was_idle) if changed_render else {}
    db.session.refresh(mv)
    return jsonify({**_mv_dict(mv), **dispatch})


# --- Versions -------------------------------------------------------------------

@bp.get("/<int:mv_id>/revisions")
def list_revisions(mv_id):
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    rows = (MusicVideoRevision.query.filter_by(music_video_id=mv_id)
            .order_by(MusicVideoRevision.created_at.desc(), MusicVideoRevision.id.desc()).all())
    return jsonify({"revisions": [r.to_dict() for r in rows]})


@bp.post("/<int:mv_id>/revisions")
def create_revision(mv_id):
    """Save the current state as a named version: {label?}."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    raw_label = (request.get_json(silent=True) or {}).get("label")
    if raw_label is not None and not isinstance(raw_label, str):
        return jsonify({"error": "label must be text"}), 400
    label = (raw_label or "").strip()[:255] or "Saved version"
    rev = _save_revision(mv, reason="manual", label=label)
    if rev is None:
        return jsonify({"error": "could not save the version"}), 500
    return jsonify(rev.to_dict()), 201


@bp.get("/<int:mv_id>/revisions/<int:rev_id>")
def get_revision(mv_id, rev_id):
    rev = db.session.get(MusicVideoRevision, rev_id)
    if rev is None or rev.music_video_id != mv_id:
        return jsonify({"error": "not_found"}), 404
    return jsonify(rev.to_dict(include_snapshot=True))


@bp.post("/<int:mv_id>/revisions/<int:rev_id>/restore")
def restore_revision(mv_id, rev_id):
    """Bring a saved version back. The current state is saved first, so a restore
    can itself be undone. Lands at the approval gate; clips whose file still
    exists and whose inputs match are kept, the rest render on approval."""
    mv = db.session.get(MusicVideo, mv_id)
    rev = db.session.get(MusicVideoRevision, rev_id)
    if mv is None or rev is None or rev.music_video_id != mv_id:
        return jsonify({"error": "not_found"}), 404
    if _is_busy(mv):
        return jsonify({"error": f"cannot restore while '{mv.current_stage}' is running; cancel it first"}), 409
    if _storyboards_rendering(mv):
        return jsonify({"error": "storyboards are being generated; restore once they finish"}), 409
    snap = rev.snapshot or {}
    if not snap.get("cut_plan"):
        return jsonify({"error": "this version has no cut plan to restore"}), 409

    _save_revision(mv, reason="restore", label=f"Before restoring version #{rev.id}")
    import copy
    db.session.refresh(mv, ["settings_json"])
    current = dict(mv.settings_json or {})
    restored = dict(snap.get("settings") or {})
    # Keep where the video is filed and its run-time bookkeeping.
    for k in ("project_document_ids", "stage_timings"):
        if k in current:
            restored[k] = current[k]
    if snap.get("name"):
        mv.name = snap["name"]
    if snap.get("style_prompt"):
        mv.style_prompt = snap["style_prompt"]
    mv.cut_plan = copy.deepcopy(snap["cut_plan"])
    mv.clips = restored_clips(snap.get("clips"))
    mv.settings_json = restored
    mv.current_stage = "awaiting_approval"
    mv.status = "awaiting_approval"
    mv.error_blob = None
    db.session.commit()
    MusicVideoService(db.session).stamp_stage(mv_id, "awaiting_approval")
    db.session.refresh(mv)
    return jsonify({**_mv_dict(mv), "restored_revision": rev.id})
