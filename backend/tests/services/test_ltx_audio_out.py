"""LTX-2.3/2.5 builders can decode the audio latent they already sample."""
import pytest

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
from backend.services import video_model_registry as vmr


def _gen():
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


CASES = [
    ("_create_ltx23_t2v_workflow", {}, "13", "4", "15"),
    ("_create_ltx23_i2v_workflow", {"image_filename": "start.png"}, "15", "4", "17"),
    ("_create_ltx25_t2v_workflow", {}, "22", "4", "24"),
    ("_create_ltx25_i2v_workflow", {"image_filename": "start.png"}, "24", "4", "26"),
]


@pytest.mark.parametrize("builder,extra,separate,audio_vae,combine", CASES)
def test_audio_out_decodes_the_final_separate_node_into_the_mux(builder, extra, separate, audio_vae, combine):
    wf = getattr(_gen(), builder)(prompt="a river at dawn", audio_out=True, **extra)
    decode = [nid for nid, n in wf.items() if isinstance(n, dict) and n.get("class_type") == "LTXVAudioVAEDecode"]
    assert len(decode) == 1
    node = wf[decode[0]]
    assert node["inputs"] == {"samples": [separate, 1], "audio_vae": [audio_vae, 0]}
    assert wf[separate]["class_type"] == "LTXVSeparateAVLatent"
    assert wf[audio_vae]["class_type"] == "LTXVAudioVAELoader"
    assert wf[combine]["class_type"] == "VHS_VideoCombine"
    assert wf[combine]["inputs"]["audio"] == [decode[0], 0]


@pytest.mark.parametrize("builder,extra,separate,audio_vae,combine", CASES)
def test_default_is_unchanged_and_silent(builder, extra, separate, audio_vae, combine):
    wf = getattr(_gen(), builder)(prompt="a river at dawn", **extra)
    types = {n["class_type"] for n in wf.values() if isinstance(n, dict)}
    assert "LTXVAudioVAEDecode" not in types
    assert "audio" not in wf[combine]["inputs"]


def test_audio_survives_interpolation():
    wf = _gen()._create_ltx25_t2v_workflow(prompt="x", audio_out=True, interpolation_multiplier=2)
    decode = next(nid for nid, n in wf.items() if isinstance(n, dict) and n.get("class_type") == "LTXVAudioVAEDecode")
    assert wf["24"]["inputs"]["audio"] == [decode, 0]
    assert wf["24"]["inputs"]["images"] != ["23", 0]  # RIFE rewired the frames, not the audio


def test_registry_keeps_ltx_audio_off_until_measured():
    for mid in ("ltx23-distilled-fp8", "ltx25-distilled-int8"):
        assert vmr.VIDEO_MODEL_REGISTRY[mid]["audio_out"] is False
        assert vmr.model_capabilities(mid)["audio_out"] is False
