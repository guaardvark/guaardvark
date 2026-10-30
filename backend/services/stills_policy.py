"""Shared stills enhance + negative policy for chat / CLI / batch.

Enhance ladder (single source of truth):
  * verbatim ON  → always ``none`` (exact user text after sanitize)
  * explicit enhance in {none, offline, director} → honor it
  * director_mode / enhance=director → ``director``
  * auto_enhance False → ``none``
  * default (auto), tags family → ``offline`` (generator style/anatomy stuffing)
  * default (auto), natural family (Z-Image) → ``director`` (LLM prose rewrite;
    falls back to the exact prompt when no chat model answers — never stuffing)

Which families are "natural" is declared in ``stills_defaults._FAMILY_DEFAULTS``.
Chat and batch share this ladder.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

EnhanceMode = Literal["none", "offline", "director"]

# Shared base negative used when enhance is on (chat used this; batch often empty).
BASE_QUALITY_NEGATIVE = (
    "blurry, low quality, distorted, deformed, ugly, bad anatomy"
)


def resolve_enhance_mode(
    *,
    enhance: str | None = None,
    director: bool = False,
    auto_enhance: bool | None = None,
    verbatim: bool | None = None,
    model: str | None = None,
) -> EnhanceMode:
    """Return the effective enhance mode for one stills request.

    ``model`` (catalog key / HF id / auto) picks the default rung: natural-language
    families default to the director rewrite, tag families to offline stuffing.
    """
    if verbatim is None:
        try:
            from backend.services.media_director import verbatim_prompts_enabled
            verbatim = bool(verbatim_prompts_enabled())
        except Exception:
            verbatim = False
    if verbatim:
        return "none"

    e = (enhance or "").strip().lower() if enhance is not None else ""
    if e in ("none", "offline", "director"):
        return e  # type: ignore[return-value]
    if e in ("auto", ""):
        pass
    elif enhance is not None and e:
        logger.debug("stills_policy: unknown enhance=%r; treating as auto", enhance)

    if director:
        return "director"
    if auto_enhance is False:
        return "none"
    if model is not None and prompt_style_for_model(model) == "natural":
        return "director"
    # Default product ladder for tag families: offline stuffing so chat == batch.
    return "offline"


def prompt_style_for_model(model: str | None) -> str:
    """'natural' or 'tags' for a model ref; 'tags' when the lookup fails."""
    try:
        from backend.services.stills_defaults import prompt_style
        return prompt_style(model)
    except Exception:
        return "tags"


def _comfy_prose_model(model: str | None) -> bool:
    """A ComfyUI family that reads prose and samples with real guidance
    (MOSS-Image, Qwen-Image 2.1). The base and style negatives are tag lists
    written for SD-era models; such a model reads them as content to avoid,
    "text" and "illustration" included, so it gets the person's own negative
    and otherwise its template's."""
    if not model or model == "auto":
        return False
    try:
        from backend.services.image_render_limits import limits_for
        lim = limits_for(model)
    except Exception:
        return False
    return lim.get("engine") == "comfy" and lim.get("prompt_style") == "natural"


def resolve_stills_negative(
    user_negative: str | None = "",
    *,
    enhance_mode: EnhanceMode = "offline",
    style: str = "realistic",
    model: str | None = None,
) -> str:
    """Single negative policy.

    - enhance ``none`` (incl. verbatim): user negative only (may be empty)
    - a ComfyUI prose model (``_comfy_prose_model``): user negative only
    - offline/director: user + base quality + style negatives (deduped lightly)
    """
    user = (user_negative or "").strip()
    if enhance_mode == "none" or _comfy_prose_model(model):
        return user

    parts: list[str] = []
    if user:
        parts.append(user)
    if BASE_QUALITY_NEGATIVE not in user:
        parts.append(BASE_QUALITY_NEGATIVE)

    try:
        from backend.services.offline_image_generator import get_image_generator
        gen = get_image_generator()
        style_cfg = (getattr(gen, "style_configs", None) or {}).get(style) or {}
        style_neg = (style_cfg.get("negative_prompt") or "").strip()
        if style_neg and style_neg not in user:
            parts.append(style_neg)
    except Exception:
        pass

    # De-dupe commas segments while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for chunk in ", ".join(parts).split(","):
        t = chunk.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return ", ".join(out)


def apply_enhance_to_prompts(
    prompts: list[str],
    *,
    enhance_mode: EnhanceMode,
    style: str = "realistic",
    extra_guidance: str | None = None,
    model: str | None = None,
) -> list[str]:
    """Apply director rewrite when mode is director; otherwise return prompts as-is.

    Offline stuffing happens inside OfflineImageGenerator when auto_enhance=True.
    ``model`` selects the rewrite contract (prose for natural families).
    """
    if not prompts:
        return []
    if enhance_mode != "director":
        return list(prompts)
    try:
        from backend.services.media_director import enhance_prompts
        refined = enhance_prompts(
            prompts, style=style, extra_guidance=extra_guidance,
            prompt_style=prompt_style_for_model(model) if model is not None else None,
        )
        if refined and len(refined) == len(prompts):
            return [r.strip() if r else p for r, p in zip(refined, prompts)]
    except Exception as e:
        logger.warning("stills_policy: director enhance failed (using originals): %s", e)
    return list(prompts)


def auto_enhance_flag(enhance_mode: EnhanceMode) -> bool:
    """Map enhance mode → ImageGenerationRequest.auto_enhance."""
    return enhance_mode == "offline"


def policy_snapshot(
    *,
    enhance_mode: EnhanceMode,
    model: str,
    width: int,
    height: int,
    steps: int,
    guidance: float,
    verbatim: bool,
) -> dict[str, Any]:
    return {
        "enhance_mode": enhance_mode,
        "model": model,
        "width": width,
        "height": height,
        "steps": steps,
        "guidance": guidance,
        "verbatim": verbatim,
    }
