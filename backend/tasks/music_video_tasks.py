"""Music-video pipeline Celery tasks.

Clones the Production swarm pattern (production_swarm_tasks.py): a stage-guarded
context manager that no-ops on stage mismatch (idempotent crash-resume), fails
the stage cleanly on any exception, and on clean exit atomically advances +
tail-calls the next agent.

Stages: analyzing → (USER GATE) → generating → assembling → complete.

The generating stage is special: it self-re-dispatches ONE clip per invocation
(see run_clip_generator) so a 100+ clip render never blocks the worker for hours,
crash-resumes per-clip, and lets other queued work interleave between clips.
"""
import logging
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from celery import Celery
from flask import current_app

from backend.models import db, MusicVideo, Document
from backend.services.music_video_service import (
    MusicVideoService,
    compute_cut_plan,
    fill_clip_to_duration,
    probe_duration,
)
from backend.services.plugin_bridge import ensure_plugin_running, PluginUnavailable

log = logging.getLogger(__name__)

PLUGIN_URL = "http://127.0.0.1:8207"   # video_editor plugin (analyze + assemble)
# Between clips, wait out the GPU gate's post-release cooldown (job_operation_gate
# GPU_RELEASE_COOLDOWN_S ~8s) before the next clip claims the GPU — otherwise the
# tail-call hits "GPU cooling down" immediately. Also the retry delay for transient
# GPU-busy / plugin-cooldown conditions.
GPU_COOLDOWN_RETRY_S = 12
# Longest a single clip may keep deferring on GPU-busy / plugin-unavailable before the stage fails.
CLIP_DEFER_MAX_S = int(os.environ.get("GUAARDVARK_MV_CLIP_DEFER_MAX_S", str(3 * 3600)))


def _settings(mv: MusicVideo) -> dict:
    """Render settings with defaults. Final timeline is landscape 1080p @24fps;
    the Wan i2v CLIPS are generated at 16fps (Wan 2.2 14B's native rate — matches
    the engine default and the VideoGen page) and conformed onto that timeline.
    Stills generated at a VRAM-friendly 16:9 and cover-scaled at fill time."""
    s = dict(mv.settings_json or {})
    s.setdefault("fps", 24)
    s.setdefault("width", 1920)
    s.setdefault("height", 1080)
    # Keyframe still at SDXL's native 16:9 bucket (1344x768 ≈ 1.03MP, matching SDXL's
    # ~1.05MP training res). The old 1024x576 (0.59MP) was well under native and made
    # SDXL render soft/blurry — a sharp, high-res identity anchor also gives the i2v a
    # far better frame to animate (it cover-scales down to i2v_width/height anyway).
    s.setdefault("still_width", 1344)
    s.setdefault("still_height", 768)
    # i2v RENDER dims — 16:9 landscape (832x480 = WAN's standard 480p, low OOM risk,
    # divisible-by-16). WITHOUT this the request defaults to 512x512 and every clip
    # renders SQUARE, then the fill cover-crops it (the "square video" bug). The
    # fill step cover-scales this to the final width/height (1920x1080), and since
    # it's already 16:9 there's no crop. Bump to 1280x720 for more detail if VRAM allows.
    s.setdefault("i2v_width", 832)
    s.setdefault("i2v_height", 480)
    # i2v model selection. Prefer explicit "i2v_model" (e.g. "wan22-14b-i2v") for full
    # flexibility like the main VideoGeneratorPage. Falls back to the legacy i2v_engine
    # mapping for backward compat.
    # Wan 2.2 I2V is generally the highest quality motion option available for the
    # storyboard → i2v flow.
    if not s.get("i2v_model"):
        engine = s.get("i2v_engine", "wan")
        s["i2v_model"] = "wan22-14b-i2v" if engine == "wan" else "cogvideox-5b-i2v"
    s.setdefault("i2v_engine", "wan")  # keep for _max_clip_s etc.
    # Keyframe model — enforce the identity-lock invariant in the BACKEND, not just
    # as a fragile frontend onChange (MusicVideoPage promotes it on the consistency
    # checkbox, but restored settings / API / CLI never fire that). A trained
    # character LoRA only binds on the FLUX-dev(+LoRA) or SDXL(+LoRA) branch of
    # comfyui_image_generator; the flux-schnell branch has no LoRA loader nodes and
    # SILENTLY drops the LoRA (comfyui_image_generator.py:165) — you get the trigger
    # word with none of the weights. So when consistency is ON and a LoRA reference
    # is reachable, never let keyframe_model stay LoRA-blind (flux-schnell / unset).
    _lora_reachable = bool(s.get("loras") or s.get("lora_paths") or s.get("subject_ids"))
    if s.get("use_lora_consistency") and _lora_reachable:
        # Route keyframe model from LoRA sidecar family (Z-Image default train base,
        # SDXL legacy, or FLUX). Never force SDXL when the cast LoRA is Z-Image —
        # character_still_pipeline handles offline vs Comfy.
        req_model = (s.get("keyframe_model") or "").lower()
        if "flux" in req_model and "dev" in req_model:
            pass
        else:
            try:
                from backend.services.media_model_registry import (
                    read_lora_sidecar,
                    resolve_inference_for_loras,
                )
                paths = []
                for p in (s.get("loras") or s.get("lora_paths") or []):
                    if isinstance(p, str) and p.strip():
                        paths.append(p.strip())
                if not paths and s.get("subject_ids"):
                    from backend.models import Subject
                    for sid in s["subject_ids"]:
                        sub = db.session.get(Subject, int(sid))
                        if sub and sub.lora_path:
                            paths.append(sub.lora_path)
                if paths:
                    route = resolve_inference_for_loras(paths)
                    fam = (route.get("family") or "").lower()
                    if fam == "zimage":
                        s["keyframe_model"] = route.get("offline_model_key") or "zimage-turbo"
                    elif fam == "flux":
                        s["keyframe_model"] = route.get("comfy_model_tag") or "flux-dev"
                    else:
                        s["keyframe_model"] = route.get("comfy_model_tag") or "sdxl"
                else:
                    s["keyframe_model"] = "zimage-turbo"
            except Exception:
                s.setdefault("keyframe_model", "zimage-turbo")
    else:
        s.setdefault("keyframe_model", "flux-schnell")
    # --- Playback / cost tuning (per-video; surfaced in the create form) -------
    # fill_method: how a short generated clip is stretched to fill its cut slot.
    #   "forward"   — forward motion only, slow-to-fill (DEFAULT; fixes the moonwalk)
    #   "boomerang" — legacy forward+reverse (the moonwalk; opt-in for ambient clips)
    #   "loop"      — forward repeat
    s.setdefault("fill_method", "forward")
    # max_stretch: per-clip stretch budget. The planner caps each cut at
    # max_clip_s × max_stretch, and the forward fill slows a clip up to this factor.
    # 2.0 = natural slowdown, no clip-halving. Raise it to trade GPU clips for
    # more CPU slowdown (fewer, longer cuts) — the opt-in "render fewer, slow down".
    s.setdefault("max_stretch", 2.0)
    # i2v_steps: override WAN denoising steps (None → engine default 25). The
    # "increase steps a hair" quality lever when slowing clips down more.
    s.setdefault("i2v_steps", None)
    # interpolation_multiplier: RIFE frame interpolation at generation (1=off,
    # 2=double, 4=quad). The "more frames" lever for smooth slow-mo. Default 2
    # preserves the prior implicit behavior (VideoGenerationRequest's own default).
    s.setdefault("interpolation_multiplier", 2)
    # style_recipe_name: controls global filter/transition palettes for the final Shotcut edit
    # (e.g. "Music Video", "Cinematic", "Grunge" from data/agent/style_recipes). Lets the
    # planner and assembler use appropriate editing tools.
    s.setdefault("style_recipe_name", "default")
    # Director: per-cut distinct prompts (the storyboard layer). ON by default; set
    # False to fall back to one global style_prompt for every clip (the old behavior).
    s.setdefault("director_enabled", True)
    # planning_mode: "narrative" (default — continuity + subjects) or "visual" / "mood_arc"
    # (abstract, energy-driven visual progression / tone poem, better for instrumental / soundtrack / thinking music).
    s.setdefault("planning_mode", "narrative")
    # Optional free-text guidance that was provided at regen time (or at create) and fed
    # to the Director as extra instructions for the mood arc / specific direction.
    s.setdefault("director_guidance", None)
    # Flux asset overrides for keyframe/storyboard still generation (flux-schnell path).
    # Passed to ComfyUIImageGenerator so per-MV choice of GGUF/clip/vae files is possible.
    for k in ("flux_unet", "flux_t5", "flux_clip", "flux_vae"):
        s.setdefault(k, None)
    return s


