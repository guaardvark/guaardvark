# backend/services/infographic_generator.py
# Infographic generator.
#
# Composes a text-layout prompt from structured fields (title, scene, labels,
# footer, hashtags) or takes a freeform prompt as written, and renders it
# through the shared stills pipeline with a model that spells reliably. The
# image is saved under outputs/infographics and filed in the Library.

from __future__ import annotations

import logging
import random
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Models offered for infographics, in order of preference. Measured 2026-10-27
# on a 16 GB consumer card: three poster layouts (5, 3 and 4 labels) x three
# seeds, 78 text elements per model, the prompt from compose_prompt() below,
# read back by a local vision model and checked by eye. Krea 2 Turbo 78/78 with
# every poster clean; Qwen-Image 2.1 78/78; Z-Image Turbo 69/78, 2 of 9 posters
# clean (drops or merges a label when there are five, misspells the odd word).
# FLUX.1-dev (~39/78) and FLUX.1-schnell (21/78, no poster clean) misspell too
# often to offer. Krea 2 is gated on Hugging Face, so Z-Image is the model a
# fresh install can render with. Qwen-Image 2.1 is offered but never picked by
# default: its licence is non-commercial.
INFOGRAPHIC_MODELS: List[Dict[str, Any]] = [
    {"id": "krea2-turbo", "default": True},
    {"id": "zimage-turbo", "default": True},
    {"id": "qwen-image-21", "default": False,
     "license_note": "Qwen Research License: non-commercial use only."},
]

# Seconds a render waits for the GPU (another render, or the cooldown after
# one) before it gives up.
GPU_WAIT_S = 600


# Style presets, written as prose: these models read prompts with an LLM text
# encoder, and comma-separated tag lists are drawn as scene content.
STYLE_PRESETS: Dict[str, str] = {
    "editorial": "a dramatic editorial illustration with cinematic lighting and vivid, saturated colours",
    "flat_vector": "a clean flat vector design with simple geometric shapes, minimal shadows and simple icons",
    "photo_real": "a photorealistic photograph with professional composition and dramatic lighting",
    "comic": "a comic book illustration with bold inked outlines, vibrant flat colours and halftone shading",
}

# Aspect -> (width, height) before each model's canvas rules are applied.
ASPECT_DIMS: Dict[str, tuple] = {
    "16:9": (1280, 720),
    "1:1": (1024, 1024),
    "9:16": (720, 1280),
    "4:5": (1024, 1280),
    "3:2": (1216, 832),
}

_ASPECT_WORDS = {"16:9": "wide", "1:1": "square", "9:16": "tall vertical", "4:5": "portrait", "3:2": "landscape"}
_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}
_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


@dataclass
class InfographicSpec:
    """Structured input; raw_prompt, when set, is used as written."""
    title: str = ""
    scene: str = ""
    footer: str = ""
    hashtags: List[str] = field(default_factory=list)
    style: str = "editorial"
    aspect: str = "16:9"
    callouts: List[str] = field(default_factory=list)
    raw_prompt: str = ""

    def compose_prompt(self) -> str:
        """One sentence per text element, each quoted and placed on the poster.
        The models measured above spell what they are given in quotes; naming
        where each element sits keeps labels from merging."""
        if self.raw_prompt.strip():
            return self.raw_prompt.strip()

        style_text = STYLE_PRESETS.get(self.style, STYLE_PRESETS["editorial"])
        shape = _ASPECT_WORDS.get(self.aspect, "wide")
        parts: List[str] = [f"A {shape} infographic poster designed as {style_text}."]

        scene = _clean(self.scene).rstrip(".")
        if scene:
            parts.append(f"The artwork behind the text: {scene}.")

        title = _clean(self.title)
        if title:
            parts.append(f'Across the top of the poster, a large bold headline reads "{title}".')

        callouts = [_clean(c).strip('"') for c in self.callouts if _clean(c).strip('"')]
        if callouts:
            count = _NUMBER_WORDS.get(len(callouts), str(len(callouts)))
            parts.append(
                f"Below the headline sit {count} separate label boxes, "
                f"each holding one short phrase in bold letters."
            )
            for i, callout in enumerate(callouts):
                ordinal = _ORDINALS[i] if i < len(_ORDINALS) else f"number {i + 1}"
                parts.append(f'The {ordinal} label reads "{callout}".')

        footer = _clean(self.footer)
        if footer:
            parts.append(f'A solid banner runs along the bottom edge with the text "{footer}" in clean white letters.')

        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in (_clean(t) for t in self.hashtags) if t)
        if tags:
            parts.append(f'Under the banner, one line of small text reads "{tags}".')

        parts.append("Every word is spelled exactly as written, with crisp, evenly spaced, easy-to-read lettering.")
        return " ".join(parts)


class InfographicModelMissing(RuntimeError):
    """The requested (or any default) infographic model is not installed."""


def _output_dir() -> Path:
    from backend.config import OUTPUT_DIR
    out = Path(OUTPUT_DIR) / "infographics"
    out.mkdir(parents=True, exist_ok=True)
    return out


