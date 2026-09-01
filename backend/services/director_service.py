"""Unified Director / Storyboard service — one planner for all three video features.

Guaardvark historically grew THREE separate "director" subsystems that all do the same
job (turn a high-level brief into per-clip VISUAL prompts via an Ollama JSON call) in three
different shapes:

  * ``music_video_director`` — song cut-plan -> treatment + per-cut prompts (the richest;
    owns the batching / tolerant-parse / energy-arc machinery that survived real failures).
  * ``video_director``       — batch sibling: enrich N independent prompts / expand 1 concept.
  * ``swarm/agents/{screenwriter,cinematographer}`` — film-crew script -> shot plans (the
    weakest per-shot step: single call, no treatment, no continuity guard).

This module is the single seam they converge on. It does NOT re-implement the vetted
primitives — it *delegates* to ``music_video_director`` (SONG_CUTPLAN engine + helpers) and
``media_director`` (CONCEPT_EXPANSION / PROMPT_LIST engines), and adds ONE new batched
planner for the film-crew SCRIPT_SCENES path. On top of that it centralizes the two things
that were divergent everywhere:

  1. **Cast identity-lock at PLAN time** — trigger word(s) + bible front-loaded onto every
     shot prompt, uniformly, replacing the three render-time injection sites. Empty cast is a
     no-op, so callers that still inject at render time are unaffected until they migrate.
  2. **Sampling** — story generation is routed through ``sampling_profiles.CREATIVE`` (the
     profile literally designed for story gen) instead of three hardcoded temperatures.
     NOTE: the CREATIVE wiring for the delegated engines lands in a follow-up step
     (additive ``sampling=`` param on those engines); the new SCRIPT_SCENES planner here
     already honors it.

``plan()`` never raises — every branch degrades to a best-effort result, matching the
never-raise contract the downstream renderers and the film-crew state machine depend on.

DESIGN GUARDRAILS (see plan cozy-growing-grove.md):
  * Planning != rendering: this module must NEVER import a video generator (there are three
    ``get_video_generator`` symbols — picking one here would be a footgun).
  * Always import the film-crew agents by full path (``backend.services.swarm.agents...``) —
    ``backend/services/swarm/`` collides by leaf-name with the unrelated ``plugins/swarm/``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from backend.services import sampling_profiles
from backend.services import media_director
from backend.services.music_video_director import (
    DIRECTOR_MODEL,
    _resolve_model,
    _director_chat,
    _generate_storyline_and_prompts,
)

log = logging.getLogger(__name__)


class DirectorMode(str, Enum):
    """Which input shape the brief carries — selects the engine ``plan()`` dispatches to."""
    SONG_CUTPLAN = "song_cutplan"            # music video: timed cuts + beat energy
    CONCEPT_EXPANSION = "concept_expansion"  # one idea -> N distinct-but-connected shots
    PROMPT_LIST = "prompt_list"              # enrich N independent prompts
    SCRIPT_SCENES = "script_scenes"          # film-crew scene/shot breakdown -> shot plans


@dataclass
class CastMember:
    """A trained cast identity. ``trigger_word`` + ``bible`` are what lock appearance; the
    LoRA fields ride along so a future unified renderer can resolve them in one place."""
    trigger_word: str
    bible: Optional[str] = None
    lora_path: Optional[str] = None
    lora_strength: Optional[float] = None
    name: Optional[str] = None


@dataclass
class DirectorBrief:
    """The unified input. Exactly one mode payload is populated per ``mode``."""
    mode: DirectorMode
    style: str = ""                                   # global look/feel (style_prompt)
    creativity: str = sampling_profiles.CREATIVE      # sampling profile name
    cast: list[CastMember] = field(default_factory=list)
    extra_guidance: Optional[str] = None
    user_treatment: Optional[str] = None
    planning_mode: str = "narrative"                  # narrative | visual/mood_arc
    model: Optional[str] = None
    # --- mode-specific payloads (exactly one populated) ---
    cut_plan: Optional[list[dict]] = None             # SONG_CUTPLAN
    concept: Optional[str] = None                     # CONCEPT_EXPANSION
    num_shots: Optional[int] = None                   # CONCEPT_EXPANSION
    prompts: Optional[list[str]] = None               # PROMPT_LIST
    scenes: Optional[list[dict]] = None               # SCRIPT_SCENES (from screenwriter)
    subjects: Optional[list[dict]] = None             # SCRIPT_SCENES (id/name/description)
    # --- optional MV stretch context (passthrough) ---
    max_stretch: Optional[float] = None
    fill_method: Optional[str] = None


@dataclass
class ShotPrompt:
    """One planned shot. ``prompt`` is always pure-visual (+ cast lock prepended). The rest
    are populated when the engine supplies them (camera/framing/etc. for SCRIPT_SCENES,
    energy/duration for SONG_CUTPLAN)."""
    prompt: str
    index: Optional[int] = None
    duration: Optional[float] = None
    energy: Optional[float] = None
    camera: Optional[str] = None
    framing: Optional[str] = None
    mood: Optional[str] = None
    subjects: Optional[list[int]] = None
    transition_to_next: Optional[str] = None
    filter_preset: Optional[str] = None
    # Spoken lines for models that render dialogue (MiniMax H3): copied from
    # the screenwriter's shot, never written into ``prompt``, which stays
    # pure-visual. Each entry: {"speaker", "text", "lang"?}.
    dialogue: Optional[list[dict]] = None
    speaker: Optional[str] = None


@dataclass
class DirectorResult:
    treatment: Optional[str]
    shots: list[ShotPrompt]
    diagnostics: Optional[dict] = None
    # Native engine dict, passed through verbatim. SONG_CUTPLAN callers use this for a
    # byte-identical migration (they keep reading result["prompts"]/director_diagnostics).
    raw: Optional[dict] = None


# --------------------------------------------------------------------------- cast helpers

# Lifted verbatim from music_video_tasks' plan-time guidance so the director stops "fighting"
# the trained token by re-describing the face. Applied whenever a brief carries cast.
_CAST_GUIDANCE_NOTE = (
    "The hero character(s) are locked by a trained identity token; do NOT describe their "
    "facial features, hair color, eye color, or body type — those are fixed by the token. "
    "Describe only their action, wardrobe, pose, framing, lighting, and environment."
)


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        s = (s or "").strip()
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _cast_lock(cast: list[CastMember]) -> str:
    """The identity string prepended to every shot prompt: trigger(s) then bible(s), in the
    proven music-video order (trigger -> bible -> scene)."""
    triggers = _dedupe([c.trigger_word for c in cast if c.trigger_word])
    bibles = _dedupe([c.bible for c in cast if c.bible])
    return ", ".join([*triggers, *bibles])


def _has_cast(cast: list[CastMember]) -> bool:
    return any(c.trigger_word for c in cast)


def _cast_descriptors(cast: list[CastMember]) -> Optional[list[str]]:
    """Visual descriptors (bible or name) for engines that weave consistency in softly
    (media_director.enhance_prompts). Distinct from the hard trigger lock."""
    descs = _dedupe([(c.bible or c.name or "") for c in cast])
    return descs or None


def _apply_lock(prompt: str, lock: str) -> str:
    p = (prompt or "").strip()
    if not lock:
        return p
    return f"{lock}, {p}" if p else lock


def _merge_guidance(brief: DirectorBrief) -> Optional[str]:
    parts = []
    if brief.extra_guidance and brief.extra_guidance.strip():
        parts.append(brief.extra_guidance.strip())
    if _has_cast(brief.cast):
        parts.append(_CAST_GUIDANCE_NOTE)
    return "  ".join(parts) if parts else None


def _cut_get(cut: dict, *keys: str) -> Any:
    for k in keys:
        if isinstance(cut, dict) and cut.get(k) is not None:
            return cut.get(k)
    return None


def _sampling(brief: DirectorBrief) -> Optional[dict]:
    """Pure sampling-knob dict for this brief's creativity profile (default CREATIVE — the
    profile designed for story generation). Window/budget keys (num_ctx/num_predict) are NOT
    here: every engine keeps its own batch-sized budget (the gemma JSON-truncation fix).

    ``creativity=None`` ⇒ return None ⇒ engines fall back to their built-in sampling. This is
    how a caller requests a BYTE-IDENTICAL migration (e.g. the fragile music-video path) — no
    sampling change, pure call-site indirection."""
    if not brief.creativity:
        return None
    return sampling_profiles.get_profile(brief.creativity)


# --------------------------------------------------------------------------- public entry

def plan(brief: DirectorBrief) -> DirectorResult:
    """Mode-dispatched director. Never raises; returns a best-effort DirectorResult."""
    try:
        if brief.mode == DirectorMode.SONG_CUTPLAN:
            return _plan_song(brief)
        if brief.mode == DirectorMode.CONCEPT_EXPANSION:
            return _plan_concept(brief)
        if brief.mode == DirectorMode.PROMPT_LIST:
            return _plan_prompt_list(brief)
        if brief.mode == DirectorMode.SCRIPT_SCENES:
            return _plan_script_scenes(brief)
    except Exception as e:  # noqa: BLE001 — director is best-effort, never sink a caller
        log.warning("director_service.plan(%s) failed (%s); empty result", brief.mode, e)
    return DirectorResult(treatment=None, shots=[], diagnostics={"reason": "plan_error"})


# --------------------------------------------------------------------------- SONG_CUTPLAN

def _plan_song(brief: DirectorBrief) -> DirectorResult:
    """Delegates to the music-video engine (the only one with batching + energy-arc). Returns
    the native dict in ``.raw`` so the MV adapter can stay byte-identical."""
    cut_plan = brief.cut_plan or []
    raw = _generate_storyline_and_prompts(
        brief.style,
        cut_plan,
        model=_resolve_model(brief.model or DIRECTOR_MODEL),
        planning_mode=brief.planning_mode,
        extra_guidance=_merge_guidance(brief),
        user_treatment=brief.user_treatment,
        max_stretch=brief.max_stretch,
        fill_method=brief.fill_method,
        sampling=_sampling(brief),
    )
    prompts = raw.get("prompts") or []
    lock = _cast_lock(brief.cast)
    shots = [
        ShotPrompt(
            prompt=_apply_lock(p, lock),
            index=i,
            energy=_cut_get(cut_plan[i], "energy") if i < len(cut_plan) else None,
            duration=_cut_get(cut_plan[i], "duration", "length", "dur") if i < len(cut_plan) else None,
        )
        for i, p in enumerate(prompts)
    ]
    return DirectorResult(
        treatment=raw.get("treatment") or raw.get("storyline"),
        shots=shots,
        diagnostics=raw.get("director_diagnostics"),
        raw=raw,
    )


# --------------------------------------------------------------------- CONCEPT / PROMPT_LIST

def _plan_concept(brief: DirectorBrief) -> DirectorResult:
    res = media_director.storyboard_from_concept(
        brief.concept or "",
        brief.num_shots or 1,
        style=brief.style,
        extra_guidance=_merge_guidance(brief) or brief.user_treatment,
        model=brief.model,
        sampling=_sampling(brief),
    )
    prompts = res.get("prompts") or []
    lock = _cast_lock(brief.cast)
    shots = [ShotPrompt(prompt=_apply_lock(p, lock), index=i) for i, p in enumerate(prompts)]
    return DirectorResult(treatment=res.get("treatment"), shots=shots, raw=res)


def _plan_prompt_list(brief: DirectorBrief) -> DirectorResult:
    out = media_director.enhance_prompts(
        brief.prompts or [],
        style=brief.style,
        extra_guidance=_merge_guidance(brief),
        model=brief.model,
        cast_descriptors=_cast_descriptors(brief.cast),
        sampling=_sampling(brief),
    )
    lock = _cast_lock(brief.cast)
    shots = [ShotPrompt(prompt=_apply_lock(p, lock), index=i) for i, p in enumerate(out)]
    return DirectorResult(treatment=None, shots=shots, raw={"prompts": out})


# --------------------------------------------------------------------------- SCRIPT_SCENES

_SCRIPT_SCENES_SYSTEM = """You are a cinematographer for an AI video generator.
Given a TREATMENT/script broken into SCENES and SHOTS, plus a list of SUBJECTS (characters/props,
each with an integer id), produce ONE shot-ready VISUAL prompt per shot AND its camera/framing.

