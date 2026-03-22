"""Send frames to Ollama vision models and return structured results.

All callers are OS threads (API endpoint threads + StreamManager workers),
so threading.Lock is correct for serialization.
"""
import base64
import io
import time
import threading
import logging
from dataclasses import dataclass
from typing import Tuple, Optional

import requests
from PIL import Image

logger = logging.getLogger("vision_pipeline.frame_analyzer")


@dataclass
class FrameAnalysis:
    description: str
    model_used: str
    inference_ms: int
    timestamp: float
    frame_dimensions: Tuple[int, int]


class FrameAnalyzer:
    def __init__(self, ollama_url: str = "http://localhost:11434", max_parallel: int = 1):
        self.ollama_url = ollama_url
        self.escalation_model: str = "llava:13b"
        self._inference_semaphore = threading.Semaphore(max_parallel)

    def analyze(self, image: Image.Image, model: str, prompt: str) -> FrameAnalysis:
        """Run vision inference via Ollama. Thread-safe using semaphore."""
        dims = image.size
        start = time.time()

        # Encode to base64 JPEG for Ollama
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=75)
        frame_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        with self._inference_semaphore:
            try:
                resp = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": prompt,
                            "images": [frame_base64]
                        }],
                        "stream": False
                    },
                    timeout=30
                )
                elapsed_ms = int((time.time() - start) * 1000)

                if resp.status_code == 200:
                    description = resp.json().get("message", {}).get("content", "")
                    return FrameAnalysis(
                        description=description.strip(),
                        model_used=model,
                        inference_ms=elapsed_ms,
                        timestamp=time.time(),
                        frame_dimensions=dims
                    )
                else:
                    logger.error(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.error(f"Vision inference failed: {e}")
                elapsed_ms = int((time.time() - start) * 1000)

        return FrameAnalysis(
            description="",
            model_used=model,
            inference_ms=elapsed_ms,
            timestamp=time.time(),
            frame_dimensions=dims
        )

    def analyze_direct(self, image: Image.Image, user_message: str) -> FrameAnalysis:
        """Direct analysis with user's question as prompt. Uses escalation model."""
        return self.analyze(image, self.escalation_model, user_message)
