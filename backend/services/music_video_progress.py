"""Whole-run progress for a music video: stages, the clip in flight, an ETA.

PURE: no I/O, no DB, no GPU. The API hands in the row's plain values plus the
live job record of the clip being rendered (unified progress, read by the
caller), and gets back one dict the page draws from. Unit-tested in isolation
like ``compute_cut_plan``.

Where the numbers come from
  * stage boundaries    settings_json["stage_timings"]  (PipelineService stamps)
  * step inside a stage settings_json["progress"]       (the Celery agents write)
  * clip cursor         clips[i].status / elapsed_s     (the clip generator)
  * sampler steps       the live job of mv_<id>_<idx>   (ComfyUI ws bridge)
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

# Share of the overall bar per stage. GPU clip rendering is nearly all of the
# wall-clock; analysis and assembly are short CPU/LLM steps. The approval gate
# takes no time of ours: it is shown as "waiting for you", never as progress.
STAGE_WEIGHTS: dict[str, float] = {
    "analyzing": 5.0,
    "storyboards": 5.0,
    "generating": 85.0,
    "assembling": 5.0,
}

STAGE_LABELS: dict[str, str] = {
    "analyzing": "Analyzing song",
    "awaiting_approval": "Your approval",
    "storyboards": "Storyboards",
    "generating": "Generating clips",
    "assembling": "Assembling",
}

PIPELINE_ORDER = ["analyzing", "awaiting_approval", "storyboards", "generating", "assembling"]

# Share of one clip per sub-step. i2v sampling dominates.
CLIP_SUBSTAGES: list[tuple[str, str, float]] = [
    ("keyframe", "Keyframe still", 15.0),
    ("i2v", "Animating (I2V)", 70.0),
    ("conform", "Conforming to the beat", 8.0),
    ("quality", "Quality check", 7.0),
]

# Analyzer sub-steps, in order, with their share of the analyzing stage.
ANALYZE_STEPS: list[tuple[str, str, float]] = [
    ("audio", "Reading the song", 10.0),
    ("beats", "Finding beats and sections", 30.0),
    ("cuts", "Planning cuts", 10.0),
    ("director", "Director writing shots", 45.0),
    ("save", "Saving the plan", 5.0),
]

ASSEMBLE_STEPS: list[tuple[str, str, float]] = [
    ("collect", "Collecting clips", 10.0),
    ("render", "Rendering the final cut", 80.0),
    ("register", "Adding to the library", 10.0),
]

DEFAULT_ASSEMBLE_S = 90  # display-only fallback for the assembler's share of the ETA

# Seconds per cut when neither this box's finished cuts nor the model's registry
# entry (music_video_cut_s) gives a figure. Not measured against any one model.
FALLBACK_SECONDS_PER_CLIP = 75.0
# Finished cuts at the same model and settings needed before their median
# replaces the model's registry figure; the first cut of a run also loads the
# model, so one cut alone is not a fair sample.
HISTORY_MIN_CLIPS = 3

NEEDS_REVIEW = "needs_review"


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except (TypeError, ValueError):
        return None


def _duration_s(timing: dict | None, now: datetime) -> float | None:
    if not timing:
        return None
    start = _parse_ts(timing.get("started_at"))
    if start is None:
        return None
    end = _parse_ts(timing.get("ended_at") or timing.get("failed_at")) or now
    return max(0.0, (end - start).total_seconds())


def _steps_pct(steps: list[tuple[str, str, float]], current: str | None, inner_pct: float | None) -> float:
    """Percent through a list of weighted steps when ``current`` is running."""
    total = sum(w for _, _, w in steps) or 1.0
    done = 0.0
    for key, _label, w in steps:
        if key == current:
            frac = max(0.0, min(100.0, float(inner_pct or 0))) / 100.0
            return min(100.0, (done + w * frac) / total * 100.0)
        done += w
    return 0.0


def _step_label(steps, key) -> str | None:
    return next((label for k, label, _ in steps if k == key), None)


def clip_progress_pct(substage: str | None, live_pct: float | None = None) -> float:
    """Percent through ONE clip. ``live_pct`` is the sampler's own percent while
    the I2V model runs (ComfyUI's step/steps), ignored for other sub-steps."""
    inner = live_pct if substage == "i2v" else 0.0
    return _steps_pct(CLIP_SUBSTAGES, substage, inner)


def _is_cancelled(stage: str, status: str) -> bool:
    return status.startswith("cancelled") or stage == "cancelled"


def estimate_seconds_per_clip(
    *,
    history: list | None,
    measured: dict | None,
    steps: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> tuple[float, str]:
    """Seconds one cut takes, and where the number came from.

    ``history`` holds the elapsed_s of cuts this box finished at the same
    model, steps and size; with HISTORY_MIN_CLIPS of them their median wins.
    Otherwise ``measured`` (the model's music_video_cut_s registry figure) is
    scaled linearly to ``steps`` and to the pixel area of ``width`` x
    ``height`` where they differ from what it was measured at; None leaves a
    factor as measured. Basis: "history", "model" or "fallback".
    """
    times = [float(x) for x in (history or []) if isinstance(x, (int, float)) and x > 0]
    if len(times) >= HISTORY_MIN_CLIPS:
        return median(times), "history"
    if measured and measured.get("seconds"):
        seconds = float(measured["seconds"])
        if steps and measured.get("steps"):
            seconds *= float(steps) / float(measured["steps"])
        ref_area = float(measured.get("width") or 0) * float(measured.get("height") or 0)
        if width and height and ref_area:
            seconds *= float(width) * float(height) / ref_area
        return seconds, "model"
    return FALLBACK_SECONDS_PER_CLIP, "fallback"


def compute_progress(
    *,
    current_stage: str | None,
    status: str | None,
    clips: list | None,
    settings: dict | None,
    live: dict | None = None,
    now: datetime | None = None,
    seconds_per_clip: float = FALLBACK_SECONDS_PER_CLIP,
) -> dict[str, Any]:
    """The whole run as one dict: overall percent, a row per stage, the clip in
    flight and an ETA. ``live`` is the job record of the clip being rendered
    (``{"progress": int, "status": str, "additional_data": {...}}``) or None."""
    now = now or datetime.now()
    stage = current_stage or "draft"
    status = status or stage
    clips = [c for c in (clips or []) if isinstance(c, dict)]
    s = settings or {}
    timings: dict = s.get("stage_timings") or {}
    detail: dict = s.get("progress") or {}
    failed_stage = status[len("failed_"):] if status.startswith("failed_") else None
    cancelled = _is_cancelled(stage, status)

    active_clips = [c for c in clips if c.get("status") != "cancelled"]
    clip_count = len(active_clips)
    done = [c for c in active_clips if c.get("status") == "done"]
    failed = [c for c in active_clips if c.get("status") == "failed"]
    # Same value as video_consistency_metrics.NEEDS_REVIEW (kept literal: this module is pure).
    held = [c for c in done if (c.get("review") or {}).get("state") == NEEDS_REVIEW]

    # Storyboards are an optional pre-approval pass; counted only when it ran.
    sb = s.get("storyboard_progress") or {}
    sb_running = bool(s.get("storyboard_generating"))
    sb_ran = sb_running or bool(sb.get("total"))

    # Where in the pipeline is this row? Index into PIPELINE_ORDER.
    if stage == "complete":
        pos = len(PIPELINE_ORDER)
    elif stage in PIPELINE_ORDER:
        pos = PIPELINE_ORDER.index(stage)
    elif cancelled:
        # Cancelled rows keep the stage they stopped in under stage_timings.
        last = next((k for k in reversed(PIPELINE_ORDER) if k in timings), "analyzing")
        pos = PIPELINE_ORDER.index(last)
    else:
        pos = -1  # draft
    if failed_stage in PIPELINE_ORDER:
        pos = PIPELINE_ORDER.index(failed_stage)

    stages: list[dict[str, Any]] = []
    current_clip: dict[str, Any] | None = None
    queued_message: str | None = None

    for i, key in enumerate(PIPELINE_ORDER):
        row: dict[str, Any] = {"key": key, "label": STAGE_LABELS[key]}
        t = timings.get(key) or {}
        if t.get("started_at"):
            row["started_at"] = t.get("started_at")
        if t.get("ended_at"):
            row["ended_at"] = t.get("ended_at")
        dur = _duration_s(t, now)
        if dur is not None:
            row["duration_s"] = round(dur, 1)

        if key == "storyboards":
            if sb_running:
                row["state"] = "active"
                tot = int(sb.get("total") or 0)
                row["pct"] = round(100.0 * int(sb.get("done") or 0) / tot, 1) if tot else 0.0
                row["detail"] = {"done": int(sb.get("done") or 0), "total": tot}
            elif sb_ran:
                row["state"] = "done"
                row["pct"] = 100.0
            else:
                row["state"] = "skipped"
            stages.append(row)
            continue

        if i < pos:
            row["state"] = "done"
            row["pct"] = 100.0
        elif i == pos and stage != "complete":
            if failed_stage == key:
                row["state"] = "failed"
            elif cancelled:
                row["state"] = "cancelled"
            elif key == "awaiting_approval":
                row["state"] = "waiting"   # parked on the person, not working
            else:
                row["state"] = "active"
            row["pct"] = 0.0
        else:
            row["state"] = "pending"
            row["pct"] = 0.0

        if row["state"] == "pending":
            # Timings left from an earlier run (revise / restore) are not this run's.
            for k in ("started_at", "ended_at", "duration_s"):
                row.pop(k, None)

        if key == "analyzing" and row["state"] in ("active", "failed", "cancelled"):
            step = detail.get("step") if detail.get("stage") == "analyzing" else None
            row["pct"] = round(_steps_pct(ANALYZE_STEPS, step, detail.get("pct")), 1)
            if step:
                row["message"] = _step_label(ANALYZE_STEPS, step)

        if key == "assembling" and row["state"] in ("active", "failed", "cancelled"):
            step = detail.get("step") if detail.get("stage") == "assembling" else None
            row["pct"] = round(_steps_pct(ASSEMBLE_STEPS, step, detail.get("pct")), 1)
            if step:
                row["message"] = _step_label(ASSEMBLE_STEPS, step)

        if key == "awaiting_approval" and row["state"] == "waiting":
            row["message"] = "Waiting for you to approve the plan"

        if key == "generating" and clip_count and row["state"] != "pending":
            frac_current = 0.0
            if row["state"] == "active" and detail.get("stage") == "generating":
                idx = detail.get("clip_index")
                sub = detail.get("substage")
                live_pct = None
                ad: dict = {}
                if live and str(live.get("status") or "") in ("processing", "start", "running"):
                    ad = live.get("additional_data") or {}
                    try:
                        live_pct = float(live.get("progress") or 0)
                    except (TypeError, ValueError):
                        live_pct = None
                target = next((c for c in active_clips if c.get("index") == idx), None)
                if target is not None and target.get("status") != "done":
                    frac_current = clip_progress_pct(sub, live_pct) / 100.0
                    current_clip = {
                        "index": idx,
                        "position": sum(1 for c in active_clips if (c.get("index") or 0) <= (idx or 0)),
                        "substage": sub,
                        "substage_label": _step_label(CLIP_SUBSTAGES, sub),
                        "pct": round(frac_current * 100.0, 1),
                    }
                    if sub == "i2v" and ad.get("steps"):
                        current_clip["step"] = ad.get("step")
                        current_clip["steps"] = ad.get("steps")
                    if target.get("deferred_since"):
                        current_clip["queued"] = True
                        queued_message = "Waiting for the GPU (another job holds it)"
            pct = 100.0 * (len(done) + frac_current) / clip_count
            row["pct"] = round(min(100.0, pct), 1) if row["state"] != "done" else 100.0
            row["detail"] = {
                "clips_done": len(done),
                "clip_count": clip_count,
                "held": len(held),
                "failed": len(failed),
                "current": current_clip,
            }
            if queued_message and row["state"] == "active":
                row["state"] = "queued"
                row["message"] = queued_message
            elif held and len(done) == clip_count:
                row["state"] = "waiting"
                row["message"] = f"{len(held)} cut(s) held for your review"
        stages.append(row)

    # Overall percent: weighted stages; storyboards only weigh when they ran.
    weights = dict(STAGE_WEIGHTS)
    if not sb_ran:
        weights.pop("storyboards")
    total_w = sum(weights.values())
    acc = 0.0
    for row in stages:
        w = weights.get(row["key"])
        if not w:
            continue
        acc += w * float(row.get("pct") or 0.0) / 100.0
    overall = 100.0 if stage == "complete" else round(min(99.9, acc / total_w * 100.0), 1)

    # ETA: measured per-clip wall-clock once clips have finished, else the fixed estimate.
    measured = [float(c["elapsed_s"]) for c in done if isinstance(c.get("elapsed_s"), (int, float)) and c["elapsed_s"] > 0]
    per_clip = median(measured) if measured else float(seconds_per_clip)
    eta_basis = "measured" if measured else "estimate"
    eta: float | None = None
    if stage in ("generating", "awaiting_approval") and clip_count and not (failed_stage or cancelled):
        cur_frac = (current_clip or {}).get("pct", 0.0) / 100.0
        remaining = max(0.0, clip_count - len(done) - cur_frac)
        eta = remaining * per_clip + DEFAULT_ASSEMBLE_S
    elif stage == "assembling" and not (failed_stage or cancelled):
        asm = next((r for r in stages if r["key"] == "assembling"), {})
        eta = DEFAULT_ASSEMBLE_S * (1.0 - float(asm.get("pct") or 0) / 100.0)

    # Elapsed is the render's own time: from the latest start of generating once
    # it has begun (a revise or restore starts it again), not from the first
    # analysis or the wait at the approval gate.
    run_started = _parse_ts((timings.get("generating") or {}).get("started_at"))
    if stage in ("generating", "assembling", "complete") and run_started:
        started = run_started
    else:
        started = _parse_ts((timings.get("analyzing") or {}).get("started_at"))
    out: dict[str, Any] = {
        "overall_pct": overall,
        "stage": stage,
        "status": status,
        "stages": stages,
        "current_clip": current_clip,
        "eta_seconds": round(eta) if eta is not None else None,
        "eta_basis": eta_basis,
        "seconds_per_clip": round(per_clip, 1),
        "elapsed_seconds": round((now - started).total_seconds()) if started and stage not in ("complete",) else None,
    }
    if stage == "complete":
        end = _parse_ts((timings.get("assembling") or {}).get("ended_at"))
        if started and end:
            out["elapsed_seconds"] = round((end - started).total_seconds())
    return out
