"""Pure tests for the music-video progress meter (no DB, no GPU)."""
from datetime import datetime, timedelta

from backend.services.music_video_progress import (
    FALLBACK_SECONDS_PER_CLIP,
    NEEDS_REVIEW,
    clip_progress_pct,
    compute_progress,
    estimate_seconds_per_clip,
)

NOW = datetime(2026, 10, 15, 12, 0, 0)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _clips(n, done=0, **extra):
    out = []
    for i in range(n):
        c = {"index": i, "start": i * 2.0, "end": i * 2.0 + 2.0, "status": "done" if i < done else "pending"}
        out.append(c)
    for i, fields in extra.items():
        out[int(i)].update(fields)
    return out


def _stage(p, key):
    return next(s for s in p["stages"] if s["key"] == key)


def test_draft_is_zero_and_everything_pending():
    p = compute_progress(current_stage="draft", status="draft", clips=[], settings={}, now=NOW)
    assert p["overall_pct"] == 0.0
    assert all(s["state"] in ("pending", "skipped") for s in p["stages"])


def test_analyzing_reports_the_step():
    settings = {"progress": {"stage": "analyzing", "step": "director", "pct": 0}}
    p = compute_progress(current_stage="analyzing", status="analyzing", clips=[], settings=settings, now=NOW)
    a = _stage(p, "analyzing")
    assert a["state"] == "active"
    assert a["message"] == "Director writing shots"
    assert 40 < a["pct"] < 60          # audio 10 + beats 30 + cuts 10 = 50 of 100
    assert 0 < p["overall_pct"] < 5    # analyzing is 5% of the whole


def test_approval_gate_waits_on_the_person():
    p = compute_progress(current_stage="awaiting_approval", status="awaiting_approval",
                         clips=_clips(4), settings={}, now=NOW)
    gate = _stage(p, "awaiting_approval")
    assert gate["state"] == "waiting"
    assert _stage(p, "analyzing")["state"] == "done"
    assert _stage(p, "storyboards")["state"] == "skipped"
    assert p["eta_basis"] == "estimate"
    assert p["eta_seconds"] == 4 * 75 + 90


def test_generating_counts_clips_and_live_sampler_steps():
    settings = {"progress": {"stage": "generating", "clip_index": 2, "substage": "i2v"}}
    live = {"status": "processing", "progress": 50, "additional_data": {"step": 10, "steps": 20}}
    p = compute_progress(current_stage="generating", status="generating",
                         clips=_clips(4, done=2), settings=settings, live=live, now=NOW)
    g = _stage(p, "generating")
    cur = p["current_clip"]
    assert cur["index"] == 2 and cur["substage"] == "i2v"
    assert cur["step"] == 10 and cur["steps"] == 20
    # keyframe 15 + half of i2v 70 = 50% of the clip
    assert cur["pct"] == 50.0
    assert g["pct"] == round(100 * 2.5 / 4, 1)
    assert g["detail"]["clips_done"] == 2


def test_eta_uses_measured_clip_time():
    clips = _clips(4, done=2, **{"0": {"elapsed_s": 100.0}, "1": {"elapsed_s": 120.0}})
    p = compute_progress(current_stage="generating", status="generating", clips=clips,
                         settings={}, now=NOW)
    assert p["eta_basis"] == "measured"
    assert p["seconds_per_clip"] == 110.0
    assert p["eta_seconds"] == round(2 * 110 + 90)


def test_gpu_wait_is_labelled_queued():
    settings = {"progress": {"stage": "generating", "clip_index": 0, "substage": "keyframe"}}
    clips = _clips(2, **{"0": {"deferred_since": 123.0}})
    p = compute_progress(current_stage="generating", status="generating", clips=clips,
                         settings=settings, now=NOW)
    g = _stage(p, "generating")
    assert g["state"] == "queued"
    assert "GPU" in g["message"]


def test_held_cuts_wait_for_review():
    clips = _clips(2, done=2, **{"1": {"review": {"state": "needs_review"}}})
    p = compute_progress(current_stage="generating", status="generating", clips=clips,
                         settings={}, now=NOW)
    g = _stage(p, "generating")
    assert g["state"] == "waiting" and g["detail"]["held"] == 1


def test_failed_stage_is_marked_and_no_eta():
    p = compute_progress(current_stage="generating", status="failed_generating",
                         clips=_clips(3, done=1), settings={}, now=NOW)
    assert _stage(p, "generating")["state"] == "failed"
    assert p["eta_seconds"] is None


