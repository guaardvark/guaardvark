"""Storyboard sessions book their keyframe model's registry price.

image_render_limits.still_vram_mb reads the price from media_model_registry
(IMAGE_FAMILY_SPECS / IMAGE_MODEL_LIMITS, which read video_model_registry for
the ComfyUI families). Film Crew and Music Video book that price for the
chosen keyframe model, capped at what each session booked as a fixed number,
so a card that admitted a storyboard still does.
"""
import pytest

from backend.services import video_model_registry as vmr
from backend.services.image_render_limits import still_vram_mb
from backend.services.media_model_registry import IMAGE_FAMILY_SPECS

CARD_8, CARD_12, CARD_16 = 8188, 12282, 16376


# ── still_vram_mb ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model, family", [
    ("zimage-turbo", "zimage"),
    ("flux-schnell", "flux"),
    ("flux-dev-lora", "flux"),
    ("moss-image", "moss-image"),
    ("qwen-image-21", "qwen-image-21"),
    ("flux3-klein", "flux3-klein"),
])
def test_price_is_the_family_row_on_every_card(model, family):
    for card in (CARD_8, CARD_12, CARD_16):
        assert still_vram_mb(model, default=1, total_vram_mb=card) == IMAGE_FAMILY_SPECS[family]["vram_mb"]


def test_a_model_row_overrides_its_family_price():
    assert still_vram_mb("sana-sprint-0.6b", default=1, total_vram_mb=CARD_8) == 5100


@pytest.mark.parametrize("model", ["sdxl", "sd-xl", "sdxl-lora"])
def test_sdxl_is_priced_for_model_offload_below_its_threshold(model):
    row = IMAGE_FAMILY_SPECS["sdxl"]
    assert still_vram_mb(model, default=1, total_vram_mb=CARD_8) == row["vram_mb_offload"]
    assert still_vram_mb(model, default=1, total_vram_mb=CARD_12) == row["vram_mb"]
    assert still_vram_mb(model, default=1, total_vram_mb=CARD_16) == row["vram_mb"]


def test_the_card_is_probed_when_not_given(monkeypatch):
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: CARD_8)
    assert still_vram_mb("sdxl", default=1) == IMAGE_FAMILY_SPECS["sdxl"]["vram_mb_offload"]
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: 0)
    assert still_vram_mb("sdxl", default=1) == IMAGE_FAMILY_SPECS["sdxl"]["vram_mb"]


@pytest.mark.parametrize("model", [None, "", "from-lora", "some-new-model"])
def test_a_model_without_a_row_gets_the_default(model):
    assert still_vram_mb(model, default=10000, total_vram_mb=CARD_8) == 10000


def test_canvas_above_one_megapixel_adds_the_family_slope():
    row = IMAGE_FAMILY_SPECS["zimage"]
    assert still_vram_mb("zimage-turbo", default=1, width=1024, height=576) == row["vram_mb"]
    two_mp = still_vram_mb("zimage-turbo", default=1, width=2048, height=1024)
    assert two_mp == row["vram_mb"] + row["vram_slope_mb_per_mp"]
    # FLUX is priced flat for ComfyUI.
    assert still_vram_mb("flux-dev", default=1, width=2048, height=1024) == IMAGE_FAMILY_SPECS["flux"]["vram_mb"]


# ── Film Crew ────────────────────────────────────────────────────────────────

def test_film_crew_books_the_heaviest_keyframe_model_up_to_its_ceiling(monkeypatch):
    from backend.tasks import production_swarm_tasks as pst
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: CARD_8)
    ceiling = pst._STORYBOARD_VRAM_CEILING_MB
    assert pst._storyboard_vram_mb(["sdxl"]) == IMAGE_FAMILY_SPECS["sdxl"]["vram_mb_offload"]
    assert pst._storyboard_vram_mb(["sdxl", "moss-image"]) == IMAGE_FAMILY_SPECS["moss-image"]["vram_mb"]
    assert pst._storyboard_vram_mb(["sdxl", "zimage-turbo"]) == min(IMAGE_FAMILY_SPECS["zimage"]["vram_mb"], ceiling)
    assert pst._storyboard_vram_mb(["flux-dev"]) == ceiling
    assert pst._storyboard_vram_mb(["sdxl", None]) == ceiling
    assert pst._storyboard_vram_mb([]) == ceiling


def test_film_crew_shot_model_follows_its_cast_loras(monkeypatch):
    from backend.tasks import production_swarm_tasks as pst
    loras = {}
    monkeypatch.setattr(pst, "_shot_loras_and_prompt", lambda shot: (loras.get(shot, []), ""))
    monkeypatch.setattr(
        "backend.services.media_model_registry.resolve_inference_for_loras",
        lambda paths: {"offline_model_key": "sd-xl", "comfy_model_tag": "sdxl"},
    )
    assert pst._shot_keyframe_model("plain") == "zimage-turbo"
    assert pst._shot_keyframe_model("plain", image_generator=object()) is None
    loras["cast"] = ["/loras/a.safetensors"]
    assert pst._shot_keyframe_model("cast") == "sd-xl"

    def unreadable(paths):
        raise ValueError("Cannot mix LoRAs trained for different bases")
    monkeypatch.setattr("backend.services.media_model_registry.resolve_inference_for_loras", unreadable)
    assert pst._shot_keyframe_model("cast") is None


# ── Music Video ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model, card, expected", [
    ("zimage-turbo", CARD_16, 10000),
    ("flux-schnell", CARD_16, 10000),
    ("sdxl", CARD_8, 5700),
    ("sdxl", CARD_16, 8000),
    ("moss-image", CARD_12, 8200),
    ("from-lora", CARD_8, 10000),
    (None, CARD_8, 10000),
])
def test_music_video_books_the_keyframe_model_up_to_the_ceiling(monkeypatch, model, card, expected):
    from backend.tasks.music_video_tasks import _keyframe_vram_mb
    monkeypatch.setattr(vmr, "_probe_total_vram_mb", lambda: card)
    assert _keyframe_vram_mb({"keyframe_model": model}, 10000) == expected
