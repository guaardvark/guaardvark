import json

from backend.services import pan2_prompt_compiler as pan2
from backend.services import video_render_limits as limits
from backend.services.video_model_registry import (
    clip_defaults_for,
    model_capabilities,
    pan2_comfyui_map,
    verify_registry,
)


def test_registry_entries_are_complete():
    assert not [p for p in verify_registry() if p.startswith("pan2")]
    for mid in ("pan2-int8", "pan2-nvfp4"):
        mapped = pan2_comfyui_map()[mid]
        assert mapped["clip"] == "qwen3.5_9b_int8_convrot.safetensors"
        assert mapped["vae"] == "wan2.2_vae.safetensors"
        caps = model_capabilities(mid)
        assert caps["modes"] == ["t2v", "i2v"]
        assert caps["license"]["name"] == "Apache License 2.0"


def test_render_limits_hold_the_measured_settings():
    assert limits.resolve_canvas("pan2-int8", 1280, 720, 121, "pan2") == (1248, 704)
    assert limits.resolve_frames("pan2-int8", None, "pan2") == 121
    assert limits.resolve_steps("pan2-int8", 20, family="pan2") == 50
    assert limits.resolve_steps("pan2-int8", 30, explicit=True, family="pan2") == 30
    assert limits.cfg_when_unset("pan2-int8", "pan2") == 2.5


def test_a_request_without_settings_gets_the_measured_clip():
    defaults = clip_defaults_for("pan2-int8")
    assert (defaults["width"], defaults["height"], defaults["duration_frames"]) == (1248, 704, 121)
    assert defaults["num_inference_steps"] == 50


def test_plain_prompt_becomes_a_caption_with_the_words_kept():
    text = pan2.caption_text("A red fox trots through fresh snow", duration_s=5.04, style="realistic")
    data = json.loads(text)
    assert data["events"][0]["event_description"] == "A red fox trots through fresh snow"
    assert data["events"][0]["time_range_sec"] == "0 to 5.04"
    assert data["video_style"].startswith("Live-action, photorealistic")


def test_a_written_caption_is_sent_unchanged():
    caption = json.dumps({"video_style": "x", "events": []})
    assert pan2.caption_text(caption, duration_s=5.0) == caption


def test_negative_keeps_typed_words_in_front_of_the_reference():
    data = json.loads(pan2.negative_text("cartoon"))
    assert data["video_style"].startswith("cartoon, low quality")
    assert json.loads(pan2.negative_text(None)) == pan2.reference_negative()