# Per-family clip clamps the i2v step has always used for the storyboard →
# I2V flow (frames, fps); models that declare native_fps / max_frames in the
# registry override them.
_LEGACY_CLIP = {"wan": (17, 49, 16), "cogvideox": (14, 25, 7)}


def _clip_profile(s: dict) -> dict:
    """{min_frames, max_frames, fps, audio_out, model} for the chosen i2v model.

    A model that declares its capabilities in the registry (native_fps,
    max_frames, min_clip_s, max_clip_s, audio_out) is described from them; the
    Wan and CogVideoX clamps stay for entries that do not."""
    model = s.get("i2v_model") or ("wan22-14b-i2v" if s.get("i2v_engine", "wan") == "wan" else "cogvideox-5b-i2v")
    caps = {}
    try:
        from backend.services.video_model_registry import model_capabilities
        caps = model_capabilities(model) or {}
    except Exception:  # noqa: BLE001 — the legacy clamps still apply
        caps = {}
    if caps.get("native_fps") and caps.get("max_frames"):
        fps = int(caps["native_fps"])
        min_frames = int(round(float(caps.get("min_clip_s") or 0) * fps)) or 5
        return {
            "model": model, "fps": fps, "min_frames": min_frames,
            "max_frames": int(caps["max_frames"]), "audio_out": bool(caps.get("audio_out")),
            "frame_rule": caps.get("frame_rule"),
        }
    lo, hi, fps = _LEGACY_CLIP["wan" if "wan" in model.lower() else "cogvideox"]
    return {"model": model, "fps": fps, "min_frames": lo, "max_frames": hi, "audio_out": False, "frame_rule": None}


def _max_clip_s(s: dict) -> float:
    """Longest real forward clip the chosen i2v model produces, in seconds.

    The planner uses this × max_stretch as its cut ceiling so a forward clip
    can always fill its slot without a reverse. A native-audio model (MiniMax
    H3) renders the whole cut in one pass, so its ceiling is its longest clip."""
    prof = _clip_profile(s)
    return prof["max_frames"] / prof["fps"]


# Fraction of the full Clip Stretch budget applied to the *shortest* (highest-energy) cuts.
# The planner targets cut length in [native_clip × stretch × MIN_STRETCH_FRACTION,
# native_clip × stretch] (loud → short end, calm → long end). At fraction 0.5 and stretch 4,
# loud cuts ≈ 2.04×2 = 4.1s and calm cuts ≈ 8.2s; at stretch 1 the whole band collapses to the
# native ~2s clip (no slow-mo). Tunable: raise toward 1.0 for more uniform (longer) loud cuts.
MIN_STRETCH_FRACTION = 0.5


def _cut_length_bounds(s: dict) -> tuple[float, float]:
    """(min_cut_s, max_cut_s) the planner targets, both scaled by the Clip Stretch budget so
    the setting actually controls clip length. See MIN_STRETCH_FRACTION."""
    native = _max_clip_s(s)
    stretch = float(s["max_stretch"])
    max_cut_s = native * stretch
    min_cut_s = native * max(1.0, stretch * MIN_STRETCH_FRACTION)
    return min_cut_s, max_cut_s


try:
    from backend.config import COMFYUI_URL
except Exception:
    COMFYUI_URL = "http://127.0.0.1:8188"


def _comfyui_free_vram():
    """Unload ComfyUI's resident models so the next step gets a clean GPU.

    CRITICAL between the FLUX still and the i2v: ComfyUI custom i2v nodes
    (CogVideoXWrapper, and to a lesser degree the WAN GGUF loader) move their
    models onto CUDA WITHOUT asking ComfyUI to evict anything first — so FLUX's
    ~10GB stays resident and the animator's text-encoder/transformer load OOMs
    (observed: CogVideoTextEncode torch.OutOfMemoryError). Freeing here gives the
    animator the full card and lets us run higher-quality (Q6/Q8) quants.

    Delegates to the canonical reclaim in gpu_resource_policy — one implementation
    shared across every image→video handoff. Best-effort — never fatal."""
    from backend.services.gpu_resource_policy import free_comfyui_vram
    free_comfyui_vram()


def _clip_dir(mv_id: int) -> Path:
    try:
        from backend.config import OUTPUT_DIR
    except Exception:
        OUTPUT_DIR = str(Path(__file__).resolve().parents[2] / "data" / "outputs")
    d = Path(OUTPUT_DIR) / "videos" / f"music_video_{mv_id}" / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _song_lyrics_guidance(mv: MusicVideo) -> str | None:
    """The song's lyrics as thematic direction for the director, when the
    song was generated here and its Document carries them (ACE-Step and
    MiniMax Music 3 write the same keys). The shots must never quote them:
    the model would render the words as text."""
    if not mv.song_document_id:
        return None
    try:
        doc = db.session.get(Document, mv.song_document_id)
        meta = doc.file_metadata if doc else None
        if isinstance(meta, str):
            meta = json.loads(meta)
        lyrics = (meta or {}).get("lyrics") or ""
    except Exception:  # noqa: BLE001 — lyrics are a bonus, never a blocker
        return None
    lyrics = " ".join(str(lyrics).split())
    if not lyrics:
        return None
    return (
        "LYRICS of the song, as thematic source only (never quote them, never show text; "
        f"use them for subject, mood and story arc): {lyrics[:1500]}"
    )


