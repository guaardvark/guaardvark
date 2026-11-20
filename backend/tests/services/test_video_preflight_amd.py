"""The video VRAM preflight on an AMD card: no NVIDIA-only refusal, the floor still applies."""
import backend.services.gpu_resource_coordinator as grc
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
from backend.services.video_model_registry import NO_VIDEO_GPU_MESSAGE

CARD = {"success": True, "total_mb": 24560, "used_mb": 540, "available_mb": 24020, "gpu_name": "AMD GPU (rocm-smi)"}


def _gen(monkeypatch, info, floor_gb):
    monkeypatch.setattr(grc, "get_available_vram", lambda: dict(info))
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    monkeypatch.setattr(ComfyUIVideoGenerator, "_min_vram_gb_for", classmethod(lambda cls, m: floor_gb))
    monkeypatch.setattr(ComfyUIVideoGenerator, "_wan_clip_device", classmethod(lambda cls, t=None: "default"))
    return gen


def test_an_amd_card_above_the_floor_passes(monkeypatch):
    assert _gen(monkeypatch, CARD, 16)._vram_preflight("wan") is None


def test_an_amd_card_below_the_floor_is_refused_with_its_size(monkeypatch):
    msg = _gen(monkeypatch, CARD, 40)._vram_preflight("wan")
    assert msg and "23.98 GB" in msg and "NVIDIA" not in msg


def test_no_gpu_at_all_is_refused_with_the_neutral_message(monkeypatch):
    msg = _gen(monkeypatch, {"success": False, "reason": "no_gpu_hardware"}, 16)._vram_preflight("wan")
    assert msg == NO_VIDEO_GPU_MESSAGE and "NVIDIA GPU detected" not in msg