def test_cancelled_row_stops_at_last_stage():
    settings = {"stage_timings": {"analyzing": {"started_at": _ts(30), "ended_at": _ts(29)},
                                  "generating": {"started_at": _ts(20)}}}
    p = compute_progress(current_stage="cancelled", status="cancelled",
                         clips=_clips(3, done=1), settings=settings, now=NOW)
    assert _stage(p, "generating")["state"] == "cancelled"
    assert p["eta_seconds"] is None


def test_complete_is_100_with_total_elapsed():
    settings = {"stage_timings": {
        "analyzing": {"started_at": _ts(60), "ended_at": _ts(58)},
        "assembling": {"started_at": _ts(5), "ended_at": _ts(1)},
    }}
    p = compute_progress(current_stage="complete", status="complete",
                         clips=_clips(2, done=2), settings=settings, now=NOW)
    assert p["overall_pct"] == 100.0
    assert all(s["state"] in ("done", "skipped") for s in p["stages"])
    assert p["elapsed_seconds"] == 59 * 60


def test_storyboards_count_only_when_they_ran():
    settings = {"storyboard_generating": True, "storyboard_progress": {"done": 1, "total": 4}}
    p = compute_progress(current_stage="awaiting_approval", status="awaiting_approval",
                         clips=_clips(4), settings=settings, now=NOW)
    sb = _stage(p, "storyboards")
    assert sb["state"] == "active" and sb["pct"] == 25.0


def test_pending_stage_drops_old_run_timings():
    settings = {"stage_timings": {"generating": {"started_at": _ts(90), "ended_at": _ts(40)}}}
    p = compute_progress(current_stage="awaiting_approval", status="awaiting_approval",
                         clips=_clips(2), settings=settings, now=NOW)
    assert "duration_s" not in _stage(p, "generating")


def test_overall_is_monotonic_through_a_run():
    clips = _clips(3)
    seq = [
        ("analyzing", {"progress": {"stage": "analyzing", "step": "audio"}}, clips),
        ("analyzing", {"progress": {"stage": "analyzing", "step": "save"}}, clips),
        ("awaiting_approval", {}, clips),
        ("generating", {"progress": {"stage": "generating", "clip_index": 0, "substage": "keyframe"}}, clips),
        ("generating", {"progress": {"stage": "generating", "clip_index": 1, "substage": "conform"}}, _clips(3, done=1)),
        ("generating", {}, _clips(3, done=3)),
        ("assembling", {"progress": {"stage": "assembling", "step": "render"}}, _clips(3, done=3)),
        ("complete", {}, _clips(3, done=3)),
    ]
    last = -1.0
    for stage, settings, cl in seq:
        pct = compute_progress(current_stage=stage, status=stage, clips=cl, settings=settings, now=NOW)["overall_pct"]
        assert pct >= last, (stage, settings, pct, last)
        last = pct


def test_clip_substage_weights():
    assert clip_progress_pct(None) == 0.0
    assert clip_progress_pct("keyframe") == 0.0
    assert clip_progress_pct("i2v", 100) == 85.0
    assert clip_progress_pct("quality") == 93.0


def test_needs_review_matches_the_quality_gate_constant():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "services" / "video_consistency_metrics.py"
    assert f'NEEDS_REVIEW = "{NEEDS_REVIEW}"' in src.read_text(encoding="utf-8")


MEASURED = {"seconds": 800, "steps": 25, "width": 832, "height": 480, "frames": 81}


def test_estimate_uses_the_models_figure_as_measured():
    assert estimate_seconds_per_clip(history=[], measured=MEASURED) == (800.0, "model")
    assert estimate_seconds_per_clip(history=None, measured=MEASURED, steps=None,
                                     width=832, height=480) == (800.0, "model")


def test_estimate_scales_the_figure_to_steps_and_size():
    sec, basis = estimate_seconds_per_clip(history=[], measured=MEASURED, steps=50, width=832, height=480)
    assert (sec, basis) == (1600.0, "model")
    sec, _ = estimate_seconds_per_clip(history=[], measured=MEASURED, width=1664, height=480)
    assert sec == 1600.0


def test_estimate_prefers_enough_history():
    # Two cuts are not enough to replace the measured figure; three are.
    assert estimate_seconds_per_clip(history=[100.0, 120.0], measured=MEASURED)[1] == "model"
    assert estimate_seconds_per_clip(history=[100.0, 120.0, 500.0], measured=MEASURED) == (120.0, "history")


def test_estimate_falls_back_without_a_figure():
    assert estimate_seconds_per_clip(history=[], measured=None) == (FALLBACK_SECONDS_PER_CLIP, "fallback")