def _resolve_song_path(mv: MusicVideo) -> str | None:
    """Absolute on-disk path for the song: cached song_path wins, else resolve the
    Document. Uploaded Documents store a path relative to UPLOAD_DIR (data/uploads),
    not cwd — try upload-relative first, then absolute, then cwd-relative."""
    if mv.song_path and os.path.exists(mv.song_path):
        return mv.song_path
    if mv.song_document_id:
        doc = db.session.get(Document, mv.song_document_id)
        if doc:
            path = getattr(doc, "file_path", None) or doc.path or doc.filename
            if path:
                from backend.config import UPLOAD_DIR
                p = Path(path)
                candidates = [p] if p.is_absolute() else [Path(UPLOAD_DIR) / p, Path.cwd() / p]
                for c in candidates:
                    if c.exists():
                        return str(c.resolve())
    return None


def _keyframe_cast_context(
    mv: MusicVideo, s: dict, base_prompt: str
) -> tuple[list[str], list[int], str]:
    """Resolve cast for music-video keyframes.

    Returns ``(lora_paths, subject_ids, scene_prompt)``. Identity lock is applied
    once inside ``render_character_still`` (identity core — not full bible, not
    bible-clause anchors). Scene prompt is returned raw.

    Settings: ``use_lora_consistency`` + ``subject_ids`` and/or explicit
    ``loras`` / ``lora_paths``. Empty paths when consistency is off.
    """
    if not s.get("use_lora_consistency"):
        return [], [], base_prompt

    lora_paths: list[str] = []
    subject_ids: list[int] = []

    explicit = s.get("loras") or s.get("lora_paths") or []
    if isinstance(explicit, (list, tuple)):
        for p in explicit:
            if isinstance(p, str) and p.strip():
                lora_paths.append(p.strip())

    raw_ids = s.get("subject_ids") or []
    if isinstance(raw_ids, (list, tuple)) and raw_ids:
        from backend.models import Subject
        from backend.services.cast_lock import subjects_to_lock

        subjects = []
        for sid in raw_ids:
            try:
                sub = db.session.get(Subject, int(sid))
            except (TypeError, ValueError):
                sub = None
            if sub is not None:
                subjects.append(sub)
                subject_ids.append(int(sid))
        subj_paths, _lock = subjects_to_lock(subjects, include_bible=False)
        lora_paths.extend(subj_paths)

    seen: set[str] = set()
    lora_paths = [p for p in lora_paths if not (p in seen or seen.add(p))]

    if not lora_paths:
        log.info(
            "music_video %s: use_lora_consistency is ON but no trained LoRA "
            "reference was reachable from settings (no loras/lora_paths and no "
            "subject_ids → Subject.lora_path) — keyframe will render off-model.",
            mv.id,
        )
        return [], [], base_prompt

    log.info(
        "music_video %s keyframe: applying %d LoRA(s) %s subject_ids=%s",
        mv.id,
        len(lora_paths),
        [os.path.basename(p) for p in lora_paths],
        subject_ids,
    )
    return lora_paths, subject_ids, (base_prompt or "").strip()


def _keyframe_loras_and_prompt(mv: MusicVideo, s: dict, base_prompt: str) -> tuple[list[str], str]:
    """Compat wrapper: paths + raw scene prompt (lock applied in render_character_still)."""
    paths, _sids, prompt = _keyframe_cast_context(mv, s, base_prompt)
    return paths, prompt


def _keyframe_lora_strength(s: dict) -> float:
    """Model-aware default keyframe LoRA strength, shared by the clip path AND both
    storyboard endpoints so review thumbnails match the final render. Delegates to the shared
    cast_lock.resolve_lora_strength (one source of truth across all video features); operator
    override in settings wins.

    ``from-lora`` / ``auto`` resolve strength from the first cast LoRA's family
    (Z-Image ~0.9, SDXL ~0.25), not from the UI label alone.
    """
    from backend.services.cast_lock import resolve_lora_strength

    model = (s.get("keyframe_model") or "").strip().lower()
    if model in ("", "from-lora", "auto", "from_lora"):
        model = "zimage-turbo"
        try:
            paths = list(s.get("loras") or s.get("lora_paths") or [])
            sids = s.get("subject_ids") or []
            if sids and not paths:
                from backend.models import Subject
                for sid in sids:
                    sub = db.session.get(Subject, int(sid))
                    if sub and sub.lora_path:
                        paths.append(sub.lora_path)
                        break
            if paths:
                from backend.services.media_model_registry import resolve_inference_for_loras
                route = resolve_inference_for_loras(paths[:1])
                model = (
                    route.get("offline_model_key")
                    or route.get("comfy_model_tag")
                    or route.get("family")
                    or "zimage-turbo"
                )
        except Exception:
            pass
    return resolve_lora_strength(model, s.get("keyframe_lora_strength"))


