# backend/utils/vision_analyzer.py
#!/usr/bin/env python3
"""
Vision Analyzer — Direct Ollama vision model calls for Agent Vision Control.

Bypasses the Vision Pipeline service to avoid its single-threaded inference lock.
Calls Ollama's /api/chat endpoint directly with image attachments.
"""

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional, Tuple

import requests
from PIL import Image

from backend.config import OLLAMA_BASE_URL
from backend.services.model_capability_data import NON_TEXT_NAME_PATTERNS
from backend.utils.ollama_resource_manager import request_options, same_model

logger = logging.getLogger(__name__)


def _writes_text(model_name: str) -> bool:
    """Whether a model can answer a chat request with text. When Ollama describes
    the model its capability list decides: it needs "completion", and embedding
    models and decision models (nimble and tev1 report only "decision") cannot
    write a reply. A model Ollama cannot describe, or one described without a
    capability list, falls back to the name rules: NON_TEXT_NAME_PATTERNS
    (embedding and vision-only names) and DECISION_NAME_PATTERNS."""
    from backend.services.model_capabilities import capabilities_for, decides_by_name
    try:
        rec = capabilities_for(model_name, with_vision=False)
    except Exception:  # noqa: BLE001
        rec = None
    if rec is None or not rec.exists or not rec.capabilities:
        lower = model_name.lower()
        return (not any(re.search(p, lower) for p in NON_TEXT_NAME_PATTERNS)
                and not decides_by_name(model_name))
    return rec.completion and not rec.embedding and "decision" not in rec.capabilities


def _sees(model_name: str) -> bool:
    """Whether the model takes images: Ollama's "vision" capability, through
    the capability resolver (which guesses by name only when Ollama cannot
    describe the model)."""
    from backend.services.model_capabilities import capabilities_for
    try:
        return bool(capabilities_for(model_name).vision)
    except Exception:  # noqa: BLE001
        from backend.services.model_capability_data import name_looks_vision
        return name_looks_vision(model_name)


def _full_chat_model(model_name: str) -> bool:
    """A model Ollama lists with "tools" is a full chat model; one without it
    (moondream, the llava family) mostly captions. A model Ollama cannot
    describe is not ranked down."""
    from backend.services.model_capabilities import capabilities_for
    try:
        rec = capabilities_for(model_name, with_vision=False)
    except Exception:  # noqa: BLE001
        return True
    return rec.tools if rec.exists and rec.capabilities else True


