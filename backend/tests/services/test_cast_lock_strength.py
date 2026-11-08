"""Family-aware character LoRA strength (Z-Image / SDXL / FLUX)."""
from backend.services.cast_lock import (
    DEFAULT_FLUX_DEV_STRENGTH,
    DEFAULT_SDXL_STRENGTH,
    DEFAULT_ZIMAGE_STRENGTH,
    apply_lock,
    resolve_lora_strength,
    subjects_to_lock,
)


def test_resolve_lora_strength_zimage_default():
    assert resolve_lora_strength("zimage-turbo") == DEFAULT_ZIMAGE_STRENGTH
    assert resolve_lora_strength("zimage") == DEFAULT_ZIMAGE_STRENGTH


def test_resolve_lora_strength_sdxl_and_flux():
    assert resolve_lora_strength("sdxl") == DEFAULT_SDXL_STRENGTH
    assert resolve_lora_strength("flux-dev") == DEFAULT_FLUX_DEV_STRENGTH


def test_resolve_lora_strength_override_wins():
    assert resolve_lora_strength("zimage-turbo", 0.55) == 0.55
    assert resolve_lora_strength("sdxl", 1.2) == 1.2


def test_apply_lock_and_subjects_lora_includes_bible_when_requested():
    class S:
        def __init__(self):
            self.lora_path = "/tmp/x.safetensors"
            self.trigger_word = "tok"
            self.name = "Hero"
            self.bible = (
                "Hero: black armored plating with rivets on chest and shoulders, "
                "pointed cowl, white LED eyes, utility belt with pouches, black bat emblem"
            )
            self.training_settings_json = {
                "class_token": "man",
                "bible_identity_marks": "shaved head, sunglasses",
            }

    paths, lock = subjects_to_lock([S()], include_bible=True)
    assert paths == ["/tmp/x.safetensors"]
    assert "tok" in lock
    assert "a photo of tok" in lock
    assert "shaved head" in lock
    # Vision bible must reach Batch/Chat/Video so costume detail sticks
    assert "armored plating" in lock
    assert "white LED eyes" in lock
    assert "Hero:" not in lock  # name label stripped
    assert apply_lock("neon alley", lock).startswith("a photo of tok")


def test_subjects_to_lock_can_skip_bible():
    class S:
        def __init__(self):
            self.lora_path = "/tmp/x.safetensors"
            self.trigger_word = "tok"
            self.name = "Hero"
            self.bible = "armored plating with rivets"
            self.training_settings_json = {"class_token": "man", "bible_identity_marks": "cowl"}

    _, lock = subjects_to_lock([S()], include_bible=False)
    assert "cowl" in lock
    assert "armored plating" not in lock


def test_apply_lock_skips_double_prefix():
    lock = "a photo of tok, man, shaved"
    already = "a photo of tok, man, shaved, neon alley"
    assert apply_lock(already, lock) == already
    assert apply_lock("a photo of tok, different scene", lock) == "a photo of tok, different scene"


def test_stacked_zimage_loras_share_the_default_strength():
    from backend.services.cast_lock import stacked_lora_strength

    single = DEFAULT_ZIMAGE_STRENGTH
    assert stacked_lora_strength("zimage-turbo", single, 1) == single
    assert abs(stacked_lora_strength("zimage-turbo", single, 2) - 0.6) < 1e-9
    assert abs(stacked_lora_strength("zimage-turbo", single, 3) - 0.45) < 1e-9
    assert abs(stacked_lora_strength("zimage-turbo", single, 5) - 0.27) < 1e-9


def test_stacking_keeps_strength_for_unmeasured_families():
    from backend.services.cast_lock import stacked_lora_strength

    assert stacked_lora_strength("sdxl", DEFAULT_SDXL_STRENGTH, 3) == DEFAULT_SDXL_STRENGTH
    assert stacked_lora_strength("flux-dev", DEFAULT_FLUX_DEV_STRENGTH, 2) == DEFAULT_FLUX_DEV_STRENGTH