@contextmanager
def _mv_run(mv_id: int, *, expected_stage: str, next_agent: str | None):
    """Stage guard + auto-advance, mirroring production_swarm_tasks._agent_run.

    No-ops if the row isn't at expected_stage (idempotent re-dispatch). On any
    exception, fail the stage and ABSORB (Celery's default retry would otherwise
    loop). On clean exit, atomically advance and tail-call next_agent.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or mv.current_stage != expected_stage:
        yield None
        return
    try:
        yield mv
    except Exception as e:  # noqa: BLE001
        log.exception("music_video stage '%s' failed for %s", expected_stage, mv_id)
        MusicVideoService(db.session).fail_stage(mv_id, stage=expected_stage, error=str(e))
    else:
        advanced = MusicVideoService(db.session).advance_if_predecessor(
            mv_id, expected_predecessor=expected_stage
        )
        if advanced and next_agent:
            from backend.celery_app import celery
            celery.send_task(f"music_video.run_{next_agent}", args=[mv_id])


# --- Stage: analyzing --------------------------------------------------------

def run_analyzer(mv_id: int):
    """Analyze the song → energy-aware cut plan → seed the per-clip cursor.

    next_agent=None: on success we advance analyzing → awaiting_approval, which is
    the USER COST GATE. Generation is dispatched only after the user approves
    (see music_video_api approve), never automatically.
    """
    with _mv_run(mv_id, expected_stage="analyzing", next_agent=None) as mv:
        if mv is None:
            return
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "analyzing")
        song = _resolve_song_path(mv)
        if not song:
            raise RuntimeError("song file not found on disk")

        resp = requests.post(
            f"{PLUGIN_URL}/analyze",
            json={"audio_path": song, "section_count": 4},
            timeout=120,
        )
        resp.raise_for_status()
        structure = resp.json()

        # Cap cut length at what a forward clip can fill (clip native length ×
        # the per-video stretch budget) so no slot needs a reverse to cover it.
        s = _settings(mv)
        # Both bounds scale with Clip Stretch so the setting lengthens clips (energy still varies
        # pacing within the band). max_cut_s also remains the forward-clip ceiling for _split_long_cuts.
        min_cut_s, max_cut_s = _cut_length_bounds(s)
        plan = compute_cut_plan(
            structure["beat_times"], structure["sections"], structure["duration_seconds"],
            max_cut_s=max_cut_s, min_cut_s=min_cut_s,
        )
        if not plan:
            raise RuntimeError("cut planner produced no cuts")

        # Bias the cut planner for slow/dreamlike treatments (user's "slow motion" intent and the treatment's "Motion is Slow").
        # This produces fewer, longer cuts so the edit feels dreamy rather than frantic, even if the raw audio energy is high.
        # The max_stretch setting (slowdown) is already used for max_cut_s, but we also dampen energy effect here.
        slow_pace = False
        treatment_text = (s.get("user_treatment") or s.get("director_treatment") or mv.style_prompt or "").lower()
        if any(kw in treatment_text for kw in ["slow", "dreamlike", "gliding", "drifting", "ethereal", "motion is slow", "slow and dreamlike"]):
            slow_pace = True

        if slow_pace:
            # Recompute plan with energy dampened so even "high energy" sections get longer cuts.
            # This respects the artistic intent from the treatment ("Motion is Slow", dreamlike) over the raw librosa energy.
            # Combined with high max_stretch (slow motion setting), this produces the desired long, stretched, atmospheric shots
            # instead of many short frantic ones.
            plan = compute_cut_plan(
                structure["beat_times"], structure["sections"], structure["duration_seconds"],
                max_cut_s=max_cut_s, min_cut_s=min_cut_s,
                slow_pace=True,
            )

        # Director: now generates an actual VISUAL STORYLINE (narrative arc mapped to
        # the song sections + energy) first, then distinct per-cut prompts that advance
        # that storyline. This prevents the "every cut is just the global style repeated"
        # problem. Still degrades gracefully to the old behavior on any LLM failure.
        shot_plans = {}
        if s.get("director_enabled", True):
            from backend.services.music_video_director import DIRECTOR_MODEL, _is_embedding_model
            director_model = s.get("director_model") or DIRECTOR_MODEL
            if _is_embedding_model(director_model):
                logging.getLogger(__name__).warning("overriding bad director_model=%s (embedding model cannot chat) -> %s", director_model, DIRECTOR_MODEL)
                director_model = DIRECTOR_MODEL
            # When a trained character is cast, identity (trigger + bible) is injected
            # into every keyframe by _keyframe_loras_and_prompt. Tell the Director NOT to
            # describe the hero's appearance — otherwise it free-styles hair/face per cut
            # and fights the lock (the "hair length drifts every scene" failure).
            _dir_guidance = s.get("director_guidance")
            if s.get("use_lora_consistency") and (s.get("subject_ids") or s.get("loras") or s.get("lora_paths")):
                _lock_note = ("A trained character is locked into every shot by a separate identity "
                              "system. Do NOT describe the main character's face, hair, skin, eyes, or "
                              "build — only their pose, action, wardrobe, framing, camera, lighting, and "
                              "setting. Refer to them only as 'the figure' or 'she/he' if needed.")
                _dir_guidance = f"{_dir_guidance}\n{_lock_note}" if _dir_guidance else _lock_note
            # Unified Director (SONG_CUTPLAN). `plan` (the cut plan) shadows the imported
            # function name, so alias it. creativity=None ⇒ engine-default sampling: this is a
            # BYTE-IDENTICAL migration of the fragile MV path (still temp 0.7/0.65). We can flip
            # MV to the CREATIVE profile later as a separate, tested change.
            from backend.services.director_service import plan as director_plan, DirectorBrief, DirectorMode
            _lyrics_note = _song_lyrics_guidance(mv)
            if _lyrics_note:
                _dir_guidance = f"{_dir_guidance}\n{_lyrics_note}" if _dir_guidance else _lyrics_note
            _res = director_plan(DirectorBrief(
                mode=DirectorMode.SONG_CUTPLAN,
                style=mv.style_prompt,
                cut_plan=plan,
                creativity=None,
                model=director_model,
                planning_mode=s.get("planning_mode", "narrative"),
                extra_guidance=_dir_guidance,
                user_treatment=s.get("user_treatment") or s.get("director_treatment"),
                max_stretch=float(s.get("max_stretch", 2.0)),
                fill_method=s.get("fill_method"),
            ))
            # Use the engine's native dict so every downstream read below is unchanged.
            result = _res.raw if _res.raw is not None else {"prompts": [], "treatment": _res.treatment}
            # P0 guard application (story-arc plan): ensure the prompts we store for
            # storyboards + i2v are distinct + energy-aware even on marginal LLM output.
            # Also the natural place to (in future) pass stretch context for duration suggestions.
            raw_prompts = result.get("prompts") or []
            guarded = raw_prompts
            try:
                from backend.services.music_video_director import _ensure_distinct_and_energy_aware
                guarded = _ensure_distinct_and_energy_aware(
                    raw_prompts, plan, mv.style_prompt,
                    max_stretch=float(s.get("max_stretch", 2.0)),
                )
            except Exception:  # noqa: BLE001 — guard is best-effort
                pass
            if guarded:
                result["prompts"] = guarded  # feed the guarded list downstream

            # If a rich user-provided treatment was supplied, we can also store the
            # raw treatment the Director produced/refined so the UI can show the
            # final version the agent settled on.
            if result.get("treatment"):
                s = dict(mv.settings_json or {})
                s["director_treatment"] = result.get("treatment")
                mv.settings_json = s
            prompts = result["prompts"]
            treatment = result.get("treatment")
            shot_plans = {s.get("index"): s for s in (result.get("shots") or []) if isinstance(s, dict)}
            if treatment:
                s = dict(mv.settings_json or {})
                s["director_treatment"] = treatment
                mv.settings_json = s
            # Store any director diagnostics (fallback reason, raw head, etc.) so _mv_dict
            # can surface them in the UI for the "why are my prompts not unique?" case.
            if result.get("director_diagnostics"):
                ss = dict(mv.settings_json or {})
                ss["director_diagnostics"] = result.get("director_diagnostics")
                mv.settings_json = ss
        else:
            prompts = [mv.style_prompt] * len(plan)

        # Enrich clips with Director's editing decisions (duration, transition, filter)
        # so the final Shotcut assembly can use real cinematic editing instead of hard-coded hard-cuts.
        mv.song_path = song  # cache the resolved path for later stages
        mv.cut_plan = plan
        mv.clips = []
        for c in plan:
            idx = c["index"]
            sp = shot_plans.get(idx, {})
            # Prefer the unique visual prompt from the detailed shot plan (produced by the Director from the treatment)
            # over the flat prompts list. This ensures we get the per-cut variation the model was instructed to create.
            # The Director is told the caller appends the global style, so do it here; the flat
            # `prompts` list already carries it from _ensure_distinct_and_energy_aware.
            shot_prompt = sp.get("prompt") or (prompts[idx] if idx < len(prompts) else mv.style_prompt)
            if sp.get("prompt") and mv.style_prompt:
                style_suffix = f", {mv.style_prompt}" if not mv.style_prompt.startswith(",") else mv.style_prompt
                if not shot_prompt.rstrip().endswith(style_suffix.strip()):
                    shot_prompt = f"{shot_prompt.rstrip().rstrip(',')}{style_suffix}"
            clip = {
                "index": idx,
                "start": c["start_s"],
                "end": c["end_s"],
                "clip_path": None,
                "status": "pending",
                "prompt": shot_prompt,
                "duration_seconds": sp.get("duration_seconds"),
                "transition_to_next": sp.get("transition_to_next"),
                "filter_preset": sp.get("filter_preset"),
            }
            mv.clips.append(clip)
        db.session.commit()
        log.info("music_video %s analyzed: %d cuts over %.1fs (director=%s, has_treatment=%s)",
                 mv_id, len(plan), structure["duration_seconds"], s.get("director_enabled", True),
                 bool(mv.settings_json.get("director_treatment") if isinstance(mv.settings_json, dict) else False))


# --- Stage: generating (self-re-dispatching, one clip per invocation) --------

def run_clip_generator(mv_id: int):
    """Generate ONE pending clip, then tail-call self. When none remain, advance
    generating → assembling and dispatch the assembler.

    Idempotent/crash-safe: a clip counts as done only if status=='done' AND its
    file exists on disk (a half-written file from a crash re-generates)."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or mv.current_stage != "generating":
        return

    if (mv.status or "").startswith("cancelled"):
        log.info(f"Music video {mv_id} is cancelled; stopping clip generation")
        return

    clips = list(mv.clips or [])
    target = None
    for c in clips:
        if c.get("status") == "cancelled":
            continue
        on_disk = c.get("clip_path") and os.path.exists(c["clip_path"])
        if not (c.get("status") == "done" and on_disk):
            target = c
            break

    if target is None:
        # All clips done → advance + dispatch assembler (atomic; race-safe).
        svc = MusicVideoService(db.session)
        if svc.advance_if_predecessor(mv_id, expected_predecessor="generating"):
            from backend.celery_app import celery
            celery.send_task("music_video.run_assembler", args=[mv_id])
        return

    from backend.celery_app import celery
    from backend.services.job_operation_gate import GpuBusyError
    try:
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "generating")
        _generate_one_clip(mv, target)
    except (GpuBusyError, PluginUnavailable) as e:
        # TRANSIENT — the GPU gate is cooling down / busy, or the plugin is still
        # coming up. Re-dispatch this same clip after the cooldown clears, but
        # only for so long: a disabled plugin or a model that never fits would
        # otherwise re-dispatch every 12 s forever.
        since = float(target.get("deferred_since") or 0) or time.time()
        if time.time() - since > CLIP_DEFER_MAX_S:
            log.error("music_video %s clip %s could not start within %ss: %s",
                      mv_id, target.get("index"), CLIP_DEFER_MAX_S, e)
            MusicVideoService(db.session).fail_stage(
                mv_id, stage="generating",
                error=f"clip {target.get('index')} waited {int(CLIP_DEFER_MAX_S // 60)} min for the GPU/plugin: {e}",
            )
            return
        target["deferred_since"] = since
        mv.clips = clips
        db.session.commit()
        log.info("music_video %s clip %s deferred (transient): %s", mv_id, target.get("index"), e)
        celery.send_task("music_video.run_clip_generator", args=[mv_id], countdown=GPU_COOLDOWN_RETRY_S)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("music_video %s clip %s generation failed", mv_id, target.get("index"))
        MusicVideoService(db.session).fail_stage(mv_id, stage="generating", error=str(e))
        return

    # Continue with the next clip — but AFTER the GPU gate's release cooldown, so
    # the next clip doesn't immediately trip "GPU cooling down". Re-queues at the
    # back, so other work interleaves between clips rather than starving.
    celery.send_task("music_video.run_clip_generator", args=[mv_id], countdown=GPU_COOLDOWN_RETRY_S)


