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
from typing import Callable, Optional, Tuple

import requests
from PIL import Image

logger = logging.getLogger("vision_pipeline.frame_analyzer")

DEFAULT_NUM_PREDICT = 256
DEFAULT_TEMPERATURE = 0.2
DEFAULT_KEEP_ALIVE = "10m"


@dataclass
class FrameAnalysis:
    description: str
    model_used: str
    inference_ms: int
    timestamp: float            # when the answer came back (wall clock)
    frame_dimensions: Tuple[int, int]
    captured_at: float = 0.0    # the caller's capture stamp, or submit time


class FrameAnalyzer:
    def __init__(self, ollama_url: str = "http://localhost:11434",
                 num_predict: int = DEFAULT_NUM_PREDICT,
                 temperature: float = DEFAULT_TEMPERATURE,
                 keep_alive: str = DEFAULT_KEEP_ALIVE,
                 timeout: float = 30.0):
        self.ollama_url = ollama_url
        self.num_predict = num_predict
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.timeout = timeout
        # Used by analyze_direct when no model is named: the tier's fallback
        # walk, so a missing escalation model never reaches Ollama literally.
        self.escalation_model: str = ""
        self.model_resolver: Optional[Callable[[], str]] = None
        # Returns True when the model advertises thinking; only then is
        # ``think: false`` sent, the flag is rejected by models without it.
        self.supports_thinking: Callable[[str], bool] = lambda model: False
        self._inference_lock = threading.Lock()

    def analyze(self, frame_base64: str, model: str, prompt: str,
                captured_at: Optional[float] = None,
                num_predict: Optional[int] = None) -> FrameAnalysis:
        """Run vision inference via Ollama. Thread-safe."""
        dims = self._get_dimensions(frame_base64)
        start = time.time()
        captured = captured_at if captured_at else start

        body = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [frame_base64],
            }],
            "stream": False,
            "options": {
                "num_predict": int(num_predict or self.num_predict),
                "temperature": self.temperature,
            },
            "keep_alive": self.keep_alive,
        }
        try:
            if self.supports_thinking(model):
                body["think"] = False
        except Exception as e:  # noqa: BLE001
            logger.debug("thinking probe failed for %s: %s", model, e)

        with self._inference_lock:
            try:
                resp = requests.post(f"{self.ollama_url}/api/chat", json=body,
                                     timeout=self.timeout)
                elapsed_ms = int((time.time() - start) * 1000)

                if resp.status_code == 200:
                    description = resp.json().get("message", {}).get("content", "")
                    return FrameAnalysis(
                        description=description.strip(),
                        model_used=model,
                        inference_ms=elapsed_ms,
                        timestamp=time.time(),
                        frame_dimensions=dims,
                        captured_at=captured,
                    )
                logger.error("Ollama returned %s: %s", resp.status_code, resp.text[:200])
            except Exception as e:  # noqa: BLE001
                logger.error("Vision inference failed: %s", e)
                elapsed_ms = int((time.time() - start) * 1000)

        return FrameAnalysis(
            description="",
            model_used=model,
            inference_ms=elapsed_ms,
            timestamp=time.time(),
            frame_dimensions=dims,
            captured_at=captured,
        )

    def analyze_direct(self, frame_base64: str, user_message: str) -> FrameAnalysis:
        """Direct analysis with the user's question as prompt, on the
        escalation model the tier resolves (or ``escalation_model`` when no
        resolver is wired)."""
        model = self.escalation_model
        if self.model_resolver is not None:
            try:
                model = self.model_resolver()
            except Exception as e:  # noqa: BLE001
                logger.error("No escalation model could be resolved: %s", e)
        if not model:
            return FrameAnalysis(description="", model_used="", inference_ms=0,
                                 timestamp=time.time(),
                                 frame_dimensions=self._get_dimensions(frame_base64))
        return self.analyze(frame_base64, model, user_message, num_predict=512)

    def _get_dimensions(self, frame_base64: str) -> Tuple[int, int]:
        """Extract frame dimensions without full decode."""
        try:
            img = Image.open(io.BytesIO(base64.b64decode(frame_base64)))
            return img.size
        except Exception:  # noqa: BLE001
            return (0, 0)
