"""Music-video pipeline state machine. DB-persisted, crash-resumable.

Owns the transition graph for a MusicVideo through its stages:
  draft → analyzing → awaiting_approval → generating → assembling → complete

Cloned 1:1 from production_service.py (the proven swarm pattern): every
transition is a single atomic UPDATE-WHERE commit, so re-dispatching at a stage
other than an agent's expected predecessor is a no-op. That idempotency is what
makes crash-resume + the per-clip tail-call dispatch (see music_video_tasks.py)
safe against double-fires.

Also home to the PURE, deterministic cut-cadence planner (`compute_cut_plan`) —
no I/O, no GPU — so it unit-tests in isolation.
"""
from __future__ import annotations

import math
import subprocess
from typing import Any

from backend.models import MusicVideo
from backend.services.pipeline_service import (
    PipelineService,
    TERMINAL_STATUSES,  # noqa: F401  re-exported for back-compat
    _coerce_error,      # noqa: F401  re-exported for back-compat
)


VALID_TRANSITIONS: dict[str, str] = {
    "draft": "analyzing",
    "analyzing": "awaiting_approval",
    "awaiting_approval": "generating",
    "generating": "assembling",
    "assembling": "complete",
    # "cancelled" is a terminal state set directly by the cancel handler (not via
    # normal advance). Listed here for completeness / introspection even though
    # the state machine doesn't transition *into* it the normal way.
    "cancelled": "cancelled",
}


# Maps a current_stage to the agent that resumes work there. None means the
# stage is user-gated (no auto-resume on boot).
STAGE_TO_AGENT: dict[str, str | None] = {
    "draft": None,                 # pre-pipeline — kickoff advances out of it
    "analyzing": "analyzer",
    "awaiting_approval": None,      # USER GATE: cost approval before any GPU spend
    "generating": "clip_generator",  # self-re-dispatching per-clip generator
    "assembling": "assembler",
    "cancelled": None,             # terminal via explicit user cancel; no agent
}


# --- Cut cadence (pure) ------------------------------------------------------

MIN_CLIP_S = 0.6   # floor: never emit a cut shorter than this


def _beats_per_cut(energy: float, emin: float, emax: float) -> int:
    """Map a section's energy to how many beats one cut should span.

    High energy → cut every beat (K=1); quietest → every 8 beats. Normalized
    WITHIN this song so the spread is relative, not absolute.
    """
    if emax <= emin:
        return 2
    t = (energy - emin) / (emax - emin)   # 0..1
    return max(1, round(8 - 7 * t))       # t=1 → 1 beat; t=0 → 8 beats


def _section_for(t: float, sections: list[dict[str, Any]]) -> dict[str, Any]:
    """Section covering time ``t`` (seconds); falls back to the last section."""
    for sec in sections:
        if sec["start"] <= t < sec["end"]:
            return sec
    return sections[-1]


def _split_long_cuts(cuts: list[dict[str, Any]], max_cut_s: float | None) -> list[dict[str, Any]]:
    """Split any cut longer than ``max_cut_s`` into near-equal forward sub-cuts.

    WHY: a generated i2v clip is at most ~``max_clip_s`` seconds of real forward
    motion (the WAN frame clamp). If the planner emits a slot longer than a clip
    can fill, the only ways to cover it are reverse (the moonwalk) or a freeze. So
    instead we make the planner size its slots to what a forward clip can deliver:
    a cut of length L becomes ``ceil(L / max_cut_s)`` equal sub-cuts, each ≤
    max_cut_s. One clip per sub-cut, all forward. ``max_cut_s`` is
    ``max_clip_s × max_stretch`` — the per-video stretch budget (see _settings).

    No-op when ``max_cut_s`` is falsy (keeps the planner pure for unit tests).
    """
    if not max_cut_s or max_cut_s <= 0:
        return cuts
    out: list[dict[str, Any]] = []
    for c in cuts:
        length = c["end_s"] - c["start_s"]
        if length <= max_cut_s + 1e-6:
            out.append(c)
            continue
        n_sub = math.ceil(length / max_cut_s)
        sub = length / n_sub
        for j in range(n_sub):
            s0 = c["start_s"] + j * sub
            s1 = c["end_s"] if j == n_sub - 1 else c["start_s"] + (j + 1) * sub
            out.append({**c, "start_s": s0, "end_s": s1})
    return out