def _generate_one_clip(mv: MusicVideo, clip: dict):
    """(Optional pre-curated storyboard still) → WAN i2v → fill-to-duration for a single cut.

    If the clip has a "storyboard_path" from the earlier "Generate Storyboards" review
    phase (and it exists), we reuse that reviewed keyframe as the i2v init image instead
    of re-generating a fresh still. This supports the thumbnails-first + individual
    regen workflow.

    GPU work (still or i2v) is wrapped in the JobOperationGate's VIDEO_RENDER slot
    so it serializes against training/other renders on the shared card. The ffmpeg
    fill is CPU-only and runs OUTSIDE the gate (don't hold the GPU for ffmpeg).

    We build the VideoGenerationRequest directly (rather than via the
    Wan22I2VGenerator adapter) because this path threads extra knobs the adapter
    doesn't expose — i2v_width/height, num_inference_steps, interpolation_multiplier.
    Result-path resolution (generate_video returns video_path RELATIVE to
    request.output_dir) is shared with the adapters via resolve_generated_video_path;
    we set output_dir to our own clip dir so the base is known."""
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services.comfyui_video_generator import (
        get_video_generator, VideoGenerationRequest, resolve_generated_video_path,
    )
    from backend.services.gpu_resource_policy import gpu_session
    from backend.services.job_types import JobKind

    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    s = _settings(mv)
    idx = clip["index"]
    clip_count = max(1, len(mv.clips or []))
    process_id = f"mv_{mv.id}_{idx}"
    progress = get_unified_progress()
    progress.create_process(
        ProcessType.VIDEO_RENDER,
        f"Music video «{mv.name}» clip {idx + 1}/{clip_count}",
        process_id=process_id,
        additional_data={
            "music_video_id": mv.id,
            "clip_index": idx,
            "kind": "music_video_clip",
        },
    )
    # Per-cut Director prompt (set in run_analyzer); falls back to the global style for
    # rows seeded before the Director existed or when the Director was disabled.
    clip_prompt = clip.get("prompt") or mv.style_prompt
    out_dir = _clip_dir(mv.id)
    still_path = str(out_dir / f"still_{idx}.png")
    final_path = str(out_dir / f"clip_{idx}.mp4")
    base_slot_s = float(clip["end"]) - float(clip["start"])
    # Planner (Director) can suggest artistic duration for this shot (longer for drama, shorter for punch).
    # This controls only the *generated motion* length for the i2v call (saves VRAM/compute on long dreamy shots).
    # The *filled output clip file* is always produced at exactly the full timeline slot length so that
    # the MLT assembly (source_out + timeline slots) matches the actual media duration on disk. This
    # contract is required for audio sync and to prevent timing drift or underruns in the .mlt.
    suggested = clip.get("duration_seconds")
    max_src = base_slot_s * float(s.get("max_stretch", 2.0))
    if suggested and 0.5 < float(suggested) <= max_src:
        motion_len_s = float(suggested)
    else:
        # P1: default to a mild stretch target (1.3x) so the final motion after fill
        # feels intentional rather than 1:1 or fully clamped by the i2v engine max.
        ideal = base_slot_s / 1.3
        motion_len_s = max(0.5, min(ideal, max_src, base_slot_s))
    fill_target_s = base_slot_s   # always the full cut slot for the final clip_*.mp4
    out_fps = s["fps"]   # final clip fps (the fill step re-times to this)

    # Engine / model selection. Full model id (wan22-14b-i2v etc.) is now first-class
    # so users can pick via GUI (similar to VideoGeneratorPage). The still (keyframe)
    # is generated first (SDXL path when LoRA consistency is on), then fed to the chosen I2V.
    # Wan 2.2 I2V is preferred for quality when the user has the GPU budget.
    prof = _clip_profile(s)
    i2v_model = prof["model"]
    # The model's own rate: Wan 2.2 14B is a 16fps model, CogVideoX 7fps, a
    # registry model whatever it declares. Tagging frames at the wrong rate
    # played motion sped-up or slowed against the Video Generator page.
    i2v_fps = prof["fps"]
    native_audio = prof["audio_out"]
    if native_audio:
        # The model renders picture and sound for the whole cut in one pass, so
        # the motion length is the cut itself (capped at its longest clip); the
        # fill step then trims to the slot and keeps the song as the master
        # track. The song slice anchors the clip so its motion lands on the beat.
        motion_len_s = min(base_slot_s, prof["max_frames"] / i2v_fps)
    frames = max(prof["min_frames"], min(prof["max_frames"], int(round(motion_len_s * i2v_fps)) or prof["max_frames"]))

    # If the user has already curated storyboards (via the "thumbnails first" review
    # flow), reuse the reviewed storyboard image as the init for i2v instead of
    # re-generating a fresh still. This honors individual storyboard approvals/regens.
    pregen_storyboard = clip.get("storyboard_path")
    use_pregen_storyboard = bool(pregen_storyboard and os.path.exists(pregen_storyboard))
    if use_pregen_storyboard:
        img = pregen_storyboard
    else:
        img = None  # will be set by still generation below

    # gpu_session = the unified front door: claims the JobOperationGate slot (same
    # fail-fast GpuBusyError + 8s cooldown) and, once we hold it, evicts Ollama so an
    # active chat's resident gemma (~5min keep_alive) can't fight WAN for the 16GB
    # card — the chat engine + training already do this before heavy GPU work; the
    # music-video render previously didn't (documented gap). The mid-session
    # _comfyui_free_vram() below stays explicit (the FLUX→i2v evict is mid-block).
    # vram_estimate_mb makes this keyframe+i2v render visible to the GPU orchestrator's
    # budget so it evicts competing in-process models instead of both fighting for 16GB.
    from backend.services.video_model_registry import vram_mb_for_model
    mv_vram_mb = vram_mb_for_model(i2v_model, default=14000)
    try:
        with gpu_session(
            JobKind.VIDEO_RENDER,
            f"mv_{mv.id}_{idx}",
            evict_ollama=True,
            free_comfyui=True,
            vram_estimate_mb=mv_vram_mb,
            require_fit=True,
            cross_process=True,
            slot_id=f"video_render:mv_{mv.id}_{idx}",
            lease_seconds=3600,
        ):
            if not use_pregen_storyboard:
                progress.update_process(
                    process_id, 5,
                    f"Clip {idx + 1}/{clip_count}: generating keyframe still…",
                )
                kf_steps = s.get("keyframe_steps") or 45
                kf_loras, kf_sids, kf_prompt = _keyframe_cast_context(mv, s, clip_prompt)
                kf_lora_strength = _keyframe_lora_strength(s)
                if kf_loras:
                    from backend.services.character_still_pipeline import render_character_still
                    still = render_character_still(
                        kf_prompt,
                        subject_ids=kf_sids or None,
                        lora_paths=kf_loras or None,
                        include_bible=True,
                        source="musicvideo",
                        width=s["still_width"],
                        height=s["still_height"],
                        steps=kf_steps,
                        seed=1000 + idx,
                        output_path=still_path,
                        lora_strength=kf_lora_strength,
                        keep_pipeline=False,
                    )
                    if not still.success:
                        raise RuntimeError(still.error or "music-video keyframe still failed")
                    img = still.image_path
                else:
                    vg = get_video_generator()
                    if not getattr(vg, "service_available", True):
                        try:
                            vg.service_available = vg._check_comfyui_connection()
                        except Exception:
                            vg.service_available = False
                    if not getattr(vg, "service_available", True):
                        raise RuntimeError(
                            "ComfyUI unavailable for music-video keyframe/i2v (start it or free VRAM)."
                        )
                    img = ComfyUIImageGenerator(
                        lora_strength=kf_lora_strength,
                        flux_unet=s.get("flux_unet"),
                        flux_t5=s.get("flux_t5"),
                        flux_clip=s.get("flux_clip"),
                        flux_vae=s.get("flux_vae"),
                    ).generate_image(
                        prompt=kf_prompt, loras=kf_loras, output_path=still_path,
                        width=s["still_width"], height=s["still_height"], seed=1000 + idx,
                        steps=kf_steps,
                        model=s.get("keyframe_model"),
                    )
                _comfyui_free_vram()
            else:
                _comfyui_free_vram()

            progress.update_process(
                process_id, 35,
                f"Clip {idx + 1}/{clip_count}: animating (I2V)…",
            )

            req_kwargs = dict(
                model=i2v_model,
                prompt=clip_prompt,
                duration_frames=frames,
                fps=i2v_fps,
                width=s["i2v_width"],
                height=s["i2v_height"],
                enhance_prompt=False,
                output_dir=out_dir,
                metadata={
                    "image_path": img,
                    "item_id": process_id,
                    "music_video_id": mv.id,
                    "clip_index": idx,
                },
                interpolation_multiplier=int(s["interpolation_multiplier"]),
            )
            if native_audio:
                # Native-audio path: the director's shot becomes the model's
                # structured prompt and the song slice for this cut is anchored
                # at frame 0 so the generated motion follows its beats. The
                # clip's own soundtrack is dropped by the fill step; the song
                # stays the master track.
                from backend.services import h3_prompt_compiler as h3
                song_path = _resolve_song_path(mv)
                intent = h3.intent_from_cut(
                    {"start_s": float(clip["start"]), "end_s": float(clip["end"])},
                    clip_prompt, song_audio_index=1, style=s.get("prompt_style") or "cinematic",
                )
                compiled, diag = h3.compile(intent)
                req_kwargs["prompt"] = compiled
                req_kwargs["h3_intent"] = h3.intent_to_dict(intent)
                req_kwargs["duration_frames"] = diag["frames"]
                req_kwargs["interpolation_multiplier"] = 1
                if song_path and os.path.exists(song_path):
                    req_kwargs["guides"] = [{
                        "kind": "audio", "path": song_path, "frame_idx": 0,
                        "seek_s": float(clip["start"]), "duration_s": base_slot_s,
                    }]
            if s.get("i2v_steps"):
                req_kwargs["num_inference_steps"] = int(s["i2v_steps"])
            req = VideoGenerationRequest(**req_kwargs)
            vg = get_video_generator()
            if not getattr(vg, "service_available", True):
                try:
                    vg.service_available = vg._check_comfyui_connection()
                except Exception:
                    vg.service_available = False
            if not getattr(vg, "service_available", True):
                raise RuntimeError("ComfyUI unavailable for music-video i2v clip render.")
            result = vg.generate_video(req)
            if not result.success or not result.video_path:
                err = result.error or "no video produced"
                if any(kw in (err or "").lower() for kw in ("oom", "out of memory", "cuda")):
                    raise RuntimeError(
                        f"{i2v_model} i2v OOM ({err}). Reduce i2v_steps/resolution, ensure VRAM free "
                        "(Comfy /free), or lower interpolation. See media team audit for preflight."
                    )
                raise RuntimeError(f"{i2v_model} i2v failed: {err}")
            wan_abs = resolve_generated_video_path(result, out_dir)
            if not wan_abs.exists():
                raise RuntimeError(f"WAN output not found at resolved path: {wan_abs}")

        progress.update_process(process_id, 85, f"Clip {idx + 1}/{clip_count}: conforming duration…")

        # Fill to the EXACT cut length (memory #721 sync fix) — CPU ffmpeg, no gate.
    # method=forward keeps motion forward (no moonwalk); max_stretch caps slowdown.
    # fill_target_s (the full slot) guarantees the written clip_*.mp4 duration matches
    # the source_out + timeline slot the assembler will declare for the .mlt.
        fill_clip_to_duration(
            str(wan_abs), fill_target_s, final_path,
            fps=out_fps, width=s["width"], height=s["height"],
            method=s["fill_method"], max_stretch=float(s["max_stretch"]),
        )

        # Persist cursor. DEEP copy then reassign: a shallow list copy shares the
        # dict objects with the stored attribute, so mutating-then-reassigning leaves
        # old == new and SQLAlchemy's JSON column flushes NOTHING (the cursor update
        # would be silently lost — and the clip would regenerate forever). deepcopy
        # makes the new value genuinely differ from the stored one.
        import copy
        clips = copy.deepcopy(mv.clips or [])
        for c in clips:
            if c["index"] == idx:
                c["clip_path"] = final_path
                c["status"] = "done"
                break
        mv.clips = clips
        db.session.commit()
        progress.complete_process(process_id, f"Clip {idx + 1}/{clip_count} complete")
        log.info("music_video %s clip %s done (%.2fs)", mv.id, idx, fill_target_s)
    except Exception as e:
        try:
            progress.error_process(process_id, f"Clip {idx + 1} failed: {e}")
        except Exception:
            pass
        raise


