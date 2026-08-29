"""LTX-2.5 Comfy graph shape — local weights only, two-stage distilled."""
import json

import pytest

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator


def _gen():
    # Skip __init__ (it probes a live ComfyUI). Graph builders only need
    # class-level LTX_MODELS / clip-device helpers.
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def _t2v():
    return _gen()._create_ltx25_t2v_workflow(
        prompt="a dog running through a meadow",
        model_key="ltx25-distilled-int8",
        num_frames=65,
        width=768,
        height=512,
        seed=42,
        fps=16,
    )


def _i2v():
    return _gen()._create_ltx25_i2v_workflow(
        image_filename="start.png",
        prompt="the camera dollies out",
        model_key="ltx25-distilled-int8",
        num_frames=65,
        width=768,
        height=512,
        seed=42,
        fps=16,
    )


def _class_types(wf):
    return {n["class_type"] for n in wf.values() if isinstance(n, dict) and "class_type" in n}


def test_ltx25_stage1_is_half_res():
    assert ComfyUIVideoGenerator._ltx25_stage1_size(768, 512) == (384, 256)


def test_t2v_uses_cliploader_not_dualclip():
    types = _class_types(_t2v())
    assert "CLIPLoader" in types
    assert "DualCLIPLoader" not in types
    assert "UNETLoader" in types
    # The official distilled template drives both stages with CFGGuider at
    # cfg=1. LTXVDualCFGGuider (separate video/audio cfg) collapses to the same
    # thing when the two are equal, and audio is discarded here anyway.
    assert "CFGGuider" in types
    assert "LTXVDualCFGGuider" not in types
    assert "LTXVLatentUpsampler" in types
    assert "LatentUpscaleModelLoader" in types
    assert "VHS_VideoCombine" in types
    assert "CreateVideo" not in types
    assert "LTXVAudioVAEDecode" not in types


def test_t2v_filenames_match_registry():
    wf = _t2v()
    assert wf["1"]["inputs"]["unet_name"] == (
        "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
    )
    assert wf["2"]["inputs"]["clip_name"] == (
        "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
    )
    assert wf["3"]["inputs"]["vae_name"] == "ltx-2.5-video-vae-bf16.safetensors"
    assert wf["4"]["inputs"]["ckpt_name"] == "ltx-2.5-audio-vae-bf16.safetensors"
    assert wf["5"]["inputs"]["model_name"] == (
        "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"
    )
    assert wf["8"]["inputs"]["width"] == 384
    assert wf["8"]["inputs"]["height"] == 256


def test_t2v_distilled_sigma_schedules():
    wf = _t2v()
    sigmas = [
        n["inputs"]["sigmas"]
        for n in wf.values()
        if n.get("class_type") == "ManualSigmas"
    ]
    assert ComfyUIVideoGenerator._LTX25_STAGE1_SIGMAS in sigmas
    assert ComfyUIVideoGenerator._LTX25_STAGE2_SIGMAS in sigmas


def test_i2v_includes_img_to_video():
    wf = _i2v()
    types = _class_types(wf)
    assert "LTXVImgToVideo" in types
    assert "LTXVPreprocess" in types
    assert "LoadImage" in types
    assert wf["10"]["inputs"]["width"] == 384
    assert wf["10"]["inputs"]["height"] == 256
    assert wf["10"]["inputs"]["image"] == ["9", 0]


def test_graphs_have_no_cloud_hooks():
    blob = json.dumps(_t2v()) + json.dumps(_i2v())
    for needle in ("ltx.io", "console.ltx", "Partner", "partner_node", "LTX Desktop"):
        assert needle.lower() not in blob.lower()


def test_ltx23_graph_untouched():
    wf = _gen()._create_ltx23_t2v_workflow(
        prompt="x",
        model_key="ltx23-distilled-fp8",
        seed=1,
    )
    types = _class_types(wf)
    assert "DualCLIPLoader" in types
    assert "CLIPLoader" not in types
    assert "LTXVLatentUpsampler" not in types
    assert "KSampler" in types


def _live_object_info():
    import json, urllib.request
    try:
        return json.load(urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=5))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"live ComfyUI not reachable at 127.0.0.1:8188 ({type(e).__name__}); "
                    f"registry validation needs the real node registry")


@pytest.mark.parametrize("build", [_t2v, _i2v], ids=["t2v", "i2v"])
def test_ltx25_graph_validates_against_live_comfyui(build):
    """Every class_type exists in the running ComfyUI and every required input
    is wired. 8a49f0a: a guider node that was not registered made every LTX-2.5
    queue attempt 400 while the unit tests stayed green."""
    info = _live_object_info()
    wf = build()
    problems = []
    for nid, node in wf.items():
        ct = node["class_type"]
        if ct not in info:
            problems.append(f"{nid}: unknown class {ct}")
            continue
        required = info[ct].get("input", {}).get("required", {})
        missing = [k for k in required if k not in node["inputs"]]
        if missing:
            problems.append(f"{nid} {ct}: missing required {missing}")
        for k, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and str(v[0]) not in wf:
                problems.append(f"{nid} {ct}.{k} -> dangling ref {v}")
    assert not problems, problems
