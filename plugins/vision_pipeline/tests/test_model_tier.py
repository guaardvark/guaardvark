"""The tier reads what a model can do from Ollama /api/show, not from its name."""
from unittest.mock import MagicMock, patch

import pytest

from service.model_tier import ModelTier, OllamaCapabilities, name_looks_vision

TAGS = {"models": [{"name": "qwen3.5:4b"}, {"name": "gemma4:12b"},
                   {"name": "qwen3:14b"}, {"name": "moondream:latest"}]}
SHOW = {
    "qwen3.5:4b": ["completion", "vision", "tools", "thinking"],
    "gemma4:12b": ["completion", "vision", "audio", "thinking"],
    "qwen3:14b": ["completion", "thinking"],
    "moondream:latest": ["completion", "vision"],
    "moondream": ["completion", "vision"],
}


def _show_router(url, json=None, timeout=None):
    tag = (json or {}).get("model")
    if tag in SHOW:
        return MagicMock(status_code=200, json=MagicMock(return_value={"capabilities": SHOW[tag]}))
    return MagicMock(status_code=404, json=MagicMock(return_value={"error": "not found"}))


@pytest.fixture
def caps():
    with patch("service.model_tier.requests.get") as mock_get, \
         patch("service.model_tier.requests.post", side_effect=_show_router):
        mock_get.return_value = MagicMock(status_code=200, json=MagicMock(return_value=TAGS))
        yield OllamaCapabilities("http://localhost:11434")


class TestOllamaCapabilities:
    def test_vision_comes_from_api_show_not_the_name(self, caps):
        # qwen3.5:4b carries no "vision" or "llava" in its tag.
        assert caps.sees_natively("qwen3.5:4b") is True
        assert caps.capabilities("qwen3.5:4b")[1] == "api_show_capabilities"
        assert caps.sees_natively("qwen3:14b") is False

    def test_thinking_flag(self, caps):
        assert caps.supports_thinking("gemma4:12b") is True
        assert caps.supports_thinking("moondream:latest") is False

    def test_not_installed_tag_sees_nothing(self, caps):
        assert caps.sees_natively("llava:13b") is False
        assert caps.capabilities("llava:13b")[1] == "not_installed"
        assert caps.is_installed("llava:13b") is False

    def test_latest_is_implied(self, caps):
        assert caps.is_installed("moondream") is True

    def test_vision_models_lists_only_seeing_models(self, caps):
        assert caps.vision_models() == ["qwen3.5:4b", "gemma4:12b", "moondream:latest"]

    def test_name_guess_only_when_ollama_is_unreachable(self):
        with patch("service.model_tier.requests.post", side_effect=ConnectionError("down")):
            c = OllamaCapabilities("http://localhost:11434")
            seen, evidence = c.capabilities("qwen3-vl:2b")
            assert evidence == "name_guess_ollama_unreachable"
            assert "vision" in seen
        assert name_looks_vision("qwen3:14b") is False


class TestModelTier:
    def _tier(self, caps, monitor="qwen3.5:4b", escalation="qwen3.5:9b"):
        return ModelTier(monitor, escalation, ["gemma4:12b", "moondream"],
                         "http://localhost:11434", "monitor prompt", "escalation prompt",
                         capabilities=caps)

    def test_preferred_models_when_installed(self, caps):
        tier = self._tier(caps, escalation="gemma4:12b")
        assert tier.select_model("background") == ("qwen3.5:4b", "monitor prompt")
        assert tier.select_model("user_query") == ("gemma4:12b", "escalation prompt")

    def test_missing_escalation_walks_the_fallback_chain(self, caps):
        tier = self._tier(caps)  # qwen3.5:9b is not in TAGS
        assert tier.select_model("user_query")[0] == "gemma4:12b"
        assert tier.resolve("llava:13b") == "gemma4:12b"

    def test_text_only_model_is_never_picked(self, caps):
        tier = self._tier(caps, monitor="qwen3:14b")
        assert tier.select_model("background")[0] == "gemma4:12b"

    def test_no_vision_model_raises(self):
        with patch("service.model_tier.requests.get") as mock_get, \
             patch("service.model_tier.requests.post", side_effect=_show_router):
            mock_get.return_value = MagicMock(
                status_code=200, json=MagicMock(return_value={"models": [{"name": "qwen3:14b"}]}))
            tier = self._tier(OllamaCapabilities("http://localhost:11434"))
            with pytest.raises(RuntimeError):
                tier.select_model("background")
