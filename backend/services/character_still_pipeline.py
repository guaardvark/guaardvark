"""Single chokepoint for cast-identity stills (LoRA + trigger + family routing).

Every product surface that needs a character face in a still — Cast generate,
Batch Image, FilmCrew / MusicVideo / VideoGen keyframes, chat — should call
``render_character_still`` (or ``render_character_stills``) instead of inventing
its own Comfy/offline + strength + trigger logic.

**Contract:** pass a raw *scene* prompt plus ``subject_ids`` (or ``subjects``).
This module owns identity lock (``compose_identity_core``), LoRA load, and
family routing (Z-Image offline vs Comfy SDXL/FLUX). Callers must not pre-apply
``subjects_to_lock`` / ``apply_lock`` unless they also skip subjects here.

I2V (Wan/Cog) does NOT apply character LoRAs; identity is baked into the still
this module produces, then animated.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Sequence

from backend.services.stills_pipeline import StillResult

log = logging.getLogger(__name__)

CharacterSource = Literal[
    "cast", "batch", "video", "filmcrew", "musicvideo", "chat", "smoke",
]


def _zimage_via_comfyui_enabled() -> bool:
    """True when Z-Image stills should render through ComfyUI instead of the
    offline Diffusers pipeline (GUAARDVARK_ZIMAGE_USE_COMFYUI=1).

    The offline Z-Image generator is CUDA-only; on Apple Silicon (MPS) the only
    working Z-Image path is the ComfyUI workflow, so this flag routes Cast stills
    there. Mirrors batch_image_generator._zimage_via_comfyui_enabled.
    """
    import os
    _v = os.environ.get("GUAARDVARK_ZIMAGE_USE_COMFYUI", "").strip().lower()
    return _v in ("1", "true", "yes", "on")


def _subjects_from_ids(subject_ids: Sequence[int] | None) -> list:
    """Load Subjects by id. Safe from daemon threads / Celery (opens app_context)."""
    if not subject_ids:
        return []
    from backend.models import db, Subject

    def _load() -> list:
        out = []
        for sid in subject_ids:
            try:
                s = db.session.get(Subject, int(sid))
            except Exception as e:
                log.warning("subject_ids resolve failed for %s: %s", sid, e)
                s = None
            if s is not None:
                out.append(s)
        return out

    try:
        from flask import has_app_context
        if has_app_context():
            return _load()
    except Exception:
        pass
    try:
        from backend.app import get_or_create_app
        app = get_or_create_app()
        with app.app_context():
            try:
                return _load()
            finally:
                db.session.remove()
    except Exception as e:
        log.warning("_subjects_from_ids app_context failed: %s", e)
        return []


def _loras_and_lock(
    *,
    subjects: Iterable | None,
    subject_ids: Sequence[int] | None,
    lora_paths: Sequence[str] | None,
    include_bible: bool,
) -> tuple[list[str], str]:
    from backend.services.cast_lock import apply_lock, subjects_to_lock

    paths: list[str] = []
    lock = ""
    subjs = list(subjects or [])
    if subject_ids and not subjs:
        subjs = _subjects_from_ids(subject_ids)
    if subjs:
        paths, lock = subjects_to_lock(subjs, include_bible=include_bible)
    if lora_paths:
        for p in lora_paths:
            p = (p or "").strip()
            if p and p not in paths:
                paths.append(p)
    return paths, lock


def render_character_still(
    prompt: str,
    *,
    subjects: Iterable | None = None,
    subject_ids: Sequence[int] | None = None,
    lora_paths: Sequence[str] | None = None,
    include_bible: bool = True,
    apply_subject_loras: bool = True,
    source: CharacterSource = "cast",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    steps_explicit: bool = False,
    guidance: float | None = None,
    seed: int | None = None,
    negative_prompt: str = "",
    output_path: str | Path | None = None,
    lora_strength: float | None = None,
    style: str = "realistic",
    enhance: str = "none",
    keep_pipeline: bool = True,
    hold_gpu: bool = False,
) -> StillResult:
    """Render one identity-locked still. Never raises — returns StillResult.

    ``apply_subject_loras=False`` still routes by subject/LoRA family but loads
    no adapters (Cast base sheet / explorative regen).
    """
    from backend.services.cast_lock import apply_lock, resolve_lora_strength
    from backend.services.image_prompt_sanitize import sanitize_image_prompt
    from backend.services.media_model_registry import (
        get_profile,
        resolve_inference_for_loras,
        subject_base_model_id,
    )
    from backend.services.stills_defaults import resolve_stills_defaults

    base = sanitize_image_prompt(prompt or "") or (prompt or "").strip()
    subjs = list(subjects or [])
    if subject_ids and not subjs:
        subjs = _subjects_from_ids(subject_ids)

    paths: list[str] = []
    lock = ""
    if apply_subject_loras:
        paths, lock = _loras_and_lock(
            subjects=subjs,
            subject_ids=None,
            lora_paths=lora_paths,
            include_bible=include_bible,
        )
    elif lora_paths:
        # Explicit paths only when caller forces adapters without subject lock.
        paths = [p.strip() for p in lora_paths if (p or "").strip()]

    # Cast was requested but nothing resolved — never silently render generic T2I.
    cast_requested = bool(subject_ids) or bool(lora_paths) or bool(subjects)
    if apply_subject_loras and cast_requested and not paths:
        return StillResult(
            success=False,
            error=(
                "Cast identity failed: no LoRA paths resolved from subject_ids/paths "
                "(check train status, app context, and lora_path on the subject)."
            ),
            prompt_used=base,
            metadata={
                "source": source,
                "subject_ids": list(subject_ids or []),
                "loras": list(lora_paths or []),
            },
        )

    final_prompt = apply_lock(base, lock) if lock else base
    if not final_prompt:
        return StillResult(success=False, error="Empty prompt after sanitize", metadata={"source": source})

    route: dict[str, Any] = {
        "family": "zimage",
        "inference_engine": "offline",
        "offline_model_key": "zimage-turbo",
        "comfy_model_tag": None,
        "base_model_id": "zimage-turbo",
    }
    path_route: dict[str, Any] | None = None
    if paths:
        try:
            path_route = resolve_inference_for_loras(list(paths))
            route = path_route
        except Exception as e:
            if not subjs:
                return StillResult(
                    success=False,
                    error=f"LoRA family resolve failed: {e}",
                    prompt_used=final_prompt,
                    metadata={"source": source, "loras": paths},
                )
    # Explicit train base on the subject wins (fixes missing LoRA sidecar → false
    # sdxl-legacy when the character was trained on Z-Image).
    if subjs:
        try:
            raw = getattr(subjs[0], "training_settings_json", None) or {}
            explicit = None
            if isinstance(raw, dict):
                explicit = raw.get("base_model_id") or raw.get("base_model")
            if explicit:
                profile = get_profile(str(explicit)) or {}
                if profile:
                    route = {
                        "family": profile.get("family") or "zimage",
                        "inference_engine": profile.get("inference_engine") or "offline",
                        "offline_model_key": profile.get("offline_model_key") or "zimage-turbo",
                        "comfy_model_tag": profile.get("comfy_model_tag"),
                        "base_model_id": profile.get("id") or explicit,
                    }
            elif not path_route:
                base_id = subject_base_model_id(subjs[0])
                profile = get_profile(base_id) or {}
                route = {
                    "family": profile.get("family") or "zimage",
                    "inference_engine": profile.get("inference_engine") or "offline",
                    "offline_model_key": profile.get("offline_model_key") or "zimage-turbo",
                    "comfy_model_tag": profile.get("comfy_model_tag"),
                    "base_model_id": profile.get("id") or base_id,
                }
        except Exception:
            pass

    family = (route.get("family") or "zimage").lower()
    engine = (route.get("inference_engine") or "offline").lower()
    if family == "zimage":
        # Z-Image defaults to the offline Diffusers pipeline, but that path is
        # CUDA-only. On Apple Silicon (MPS) route through ComfyUI when the
        # operator opts in via GUAARDVARK_ZIMAGE_USE_COMFYUI=1.
        engine = "comfy" if _zimage_via_comfyui_enabled() else "offline"

    strength_model = (
        route.get("offline_model_key")
        or route.get("comfy_model_tag")
        or family
        or "zimage-turbo"
    )
    strength = resolve_lora_strength(strength_model, lora_strength)

    defaults = resolve_stills_defaults(
        route.get("offline_model_key") or route.get("comfy_model_tag") or "auto",
        width=width,
        height=height,
        steps=steps,
        steps_explicit=steps_explicit,
        guidance=guidance,
    )
    w = int(width if width else defaults["width"])
    h = int(height if height else defaults["height"])
    st = int(defaults["steps"])
    g = float(guidance if guidance is not None else defaults["guidance"])

    dest = Path(output_path) if output_path else Path(tempfile.gettempdir()) / (
        f"char_still_{source}_{int(time.time() * 1000)}.png"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    meta = {
        "source": source,
        "steps": st,
        "steps_requested": defaults["steps_requested"],
        "steps_notice": defaults["steps_notice"],
        "family": family,
        "engine": engine,
        "base_model_id": route.get("base_model_id"),
        "loras": paths,
        "lora_strength": strength,
        "lock_prefix": lock,
        "include_bible": include_bible,
    }

    try:
        if engine == "offline":
            from backend.services.offline_image_generator import (
                ImageGenerationRequest,
                get_image_generator,
            )
            gen = get_image_generator()
            result = gen.generate_image(ImageGenerationRequest(
                prompt=final_prompt,
                negative_prompt=negative_prompt or "",
                width=w,
                height=h,
                num_inference_steps=st,
                steps_explicit=steps_explicit,
                guidance_scale=g,
                style=style,
                seed=seed,
                model=route.get("offline_model_key") or "zimage-turbo",
                auto_enhance=enhance not in (None, "none", False),
                restore_faces=False,
                enhance_anatomy=False,
                enhance_faces=False,
                enhance_hands=False,
                loras=list(paths) if paths else None,
                lora_scale=strength,
                keep_pipeline_loaded=keep_pipeline,
            ))
            st = (getattr(result, "metadata", None) or {}).get("steps", st)
            meta["steps"] = st
            if not result.success or not result.image_path:
                return StillResult(
                    success=False,
                    error=result.error or "offline character still failed",
                    prompt_used=final_prompt,
                    model_used=route.get("offline_model_key") or "zimage-turbo",
                    width=w,
                    height=h,
                    steps=st,
                    guidance=g,
                    generation_time=time.time() - t0,
                    metadata=meta,
                )
            src = Path(result.image_path)
            if src.resolve() != dest.resolve():
                shutil.copy2(str(src), str(dest))
            return StillResult(
                success=True,
                image_path=str(dest),
                seed_used=result.seed_used if hasattr(result, "seed_used") else seed,
                model_used=f"{route.get('base_model_id') or 'zimage-turbo'}+lora",
                prompt_used=final_prompt,
                negative_used=negative_prompt or "",
                width=w,
                height=h,
                steps=st,
                guidance=g,
                enhance_mode=enhance or "none",
                generation_time=time.time() - t0,
                metadata=meta,
            )

        # Comfy SDXL / FLUX / Z-Image (Z-Image only when opted in via
        # GUAARDVARK_ZIMAGE_USE_COMFYUI=1 — the offline path is CUDA-only).
        from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        if family == "zimage":
            model_tag = "zimage"
        else:
            model_tag = route.get("comfy_model_tag") or ("flux-dev" if family == "flux" else "sdxl")
        gen = ComfyUIImageGenerator(lora_strength=strength)
        path = gen.generate_image(
            prompt=final_prompt,
            loras=list(paths) if paths else None,
            output_path=str(dest),
            width=w,
            height=h,
            seed=seed if seed is not None else 42,
            steps=st if st > 0 else (8 if family == "zimage" else (20 if family == "flux" else 30)),
            steps_explicit=steps_explicit,
            model=model_tag,
            negative_prompt=negative_prompt or None,
        )
        st = getattr(gen, "last_steps", st)
        meta["steps"] = st
        if not path or not Path(path).is_file():
            return StillResult(
                success=False,
                error="Comfy character still missing after generation",
                prompt_used=final_prompt,
                model_used=model_tag,
                width=w,
                height=h,
                steps=st,
                guidance=g,
                generation_time=time.time() - t0,
                metadata=meta,
            )
        return StillResult(
            success=True,
            image_path=str(path),
            seed_used=seed,
            model_used=f"{route.get('base_model_id') or model_tag}+lora",
            prompt_used=final_prompt,
            negative_used=negative_prompt or "",
            width=w,
            height=h,
            steps=st,
            guidance=g,
            enhance_mode=enhance or "none",
            generation_time=time.time() - t0,
            metadata=meta,
        )
    except Exception as e:
        log.exception("render_character_still failed (source=%s): %s", source, e)
        return StillResult(
            success=False,
            error=str(e),
            prompt_used=final_prompt,
            generation_time=time.time() - t0,
            metadata=meta,
        )


def render_character_stills(
    prompts: list[str],
    **kwargs,
) -> list[StillResult]:
    """Convenience: one ``render_character_still`` per prompt (shared LoRA args)."""
    return [render_character_still(p, **kwargs) for p in (prompts or [])]