Each shot MUST:
- Be PURE visual: subject action, setting, camera (lens/angle/movement), lighting, color palette,
  mood, and the motion across the clip. No narration, no character NAMES, no dialogue, no quotes.
- Keep world, lighting, and character continuity across the whole sequence.
- Reference subjects ONLY by their given integer ids in "subjects_in_shot".

Return STRICT JSON:
{"treatment": "<one short evocative paragraph>",
 "shots": [{"index": <int>, "prompt": "<pure visual>", "camera_angle": "<e.g. low angle>",
            "framing": "<e.g. medium close-up>", "mood": "<e.g. tense>",
            "duration_seconds": <number>, "subjects_in_shot": [<ids>]}, ...]}
with exactly one entry per input shot, in order. No commentary outside the JSON."""


def _script_dialogue(scenes: list[dict]) -> dict[int, tuple]:
    """Shot index → (character_name, dialogue) in the same flattened order
    _build_script_user numbers the shots, so a planned shot keeps its line."""
    out: dict[int, tuple] = {}
    n = 0
    for sc in scenes or []:
        for sh in sc.get("shots", []) or []:
            line = (sh.get("dialogue") or sh.get("dialogue_text") or "").strip()
            if line:
                out[n] = (sh.get("character_name") or sh.get("speaker") or None, line)
            n += 1
    return out


def _build_script_user(scenes: list[dict], subjects: list[dict]) -> tuple[str, int]:
    """Flatten the screenwriter breakdown into a compact user prompt; returns (text, n_shots)."""
    subj_lines = []
    for s in subjects or []:
        sid = s.get("id", s.get("index"))
        name = s.get("name") or s.get("character") or "?"
        desc = s.get("description") or s.get("appearance") or ""
        subj_lines.append(f"  [{sid}] {name}: {desc}".rstrip())

    shot_lines = []
    n = 0
    for sc in scenes or []:
        loc = sc.get("location") or sc.get("setting") or ""
        mood = sc.get("mood") or ""
        for sh in sc.get("shots", []) or []:
            desc = sh.get("description") or sh.get("action") or sh.get("prompt") or ""
            in_shot = sh.get("subjects_in_shot") or sh.get("subjects") or sh.get("characters") or []
            shot_lines.append(
                f"  shot {n}: {desc} | scene_loc: {loc} | scene_mood: {mood} | subjects: {in_shot}"
            )
            n += 1

    text = (
        "SUBJECTS:\n" + ("\n".join(subj_lines) if subj_lines else "  (none)") +
        f"\n\nSHOTS ({n}), in order:\n" + ("\n".join(shot_lines) if shot_lines else "  (none)") +
        "\n\nTASK: Return ONLY the JSON with a 'treatment' and exactly "
        f"{n} 'shots' (one per input shot, same order)."
    )
    return text, n


def _extract_shot_meta(raw_content: str) -> dict[int, dict]:
    """Best-effort pull of the rich per-shot fields (camera/framing/mood/duration/subjects)
    that the shared tolerant parser drops. Keyed by shot index. Returns {} on any failure."""
    try:
        c = (raw_content or "").strip()
        start, end = c.find("{"), c.rfind("}")
        if start == -1 or end <= start:
            return {}
        data = json.loads(c[start:end + 1])
        shots = data.get("shots") if isinstance(data, dict) else data
        if not isinstance(shots, list):
            return {}
        meta: dict[int, dict] = {}
        for i, item in enumerate(shots):
            if isinstance(item, dict):
                meta[int(item.get("index", i))] = item
        return meta
    except Exception:  # noqa: BLE001
        return {}


def _plan_script_scenes(brief: DirectorBrief) -> DirectorResult:
    """The film-crew cinematographer upgrade: one batched, treatment-driven call that emits
    pure-visual shot prompts + camera/framing, replacing the terse single-shot agent."""
    user, n = _build_script_user(brief.scenes or [], brief.subjects or [])
    if n == 0:
        return DirectorResult(treatment=None, shots=[], diagnostics={"reason": "no_shots"})

    style_line = f"\nGlobal visual style to honor in every shot: {brief.style}." if brief.style else ""
    guidance = _merge_guidance(brief)
    guide_line = f"\nDirector guidance: {guidance}." if guidance else ""
    user = user + style_line + guide_line

    import ollama
    parsed, raw_content = _director_chat(
        ollama=ollama,
        model=_resolve_model(brief.model or DIRECTOR_MODEL),
        system=_SCRIPT_SCENES_SYSTEM,
        user=user,
        batch_len=n,
        rich=True,
        sampling=_sampling(brief),
    )
    prompts_map: dict[int, str] = parsed.get("prompts") or {}
    meta = _extract_shot_meta(raw_content)
    lock = _cast_lock(brief.cast)
    spoken = _script_dialogue(brief.scenes or [])

    shots: list[ShotPrompt] = []
    for i in range(n):
        p = prompts_map.get(i, "")
        m = meta.get(i, {})
        subj = m.get("subjects_in_shot") or m.get("subjects")
        subj_ids = [int(x) for x in subj if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()] if isinstance(subj, list) else None
        speaker, line = spoken.get(i, (None, None))
        shots.append(ShotPrompt(
            prompt=_apply_lock(p, lock),
            index=i,
            camera=m.get("camera_angle") or m.get("camera"),
            framing=m.get("framing"),
            mood=m.get("mood"),
            duration=m.get("duration_seconds") if isinstance(m.get("duration_seconds"), (int, float)) else None,
            subjects=subj_ids,
            dialogue=[{"speaker": speaker or "the character", "text": line}] if line else None,
            speaker=speaker,
        ))

    # Signal a thin/failed result so the caller can fall back to the swarm Cinematographer.
    diagnostics = None
    if not prompts_map:
        diagnostics = {"reason": "empty", "raw_head": (raw_content or "")[:200]}
    return DirectorResult(treatment=parsed.get("treatment"), shots=shots, diagnostics=diagnostics)