def _coarsen_cuts_for_slow_pace(cuts: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """When the user/treatment asked for slow/dreamlike pacing, aggressively merge
    adjacent cuts so the final edit has fewer, longer, more atmospheric holds instead
    of frantic beat-sliced micro-cuts. This runs *after* the energy-based grouping
    but *before* the max_cut_s split (so we don't fight the forward-clip budget).
    Target: roughly one cut every 2-4s for dreamy material (user can still override
    via max_stretch for the fill slowdown).
    """
    if not cuts or len(cuts) <= 1:
        return cuts
    # Rough target: for a short song aim for ~8-14 cuts max; for longer scale gently.
    target_max = max(6, int(duration / 2.2))
    if len(cuts) <= target_max:
        return cuts

    out: list[dict[str, Any]] = [dict(cuts[0])]
    for c in cuts[1:]:
        if len(out) >= target_max:
            out.append(dict(c))
            continue
        prev = out[-1]
        p_len = prev["end_s"] - prev["start_s"]
        c_len = c["end_s"] - c["start_s"]
        e_delta = abs(float(prev.get("energy", 0.0)) - float(c.get("energy", 0.0)))
        # Merge if combined is still reasonable or energies are similar (calm section)
        if (p_len + c_len < 5.5) or (e_delta < 0.18 and p_len < 4.0):
            prev["end_s"] = c["end_s"]
            prev["energy"] = (float(prev.get("energy", 0.0)) + float(c.get("energy", 0.0))) / 2.0
        else:
            out.append(dict(c))
    return out


def compute_cut_plan(
    beat_times: list[float],
    sections: list[dict[str, Any]],
    duration: float,
    max_cut_s: float | None = None,
    slow_pace: bool = False,
    min_cut_s: float | None = None,
) -> list[dict[str, Any]]:
    """Build an energy-aware, beat-snapped cut plan covering [0, duration].

    Returns ordered cuts: [{index, start_s, end_s, energy, section_label}].
    - Boundaries (except the final one) are exact beat timestamps.
    - Slow cuts in low-energy sections, near-every-beat in high-energy ones.
    - Every cut respects MIN_CLIP_S; the final cut is snapped to the song end so
      audio and video coterminate. Deterministic — same inputs → same plan.
    - When ``max_cut_s`` is given, no cut exceeds it (long holds are split into
      forward sub-cuts) so a forward clip can always fill its slot without a
      reverse. ``None`` (default) leaves cut lengths untouched.
    - When BOTH ``min_cut_s`` and ``max_cut_s`` are given, each cut is sized toward
      an energy-mapped TARGET length in ``[min_cut_s, max_cut_s]`` (high energy →
      min, calm → max), beat-snapped. This is how the Clip Stretch setting actually
      lengthens clips: the caller passes ``min/max = wan_motion_s × stretch-derived``
      bounds, and ``fill_clip_to_duration`` later time-stretches the ~2s WAN motion
      up to fill the longer slot. When either is ``None`` the legacy energy-only
      ``_beats_per_cut`` mapping is used (keeps the pure planner unit tests intact).
    """
    if not sections:
        sections = [{"label": "unlabeled", "start": 0.0, "end": duration, "mean_energy": 0.0}]

    # No beats detected → one cut for the whole song (degenerate but valid).
    if not beat_times:
        sec = sections[0]
        cuts = [{
            "start_s": 0.0, "end_s": duration,
            "energy": sec.get("mean_energy", 0.0), "section_label": sec.get("label", "unlabeled"),
        }]
        cuts = _split_long_cuts(cuts, max_cut_s)
        for i, c in enumerate(cuts):
            c["index"] = i
        return cuts

    energies = [s.get("mean_energy", 0.0) for s in sections]
    emin, emax = min(energies), max(energies)

    cuts: list[dict[str, Any]] = []
    n = len(beat_times)
    bi = 0          # index of the next beat to consume
    cut_start = 0.0

    # When a [min_cut_s, max_cut_s] band is supplied we size cuts toward an energy-mapped
    # target length (scaled by Clip Stretch upstream) instead of the legacy energy→beats map.
    # Needs a representative beat spacing to convert a target length into a whole-beat count.
    use_target = (
        min_cut_s is not None and max_cut_s is not None
        and max_cut_s >= min_cut_s > 0
    )
    if use_target and n >= 2:
        _diffs = sorted(beat_times[i + 1] - beat_times[i] for i in range(n - 1))
        beat_period = _diffs[len(_diffs) // 2]  # median beat interval (tempo-robust)
    else:
        beat_period = 0.0

    while bi < n:
        sec = _section_for(cut_start, sections)
        if use_target and beat_period > 0:
            # high energy (t→1) → shorter target (min_cut_s); calm (t→0) → longer (max_cut_s)
            t = (sec.get("mean_energy", 0.0) - emin) / (emax - emin) if emax > emin else 0.5
            t = min(1.0, max(0.0, t))
            target_s = max_cut_s - t * (max_cut_s - min_cut_s)
            k = max(1, round(target_s / beat_period))
        else:
            k = _beats_per_cut(sec.get("mean_energy", 0.0), emin, emax)
        if slow_pace:
            # For slow/dreamlike treatments, force longer cuts even on high detected energy.
            # Stronger bias + post-merge below so a 29s dreamy song doesn't become 20+ micro-cuts.
            k = max(k, 4)
            k = int(k * 2.5)
        end_i = min(bi + k - 1, n - 1)      # beat index that ends this cut
        cut_end = beat_times[end_i]
        # Floor: extend forward by whole beats until the cut clears MIN_CLIP_S.
        while cut_end - cut_start < MIN_CLIP_S and end_i < n - 1:
            end_i += 1
            cut_end = beat_times[end_i]
        cuts.append({
            "start_s": cut_start,
            "end_s": cut_end,
            "energy": sec.get("mean_energy", 0.0),
            "section_label": sec.get("label", "unlabeled"),
        })
        cut_start = cut_end
        bi = end_i + 1

    # Tail: snap the last boundary to the song end (covers the post-final-beat
    # outro) so the video runs exactly as long as the audio.
    cuts[-1]["end_s"] = duration
    # If snapping somehow left a sub-floor runt, fold it into its predecessor.
    if len(cuts) > 1 and (cuts[-1]["end_s"] - cuts[-1]["start_s"]) < MIN_CLIP_S:
        cuts[-2]["end_s"] = duration
        cuts.pop()

    if slow_pace:
        cuts = _coarsen_cuts_for_slow_pace(cuts, duration)

    # Size every slot to what a forward clip can fill (no reverse needed).
    cuts = _split_long_cuts(cuts, max_cut_s)

    for i, c in enumerate(cuts):
        c["index"] = i
    return cuts


def probe_duration(path: str) -> float:
    """Duration of a media file in seconds via ffprobe. 0.0 if unknown."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return 0.0


def fill_clip_to_duration(
    src_clip: str,
    target_s: float,
    output_path: str,
    *,
    fps: int = 24,
    width: int = 1080,
    height: int = 1920,
    method: str = "forward",
    max_stretch: float = 2.0,
) -> str:
    """Fill/trim a short generated clip to EXACTLY ``target_s`` seconds.

    WHY THIS EXISTS (memory obs #721, the pipeline's highest functional risk):
    WAN i2v clamps every clip to ~0.7-2.0s regardless of the requested duration,
    and the MLT composer does NOT auto-stretch a source to fill a longer timeline
    slot — it leaves the remainder blank, so audio drifts over black. So before
    assembly each clip must be made exactly as long as its cut interval.

    METHODS (per-video ``settings_json.fill_method``):
    - ``forward`` (default): forward motion only — NO reverse. Slow the clip with
      ``setpts`` to cover the slot, capped at ``max_stretch`` so it never freezes;
      if the slot is still longer (rare — the planner sizes cuts to ≤
      max_clip_s×max_stretch, see compute_cut_plan), hold the last frame
      (``tpad``) for the remainder. Directional motion (walking crows) stays
      forward — this is the moonwalk fix.
    - ``boomerang``: the legacy forward+reverse concat (seamless, no loop-jump,
      but motion reverses halfway — the moonwalk). Kept as an opt-in because it
      reads fine for abstract/ambient clips.
    - ``loop``: forward repeat via input ``-stream_loop`` (continuous, clean
      timestamps; a visible jump at each loop seam but motion never reverses).

    A ``-t target_s`` hard-trims so the output is EXACT for any case — the
    assembler contract (music_video_tasks: source_out == cut_len) depends on it.
    Pure ffmpeg — zero GPU; the caller runs it OUTSIDE the GPU gate.
    """
    src_len = probe_duration(src_clip) or 5.0
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    input_opts: list[str] = []

    if method == "boomerang":
        # Legacy: forward + reverse. Seamless but reverses motion (the moonwalk).
        boomerang_len = 2 * src_len
        k = (target_s / boomerang_len) if target_s > boomerang_len else 1.0
        graph = (
            f"[0:v]reverse[r];[0:v][r]concat=n=2:v=1[bm];"
            f"[bm]{cover},setpts={k:.4f}*PTS,format=yuv420p[v]"
        )
    elif method == "loop":
        # Forward-only repeat. -stream_loop loops the demuxer with continuous,
        # monotonic timestamps (no filtergraph PTS games); -t trims to exact.
        input_opts = ["-stream_loop", "-1"]
        graph = f"[0:v]{cover},format=yuv420p[v]"
    else:  # "forward" (default) — forward motion only, never reversed
        needed = (target_s / src_len) if src_len > 0 else 1.0
        # k<1 → slot shorter than clip (play forward at normal speed, trim);
        # 1≤k≤max_stretch → slow forward to fill exactly; k capped at max_stretch.
        k = min(max(needed, 1.0), max_stretch)
        graph = f"[0:v]{cover},setpts={k:.4f}*PTS,format=yuv420p"
        if needed > max_stretch + 1e-6:
            # Slot longer than the max-stretched clip → hold the last frame for the
            # remainder (forward freeze, never a reverse). -t trims the held tail.
            graph += f",tpad=stop_mode=clone:stop_duration={target_s:.4f}"
        graph += "[v]"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", *input_opts, "-i", src_clip,
        "-filter_complex", graph, "-map", "[v]",
        "-t", f"{target_s:.4f}", "-r", str(fps), "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"fill_clip_to_duration failed: {result.stderr[-500:]}")
    return output_path


class MusicVideoService(PipelineService):
    """Coordinates state transitions on MusicVideo rows.

    Takes a SQLAlchemy session (Flask-SQLAlchemy's ``db.session`` works).
    Optionally wire a ``gate`` (JobOperationGate) for GPU exclusivity. The
    lifecycle plumbing lives in ``PipelineService``; this class adds the
    music-video ``create()`` and the song-shaped cut/clip helpers above.
    """

    model_cls = MusicVideo
    valid_transitions = VALID_TRANSITIONS
    stage_to_agent = STAGE_TO_AGENT
    task_namespace = "music_video"
    stage_context = "music-video"

    # --- Lifecycle ---------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        song_document_id: int | None,
        song_path: str | None,
        style_prompt: str,
        project_id: int | None,
        settings: dict | None = None,
    ) -> MusicVideo:
        mv = MusicVideo(
            name=name,
            song_document_id=song_document_id,
            song_path=song_path,
            style_prompt=style_prompt,
            project_id=project_id,
            status="draft",
            current_stage="draft",
            settings_json=settings or {},
        )
        self.s.add(mv)
        self.s.commit()
        return mv

    # State-machine plumbing (advance_if_predecessor / fail_stage /
    # find_non_terminal / dispatch_agent / resume_all / gpu_stage) is inherited
    # from PipelineService, driven by the class attributes set above.
    # dispatch_agent runs the stage prep for stage_context + current_stage when
    # GUAARDVARK_PIPELINE_STAGE_PREP is on (e.g. ollama for analyzing, comfyui
    # for generating).

    # --- Plan / Director helpers (pre-approval editing & re-planning) --------

    def update_clip_prompts(self, mv_id: int, prompt_updates: dict[int, str]) -> MusicVideo | None:
        """Update per-cut prompts on the clips list (used for operator edits before approval).

        prompt_updates: {index: "new prompt text", ...}
        Only mutates while the video is at 'awaiting_approval' (safe window).
        Returns the refreshed MusicVideo or None if not found / not editable.
        """
        mv = self.s.get(self.model_cls, mv_id)
        if not mv or mv.current_stage != "awaiting_approval":
            return None
        if not prompt_updates:
            return mv

        import copy
        clips = copy.deepcopy(mv.clips or [])
        changed = False
        for c in clips:
            idx = c.get("index")
            if isinstance(idx, int) and idx in prompt_updates:
                new_p = (prompt_updates[idx] or "").strip()
                if new_p:
                    c["prompt"] = new_p
                    changed = True
        if changed:
            mv.clips = clips
            self.s.commit()
        return mv

    def regenerate_director_prompts(
        self,
        mv_id: int,
        *,
        feedback: str | None = None,
        planning_mode: str | None = None,
        director_model: str | None = None,
    ) -> MusicVideo | None:
        """Re-run the Director over the *existing* cut_plan to produce fresh per-cut prompts.

        Safe only before generation has started (primarily 'awaiting_approval').
        Respects the stored director_enabled flag. If disabled, this is a no-op (keeps global style).
        The planning_mode, feedback (extra_guidance), and director_model (dedicated small model for
        the Director agent) are applied for this regeneration and also persisted into settings_json
        so the chosen approach travels with the MV.
        Returns the refreshed MV or None.
        """
        mv = self.s.get(self.model_cls, mv_id)
        if not mv or not mv.cut_plan:
            return None
        if mv.current_stage not in ("awaiting_approval", "analyzing"):
            # Allow during analyzing in theory, but the common case is the approval gate.
            if mv.current_stage != "awaiting_approval":
                return None

        s = dict(mv.settings_json or {})
        if not s.get("director_enabled", True):
            # Director off — nothing to regenerate; leave prompts as global style copies.
            return mv

        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "analyzing")  # Director requires Ollama (and video_editor) for per-cut visual prompts from treatment
        from backend.services.music_video_director import DIRECTOR_MODEL, _is_embedding_model

        mode = planning_mode or s.get("planning_mode", "narrative")
        guidance = feedback or s.get("director_guidance")
        dmodel = (director_model or s.get("director_model") or DIRECTOR_MODEL).strip() or DIRECTOR_MODEL
        if _is_embedding_model(dmodel):
            logger = __import__("logging").getLogger(__name__)
            logger.warning("overriding bad director_model=%s (embedding model cannot chat) -> %s", dmodel, DIRECTOR_MODEL)
            dmodel = DIRECTOR_MODEL

        try:
            # Unified Director (SONG_CUTPLAN). creativity=None ⇒ engine-default sampling, a
            # BYTE-IDENTICAL migration of this fragile replan path.
            from backend.services.director_service import plan as director_plan, DirectorBrief, DirectorMode
            _res = director_plan(DirectorBrief(
                mode=DirectorMode.SONG_CUTPLAN,
                style=mv.style_prompt,
                cut_plan=mv.cut_plan,
                creativity=None,
                model=dmodel,
                planning_mode=mode,
                extra_guidance=guidance,
                user_treatment=s.get("user_treatment") or s.get("director_treatment"),
                max_stretch=float(s.get("max_stretch", 2.0)),
                fill_method=s.get("fill_method"),
            ))
            res = _res.raw if _res.raw is not None else {"prompts": [], "treatment": _res.treatment}
            raw_prompts = res.get("prompts") or []
            # P0 guard (story-arc plan): apply distinctness/energy injection here too so a
            # replan immediately gives usable varied prompts for subsequent storyboard regen.
            try:
                from backend.services.music_video_director import _ensure_distinct_and_energy_aware
                guarded = _ensure_distinct_and_energy_aware(
                    raw_prompts, mv.cut_plan, mv.style_prompt,
                    max_stretch=float(s.get("max_stretch", 2.0)),
                )
                if guarded:
                    res["prompts"] = guarded
            except Exception:  # noqa: BLE001
                pass
            prompts = res["prompts"]
            treatment = res.get("treatment")
            if treatment:
                s = dict(mv.settings_json or {})
                s["director_treatment"] = treatment
                mv.settings_json = s
        except Exception:  # noqa: BLE001
            # On any surprise, leave existing prompts untouched (graceful).
            log = __import__("logging").getLogger(__name__)
            log.warning("regenerate_director_prompts failed for music_video %s; leaving prior prompts", mv_id)
            return mv

        if not prompts:
            return mv

        import copy
        clips = copy.deepcopy(mv.clips or [])
        for c in clips:
            idx = c.get("index")
            if isinstance(idx, int) and idx < len(prompts):
                c["prompt"] = prompts[idx]
                # P0 stale-storyboard fix: when the Director prompt for a cut changes,
                # clear any previously generated storyboard still so the next "Generate
                # Storyboards" or per-cut regen will pick up the new unique prompt.
                # (The "thumbnails first" design intentionally skips existing paths for
                # cost reasons; a prompt change is the signal that they are now stale.)
                if "storyboard_path" in c:
                    c["storyboard_path"] = None
                if "storyboard_variation" in c:
                    c["storyboard_variation"] = None

        # Persist chosen mode/guidance/director_model for future (and for the actual generation later)
        s["planning_mode"] = mode
        if guidance:
            s["director_guidance"] = guidance
        if dmodel:
            s["director_model"] = dmodel
        # Store diagnostics if the (guarded) Director run produced any (fallback etc.).
        if isinstance(res, dict) and res.get("director_diagnostics"):
            s["director_diagnostics"] = res.get("director_diagnostics")
        mv.settings_json = s
        mv.clips = clips
        self.s.commit()

        return mv
