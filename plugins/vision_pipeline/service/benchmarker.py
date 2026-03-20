"""Hardware profiling across Ollama vision models.

Benchmarks each model at multiple resolutions, scoring speed, quality,
and VRAM usage. Recommends optimal monitor and escalation models.
_compute_quality_score and _assign_role are module-level functions
(tests import them directly).
"""
import time
import json
import base64
import io
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional, Generator

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("vision_pipeline.benchmarker")

# Spatial relationship keywords for quality scoring
SPATIAL_KEYWORDS = ["left", "right", "above", "below", "behind", "next to",
                    "in front", "beside", "between", "on top", "under", "near"]


@dataclass
class BenchmarkResult:
    model: str
    resolution: tuple
    avg_inference_ms: float
    min_inference_ms: float
    max_inference_ms: float
    sustainable_fps: float
    vram_used_mb: float
    quality_score: float
    recommended_role: str


def _compute_quality_score(description: str) -> float:
    """Quality rubric (1-10):
    - 1pt per 10 tokens (max 5)
    - 1pt per distinct entity/object (max 3) — count nouns heuristically
    - 2pt if spatial relationships referenced
    """
    if not description:
        return 0.0

    tokens = description.split()
    # Token count score (max 5)
    token_score = min(len(tokens) / 10.0, 5.0)

    # Entity score (max 3) — count capitalized words and common object nouns
    entities = set()
    for word in tokens:
        cleaned = word.strip(".,;:!?\"'()[]")
        if len(cleaned) > 2 and (cleaned[0].isupper() or cleaned in {
            "person", "people", "car", "desk", "laptop", "phone", "book",
            "chair", "table", "screen", "window", "door", "wall", "floor",
            "building", "tree", "road", "sign", "light", "camera"
        }):
            entities.add(cleaned.lower())
    entity_score = min(len(entities), 3.0)

    # Spatial score (0 or 2)
    desc_lower = description.lower()
    spatial_score = 2.0 if any(kw in desc_lower for kw in SPATIAL_KEYWORDS) else 0.0

    return min(token_score + entity_score + spatial_score, 10.0)


def _assign_role(avg_ms: float) -> str:
    """Assign recommended role based on inference speed."""
    if avg_ms < 200:
        return "monitor"
    elif avg_ms < 800:
        return "escalation"
    return "too_slow"


class Benchmarker:
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url
        self.results: List[BenchmarkResult] = []

    def run(self, models: List[str] = None, frame_count: int = 5,
            resolutions: List[tuple] = None) -> Generator[BenchmarkResult, None, None]:
        """Benchmark each model x resolution combo. Yields results as they complete."""
        if resolutions is None:
            resolutions = [(512, 512)]

        if models is None:
            models = self._discover_vision_models()

        for model in models:
            for resolution in resolutions:
                result = self._benchmark_single(model, resolution, frame_count)
                self.results.append(result)
                yield result

    def _benchmark_single(self, model: str, resolution: tuple, frame_count: int) -> BenchmarkResult:
        """Benchmark a single model at a single resolution."""
        frame = self._generate_test_frame(resolution)
        timings = []
        last_description = ""

        for i in range(frame_count):
            start = time.time()
            try:
                resp = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Describe this image in detail.", "images": [frame]}],
                        "stream": False
                    },
                    timeout=60
                )
                elapsed_ms = (time.time() - start) * 1000
                timings.append(elapsed_ms)
                if resp.status_code == 200:
                    last_description = resp.json().get("message", {}).get("content", "")
            except Exception as e:
                elapsed_ms = (time.time() - start) * 1000
                timings.append(elapsed_ms)
                logger.warning(f"Benchmark inference failed for {model}: {e}")

        avg_ms = sum(timings) / len(timings) if timings else 9999
        vram = self._get_vram_usage()

        return BenchmarkResult(
            model=model,
            resolution=resolution,
            avg_inference_ms=round(avg_ms, 1),
            min_inference_ms=round(min(timings), 1) if timings else 0,
            max_inference_ms=round(max(timings), 1) if timings else 0,
            sustainable_fps=round(min(1000.0 / avg_ms, 10.0), 2) if avg_ms > 0 else 0,
            vram_used_mb=vram,
            quality_score=round(_compute_quality_score(last_description), 1),
            recommended_role=_assign_role(avg_ms),
        )

    def get_recommendations(self) -> dict:
        """Return best monitor + escalation models from results."""
        monitors = [r for r in self.results if r.recommended_role == "monitor"]
        escalations = [r for r in self.results if r.recommended_role == "escalation"]

        return {
            "monitor_model": min(monitors, key=lambda r: r.avg_inference_ms).model if monitors else None,
            "escalation_model": max(escalations, key=lambda r: r.quality_score).model if escalations else None,
            "max_fps": min(monitors, key=lambda r: r.avg_inference_ms).sustainable_fps if monitors else 1.0,
            "recommended_resolution": 512,
        }

    def save_results(self, path: str):
        """Persist results to JSON."""
        with open(path, "w") as f:
            json.dump({
                "last_run": time.time(),
                "results": [asdict(r) for r in self.results]
            }, f, indent=2)

    def _generate_test_frame(self, resolution: tuple) -> str:
        """Create a test image with colored shapes and text overlay."""
        img = Image.new("RGB", resolution, (70, 130, 180))
        draw = ImageDraw.Draw(img)
        w, h = resolution
        # Draw some shapes for the model to describe
        draw.rectangle([w//4, h//4, 3*w//4, 3*h//4], fill=(200, 50, 50))
        draw.ellipse([w//3, h//3, 2*w//3, 2*h//3], fill=(50, 200, 50))
        draw.text((10, 10), "Benchmark Test Frame", fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    def _discover_vision_models(self) -> List[str]:
        """Query Ollama for available vision models."""
        from service.model_tier import VISION_MODEL_PATTERNS
        import re
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return [m for m in models
                        if any(re.search(p, m.lower()) for p in VISION_MODEL_PATTERNS)]
        except Exception:
            pass
        return []

    def _get_vram_usage(self) -> float:
        """Query current VRAM usage in MB. Best-effort."""
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return round(info.used / (1024 * 1024), 1)
        except Exception:
            return 0.0
