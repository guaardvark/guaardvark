"""CogVideoX I2V loads the registry's local files; generation never downloads.

2026-08-28: the I2V workflow handed DownloadAndLoadCogVideoModel a Hugging Face
repo id, so pressing Generate fetched an 11GB diffusers snapshot the person
never asked for, while the 10.4GB file Manage Video Models had installed sat
unused. These pin the file-based graph and the launch-time guards.
"""
import re
from pathlib import Path

from backend.services import comfyui_launch_flags as flags
from backend.services import video_model_registry as vmr
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

REPO = Path(__file__).resolve().parents[3]
START_SH = REPO / "plugins" / "comfyui" / "scripts" / "start.sh"
HUB_ID = re.compile(r"^(kijai|thudm|comfy-org)/", re.I)


def _gen():
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def _i2v(**kw):
    args = dict(image_filename="start.png", prompt="the camera pushes in",
                num_frames=49, width=720, height=480, seed=42, fps=8)
    args.update(kw)
    return _gen()._create_cogvideox_i2v_workflow(**args)


def _class_types(wf):
    return {n["class_type"] for n in wf.values() if isinstance(n, dict) and "class_type" in n}


def test_registry_is_clean():
    assert vmr.verify_registry() == []


def test_i2v_entry_installs_a_file_the_loader_can_see():
    entry = vmr.VIDEO_MODEL_REGISTRY["cogvideox-5b-i2v"]
    assert entry["local_subdir"] == "checkpoints"          # existing installs stay valid
    assert entry["check_files"] == ["CogVideoX_1_5_5b_I2V_bf16.safetensors"]
    assert entry["files"][0]["also_link"] == "diffusion_models"
    assert "cogvideox-vae" in entry["requires"]
    assert vmr.VIDEO_MODEL_REGISTRY["cogvideox-vae"]["local_subdir"] == "vae"


def test_map_derives_from_registry():
    assert vmr.cogvideox_comfyui_map() == {
        "cogvideox-5b-i2v": {
            "unet": "CogVideoX_1_5_5b_I2V_bf16.safetensors",
            "vae": "cogvideox_vae_bf16.safetensors",
        }
    }


def test_i2v_graph_never_names_a_hub_id():
    wf = _i2v()
    types = _class_types(wf)
    assert "DownloadAndLoadCogVideoModel" not in types
    assert "CogVideoXModelLoader" in types
    assert "CogVideoXVAELoader" in types
    assert wf["4"]["inputs"]["model"] == "CogVideoX_1_5_5b_I2V_bf16.safetensors"
    assert wf["4v"]["inputs"]["model_name"] == "cogvideox_vae_bf16.safetensors"
    # No node input carries a Hugging Face repo id any more.
    for node in wf.values():
        for v in node.get("inputs", {}).values():
            if isinstance(v, str):
                assert not HUB_ID.match(v), v


def test_i2v_graph_wires_vae_from_its_own_loader():
    wf = _i2v()
    consumers = [n for n in wf.values() if n.get("inputs", {}).get("vae")]
    assert consumers, "expected image-encode and decode nodes to take a vae"
    for n in consumers:
        assert n["inputs"]["vae"] == ["4v", 0]
    sampler = next(n for n in wf.values() if n["class_type"] == "CogVideoSampler")
    assert sampler["inputs"]["model"] == ["4", 0]


def test_transformer_waits_on_the_offload_device():
    wf = _i2v()
    assert wf["4"]["inputs"]["load_device"] == "offload_device"
    assert wf["4"]["inputs"]["base_precision"] == "bf16"
    assert wf["4"]["inputs"]["quantization"] == "disabled"


def test_generator_has_no_hub_id_for_i2v():
    assert ComfyUIVideoGenerator.COGVIDEOX_MODELS["cogvideox-5b-i2v"] is None
    assert _gen()._cogvideox_i2v_files("cogvideox-5b-i2v") == {
        "unet": "CogVideoX_1_5_5b_I2V_bf16.safetensors",
        "vae": "cogvideox_vae_bf16.safetensors",
    }


def test_missing_files_reconcile_link_and_report_vae(tmp_path, monkeypatch):
    import backend.services.comfyui_video_generator as gen_mod
    models = tmp_path / "models"
    (models / "checkpoints").mkdir(parents=True)
    canonical = models / "checkpoints" / "CogVideoX_1_5_5b_I2V_bf16.safetensors"
    canonical.write_bytes(b"weights")
    monkeypatch.setattr(gen_mod, "COMFYUI_DIR", str(tmp_path))
    missing = _gen()._cogvideox_i2v_missing_files("cogvideox-5b-i2v")
    linked = models / "diffusion_models" / "CogVideoX_1_5_5b_I2V_bf16.safetensors"
    assert linked.exists() and linked.read_bytes() == b"weights"
    assert missing == ["vae/cogvideox_vae_bf16.safetensors"]


def test_missing_files_names_the_transformer_when_nothing_is_installed(tmp_path, monkeypatch):
    import backend.services.comfyui_video_generator as gen_mod
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(gen_mod, "COMFYUI_DIR", str(tmp_path))
    missing = _gen()._cogvideox_i2v_missing_files("cogvideox-5b-i2v")
    assert missing == [
        "checkpoints/CogVideoX_1_5_5b_I2V_bf16.safetensors",
        "vae/cogvideox_vae_bf16.safetensors",
    ]


def test_preflight_i2v_requires_companions(monkeypatch):
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m == "cogvideox-5b-i2v")
    ok, err = vmr.preflight_video_model("cogvideox-5b-i2v")
    assert ok is False
    assert "companion" in err


def test_start_sh_is_in_lockstep_with_launch_flags():
    text = START_SH.read_text()
    for key, val in flags.LOCAL_ONLY_ENV.items():
        assert f"export {key}={val}" in text, key
    launch = next(l for l in text.splitlines() if "main.py --listen" in l)
    for arg in flags.LOCAL_ONLY_CLI_ARGS:
        assert arg in launch, arg
    # The exports must precede the launch line, not trail it.
    assert text.index("export HF_HUB_OFFLINE=1") < text.index("main.py --listen")