def _saved_chat_model() -> str:
    """The chat model the person chose, from the saved setting (no Flask or DB)."""
    try:
        from backend.config import _read_saved_model_name
        name = (_read_saved_model_name() or "").strip()
    except Exception:
        return ""
    if name and ":" not in name:
        name += ":latest"
    return name


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

    # Vision models to try in order — gemma4 first (unified brain), moondream as fallback
    _VISION_MODEL_PRIORITY = [
        "gemma4:e4b",
        "moondream:latest",
        "llava:latest",
    ]

    def __init__(
        self,
        ollama_url: str = None,
        default_model: str = None,
        max_width: int = 1024,
        timeout: int = 45,
    ):
        self.ollama_url = ollama_url or OLLAMA_BASE_URL
        self.default_model = default_model or self._detect_vision_model()
        self.max_width = max_width
        self.timeout = timeout

    def _detect_vision_model(self) -> str:
        """Pick a model that can actually accept images.

        Order: a vision-capable model already in VRAM (so we do not load a
        second multi-GB model to look at one screenshot), then the best
        installed one.

        This used to decide by name, guarded by the servo store's has_vision
        flag, under the belief — written in the old comment here — that "every
        gemma4 tag is multimodal". On this machine
        `VladimirGav/gemma4-26b-16GB-VRAM-Uncensored` disproves it: the name
        matches, the model has no vision tower, and handing it an image earns
        an Ollama 400. Meanwhile the name rules were rejecting genuinely
        multimodal Mistral and Qwen builds. Ollama's own capabilities answer
        both cases; the capability resolver is where that lives now.
        """
        try:
            from backend.services.model_capability_resolver import (
                coords_for, sees_natively, _installed, _resident,
            )

            for active in _resident():
                if sees_natively(active):
                    logger.info("[VISION] Reusing the vision model already in VRAM: %s", active)
                    return active

            candidates = [m for m in _installed() if sees_natively(m)]
            if candidates:
                # Prefer an eye whose pointing convention we have actually
                # measured — an unmeasured one is readable for describing but
                # not trustworthy for clicking.
                candidates.sort(key=lambda m: (-coords_for(m).confidence, m))
                pick = candidates[0]
                logger.info("[VISION] Auto-detected vision model: %s", pick)
                return pick
        except Exception as e:  # noqa: BLE001
            logger.debug("Vision detection via resolver failed (%s); using the static list", e)

        # Resolver or Ollama unavailable. Static list, unchanged.
        try:
            tags_resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if tags_resp.status_code == 200:
                available = {m["name"] for m in tags_resp.json().get("models", [])}
                for model in self._VISION_MODEL_PRIORITY:
                    if model in available:
                        logger.info(f"[VISION] Auto-detected vision model: {model}")
                        return model
        except Exception as e:
            logger.debug(f"Vision detection error: {e}")
        return "moondream:latest"  # Final fallback

    def text_query(self, prompt: str, model: str = None, think: bool = False,
                   system: Optional[str] = None, num_predict: Optional[int] = None,
                   temperature: Optional[float] = None) -> VisionResult:
        """
        Query a text LLM (no image) for reasoning/decision-making.

        The "brain" uses a text model for structured decision output.
        The "eye" (analyze) uses a vision model for scene description.
        These are intentionally separate — vision models produce poor
        structured JSON; text models can't see images.

        Args:
            prompt: Text prompt (includes scene description from vision model)
            model: Ollama text model name (default: auto-detect from active models)
            think: Allow thinking tokens (default: False)

        Returns:
            VisionResult with the LLM's text response
        """
        if not model:
            # The agent loop must always name its brain. A silent auto-pick is
            # how a blind user model ended up "deciding" through a third model
            # nobody chose; see resolve_brain_eye in agent_control_service.
            model, why = self._pick_decision_model()
            logger.warning("[VISION] text_query called without model= — auto-picked %s: %s "
                           "(legacy path; pass the brain explicitly)", model, why)
            if not model:
                return VisionResult(success=False, error=why)

        try:
            import time
            start = time.time()

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            opts = {}
            if num_predict is not None:
                opts["num_predict"] = num_predict
            if temperature is not None:
                opts["temperature"] = temperature
            request_body = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": request_options(model, **opts),
            }
            if not think:
                request_body["think"] = False

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=request_body,
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

    def _get_decision_model(self) -> Optional[str]:
        """The model the legacy text path will use, or None when no installed
        model can write a reply (_pick_decision_model says why)."""
        return self._pick_decision_model()[0]

    def _pick_decision_model(self) -> Tuple[Optional[str], str]:
        """Legacy fallback: pick a model that can write a text reply when the
        caller named none. Returns (model or None, why).

        Only for callers that genuinely have no brain of their own
        (apprentice_engine, film_curator). The agent loop passes its brain
        explicitly and must never land here.

        Order: GUAARDVARK_DECISION_MODEL when installed; the person's saved
        chat model when Ollama already has it loaded (under that name or
        another name for the same build), then any loaded text-only model, so
        one decision does not load a second model and evict chat on a small
        card; the preferred text models; the first installed model that does
        not see (Ollama's "vision" capability, not its name); then a model
        that sees after all, the saved chat model first, then full chat models
        before captioners. A vision model must not quietly become the decider
        while a text model is installed, but on a stock install (gemma4:12b or
        qwen3.5, and nomic-embed-text) it is the only model that can reply. Embedding and
        decision models never qualify (_writes_text), the override included:
        they cannot answer.
        """
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
        except Exception as e:  # noqa: BLE001
            return None, (f"could not reach Ollama at {self.ollama_url} to list its models "
                          f"({type(e).__name__})")
        if response.status_code != 200:
            return None, f"Ollama returned {response.status_code} when listing its models"
        try:
            tags = list(response.json().get("models", []))
            models = [m["name"] for m in tags]
        except Exception as e:  # noqa: BLE001
            return None, f"could not read Ollama's model list: {e}"

        override = os.environ.get("GUAARDVARK_DECISION_MODEL")
        if override and override in models:
            if _writes_text(override):
                return override, "GUAARDVARK_DECISION_MODEL"
            logger.warning("[VISION] GUAARDVARK_DECISION_MODEL=%s cannot write a text reply "
                           "(embedding or decision model); picking another", override)

        saved = _saved_chat_model()
        loaded = self._loaded_models()
        if saved:
            for name in loaded:
                if (name == saved or same_model(name, saved, tags=tags)) and _writes_text(name):
                    return name, "the saved chat model, already loaded"
        for name in loaded:
            if not _sees(name) and _writes_text(name):
                return name, "a text-only model already loaded"

        # Prefer these text models in order (smarter models first for planning)
        for preferred in ["llama3.1:8b", "llama3:8b", "llama3:latest",
                          "mistral:latest", "gemma2:latest"]:
            if preferred in models:
                return preferred, "preferred text model"

        for m in models:
            if not _sees(m) and _writes_text(m):
                return m, "first installed text-only model"

        seeing = [m for m in models if _sees(m) and _writes_text(m)]
        if saved in seeing:
            return saved, "the saved chat model; no text-only model is installed"
        seeing.sort(key=lambda m: not _full_chat_model(m))
        if seeing:
            return seeing[0], "no text-only model is installed"
        return None, ("no installed Ollama model can write a text reply (embedding and "
                      "decision models cannot); install a chat model")

    def _loaded_models(self) -> list:
        """Names of the models Ollama has loaded (/api/ps); [] when it cannot say."""
        try:
            response = requests.get(f"{self.ollama_url}/api/ps", timeout=5)
            if response.status_code != 200:
                return []
            return [m["name"] for m in response.json().get("models", []) if m.get("name")]
        except Exception:  # noqa: BLE001
            return []

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
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def analyze(
        self,
        image: Image.Image,
        prompt: str,
        model: str = None,
        num_predict: int = 256,
        temperature: float = 0.3,
        think: bool = False,
        system: Optional[str] = None,
    ) -> VisionResult:
        """
        Analyze an image using an Ollama vision model.

        Args:
            image: PIL Image to analyze
            prompt: Text prompt for the vision model
            model: Ollama model name (default: self.default_model)
            num_predict: Max tokens to generate (default: 256)
            temperature: Sampling temperature (default: 0.3)
            think: Allow thinking tokens (default: False — thinking models
                   like Gemma4 burn through num_predict on hidden reasoning,
                   returning empty content)
            system: Optional system-role content. Used to carry persistent
                   instructions or knowledge that must not compete with the
                   per-call user prompt for action-format conditioning. The
                   agent's cross-session memory rides this slot.

        Returns:
            VisionResult with description or error
        """
        model = model or self.default_model
        image_b64 = self.encode_image(image)

        try:
            import time
            start = time.time()

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            })

            request_body = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": request_options(model, num_predict=num_predict, temperature=temperature),
            }
            if not think:
                request_body["think"] = False

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=request_body,
                timeout=self.timeout,
            )

            elapsed_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                err_text = response.text[:200]
                # Text-only model got images — retry once with a known vision model.
                if (
                    response.status_code == 400
                    and "multimodal" in err_text.lower()
                    and not getattr(self, "_multimodal_retry", False)
                ):
                    for fallback in self._VISION_MODEL_PRIORITY:
                        if fallback == model:
                            continue
                        logger.warning(
                            "[VISION] %s rejected multimodal input — retrying with %s",
                            model, fallback,
                        )
                        self._multimodal_retry = True
                        try:
                            return self.analyze(
                                image, prompt, model=fallback,
                                num_predict=num_predict, temperature=temperature,
                                think=think, system=system,
                            )
                        finally:
                            self._multimodal_retry = False
                return VisionResult(
                    success=False,
                    error=f"Ollama returned {response.status_code}: {err_text}",
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

    def analyze_fullsize(
        self,
        image: Image.Image,
        prompt: str,
        model: str = None,
        num_predict: int = 256,
        temperature: float = 0.3,
        think: bool = False,
    ) -> VisionResult:
        """Analyze an image WITHOUT resizing — critical for coordinate accuracy.

        The standard analyze() resizes to max_width=1024px. Through Ollama,
        Gemma4 returns raw pixel coordinates in the image's own space. Resizing
        makes those coordinates wrong.

        Empirically verified 2026-04-10 (on old 1280x720 screen):
          Full 1280x720 -> 35px error (HIT)
          Resized 1024x576 -> 263px error (MISS)
        With 1024x1024 square screen, box_2d /1024 * 1024 = identity (0px error expected).
        """
        model = model or self.default_model

        # Encode WITHOUT resize — just JPEG compress
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        try:
            import time
            start = time.time()

            request_body = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
                "stream": False,
                "options": request_options(model, num_predict=num_predict, temperature=temperature),
            }
            if not think:
                request_body["think"] = False

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=request_body,
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
        except Exception as e:
            logger.error(f"Vision analyze_fullsize error: {e}", exc_info=True)
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
        think: bool = False,
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
            think: Allow thinking tokens (default: False)

        Returns:
            VisionResult with description or error
        """
        model = model or self.default_model

        try:
            import time
            start = time.time()

            request_body = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
                "stream": False,
                "options": request_options(model, num_predict=num_predict, temperature=temperature),
            }
            if not think:
                request_body["think"] = False

            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=request_body,
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
