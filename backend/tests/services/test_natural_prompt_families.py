"""Natural-language encoder families (Z-Image) never get CLIP-era tag stuffing.

Measured 2026-09-07: "full body shot, realistic stance, correct anatomy, ..."
appended to a seated two-person scene produced tangled legs on every seed;
the bare sentence and a prose rewrite were clean on the same seeds.
"""
from unittest.mock import patch

from backend.services import stills_defaults
from backend.services.stills_policy import (
    apply_enhance_to_prompts,
    prompt_style_for_model,
    resolve_enhance_mode,
)
from backend.services.offline_image_generator import OfflineImageGenerator


COUCH = "A man and woman watching a movie on a couch, her head is on his shoulder."


def test_prompt_style_is_declared_per_family():
    assert stills_defaults.prompt_style("zimage-turbo") == "natural"
    assert stills_defaults.prompt_style("Tongyi-MAI/Z-Image-Turbo") == "natural"
    assert stills_defaults.prompt_style("sd-xl") == "tags"
    assert stills_defaults.prompt_style("krea2-turbo") == "tags"
    # Qwen-Image 2.1 encodes with Qwen3-VL and reads prompts as prose.
    assert stills_defaults.prompt_style("qwen-image-21") == "natural"
    assert stills_defaults.prompt_style("user-qwen-image-21-uc-fp8") == "natural"
    assert stills_defaults.prompt_style_for_family("qwen-image-21") == "natural"
    # generator-side family names resolve too
    assert stills_defaults.prompt_style_for_family("zimage") == "natural"
    assert stills_defaults.prompt_style_for_family("krea2") == "tags"
    assert stills_defaults.prompt_style_for_family("") == "tags"


def test_default_ladder_is_director_for_natural_and_offline_for_tags():
    assert resolve_enhance_mode(model="zimage-turbo", verbatim=False) == "director"
    assert resolve_enhance_mode(model="sd-xl", verbatim=False) == "offline"
    # no model → legacy default unchanged
    assert resolve_enhance_mode(verbatim=False) == "offline"
    # explicit choices still win
    assert resolve_enhance_mode(model="zimage-turbo", auto_enhance=False, verbatim=False) == "none"
    assert resolve_enhance_mode(model="zimage-turbo", enhance="offline", verbatim=False) == "offline"
    assert resolve_enhance_mode(model="zimage-turbo", verbatim=True) == "none"


def test_offline_enhancer_sends_natural_family_prompt_as_written():
    gen = OfflineImageGenerator()
    enhanced, neg, det = gen.enhance_prompt_for_quality(
        COUCH, style="realistic", auto_enhance=True, family="zimage",
    )
    assert enhanced == COUCH
    assert det["enhancements_applied"] == []
    assert det["prompt_style"] == "natural"
    assert "full body shot" not in enhanced
    assert isinstance(neg, str)


def test_offline_enhancer_adds_one_prose_clause_for_non_photo_styles():
    gen = OfflineImageGenerator()
    enhanced, _neg, _det = gen.enhance_prompt_for_quality(
        "a fox in the snow", style="sketch", auto_enhance=True, family="zimage",
    )
    assert enhanced.startswith("a fox in the snow.")
    assert "pencil sketch" in enhanced
    assert "," not in enhanced.split("The image is", 1)[0]
    assert "masterpiece" not in enhanced and "high quality" not in enhanced


def test_tag_families_keep_the_legacy_suffixes():
    gen = OfflineImageGenerator()
    enhanced, _neg, det = gen.enhance_prompt_for_quality(
        COUCH, style="realistic", auto_enhance=True, family="sdxl",
    )
    assert enhanced.startswith(COUCH)
    assert det["enhancements_applied"]


def test_director_uses_prose_contract_for_natural_models():
    captured = {}

    def fake_enhance(prompts, *, style="", extra_guidance=None, prompt_style=None, **_kw):
        captured["prompt_style"] = prompt_style
        return [p + " rewritten" for p in prompts]

    with patch("backend.services.media_director.enhance_prompts", fake_enhance):
        out = apply_enhance_to_prompts([COUCH], enhance_mode="director", model="zimage-turbo")
    assert out == [COUCH + " rewritten"]
    assert captured["prompt_style"] == "natural"
    assert prompt_style_for_model("zimage-turbo") == "natural"
