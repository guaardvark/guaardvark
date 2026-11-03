"""Hardware profiling across Ollama vision models.

Benchmarks each model at multiple resolutions for latency and VRAM, and
recommends monitor and escalation models by speed alone. It does not score
answer quality: the earlier token-count rubric rewarded verbosity, not
accuracy, and was removed rather than left to steer a model choice.
_assign_role is a module-level function (tests import it directly).
"""
import time
import json
import base64
import io
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional, Generator

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger("vision_pipeline.benchmarker")


@dataclass
class BenchmarkResult:
    model: str
    resolution: tuple
    avg_inference_ms: float
    min_inference_ms: float
    max_inference_ms: float
    sustainable_fps: float
    vram_used_mb: float
    recommended_role: str


def _assign_role(avg_ms: float) -> str:
    """Assign recommended role based on inference speed."""
    if avg_ms < 200:
        return "monitor"
    elif avg_ms < 800:
        return "escalation"
    return "too_slow"


class Benchmarker:
    def __init__(self, ollama_url: str = "http://localhost:11434", capabilities=None):
        self.ollama_url = ollama_url
        self.capabilities = capabilities
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
                if resp.status_code != 200:
                    logger.warning("Benchmark: %s returned %s", model, resp.status_code)
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
            recommended_role=_assign_role(avg_ms),
        )

    def get_recommendations(self) -> dict:
        """Fastest model in each latency band. Speed only; see the module docstring."""
        monitors = [r for r in self.results if r.recommended_role == "monitor"]
        escalations = [r for r in self.results if r.recommended_role == "escalation"]

        return {
            "monitor_model": min(monitors, key=lambda r: r.avg_inference_ms).model if monitors else None,
            "escalation_model": min(escalations, key=lambda r: r.avg_inference_ms).model if escalations else None,
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
        """Installed models that report the vision capability."""
        from service.model_tier import OllamaCapabilities
        caps = self.capabilities or OllamaCapabilities(self.ollama_url)
        return caps.vision_models()

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
