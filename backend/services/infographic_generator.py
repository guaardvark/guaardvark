# backend/services/infographic_generator.py
# Flux schnell GGUF infographic generator.
#
# Composes a Flux-friendly prompt from structured fields (title, scene,
# footer, hashtags) or a freeform blurb, fires the workflow at the
# already-running ComfyUI on :8188, polls for completion, and returns
# the saved PNG path. Designed for "type → 5 seconds → PNG" UX.
#
# Flux schnell is the model picked here specifically because it renders
# short bold text legibly. Don't swap for SDXL — that breaks the whole
# point of having an infographic mode.

from __future__ import annotations

import copy
import logging
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    from backend.config import COMFYUI_URL  # type: ignore
except ImportError:
    COMFYUI_URL = "http://127.0.0.1:8188"

logger = logging.getLogger(__name__)


# Workflow in ComfyUI's API format (the shape /prompt expects). Stored as
# a python dict instead of a JSON file so the placeholder substitution
# stays type-safe and the diff stays readable.
_FLUX_INFOGRAPHIC_WORKFLOW: Dict[str, Dict[str, Any]] = {
    "1": {
        "class_type": "UnetLoaderGGUF",
        "inputs": {"unet_name": "flux1-schnell-Q8_0.gguf"},
    },
    "2": {
        "class_type": "DualCLIPLoader",
        "inputs": {
            "clip_name1": "t5/t5xxl_fp8_e4m3fn.safetensors",
            "clip_name2": "clip_l.safetensors",
            "type": "flux",
        },
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "ae.safetensors"},
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["2", 0]},
    },
    "5": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["2", 0]},
    },
    "6": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1280, "height": 720, "batch_size": 1},
    },
    "7": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 4,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
            "model": ["1", 0],
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": ["6", 0],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "infographic", "images": ["8", 0]},
    },
}


# Style presets — appended to the scene prompt to nudge Flux toward the
# look the user wants without bloating every request.
STYLE_PRESETS: Dict[str, str] = {
    "editorial": (
        "dramatic editorial illustration, cinematic lighting, vivid saturated colors, "
        "comic book composition, bold dynamic style"
    ),
    "flat_vector": (
        "clean flat vector design, geometric shapes, minimal shadows, "
        "professional infographic illustration, simple icons"
    ),
    "photo_real": (
        "photorealistic, professional photography composition, "
        "dramatic lighting, cinematic depth of field"
    ),
    "comic": (
        "comic book illustration, bold inked outlines, vibrant flat colors, "
        "halftone shading, dynamic action panels"
    ),
}

# Aspect → (width, height). Flux schnell trains at 1MP-ish; these are
# the standard buckets that don't break it.
ASPECT_DIMS: Dict[str, tuple] = {
    "16:9": (1280, 720),
    "1:1": (1024, 1024),
    "9:16": (720, 1280),
    "4:5": (1024, 1280),
    "3:2": (1216, 832),
}


@dataclass
class InfographicSpec:
    """Structured input. Either build this directly or use compose_from_freeform()."""
    title: str = ""
    scene: str = ""
    footer: str = ""
    hashtags: List[str] = field(default_factory=list)
    style: str = "editorial"
    aspect: str = "16:9"
    callouts: List[str] = field(default_factory=list)
    raw_prompt: str = ""  # if non-empty, used verbatim instead of composing

    def compose_prompt(self) -> str:
        """Turn the structured fields into a single Flux-ready prompt string."""
        if self.raw_prompt.strip():
            return self.raw_prompt.strip()

        style_text = STYLE_PRESETS.get(self.style, STYLE_PRESETS["editorial"])
        parts: List[str] = ["Infographic poster", style_text]

        if self.scene:
            parts.append(self.scene.strip().rstrip("."))

        # Flux renders text most reliably when it's quoted and called out
        # explicitly as "the title reads" / "banner reads" / "text reads".
        if self.title:
            parts.append(
                f'Title at the top reads: "{self.title.strip()}" '
                f'in bold uppercase typography, large dramatic headline'
            )

        for callout in self.callouts:
            cleaned = callout.strip().strip('"')
            if cleaned:
                parts.append(f'A label reads: "{cleaned}" in bold caps')

        if self.footer:
            parts.append(
                f'Bottom banner text reads: "{self.footer.strip()}" '
                f'in bold uppercase across the bottom'
            )

        if self.hashtags:
            tag_text = " ".join(
                t if t.startswith("#") else f"#{t}" for t in self.hashtags
            )
            parts.append(
                f"Bottom row of small hashtag text: {tag_text}, "
                f"thin sans-serif, centered"
            )

        parts.append(
            "vivid colors, professional design, clear composition, "
            "rendered text is sharp and legible"
        )
        return ". ".join(parts) + "."


class ComfyUIUnavailable(RuntimeError):
    """ComfyUI on :8188 is unreachable. Plugin probably isn't started."""