class InfographicGenerator:
    """Model choice, prompt and output filing around run_stills_pipeline."""

    # -- models -------------------------------------------------------------

    def models(self) -> List[Dict[str, Any]]:
        """The offered models with their install state, in preference order."""
        from backend.services.offline_image_generator import get_image_generator

        try:
            catalog = get_image_generator().get_available_models(probe_remote=False)
        except Exception as e:  # noqa: BLE001 — list what we can rather than fail the page
            logger.warning("Infographic model catalog unavailable: %s", e)
            catalog = {}
        rows = []
        for entry in INFOGRAPHIC_MODELS:
            info = catalog.get(entry["id"]) or {}
            rows.append({
                "id": entry["id"],
                "name": info.get("label") or entry["id"],
                "description": info.get("description", ""),
                "installed": bool(info.get("downloaded")),
                "gated": bool(info.get("gated")),
                "size_gb": info.get("size_gb"),
                "default": bool(entry.get("default")),
                "license_note": entry.get("license_note"),
            })
        return rows

    def default_model(self) -> Optional[str]:
        for row in self.models():
            if row["default"] and row["installed"]:
                return row["id"]
        return None

    def resolve_model(self, requested: Optional[str]) -> str:
        rows = {row["id"]: row for row in self.models()}
        if requested:
            row = rows.get(requested)
            if row is None:
                raise ValueError(
                    f"'{requested}' is not an infographic model; choose one of: {', '.join(rows)}"
                )
            if not row["installed"]:
                raise InfographicModelMissing(
                    f"{row['name']} is not installed. Install it from Settings > Generation > Infographic."
                )
            return requested
        chosen = self.default_model()
        if not chosen:
            raise InfographicModelMissing(
                "No infographic model is installed. Install Krea 2 Turbo or Z-Image Turbo "
                "from Settings > Generation > Infographic."
            )
        return chosen

    # -- render ---------------------------------------------------------------

    def generate(self, spec: InfographicSpec, seed: Optional[int] = None,
                 model: Optional[str] = None) -> Dict[str, Any]:
        """Render one infographic and wait for it, queueing behind any render
        already on the GPU.

        Returns {filename, image_url, prompt, model, model_name, width, height,
        seed, duration_s, document_id}.
        """
        from backend.services.gpu_resource_policy import (
            compositor_vram_reserve_mb,
            gpu_session_when_free,
        )
        from backend.services.image_render_limits import resolve_canvas
        from backend.services.job_types import JobKind
        from backend.services.offline_image_generator import get_image_generator
        from backend.services.stills_pipeline import run_stills_pipeline

        model_id = self.resolve_model(model)
        prompt_text = spec.compose_prompt()
        width, height = ASPECT_DIMS.get(spec.aspect, ASPECT_DIMS["16:9"])
        width, height, _ = resolve_canvas(width, height, model_id)
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        out_dir = _output_dir()

        generator = get_image_generator()
        started = time.monotonic()
        # One lease for the whole render, waiting its turn; the pipeline's own
        # session inside it is a same-thread pass-through.
        with gpu_session_when_free(
            JobKind.VIDEO_RENDER, f"infographic_{uuid.uuid4().hex[:8]}",
            wait_s=GPU_WAIT_S,
            evict_ollama=True,
            vram_estimate_mb=generator._vram_estimate_mb(model_id, width, height),
            ram_estimate_gb=generator._ram_estimate_gb(model_id, width, height),
            require_fit=True,
            cross_process=True,
            vram_reserve_mb=compositor_vram_reserve_mb(),
        ):
            logger.info(
                f"[INFOGRAPHIC] {model_id} {width}x{height} seed={seed} style={spec.style}"
            )
            results = run_stills_pipeline(
                [prompt_text],
                model=model_id,
                width=width,
                height=height,
                seed=seed,
                source="infographic",
                enhance="none",
                verbatim=True,
                auto_enhance=False,
                enhance_anatomy=False,
                enhance_faces=False,
                enhance_hands=False,
                hold_gpu=True,
                output="path",
                output_dir=out_dir,
            )
        still = results[0] if results else None
        if still is None or not still.success or not still.image_path:
            raise RuntimeError((still.error if still else None) or "The render produced no image.")

        name = f"infographic_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        dest = out_dir / name
        shutil.move(str(still.image_path), dest)

        document_id = None
        try:
            from backend.services.output_registration import register_file
            doc = register_file(
                str(dest), folder_name="Images", subfolder_name="Infographics",
                file_metadata={"model": model_id, "seed": seed, "prompt": prompt_text,
                               "width": width, "height": height, "source": "infographic"},
            )
            document_id = getattr(doc, "id", None)
        except Exception as e:  # noqa: BLE001 — the image exists; the Library entry is a convenience
            logger.warning("Infographic not filed in the Library: %s", e)

        names = {row["id"]: row["name"] for row in self.models()}
        return {
            "filename": name,
            "image_url": f"/api/infographic/view?filename={name}",
            "prompt": prompt_text,
            "model": model_id,
            "model_name": names.get(model_id, model_id),
            "width": width,
            "height": height,
            "seed": seed,
            "duration_s": round(time.monotonic() - started, 2),
            "document_id": document_id,
        }

    @staticmethod
    def image_path(filename: str) -> Optional[Path]:
        """The saved infographic with this file name, or None."""
        name = Path(filename or "").name
        if not name or name != filename or not name.endswith(".png"):
            return None
        path = _output_dir() / name
        return path if path.is_file() else None


# Module-level singleton — cheap, no state worth pooling
_instance: Optional[InfographicGenerator] = None


def get_infographic_generator() -> InfographicGenerator:
    global _instance
    if _instance is None:
        _instance = InfographicGenerator()
    return _instance
