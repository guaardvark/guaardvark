"""get_available_vram() and has_gpu() on a host with an AMD card and no NVIDIA stack."""
import sys

import pytest

from backend.services import gpu_resource_coordinator as grc

MIB = 1024 * 1024
CARD = (24560 * MIB, 540 * MIB)


@pytest.fixture
def coord(monkeypatch):
    monkeypatch.setattr(grc.platform, "system", lambda: "Linux")
    monkeypatch.setattr(grc.GPUResourceCoordinator, "_no_gpu_detected", False)
    monkeypatch.setitem(sys.modules, "pynvml", None)  # `import pynvml` raises ImportError

    def no_nvidia_smi(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(grc.subprocess, "run", no_nvidia_smi)
    c = grc.GPUResourceCoordinator.__new__(grc.GPUResourceCoordinator)
    monkeypatch.setattr(grc, "get_gpu_coordinator", lambda: c)
    return c


def _stub_amd(monkeypatch, result):
    import backend.services.hardware_detector as hd
    monkeypatch.setattr(hd, "probe_amd_vram_bytes", lambda *a, **k: result)


def test_an_amd_card_is_reported_in_the_nvidia_shape(coord, monkeypatch):
    _stub_amd(monkeypatch, CARD)
    info = coord.get_available_vram()
    assert info["success"] is True
    assert info["total_mb"] == 24560
    assert info["used_mb"] == 540
    assert info["available_mb"] == 24560 - 540
    assert info["gpu_name"]
    assert grc.GPUResourceCoordinator._no_gpu_detected is False
    assert grc.has_gpu() is True


def test_no_probe_answering_still_means_no_gpu_hardware(coord, monkeypatch):
    _stub_amd(monkeypatch, None)
    info = coord.get_available_vram()
    assert info["success"] is False
    assert info["reason"] == "no_gpu_hardware"
    assert grc.GPUResourceCoordinator._no_gpu_detected is True
    assert grc.has_gpu() is False


def test_nvidia_comes_first(coord, monkeypatch):
    nvidia = {"success": True, "available_mb": 100, "total_mb": 200, "used_mb": 100, "gpu_name": "N"}
    monkeypatch.setattr(coord, "_get_vram_via_nvidia_smi", lambda: nvidia)

    def boom(*a, **k):
        raise AssertionError("AMD probe must not run when NVIDIA answers")

    import backend.services.hardware_detector as hd
    monkeypatch.setattr(hd, "probe_amd_vram_bytes", boom)
    assert coord.get_available_vram() == nvidia
