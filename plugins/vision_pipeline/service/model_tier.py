"""Model tier selection — routes to monitor (fast) or escalation (detailed) models.

Three triggers:
- 'background': monitor model, terse prompt
- 'change_detected': monitor model, change-focused prompt
- 'user_query': escalation model, detailed prompt
"""
import logging
import requests
from typing import Tuple, List, Optional

logger = logging.getLogger("vision_pipeline.model_tier")

# Same vision model patterns as backend/utils/chat_utils.py
VISION_MODEL_PATTERNS = [
    "vision", "llava", "moondream", "bakllava", "qwen.*vl",
    "minicpm-v", "llama.*vision", "granite.*vision", "gemma.*vision",
    "cogvlm", "internvl", "phi.*vision", "deepseek.*vl",
    "pixtral", "molmo",
]


class ModelTier:
    MONITOR = "monitor"
    ESCALATION = "escalation"
    DIRECT = "direct"

    def __init__(self, monitor_model: str, escalation_model: str,
                 fallback_order: List[str], ollama_url: str,
                 monitor_prompt: str, escalation_prompt: str):
        self.monitor_model = monitor_model
        self.escalation_model = escalation_model
        self.fallback_order = fallback_order
        self.ollama_url = ollama_url
        self.monitor_prompt = monitor_prompt
        self.escalation_prompt = escalation_prompt
        self._available_vision_models: List[str] = []
        self._cache_time: float = 0

    def select_model(self, trigger: str) -> Tuple[str, str]:
        """Returns (model_name, prompt) based on trigger type."""
        if trigger == "user_query":
            model = self._resolve_model(self.escalation_model)
            return model, self.escalation_prompt
        elif trigger == "change_detected":
            model = self._resolve_model(self.monitor_model)
            return model, "Describe what changed compared to the previous scene."
        else:  # background
            model = self._resolve_model(self.monitor_model)
            return model, self.monitor_prompt

    def verify_model_available(self, model_name: str) -> bool:
        """Check if model is available in Ollama."""
        models = self._get_vision_models()
        return any(model_name.lower() in m.lower() for m in models)

    def get_any_available_model(self) -> Optional[str]:
        """Return first available vision model, or None."""
        models = self._get_vision_models()
        return models[0] if models else None

    def _resolve_model(self, preferred: str) -> str:
        """Return preferred model if available, else first from fallback_order, else any."""
        if self.verify_model_available(preferred):
            return preferred
        for fallback in self.fallback_order:
            if self.verify_model_available(fallback):
                logger.info(f"Preferred model {preferred} unavailable, using {fallback}")
                return fallback
        any_model = self.get_any_available_model()
        if any_model:
            logger.warning(f"No preferred model available, using {any_model}")
            return any_model
        raise RuntimeError("No vision model available in Ollama")

    def _get_vision_models(self) -> List[str]:
        """Query Ollama for available vision models. Cached 30s."""
        import re
        import time
        now = time.time()
        if now - self._cache_time < 30 and self._available_vision_models:
            return self._available_vision_models

        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                all_models = [m["name"] for m in resp.json().get("models", [])]
                vision = []
                for model in all_models:
                    for pattern in VISION_MODEL_PATTERNS:
                        if re.search(pattern, model.lower()):
                            vision.append(model)
                            break
                self._available_vision_models = vision
                self._cache_time = now
        except Exception as e:
            logger.warning(f"Failed to query Ollama models: {e}")

        return self._available_vision_models
