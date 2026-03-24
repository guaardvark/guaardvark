# backend/utils/vision_analyzer.py
#!/usr/bin/env python3
"""
Vision Analyzer — Direct Ollama vision model calls for Agent Vision Control.

Bypasses the Vision Pipeline service to avoid its single-threaded inference lock.
Calls Ollama's /api/chat endpoint directly with image attachments.
"""

import base64
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

from backend.config import OLLAMA_BASE_URL

logger = logging.getLogger(__name__)


@dataclass
class VisionResult:
    """Result from a vision analysis call."""
    description: str = ""
    model_used: str = ""
    success: bool = True
    error: Optional[str] = None
    inference_ms: int = 0


class VisionAnalyzer:
    """
    Direct Ollama vision analysis — bypasses Vision Pipeline.

    This exists because the Vision Pipeline's FrameAnalyzer holds a
    threading.Lock during inference. The AgentLoop needs concurrent
    access without blocking video chat or other vision consumers.
    """

    # Vision models to try in order — qwen3-vl first (moondream fails on dark UIs)
    _VISION_MODEL_PRIORITY = [
        "qwen3-vl:2b-instruct",
        "qwen3-vl:8b",
        "moondream:latest",
        "llava:latest",
    ]

    def __init__(
        self,
        ollama_url: str = None,
        default_model: str = None,
        max_width: int = 1024,
        timeout: int = 30,
    ):
        self.ollama_url = ollama_url or OLLAMA_BASE_URL
        self.default_model = default_model or self._detect_vision_model()
        self.max_width = max_width
        self.timeout = timeout

    def _detect_vision_model(self) -> str:
        """Auto-detect best available vision model from Ollama."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                available = {m["name"] for m in response.json().get("models", [])}
                for model in self._VISION_MODEL_PRIORITY:
                    if model in available:
                        logger.info(f"[VISION] Auto-detected vision model: {model}")
                        return model
        except Exception:
            pass
        return "moondream:latest"  # Final fallback

    def text_query(self, prompt: str, model: str = None) -> VisionResult:
        """
        Query a text LLM (no image) for reasoning/decision-making.

        The "brain" uses a text model for structured decision output.
        The "eye" (analyze) uses a vision model for scene description.
        These are intentionally separate — vision models produce poor
        structured JSON; text models can't see images.

        Args:
            prompt: Text prompt (includes scene description from vision model)
            model: Ollama text model name (default: auto-detect from active models)

        Returns:
            VisionResult with the LLM's text response
        """
        model = model or self._get_decision_model()

        try:
            import time
            start = time.time()

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=self.timeout,
            )

            elapsed_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                return VisionResult(
                    success=False,
                    error=f"Ollama returned {response.status_code}: {response.text[:200]}",
                    model_used=model,
                    inference_ms=elapsed_ms,
                )

            content = response.json().get("message", {}).get("content", "").strip()
            return VisionResult(
                description=content,
                model_used=model,
                success=True,
                inference_ms=elapsed_ms,
            )

        except requests.Timeout:
            return VisionResult(success=False, error=f"Ollama timed out after {self.timeout}s", model_used=model)
        except requests.ConnectionError:
            return VisionResult(success=False, error=f"Connection error — is Ollama running at {self.ollama_url}?", model_used=model)
        except Exception as e:
            logger.error(f"Text query error: {e}", exc_info=True)
            return VisionResult(success=False, error=str(e), model_used=model)

    def _get_decision_model(self) -> str:
        """Auto-detect best available text model for decision-making."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                # Prefer these text models in order (smarter models first for planning)
                for preferred in ["qwen3.5:9b", "qwen3:8b", "qwen3:latest", "llama3.1:8b",
                                  "llama3:8b", "llama3:latest", "mistral:latest", "gemma2:latest"]:
                    if preferred in models:
                        return preferred
                # Fall back to any non-vision model
                vision_patterns = ["moondream", "llava", "bakllava", "qwen-vl"]
                for m in models:
                    if not any(vp in m.lower() for vp in vision_patterns):
                        return m
        except Exception:
            pass
        return "llama3:8b"  # Final fallback

    def encode_image(self, image: Image.Image) -> str:
        """
        Encode PIL Image to base64 JPEG string, resizing if needed.

        Args:
            image: PIL Image to encode

        Returns:
            Base64-encoded JPEG string
        """
        # Resize if wider than max_width
        if image.width > self.max_width:
            ratio = self.max_width / image.width
            new_height = int(image.height * ratio)
            image = image.resize((self.max_width, new_height), Image.LANCZOS)

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=70)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def analyze(
        self,
        image: Image.Image,
        prompt: str,
        model: str = None,
        num_predict: int = 256,
        temperature: float = 0.3,
    ) -> VisionResult:
        """
        Analyze an image using an Ollama vision model.

        Args:
            image: PIL Image to analyze
            prompt: Text prompt for the vision model
            model: Ollama model name (default: self.default_model)
            num_predict: Max tokens to generate (default: 256)
            temperature: Sampling temperature (default: 0.3)

        Returns:
            VisionResult with description or error
        """
        model = model or self.default_model
        image_b64 = self.encode_image(image)

        try:
            import time
            start = time.time()

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": prompt,
                        "images": [image_b64],
                    }],
                    "stream": False,
                    "options": {
                        "num_predict": num_predict,
                        "temperature": temperature,
                    },
                },
                timeout=self.timeout,
            )

            elapsed_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                return VisionResult(
                    success=False,
                    error=f"Ollama returned {response.status_code}: {response.text[:200]}",
                    model_used=model,
                    inference_ms=elapsed_ms,
                )

            content = response.json().get("message", {}).get("content", "").strip()
            return VisionResult(
                description=content,
                model_used=model,
                success=True,
                inference_ms=elapsed_ms,
            )

        except requests.Timeout:
            return VisionResult(
                success=False,
                error=f"Ollama timed out after {self.timeout}s",
                model_used=model,
            )
        except requests.ConnectionError:
            return VisionResult(
                success=False,
                error=f"Connection error — is Ollama running at {self.ollama_url}?",
                model_used=model,
            )
        except Exception as e:
            logger.error(f"Vision analysis error: {e}", exc_info=True)
            return VisionResult(
                success=False,
                error=str(e),
                model_used=model,
            )

    def analyze_base64(
        self,
        image_b64: str,
        prompt: str,
        model: str = None,
        num_predict: int = 256,
        temperature: float = 0.3,
    ) -> VisionResult:
        """
        Analyze an image from raw base64 — bypasses PIL entirely.

        Use this when the image bytes may be in a format Pillow cannot decode
        (e.g., AVIF without pillow-heif, or exotic browser-supplied formats).
        Ollama/moondream can often handle formats that Pillow cannot.

        Args:
            image_b64: Base64-encoded image bytes (no data URI prefix)
            prompt: Text prompt for the vision model
            model: Ollama model name (default: self.default_model)
            num_predict: Max tokens to generate (default: 256)
            temperature: Sampling temperature (default: 0.3)

        Returns:
            VisionResult with description or error
        """
        model = model or self.default_model

        try:
            import time
            start = time.time()

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": prompt,
                        "images": [image_b64],
                    }],
                    "stream": False,
                    "options": {
                        "num_predict": num_predict,
                        "temperature": temperature,
                    },
                },
                timeout=self.timeout,
            )

            elapsed_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                return VisionResult(
                    success=False,
                    error=f"Ollama returned {response.status_code}: {response.text[:200]}",
                    model_used=model,
                    inference_ms=elapsed_ms,
                )

            content = response.json().get("message", {}).get("content", "").strip()
            return VisionResult(
                description=content,
                model_used=model,
                success=True,
                inference_ms=elapsed_ms,
            )

        except requests.Timeout:
            return VisionResult(
                success=False,
                error=f"Ollama timed out after {self.timeout}s",
                model_used=model,
            )
        except requests.ConnectionError:
            return VisionResult(
                success=False,
                error=f"Connection error — is Ollama running at {self.ollama_url}?",
                model_used=model,
            )
        except Exception as e:
            logger.error(f"Vision analyze_base64 error: {e}", exc_info=True)
            return VisionResult(
                success=False,
                error=str(e),
                model_used=model,
            )
