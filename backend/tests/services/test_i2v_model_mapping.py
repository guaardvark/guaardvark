"""Cinematic keyframe → the chosen model animates it, never a silent swap."""
import pytest

from backend.services import video_model_registry as vmr
from backend.services.batch_video_generator import BatchVideoGenerator

GEN_TYPES = {"wan", "cogvideox", "ltx", "hunyuan", "minimax"}


def _generation_models():
    return [k for k, e in vmr.VIDEO_MODEL_REGISTRY.items() if e["type"] in GEN_TYPES]


@pytest.mark.parametrize("model_id", _generation_models())
def test_every_model_animates_in_its_own_family(model_id):
    target = BatchVideoGenerator._to_i2v_model(model_id)
    assert target in vmr.VIDEO_MODEL_REGISTRY
    assert vmr.VIDEO_MODEL_REGISTRY[target]["type"] == vmr.VIDEO_MODEL_REGISTRY[model_id]["type"]
    assert vmr.supports_first_frame_i2v(target)


def test_native_first_frame_models_keep_the_job():
    for mid in ("minimax-h3-int8", "ltx25-distilled-int8", "ltx23-distilled-fp8",
                "wan22-5b", "wan22-14b-i2v", "cogvideox-5b-i2v", "hunyuan-i2v"):
        assert BatchVideoGenerator._to_i2v_model(mid) == mid, mid


def test_pure_t2v_models_hand_off_to_their_sibling():
    assert BatchVideoGenerator._to_i2v_model("wan22-14b") == "wan22-14b-i2v"
    assert BatchVideoGenerator._to_i2v_model("cogvideox-5b") == "cogvideox-5b-i2v"
    assert BatchVideoGenerator._to_i2v_model("hunyuan-t2v") == "hunyuan-i2v"


def test_unknown_model_falls_back_to_default_i2v():
    assert BatchVideoGenerator._to_i2v_model("") == vmr.DEFAULT_I2V_MODEL
    assert BatchVideoGenerator._to_i2v_model("not-a-model") == vmr.DEFAULT_I2V_MODEL