class InfographicGenerator:
    """Thin wrapper around ComfyUI's /prompt + /history + /view for Flux."""

    def __init__(self, comfy_url: Optional[str] = None):
        self.comfy_url = (comfy_url or COMFYUI_URL).rstrip("/")

    # -- public API --------------------------------------------------------

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.comfy_url}/system_stats", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def generate(self, spec: InfographicSpec, seed: Optional[int] = None) -> Dict[str, Any]:
        """Queue a generation and block until the PNG is on disk.

        Returns:
            {
              "prompt_id": str,
              "filename": str,        # filename inside ComfyUI/output/
              "image_url": str,       # served URL the frontend can <img src=> directly
              "prompt": str,          # what Flux actually got
              "width": int, "height": int,
              "duration_s": float,
            }
        """
        if not self.is_available():
            raise ComfyUIUnavailable(
                f"ComfyUI not reachable at {self.comfy_url}. "
                f"Enable the 'comfyui' plugin from /plugins."
            )

        prompt_text = spec.compose_prompt()
        width, height = ASPECT_DIMS.get(spec.aspect, ASPECT_DIMS["16:9"])
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)

        workflow = copy.deepcopy(_FLUX_INFOGRAPHIC_WORKFLOW)
        workflow["4"]["inputs"]["text"] = prompt_text
        workflow["6"]["inputs"]["width"] = width
        workflow["6"]["inputs"]["height"] = height
        workflow["7"]["inputs"]["seed"] = seed

        started = time.monotonic()
        prompt_id = self._queue(workflow)
        logger.info(
            f"[INFOGRAPHIC] queued prompt_id={prompt_id} "
            f"{width}x{height} seed={seed} style={spec.style}"
        )

        outputs = self._wait_for(prompt_id, timeout=180)
        filename, subfolder = self._extract_image(outputs)

        # `/view` is ComfyUI's serving endpoint. The frontend can hit it
        # directly via the proxy (vite proxies /api/* → backend, but
        # the static-asset path needs to go through our own thin proxy or
        # be served via the file system). We surface both: a comfy-direct
        # URL the user could open, and an api-proxied URL that the
        # frontend uses by default so we don't hit CORS.
        proxied_url = f"/api/infographic/view?{urllib.parse.urlencode({'filename': filename, 'subfolder': subfolder})}"

        return {
            "prompt_id": prompt_id,
            "filename": filename,
            "subfolder": subfolder,
            "image_url": proxied_url,
            "prompt": prompt_text,
            "width": width,
            "height": height,
            "seed": seed,
            "duration_s": round(time.monotonic() - started, 2),
        }

    def fetch_image_bytes(self, filename: str, subfolder: str = "") -> bytes:
        """Pull a generated PNG from ComfyUI's /view by filename."""
        params = {"filename": filename, "type": "output"}
        if subfolder:
            params["subfolder"] = subfolder
        url = f"{self.comfy_url}/view?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()

    # -- internals ---------------------------------------------------------

    def _queue(self, workflow: Dict[str, Any]) -> str:
        r = requests.post(
            f"{self.comfy_url}/prompt",
            json={"prompt": workflow},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            # ComfyUI returned without queuing — usually a node error.
            errs = data.get("node_errors") or {}
            raise RuntimeError(f"ComfyUI rejected workflow: {errs or data}")
        return prompt_id

    def _wait_for(self, prompt_id: str, timeout: int = 180) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        backoff = 0.5
        while time.monotonic() < deadline:
            try:
                r = requests.get(
                    f"{self.comfy_url}/history/{prompt_id}", timeout=5
                )
                if r.ok:
                    history = r.json() or {}
                    if prompt_id in history:
                        entry = history[prompt_id]
                        status = entry.get("status") or {}
                        if status.get("status_str") == "error":
                            raise RuntimeError(
                                f"ComfyUI generation failed: {status.get('messages')}"
                            )
                        outputs = entry.get("outputs") or {}
                        if outputs:
                            return outputs
            except requests.RequestException as e:
                logger.debug(f"history poll transient: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 1.3, 2.0)
        raise TimeoutError(
            f"ComfyUI generation {prompt_id} didn't complete within {timeout}s"
        )

    @staticmethod
    def _extract_image(outputs: Dict[str, Any]) -> tuple:
        """Find the first image entry across all SaveImage nodes."""
        for _node_id, node_out in outputs.items():
            images = node_out.get("images") or []
            for img in images:
                fname = img.get("filename")
                if fname:
                    return fname, img.get("subfolder", "")
        raise RuntimeError("ComfyUI returned no image in outputs")


# Module-level singleton — cheap, no state worth pooling
_instance: Optional[InfographicGenerator] = None


def get_infographic_generator() -> InfographicGenerator:
    global _instance
    if _instance is None:
        _instance = InfographicGenerator()
    return _instance
