"""Stable Audio Open device selection: CUDA first, Metal only when opted in."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backends.audio_fx_sao import StableAudioOpenBackend  # noqa: E402


def _torch(cuda: bool, mps: bool | None):
    backends = SimpleNamespace(mps=None if mps is None else SimpleNamespace(is_available=lambda: mps))
    return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda), backends=backends)


def test_cuda_wins(monkeypatch):
    monkeypatch.setenv(StableAudioOpenBackend.MPS_OPT_IN_ENV, "1")
    assert StableAudioOpenBackend.select_device(_torch(True, True)) == ("cuda", None)


def test_metal_is_off_by_default_and_names_the_switch(monkeypatch):
    monkeypatch.delenv(StableAudioOpenBackend.MPS_OPT_IN_ENV, raising=False)
    device, reason = StableAudioOpenBackend.select_device(_torch(False, True))
    assert device is None
    assert StableAudioOpenBackend.MPS_OPT_IN_ENV in reason and "experimental" in reason


def test_metal_when_opted_in(monkeypatch):
    monkeypatch.setenv(StableAudioOpenBackend.MPS_OPT_IN_ENV, "1")
    assert StableAudioOpenBackend.select_device(_torch(False, True)) == ("mps", None)


@pytest.mark.parametrize("mps", [False, None])
def test_no_gpu_at_all_keeps_the_cuda_message(monkeypatch, mps):
    monkeypatch.setenv(StableAudioOpenBackend.MPS_OPT_IN_ENV, "1")
    device, reason = StableAudioOpenBackend.select_device(_torch(False, mps))
    assert device is None and "NVIDIA GPU with CUDA" in reason


def test_availability_uses_the_selector(monkeypatch):
    monkeypatch.delenv(StableAudioOpenBackend.MPS_OPT_IN_ENV, raising=False)
    backend = StableAudioOpenBackend(output_root=Path("/nonexistent"))
    monkeypatch.setitem(sys.modules, "torch", _torch(False, True))
    available, reason = backend.availability()
    assert available is False and StableAudioOpenBackend.MPS_OPT_IN_ENV in reason
