"""Shared character identity-lock helper — the single source of truth for how a trained
cast member's LoRA + trigger + bible are applied to a generation prompt.

Three video features independently grew the same logic:
  * music-video  (music_video_tasks._keyframe_loras_and_prompt) — trigger + bible + strength;
  * film-crew    (production_swarm_tasks._shot_loras_and_prompt)  — trigger only;
  * batch        — none (bare lora_name passthrough).

This module unifies that. **Injection stays at RENDER time on purpose** — that is strictly more
robust than injecting at plan/storyboard time:
  - it survives a user editing a clip/shot prompt afterward (the trigger can never be lost);
  - it is naturally PER-SHOT scoped, so a multi-character production only locks the characters
    actually present in a given shot (a global plan-time cast would over-lock every shot).
The Director already receives a "do NOT describe the hero's face" guidance note, so plan-time
injection's only real benefit is already captured without giving up render-time's guarantees.

The pieces:
  * ``resolve_lora_strength(model, override)`` — model-aware default strength (Z-Image ~0.9,
    FLUX-dev ~0.9, SDXL rank-16 ~0.25); operator override or Settings wins.
  * ``subjects_to_lock(subjects, include_bible)`` — Subject objects → (deduped lora_paths,
    "trigger, bible, …" prefix).
  * ``apply_lock(base_prompt, lock_prefix)`` — front-load the lock onto a prompt.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Model-aware default keyframe/storyboard LoRA strength. SDXL rank-16 character LoRAs sit at
# ~0.25 (higher fries them); Z-Image and FLUX-dev hold identity near ~0.9.
DEFAULT_ZIMAGE_STRENGTH = 0.9
DEFAULT_FLUX_DEV_STRENGTH = 0.9
DEFAULT_SDXL_STRENGTH = 0.25

# Per-LoRA strength when several cast LoRAs stack on one Z-Image render, as a
# share of the single-LoRA strength. Each at 0.9 dissolves the frame into
# speckle. Measured 2026-11-08 on three Z-Image Turbo cast LoRAs (a character, a
# prop, an environment; rank 16, 512 px), 1024x576, 9 steps, seeds 3101/3102:
# two at 0.75 grainy (prop + environment), at 0.6 clean; three at 0.6 grainy,
# at 0.45 clean. More than three: the same total, split evenly (unmeasured).
ZIMAGE_STACKED_STRENGTH = {2: 0.6, 3: 0.45}
ZIMAGE_STACKED_TOTAL = 1.35
_MIN_STRENGTH, _MAX_STRENGTH = 0.0, 1.5

# SystemSetting keys (optional operator overrides).
SETTING_STRENGTH_ZIMAGE = "character_lora_strength_zimage"
SETTING_STRENGTH_SDXL = "character_lora_strength_sdxl"
SETTING_STRENGTH_FLUX = "character_lora_strength_flux"


def _setting_float(key: str) -> Optional[float]:
    try:
        from backend.models import SystemSetting
        row = SystemSetting.query.filter_by(key=key).first()
        if row is None or row.value in (None, ""):
            return None
        return float(row.value)
    except Exception:
        return None


def resolve_lora_strength(model: Optional[str], override=None) -> float:
    """Model-aware character-LoRA strength, clamped to [0, 1.5].

    ``model`` is a keyframe/storyboard/offline model name or family tag
    (e.g. 'zimage-turbo', 'flux-dev', 'sdxl'). Explicit ``override`` wins;
    else optional Settings per family; else family defaults.
    """
    kfm = (model or "").lower()
    if "zimage" in kfm or kfm in ("z-image", "z_image"):
        family = "zimage"
        default = DEFAULT_ZIMAGE_STRENGTH
        setting_key = SETTING_STRENGTH_ZIMAGE
    elif "flux" in kfm and "dev" in kfm:
        family = "flux"
        default = DEFAULT_FLUX_DEV_STRENGTH
        setting_key = SETTING_STRENGTH_FLUX
    elif "flux" in kfm:
        # flux-schnell etc. rarely take cast LoRAs; use FLUX-dev-ish strength if forced
        family = "flux"
        default = DEFAULT_FLUX_DEV_STRENGTH
        setting_key = SETTING_STRENGTH_FLUX
    else:
        family = "sdxl"
        default = DEFAULT_SDXL_STRENGTH
        setting_key = SETTING_STRENGTH_SDXL

    if override is not None:
        try:
            val = float(override)
        except (TypeError, ValueError):
            val = default
    else:
        from_settings = _setting_float(setting_key)
        val = from_settings if from_settings is not None else default
    return max(_MIN_STRENGTH, min(_MAX_STRENGTH, val))


def stacked_lora_strength(model: Optional[str], strength: float, n_loras: int) -> float:
    """Per-LoRA strength for ``n_loras`` cast LoRAs on one render.

    Scales the resolved single-LoRA ``strength`` by the measured stacking share
    for the family; families without a measurement keep ``strength``.
    """
    if n_loras < 2 or "zimage" not in (model or "").lower():
        return strength
    stacked = ZIMAGE_STACKED_STRENGTH.get(n_loras, ZIMAGE_STACKED_TOTAL / n_loras)
    return max(_MIN_STRENGTH, strength * stacked / DEFAULT_ZIMAGE_STRENGTH)


def _dedup(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# Z-Image has a long prompt budget; keep bible useful without drowning the scene.
DEFAULT_BIBLE_LOCK_MAX_CHARS = 480


def bible_for_lock(subject, *, max_chars: int = DEFAULT_BIBLE_LOCK_MAX_CHARS) -> str:
    """Clean subject.bible for prompt lock: strip name prefix, collapse whitespace, cap length."""
    raw = (getattr(subject, "bible", None) or "").replace("\n", " ").strip()
    if not raw:
        return ""
    name = (getattr(subject, "name", None) or "").strip()
    # "Batman 2: The character is …" → drop leading "Name:" label
    if name and raw.lower().startswith(name.lower()):
        rest = raw[len(name):].lstrip(" :,-")
        if rest:
            raw = rest
    # Collapse spaces
    raw = " ".join(raw.split())
    if len(raw) > max_chars:
        cut = raw[:max_chars].rsplit(" ", 1)[0].strip()
        raw = cut or raw[:max_chars]
    return raw.strip().strip(",")


def subjects_to_lock(
    subjects: Iterable,
    *,
    include_bible: bool = True,
    include_marks: bool = True,
) -> tuple[list[str], str]:
    """Given trained cast Subject objects (anything with ``.lora_path``, ``.trigger_word`` /
    ``.name``, and optionally ``.bible``), return ``(lora_paths, lock_prefix)``.

    Only subjects that actually have a ``lora_path`` contribute — an untrained subject can't lock
    anything.

    Lock shape for LoRA subjects:
      ``a photo of {trigger}, {class}[, short marks][, vision bible]``

    Short marks come from ``training_settings_json.bible_identity_marks``.
    When ``include_bible`` is True (default), the stored vision bible is appended so
    costume detail (armor, cowl, emblem color, …) reaches Batch/Chat/Video — not only
    the weak one-line marks. Callers that pre-compose the full prompt (Cast sheet) should
    pass ``include_bible=False`` to avoid doubling.
    """
    from backend.services.character_identity_prompt import (
        compose_identity_core,
        resolve_class_token,
        short_marks_from_subject,
    )

    lora_paths: list[str] = []
    lock_parts: list[str] = []
    for subj in subjects or []:
        if not getattr(subj, "lora_path", None):
            continue
        lora_paths.append(subj.lora_path)
        trigger = (
            getattr(subj, "trigger_word", None) or getattr(subj, "name", None) or ""
        ).strip()
        marks = short_marks_from_subject(subj) if include_marks else ""
        cls = resolve_class_token(subj)
        # Prefer class-anchored core so FilmCrew/chat match Cast generate shape.
        core = compose_identity_core(trigger, cls, marks if include_marks else "")
        piece_parts: list[str] = []
        if core:
            piece_parts.append(core)
        elif trigger:
            piece_parts.append(trigger)
        if include_bible:
            bible_txt = bible_for_lock(subj)
            if bible_txt:
                # Avoid repeating the entire core / marks if bible starts the same way.
                bl = bible_txt.lower()
                already = " ".join(piece_parts).lower()
                if bl not in already and not already.endswith(bl[:40]):
                    piece_parts.append(bible_txt)
        if piece_parts:
            lock_parts.append(", ".join(piece_parts))
    return _dedup(lora_paths), ", ".join(_dedup(lock_parts))


def apply_lock(base_prompt: str, lock_prefix: str) -> str:
    """Front-load the identity lock onto a prompt. No-op when ``lock_prefix`` is empty.

    Double-lock guard: if the base already starts with the lock (or the same
    ``a photo of {trigger}`` core), do not prefix again — FilmCrew / Cast sheet
    may precompose identity and still pass subjects into the pipeline.
    """
    base = (base_prompt or "").strip()
    if not lock_prefix:
        return base
    lock = lock_prefix.strip()
    if not lock:
        return base
    bl = base.lower()
    ll = lock.lower()
    if bl.startswith(ll):
        return base
    # Core often begins "a photo of {trigger}"; if already present, skip.
    if ll.startswith("a photo of ") and bl.startswith("a photo of "):
        # Same trigger token after "a photo of "
        lock_rest = ll[len("a photo of "):].split(",", 1)[0].strip()
        base_rest = bl[len("a photo of "):].split(",", 1)[0].strip()
        if lock_rest and lock_rest == base_rest:
            return base
    return f"{lock}, {base}" if base else lock
