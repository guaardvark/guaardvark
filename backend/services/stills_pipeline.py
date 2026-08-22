"""Unified stills generation façade (chat / CLI / batch).

Callers collect intent only — they must not invent different defaults for size,
steps, CFG, enhance, or model. All stills go through ``run_stills_pipeline``.

Internal ladder:
  1. sanitize_image_prompt
  2. resolve model (shared active image model when auto)
  3. resolve W/H/steps/CFG via stills_defaults
  4. resolve enhance mode (verbatim → none)
  5. director rewrite if enhance=director
  6. resolve negatives
  7. OfflineImageGenerator.generate_image (or Comfy for FLUX when requested)
  8. return StillResult with prompt_used / sampling
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

Source = Literal["chat", "cli", "batch", "discord", "video", "smoke", "agent"]


@dataclass
class StillResult:
    success: bool
    image_path: Optional[str] = None
    image_url: Optional[str] = None
    seed_used: Optional[int] = None
    model_used: str = ""
    prompt_used: str = ""
    negative_used: str = ""
    width: int = 0
    height: int = 0
    steps: int = 0
    guidance: float = 0.0
    enhance_mode: str = "none"
    generation_time: float = 0.0
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "image_path": self.image_path,
            "image_url": self.image_url,
            "seed_used": self.seed_used,
            "model_used": self.model_used,
            "prompt_used": self.prompt_used,
            "negative_used": self.negative_used,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "guidance": self.guidance,
            "enhance_mode": self.enhance_mode,
            "generation_time": self.generation_time,
            "error": self.error,
            "metadata": self.metadata,
        }


def run_stills_pipeline(
    prompts: list[str],
    *,
    model: str = "auto",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    style: str = "realistic",
    negative_prompt: str = "",
    seed: int | None = None,
    source: Source = "chat",
    enhance: str | None = None,
    director: bool = False,
    auto_enhance: bool | None = None,
    verbatim: bool | None = None,
    loras: list[str] | None = None,
    lora_scale: float = 1.0,
    keep_pipeline: bool = False,
    output: Literal["chat_copy", "batch_dir", "path", "none"] = "path",
    output_dir: Path | str | None = None,
    content_preset: str | None = None,
    enhance_anatomy: bool = True,
    enhance_faces: bool = True,
    enhance_hands: bool = True,
    restore_faces: bool = False,
    face_restoration_weight: float = 0.5,
    remove_background: bool = False,
    extra_guidance: str | None = None,
    hold_gpu: bool = True,
    replace_legacy_sd_markers: bool = True,
) -> list[StillResult]:
    """Generate one or more stills with shared policy. Never raises for per-prompt failures."""
    from backend.services.image_prompt_sanitize import sanitize_image_prompt
    from backend.services.stills_defaults import resolve_stills_defaults
    from backend.services.stills_policy import (
        apply_enhance_to_prompts,
        auto_enhance_flag,
        resolve_enhance_mode,
        resolve_stills_negative,
    )

    if verbatim is None:
        try:
            from backend.services.media_director import verbatim_prompts_enabled
            verbatim = bool(verbatim_prompts_enabled())
        except Exception:
            verbatim = False

    cleaned: list[str] = []
    for p in prompts or []:
        s = sanitize_image_prompt(p)
        if s:
            cleaned.append(s)
    if not cleaned:
        return [StillResult(success=False, error="No valid prompts after sanitize")]

    enhance_mode = resolve_enhance_mode(
        enhance=enhance,
        director=director,
        auto_enhance=auto_enhance,
        verbatim=verbatim,
    )

    # Director rewrite (batch-level director already applied: pass enhance=none)
    if enhance_mode == "director":
        cleaned = apply_enhance_to_prompts(
            cleaned, enhance_mode="director", style=style, extra_guidance=extra_guidance,
        )
        # After director, offline stuffing would double-rewrite — use none for auto_enhance
        req_auto_enhance = False
        effective_mode = "director"
    else:
        req_auto_enhance = auto_enhance_flag(enhance_mode)
        effective_mode = enhance_mode

    defaults = resolve_stills_defaults(
        model,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        replace_legacy_sd_markers=replace_legacy_sd_markers,
    )
    w = int(defaults["width"])
    h = int(defaults["height"])
    st = int(defaults["steps"])
    g = float(defaults["guidance"])
    model_id = (model or "auto").strip() or "auto"

    neg = resolve_stills_negative(
        negative_prompt, enhance_mode=effective_mode if effective_mode != "director" else "none",
        style=style,
    )
    # After director, still attach base quality negatives (not empty)
    if effective_mode == "director":
        neg = resolve_stills_negative(
            negative_prompt, enhance_mode="offline", style=style,
        )

    results: list[StillResult] = []
    for prompt_text in cleaned:
        results.append(
            _generate_one(
                prompt=prompt_text,
                negative=neg,
                model=model_id,
                width=w,
                height=h,
                steps=st,
                guidance=g,
                style=style,
                seed=seed,
                source=source,
                enhance_mode=effective_mode,
                auto_enhance=req_auto_enhance,
                loras=loras,
                lora_scale=lora_scale,
                keep_pipeline=keep_pipeline,
                output=output,
                output_dir=output_dir,
                content_preset=content_preset,
                enhance_anatomy=enhance_anatomy,
                enhance_faces=enhance_faces,
                enhance_hands=enhance_hands,
                restore_faces=restore_faces,
                face_restoration_weight=face_restoration_weight,
                remove_background=remove_background,
                hold_gpu=hold_gpu,
            )
        )
    return results


def _generate_one(
    *,
    prompt: str,
    negative: str,
    model: str,
    width: int,
    height: int,
    steps: int,
    guidance: float,
    style: str,
    seed: int | None,
    source: Source,
    enhance_mode: str,
    auto_enhance: bool,
    loras: list[str] | None,
    lora_scale: float,
    keep_pipeline: bool,
    output: str,
    output_dir: Path | str | None,
    content_preset: str | None,
    enhance_anatomy: bool,
    enhance_faces: bool,
    enhance_hands: bool,
    restore_faces: bool,
    face_restoration_weight: float,
    remove_background: bool,
    hold_gpu: bool,
) -> StillResult:
    from backend.services.offline_image_generator import (
        ImageGenerationRequest,
        get_image_generator,
    )

    # FLUX via Comfy is still a separate engine; offline path is the default.
    mid = (model or "").lower()
    if "flux" in mid:
        return _generate_comfy_flux(
            prompt=prompt, negative=negative, model=model,
            width=width, height=height, steps=steps, guidance=guidance,
            seed=seed, enhance_mode=enhance_mode, output=output, output_dir=output_dir,
        )

    try:
        generator = get_image_generator()
    except Exception as e:
        return StillResult(success=False, error=f"Image generator unavailable: {e}", prompt_used=prompt)

    if not getattr(generator, "service_available", True):
        return StillResult(
            success=False,
            error="Image generation service not available (diffusion deps / GPU).",
            prompt_used=prompt,
            enhance_mode=enhance_mode,
        )

    request = ImageGenerationRequest(
        prompt=prompt,
        negative_prompt=negative,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance,
        style=style,
        seed=seed,
        model=model,
        content_preset=content_preset,
        auto_enhance=auto_enhance,
        enhance_anatomy=enhance_anatomy,
        enhance_faces=enhance_faces,
        enhance_hands=enhance_hands,
        restore_faces=restore_faces,
        face_restoration_weight=face_restoration_weight,
        remove_background=remove_background,
        keep_pipeline_loaded=keep_pipeline,
        loras=loras,
        lora_scale=lora_scale,
    )

    try:
        if hold_gpu:
            from backend.services.gpu_resource_policy import gpu_session
            from backend.services.job_operation_gate import GpuBusyError
            from backend.services.job_types import JobKind

            # Resolution-aware estimates + compositor reserve (2026-08-04 client box
            # 2048² incident): admission must price the requested canvas, and
            # diffusers-image sessions hold back the desktop's VRAM share.
            from backend.services.gpu_resource_policy import compositor_vram_reserve_mb
            ram_est = (
                generator._ram_estimate_gb(model, width, height)
                if hasattr(generator, "_ram_estimate_gb") else 10.0
            )
            vram_est = (
                generator._vram_estimate_mb(model, width, height)
                if hasattr(generator, "_vram_estimate_mb") else 11000
            )
            try:
                with gpu_session(
                    JobKind.VIDEO_RENDER,
                    f"stills_{source}_{uuid.uuid4().hex[:8]}",
                    on_busy="raise",
                    evict_ollama=True,
                    vram_estimate_mb=vram_est,
                    ram_estimate_gb=ram_est,
                    require_fit=True,
                    cross_process=True,
                    vram_reserve_mb=compositor_vram_reserve_mb(),
                ):
                    gen_result = generator.generate_image(request)
            except GpuBusyError as busy:
                logger.warning("stills admission refused (%s): %s", source, busy)
                return StillResult(
                    success=False,
                    error="GPU is busy with another render right now — try again in a moment.",
                    prompt_used=prompt,
                    enhance_mode=enhance_mode,
                    width=width,
                    height=height,
                    steps=steps,
                    guidance=guidance,
                )
        else:
            # Batch path often holds gpu_session at batch level + adopt_gpu_session
            gen_result = generator.generate_image(request)
    except Exception as e:
        logger.error("stills_pipeline generate failed: %s", e, exc_info=True)
        return StillResult(
            success=False,
            error=str(e),
            prompt_used=prompt,
            enhance_mode=enhance_mode,
        )
    finally:
        if not keep_pipeline:
            try:
                if hasattr(generator, "_unload_pipeline"):
                    generator._unload_pipeline()
                import gc
                gc.collect()
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    prompt_used = getattr(gen_result, "prompt_used", None) or prompt
    neg_used = getattr(gen_result, "negative_prompt_used", None) or negative
    base = StillResult(
        success=bool(gen_result.success),
        image_path=gen_result.image_path,
        seed_used=gen_result.seed_used,
        model_used=gen_result.model_used or model,
        prompt_used=prompt_used,
        negative_used=neg_used,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        enhance_mode=enhance_mode,
        generation_time=float(getattr(gen_result, "generation_time", 0) or 0),
        error=gen_result.error if not gen_result.success else None,
        metadata={"source": source},
    )

    if not gen_result.success or not gen_result.image_path:
        return base

    # Optional copy into chat outputs registry path
    if output == "chat_copy":
        try:
            from backend.config import OUTPUT_DIR
            out = Path(OUTPUT_DIR) / "generated_images"
            out.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"gen_{ts}_{uuid.uuid4().hex[:8]}.png"
            dest = out / name
            shutil.copy2(gen_result.image_path, dest)
            try:
                if os.path.exists(gen_result.image_path):
                    os.unlink(gen_result.image_path)
            except Exception:
                pass
            base.image_path = str(dest)
            base.image_url = f"/api/outputs/generated_images/{name}"
        except Exception as e:
            logger.warning("stills_pipeline chat_copy failed: %s", e)
    elif output == "batch_dir" and output_dir:
        # Caller (batch) owns final naming; leave path as generator wrote it
        pass

    return base


def _generate_comfy_flux(
    *,
    prompt: str,
    negative: str,
    model: str,
    width: int,
    height: int,
    steps: int,
    guidance: float,
    seed: int | None,
    enhance_mode: str,
    output: str,
    output_dir: Path | str | None,
) -> StillResult:
    try:
        from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        gen = ComfyUIImageGenerator()
        out_path = None
        if output_dir:
            out_path = str(Path(output_dir) / f"flux_{uuid.uuid4().hex[:8]}.png")
        path = gen.generate_image(
            prompt=prompt,
            output_path=out_path or str(Path("/tmp") / f"flux_{uuid.uuid4().hex[:8]}.png"),
            width=width,
            height=height,
            negative_prompt=negative or None,
            seed=seed if seed is not None else 42,
            model="flux",
            steps=steps,
            cfg=guidance,
        )
        url = None
        if output == "chat_copy" and path:
            try:
                from backend.config import OUTPUT_DIR
                out = Path(OUTPUT_DIR) / "generated_images"
                out.mkdir(parents=True, exist_ok=True)
                name = f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
                dest = out / name
                shutil.copy2(path, dest)
                path = str(dest)
                url = f"/api/outputs/generated_images/{name}"
            except Exception:
                pass
        return StillResult(
            success=True,
            image_path=path,
            image_url=url,
            seed_used=seed,
            model_used=model,
            prompt_used=prompt,
            negative_used=negative,
            width=width,
            height=height,
            steps=steps,
            guidance=guidance,
            enhance_mode=enhance_mode,
        )
    except Exception as e:
        return StillResult(
            success=False,
            error=str(e),
            prompt_used=prompt,
            enhance_mode=enhance_mode,
            width=width,
            height=height,
            steps=steps,
            guidance=guidance,
        )
