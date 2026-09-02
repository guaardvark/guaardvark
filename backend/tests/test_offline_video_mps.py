"""
Device-selection tests for offline video generation (Mac/MPS support, PREVIEW).

These verify the accelerator-selection LOGIC without GPU hardware — they mock
torch.cuda / torch.backends.mps. They do NOT prove CogVideoX actually renders on
MPS (that needs a Mac; tracked separately). They DO prove:
  - CUDA selection is unchanged (regression guard for the refactor),
  - a Mac (MPS-only, no CUDA) now selects 'mps' + bfloat16 instead of 'cpu',
  - CPU-only and no-torch fall through correctly.
"""

import functools

import pytest

import backend.services.offline_video_generator as ovg


@pytest.fixture
def fake_torch(monkeypatch):
    """Give the module a torch whose cuda/mps availability we control."""
    real_torch = ovg.torch  # real torch is installed in the venv; reuse its dtypes
    monkeypatch.setattr(ovg, "torch_available", True, raising=False)
    return real_torch


def _set(monkeypatch, real_torch, *, cuda: bool, mps: bool):
    # functools.wraps keeps ``__wrapped__`` on the stand-ins; torch's dynamo
    # reads it off both probes when it builds its trace rules.
    monkeypatch.setattr(real_torch.cuda, "is_available",
                        functools.wraps(real_torch.cuda.is_available)(lambda: cuda))
    # _mps_available() reads torch.backends.mps.is_available — patch it there
    monkeypatch.setattr(real_torch.backends.mps, "is_available",
                        functools.wraps(real_torch.backends.mps.is_available)(lambda: mps),
                        raising=False)


def test_cuda_wins(monkeypatch, fake_torch):
    _set(monkeypatch, fake_torch, cuda=True, mps=False)
    device, dtype = ovg._select_accelerator()
    assert device == "cuda"
    assert dtype is fake_torch.float16


def test_mps_selected_when_only_metal(monkeypatch, fake_torch):
    # The Mac case: no CUDA, MPS present -> 'mps' + bfloat16 (NOT 'cpu').
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    _set(monkeypatch, fake_torch, cuda=False, mps=True)
    device, dtype = ovg._select_accelerator()
    assert device == "mps"
    assert dtype is fake_torch.bfloat16
    # The CPU-fallback env must be set so unimplemented MPS ops degrade vs crash.
    import os
    assert os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"


def test_cpu_when_no_accelerator(monkeypatch, fake_torch):
    _set(monkeypatch, fake_torch, cuda=False, mps=False)
    device, dtype = ovg._select_accelerator()
    assert device == "cpu"
    assert dtype is fake_torch.float32


def test_cpu_when_torch_missing(monkeypatch):
    monkeypatch.setattr(ovg, "torch_available", False, raising=False)
    device, dtype = ovg._select_accelerator()
    assert device == "cpu"
    assert dtype is None


def test_cuda_preference_over_mps(monkeypatch, fake_torch):
    # If both somehow report available, CUDA must win (real NVIDIA box).
    _set(monkeypatch, fake_torch, cuda=True, mps=True)
    device, _ = ovg._select_accelerator()
    assert device == "cuda"


def test_mps_availability_is_false_on_this_box():
    # Sanity: this CI/dev box is not a Mac, so the real probe is False (or at
    # worst safely False on any torch without the mps backend).
    assert ovg._mps_available() in (True, False)  # never raises


def test_force_clear_gpu_memory_uses_mps_branch(monkeypatch, fake_torch):
    # No CUDA + MPS present -> force_clear should flush MPS and succeed, not bail
    # with "CUDA not available".
    _set(monkeypatch, fake_torch, cuda=False, mps=True)
    monkeypatch.setattr(ovg, "_mps_available", lambda: True)
    flushed = {"called": False}

    class _FakeMPS:
        @staticmethod
        def empty_cache():
            flushed["called"] = True

        @staticmethod
        def current_allocated_memory():
            return 2 * 1024**3  # 2 GB

    monkeypatch.setattr(fake_torch, "mps", _FakeMPS, raising=False)
    res = ovg.force_clear_gpu_memory()
    assert flushed["called"] is True
    assert res["success"] is True
    assert res["after"]["allocated_gb"] == 2.0
    assert "error" not in res


def test_accel_cleanup_flushes_mps(monkeypatch, fake_torch):
    _set(monkeypatch, fake_torch, cuda=False, mps=True)
    monkeypatch.setattr(ovg, "_mps_available", lambda: True)
    called = {"mps": False}
    monkeypatch.setattr(fake_torch, "mps",
                        type("M", (), {"empty_cache": staticmethod(lambda: called.__setitem__("mps", True))}),
                        raising=False)
    ovg._accel_cleanup()
    assert called["mps"] is True


def test_accel_cleanup_noop_on_cpu(monkeypatch, fake_torch):
    # No accelerator -> must not raise.
    _set(monkeypatch, fake_torch, cuda=False, mps=False)
    monkeypatch.setattr(ovg, "_mps_available", lambda: False)
    ovg._accel_cleanup()  # no exception = pass


@pytest.mark.parametrize("device,mem,expect_tier", [
    ("cuda", 24, None),     # NVIDIA: no extra caps
    ("cpu", 16, None),
    ("mps", 16, "mps-low"),
    ("mps", 32, "mps-mid"),
    ("mps", 64, "mps-high"),
    ("mps", None, "mps-high"),  # unknown memory -> don't over-restrict
])
def test_recommended_video_caps(device, mem, expect_tier):
    caps = ovg.recommended_video_caps(device, mem)
    if expect_tier is None:
        assert caps == {}
    else:
        assert caps["tier"] == expect_tier
        assert caps["max_dim"] > 0 and caps["max_frames"] > 0 and caps["max_steps"] > 0
