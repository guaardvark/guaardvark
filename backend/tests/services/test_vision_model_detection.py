"""Vision model detection must never pick text-only VRAM residents."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.servo_knowledge_store import (
    get_vision_config,
    model_name_looks_vision,
)


def test_text_only_qwen_does_not_look_vision():
    assert not model_name_looks_vision("jaahas/qwen3.5-uncensored:latest")
    assert not model_name_looks_vision("qwen3.5:9b")
    assert not model_name_looks_vision("llama3:latest")


def test_known_vision_names_look_vision():
    assert model_name_looks_vision("gemma4:e4b")
    assert model_name_looks_vision("moondream:latest")
    assert model_name_looks_vision("qwen3-vl:4b-instruct")
    assert model_name_looks_vision("qwen2.5vl:7b-q4_K_M")


def test_unknown_text_model_config_has_no_vision():
    """A model with no config row follows what Ollama reports for it."""
    from backend.services import model_capability_resolver as R

    with patch.object(R, "_info", return_value={"capabilities": ["completion", "tools"]}):
        cfg = get_vision_config("jaahas/qwen3.5-uncensored:latest")
    assert cfg["has_vision"] is False
    assert cfg.get("vision_model")  # external eyes


def test_unknown_model_ollama_reports_as_sighted_uses_its_own_eyes():
    from backend.services import model_capability_resolver as R

    with patch.object(R, "_info", return_value={"capabilities": ["completion", "vision"]}):
        cfg = get_vision_config("jaahas/qwen3.5-uncensored:latest")
    assert cfg["has_vision"] is True
    assert cfg["vision_model"] is None
    assert cfg["source"] == "resolver_api_show_capabilities"


def test_detect_skips_text_only_active_and_picks_a_real_vision_model():
    """A text-only model sitting in VRAM must never be chosen as the eye.

    The reuse-what-is-resident rule exists to avoid loading a second multi-GB
    model for one screenshot, and it is worth keeping — but only for a model
    that can actually take an image. Sending one to a text-only model is an
    Ollama 400.

    Mocked at the capability resolver rather than at requests, because that is
    where the question is now answered. The previous version of this test
    patched vision_analyzer's own requests.get, which no longer sees the calls.
    """
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services import model_capability_resolver as R

    sighted = {"gemma4:e4b", "moondream:latest"}
    with patch.object(R, "_resident", return_value=["jaahas/qwen3.5-uncensored:latest"]), \
         patch.object(R, "_installed", return_value=[
             "jaahas/qwen3.5-uncensored:latest", "gemma4:e4b", "moondream:latest"]), \
         patch.object(R, "sees_natively", side_effect=lambda m: m in sighted):
        az = VisionAnalyzer(ollama_url="http://localhost:11434")
    assert az.default_model == "gemma4:e4b", (
        "a text-only VRAM resident must not be picked as the eye")


def test_detect_reuses_a_vision_model_already_in_vram():
    """The VRAM-saving half of the same rule."""
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services import model_capability_resolver as R

    with patch.object(R, "_resident", return_value=["qwen3.5:9b"]), \
         patch.object(R, "_installed", return_value=["qwen3.5:9b", "gemma4:e4b"]), \
         patch.object(R, "sees_natively", return_value=True):
        az = VisionAnalyzer(ollama_url="http://localhost:11434")
    assert az.default_model == "qwen3.5:9b", "should not load a second model needlessly"
