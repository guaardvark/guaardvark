"""Model tier selection: routes to monitor (fast) or escalation (detailed) models.

Three triggers:
- 'background': monitor model, terse prompt
- 'change_detected': monitor model, change-focused prompt
- 'user_query': escalation model, detailed prompt

Which installed models can see is read from Ollama's ``/api/show``
``capabilities`` array, the same rule as
``backend/services/model_capability_resolver.sees_natively``. ``/api/tags``
is not a substitute: it omits ``vision`` for the gemma4 and qwen3-vl
families, and a name filter misses them too (``qwen3-vl:2b`` carries no
"vision" or "llava" in its tag). Only when Ollama cannot describe a model
does the name guess below apply, and the log says so.
"""
import logging
import re
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger("vision_pipeline.model_tier")

# Name guess, used only when /api/show is unreachable for a tag.
VISION_MODEL_PATTERNS = [
    "vision", "llava", "moondream", "bakllava",
    "minicpm-v", "llama.*vision", "granite.*vision", "gemma4", "gemma3",
    "qwen.*vl", "qwen3\\.5", "cogvlm", "internvl", "phi.*vision", "deepseek.*vl",
    "pixtral", "molmo", "lfm3-vl", "lfm3-omni",
]

TAGS_CACHE_SECONDS = 30
SHOW_CACHE_SECONDS = 600


def name_looks_vision(tag: str) -> bool:
    low = tag.lower()
    return any(re.search(p, low) for p in VISION_MODEL_PATTERNS)


class OllamaCapabilities:
    """Cached view of the installed models and what each one can do."""

    def __init__(self, ollama_url: str, timeout: float = 5.0):
        self.ollama_url = ollama_url.rstrip("/")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._tags: List[str] = []
        self._tags_at: float = 0.0
        # tag -> (capabilities, evidence, fetched_at)
        self._show: Dict[str, Tuple[Set[str], str, float]] = {}

    def installed(self) -> List[str]:
        now = time.time()
        with self._lock:
            if now - self._tags_at < TAGS_CACHE_SECONDS and self._tags:
                return list(self._tags)
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=self.timeout)
            if resp.status_code == 200:
                tags = [m["name"] for m in resp.json().get("models", []) if m.get("name")]
                with self._lock:
                    self._tags = tags
                    self._tags_at = now
                return list(tags)
            logger.warning("Ollama /api/tags returned %s", resp.status_code)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to query Ollama models: %s", e)
        with self._lock:
            return list(self._tags)

    def capabilities(self, tag: str) -> Tuple[Set[str], str]:
        """``(capabilities, evidence)`` for one tag.

        evidence is ``api_show_capabilities`` when Ollama described the tag,
        ``not_installed`` when it answered 404, or
        ``name_guess_ollama_unreachable`` when it could not be reached and
        the name was used instead.
        """
        now = time.time()
        with self._lock:
            hit = self._show.get(tag)
            if hit and now - hit[2] < SHOW_CACHE_SECONDS:
                return set(hit[0]), hit[1]
        caps: Optional[Set[str]] = None
        try:
            resp = requests.post(f"{self.ollama_url}/api/show",
                                 json={"model": tag}, timeout=self.timeout)
            if resp.status_code == 200:
                caps = set((resp.json() or {}).get("capabilities") or [])
                evidence = "api_show_capabilities"
            elif resp.status_code == 404:
                # Ollama answered: the tag is not installed, so it sees nothing.
                caps = set()
                evidence = "not_installed"
            else:
                logger.warning("Ollama /api/show %s returned %s", tag, resp.status_code)
        except Exception as e:  # noqa: BLE001
            logger.warning("Ollama /api/show failed for %s: %s", tag, e)
        if caps is None:
            caps = {"vision"} if name_looks_vision(tag) else set()
            evidence = "name_guess_ollama_unreachable"
            logger.info("Guessed capabilities of %s from its name: %s", tag, sorted(caps))
        with self._lock:
            self._show[tag] = (set(caps), evidence, now)
        return set(caps), evidence

    def sees_natively(self, tag: str) -> bool:
        return "vision" in self.capabilities(tag)[0]

    def supports_thinking(self, tag: str) -> bool:
        return "thinking" in self.capabilities(tag)[0]

    def vision_models(self) -> List[str]:
        return [t for t in self.installed() if self.sees_natively(t)]

    def is_installed(self, tag: str) -> bool:
        """Exact tag, or the tag with ``:latest`` implied."""
        tags = set(self.installed())
        if tag in tags:
            return True
        if ":" not in tag and f"{tag}:latest" in tags:
            return True
        return False


class ModelTier:
    MONITOR = "monitor"
    ESCALATION = "escalation"
    DIRECT = "direct"

    def __init__(self, monitor_model: str, escalation_model: str,
                 fallback_order: List[str], ollama_url: str,
                 monitor_prompt: str, escalation_prompt: str,
                 capabilities: Optional[OllamaCapabilities] = None):
        self.monitor_model = monitor_model
        self.escalation_model = escalation_model
        self.fallback_order = fallback_order
        self.ollama_url = ollama_url
        self.monitor_prompt = monitor_prompt
        self.escalation_prompt = escalation_prompt
        self.capabilities = capabilities or OllamaCapabilities(ollama_url)

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
        """Installed in Ollama and reports the vision capability."""
        if not model_name:
            return False
        return (self.capabilities.is_installed(model_name)
                and self.capabilities.sees_natively(model_name))

    def get_any_available_model(self) -> Optional[str]:
        """Return first installed vision model, or None."""
        models = self.capabilities.vision_models()
        return models[0] if models else None

    def supports_thinking(self, model_name: str) -> bool:
        return self.capabilities.supports_thinking(model_name)

    def resolve(self, preferred: str) -> str:
        """Public form of the fallback walk, for callers outside the loop."""
        return self._resolve_model(preferred)

    def _resolve_model(self, preferred: str) -> str:
        """Return preferred model if available, else first from fallback_order, else any."""
        if self.verify_model_available(preferred):
            return preferred
        for fallback in self.fallback_order:
            if self.verify_model_available(fallback):
                logger.info("Preferred model %s unavailable, using %s", preferred, fallback)
                return fallback
        any_model = self.get_any_available_model()
        if any_model:
            logger.warning("No preferred model available, using %s", any_model)
            return any_model
        raise RuntimeError("No vision model available in Ollama")

    def _get_vision_models(self) -> List[str]:
        return self.capabilities.vision_models()
