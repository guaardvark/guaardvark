"""The platform layer answers the same way everywhere, and its messages never
name a card the user does not have."""
from __future__ import annotations

import pytest

from backend.utils import platform as plat


def test_device_kind_prefers_cuda_then_mps_then_cpu(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: True)
    monkeypatch.setattr(plat, "has_mps", lambda: True)
    assert plat.device_kind() == "cuda"
    monkeypatch.setattr(plat, "has_cuda", lambda: False)
    assert plat.device_kind() == "mps"
    monkeypatch.setattr(plat, "has_mps", lambda: False)
    assert plat.device_kind() == "cpu"


def test_require_cuda_passes_on_cuda(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: True)
    plat.require_cuda("LoRA training")


def test_require_cuda_tells_a_mac_user_the_metal_status(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: False)
    monkeypatch.setattr(plat, "has_mps", lambda: True)
    monkeypatch.setattr(plat, "os_name", lambda: "Darwin")
    monkeypatch.setattr(plat, "gpu_name", lambda: "Apple Silicon (Metal)")
    with pytest.raises(plat.PlatformUnsupported) as exc:
        plat.require_cuda("FX Lab (Stable Audio)", metal_status="untested")
    msg = str(exc.value)
    assert "FX Lab (Stable Audio) needs an NVIDIA GPU" in msg
    assert "untested" in msg and "Darwin, mps" in msg


def test_require_cuda_on_a_gpu_less_linux_box(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: False)
    monkeypatch.setattr(plat, "has_mps", lambda: False)
    monkeypatch.setattr(plat, "os_name", lambda: "Linux")
    monkeypatch.setattr(plat, "gpu_name", lambda: None)
    with pytest.raises(plat.PlatformUnsupported) as exc:
        plat.require_cuda("LoRA training")
    assert "none is available" in str(exc.value) and "Linux, cpu" in str(exc.value)


def test_screen_agent_is_linux_only(monkeypatch):
    monkeypatch.setattr(plat, "os_name", lambda: "Darwin")
    assert plat.screen_agent_available() is False
    monkeypatch.setattr(plat, "os_name", lambda: "Linux")
    assert plat.screen_agent_available() is True