# --- Stage: assembling -------------------------------------------------------

def run_assembler(mv_id: int):
    """Compose the filled clips against their exact cut timestamps with the song
    as the audio track; render the final mp4 via the MLT/melt plugin."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv and (mv.status or "").startswith("cancelled"):
        log.info(f"Music video {mv_id} is cancelled; skipping assemble")
        return

    with _mv_run(mv_id, expected_stage="assembling", next_agent=None) as mv:
        if mv is None:
            return
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "assembling")

        clips = [
            c for c in (mv.clips or [])
            if c.get("status") == "done" and c.get("clip_path") and os.path.exists(c["clip_path"])
        ]
        if not clips:
            raise RuntimeError("no completed clips to assemble")

        s = _settings(mv)
        arrangement_clips = []
        for c in clips:
            # Use the full energy-planned cut length for timeline slot (audio sync).
            # Prefer the *actual* duration of the filled clip file on disk for source_out
            # (defensive against any pre-fix renders or rounding). This ensures the .mlt
            # chains/entries request a play length that the avformat producer can actually deliver.
            base_cut_len = float(c["end"]) - float(c["start"])
            actual_dur = probe_duration(c["clip_path"]) or base_cut_len
            source_out = min(actual_dur, base_cut_len)

            arrangement_clips.append({
                "clip_id": f"mv{mv_id}_{c['index']}",
                "source_path": c["clip_path"],
                "section_label": "",
                "timeline_start": float(c["start"]),
                "timeline_end": float(c["end"]),
                "source_in": 0.0,
                "source_out": source_out,
                "filter_preset": c.get("filter_preset") or "none",
                "transition_to_next": c.get("transition_to_next") or "hard-cut",
            })

        song_duration = mv.cut_plan[-1]["end_s"] if mv.cut_plan else None
        style_recipe = s.get("style_recipe_name", "default")
        body = {
            "arrangement": {"style_recipe_name": style_recipe, "seed": 0, "clips": arrangement_clips},
            "audio_path": mv.song_path,
            "audio_volume": 1.0,
            "song_duration_seconds": song_duration,
            "fps_num": s["fps"], "fps_den": 1,
            "width": s["width"], "height": s["height"],
            "render_mp4": True, "register": True,
        }
        resp = requests.post(f"{PLUGIN_URL}/shotcut/compose-arrangement", json=body, timeout=1800)
        resp.raise_for_status()
        result = resp.json()

        # compose-arrangement registers BOTH the .mlt project AND the rendered .mp4
        # as Documents. Pick the .mp4 — docs[0] is often the .mlt, which made the
        # in-page <video> player point at a timeline file it can't play.
        docs = [d for d in (result.get("documents") or []) if isinstance(d, dict)]
        def _is_mp4(d):
            return str(d.get("path") or d.get("file_path") or d.get("filename") or "").lower().endswith(".mp4")
        mp4_doc = next((d for d in docs if _is_mp4(d)), None) or (docs[0] if docs else None)
        if mp4_doc:
            mv.output_document_id = mp4_doc.get("id")

        # Convenience copy: put a nicely-named .mlt next to the music_video's clips/
        # so the user can easily find and open "the shotcut file" for this project
        # without hunting in the opaque mlt-projects/ hash dir. The XML still uses
        # absolute paths to the clips, so it is self-contained for Shotcut.
        try:
            mlt_path = result.get("mlt_path")
            if mlt_path and os.path.exists(mlt_path):
                nice_mlt = _clip_dir(mv_id).parent / f"music_video_{mv_id}.mlt"
                import shutil
                shutil.copy2(mlt_path, nice_mlt)
                log.info("music_video %s: convenience .mlt also at %s", mv_id, nice_mlt)
        except Exception:  # noqa: BLE001
            pass

        db.session.commit()
        log.info("music_video %s assembled → %s (doc %s)",
                 mv_id, result.get("rendered_mp4"), mv.output_document_id)


# --- Storyboard generator (async, pre-approval review thumbnails) ------------

def _set_storyboard_generating(mv_id: int, *, generating: bool, error: str | None = None) -> None:
    mv = db.session.get(MusicVideo, mv_id)
    if not mv:
        return
    s = dict(mv.settings_json or {})
    s["storyboard_generating"] = generating
    if error:
        s["storyboard_error"] = error
    elif not generating:
        s.pop("storyboard_error", None)
    mv.settings_json = s
    db.session.commit()


def run_storyboard_generator(mv_id: int, force: bool = False):
    """Generate storyboard keyframes for all cuts (Celery worker entry point)."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv:
        log.warning("run_storyboard_generator: mv %s not found", mv_id)
        return
    if mv.current_stage != "awaiting_approval":
        _set_storyboard_generating(
            mv_id, generating=False,
            error=f"storyboard generation only from awaiting_approval, current={mv.current_stage}",
        )
        return

    from backend.services.plugin_bridge import ensure_plugins_for_stage, prepare_plugins_for_route
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services.comfyui_video_generator import get_video_generator
    from backend.services.gpu_resource_policy import gpu_session, free_comfyui_vram
    from backend.services.job_types import JobKind
    from backend.services.job_operation_gate import GpuBusyError
    import copy

    ensure_plugins_for_stage("music-video", "storyboard")
    try:
        prepare_plugins_for_route("/music-video/storyboard")
    except Exception:
        log.warning("Failed to prepare plugins for music-video storyboard (non-fatal)", exc_info=True)

    s = _settings(mv)
    clips = copy.deepcopy(mv.clips or [])
    out_dir = _clip_dir(mv_id)
    kf_lora_strength = _keyframe_lora_strength(s)

    try:
        vg = get_video_generator()
        if not getattr(vg, "service_available", True):
            vg.service_available = getattr(vg, "_check_comfyui_connection", lambda: False)()
    except Exception:
        pass

    error_msg = None
    try:
        with gpu_session(JobKind.VIDEO_RENDER, f"mv_storyboards_{mv_id}", evict_ollama=True,
                         free_comfyui=True, vram_estimate_mb=10000, require_fit=True):
            for c in clips:
                has_existing = bool(c.get("storyboard_path") and os.path.exists(c.get("storyboard_path")))
                if has_existing and not force:
                    continue
                prompt = c.get("prompt") or mv.style_prompt
                idx = c["index"]
                still_path = str(out_dir / f"storyboard_{idx}.png")
                try:
                    kf_loras, kf_sids, kf_prompt = _keyframe_cast_context(mv, s, prompt)
                    if kf_loras:
                        from backend.services.character_still_pipeline import render_character_still
                        still = render_character_still(
                            kf_prompt,
                            subject_ids=kf_sids or None,
                            lora_paths=kf_loras or None,
                            include_bible=True,
                            source="musicvideo",
                            width=s.get("still_width", 1024),
                            height=s.get("still_height", 576),
                            steps=s.get("keyframe_steps") or 20,
                            seed=2000 + idx,
                            output_path=still_path,
                            lora_strength=kf_lora_strength,
                            keep_pipeline=True,
                        )
                        if not still.success:
                            raise RuntimeError(still.error or "storyboard still failed")
                    else:
                        gen = ComfyUIImageGenerator(
                            lora_strength=kf_lora_strength,
                            flux_unet=s.get("flux_unet"),
                            flux_t5=s.get("flux_t5"),
                            flux_clip=s.get("flux_clip"),
                            flux_vae=s.get("flux_vae"),
                        )
                        gen.generate_image(
                            prompt=kf_prompt,
                            loras=kf_loras,
                            output_path=still_path,
                            width=s.get("still_width", 1024),
                            height=s.get("still_height", 576),
                            seed=2000 + idx,
                            steps=s.get("keyframe_steps") or 20,
                            model=s.get("keyframe_model"),
                        )
                    c["storyboard_path"] = still_path
                    c["storyboard_variation"] = None
                except RuntimeError as e:
                    log.error("run_storyboard_generator ComfyUI failure for mv %s cut %s: %s", mv_id, idx, e)
                except Exception as e:
                    log.warning("storyboard still failed for mv %s cut %s: %s", mv_id, idx, e)
            free_comfyui_vram()
    except GpuBusyError as e:
        error_msg = f"GPU busy, cannot generate storyboards right now: {e}"
    except Exception as e:
        log.exception("run_storyboard_generator failed for mv %s", mv_id)
        error_msg = str(e)
    else:
        mv = db.session.get(MusicVideo, mv_id)
        if mv:
            mv.clips = clips
            db.session.commit()

    _set_storyboard_generating(mv_id, generating=False, error=error_msg)


# --- Celery factory ----------------------------------------------------------

def create_music_video_tasks(celery_app: Celery):
    @celery_app.task(name="music_video.run_analyzer")
    def run_analyzer_task(mv_id: int):
        with current_app.app_context():
            run_analyzer(mv_id)

    @celery_app.task(name="music_video.run_clip_generator")
    def run_clip_generator_task(mv_id: int):
        with current_app.app_context():
            run_clip_generator(mv_id)

    @celery_app.task(name="music_video.run_assembler")
    def run_assembler_task(mv_id: int):
        with current_app.app_context():
            run_assembler(mv_id)

    @celery_app.task(name="music_video.run_storyboard_generator")
    def run_storyboard_generator_task(mv_id: int, force: bool = False):
        with current_app.app_context():
            run_storyboard_generator(mv_id, force=force)

    return {
        "run_analyzer": run_analyzer_task,
        "run_clip_generator": run_clip_generator_task,
        "run_assembler": run_assembler_task,
        "run_storyboard_generator": run_storyboard_generator_task,
    }
