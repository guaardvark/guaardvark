"""The music facade routes a request's ``model`` to ACE-Step v1 or 1.5.

A request that names no model must behave as v1 alone did: same backend name,
same VRAM request, same load. A named model is honoured or refused, never
answered by the other engine. Stub engines and a stub orchestrator; no
subprocess, GPU or network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.base import AudioBackend, GenerationResult  # noqa: E402
from backends.music_gen import MusicGenBackend, UnknownMusicModel  # noqa: E402
from service.dispatcher import Dispatcher, Intent  # noqa: E402


class _Engine(AudioBackend):
    def __init__(self, name: str, vram: int) -> None:
        self.name = name
        self.vram_mb_estimate = vram
        self.loaded = False
        self.calls: list = []

    @property
    def is_loaded(self) -> bool:
        return self.loaded

    def load(self) -> None:
        self.loaded = True
        self.calls.append("load")

    def unload(self) -> None:
        if self.loaded:
            self.calls.append("unload")
        self.loaded = False

    def generate(self, **params):
        self.calls.append(("generate", dict(params)))
        return GenerationResult(path=Path("/dev/null"), duration_s=1.0, sample_rate=48000,
                                meta={"backend": self.name})


@pytest.fixture
def rig():
    v1 = _Engine("ace_step_v1_3.5b", 10000)
    v15 = _Engine("ace_step_1.5_turbo", 14400)
    orch = MagicMock()
    d = Dispatcher(orchestrator=orch)
    d.register(Intent.MUSIC, MusicGenBackend(v1, v15))
    return d, v1, v15, orch


def test_no_model_is_v1_with_v1s_name_and_vram(rig):
    d, v1, v15, orch = rig
    assert d.status()["music"]["backend"] == "ace_step_v1_3.5b"
    result = d.generate(Intent.MUSIC, style_prompt="lofi", duration_s=10.0)
    assert result.meta["backend"] == "ace_step_v1_3.5b"
    assert v1.calls[0] == "load" and v15.calls == []
    assert orch.request_vram.call_args.args[1] == 10000
    assert orch.request_vram.call_args.kwargs["exclusive"] is True
    orch.evict.assert_not_called()


def test_the_model_field_never_reaches_an_engine(rig):
    d, v1, v15, _ = rig
    d.generate(Intent.MUSIC, style_prompt="lofi", model="ace-step")
    d.generate(Intent.MUSIC, style_prompt="lofi", model="ace-step-1.5")
    for engine in (v1, v15):
        generated = [c[1] for c in engine.calls if isinstance(c, tuple)]
        assert generated and all("model" not in p for p in generated)


def test_switching_unloads_the_other_engine_and_releases_its_slot(rig):
    d, v1, v15, orch = rig
    d.generate(Intent.MUSIC, style_prompt="lofi")
    orch.reset_mock()
    d.generate(Intent.MUSIC, style_prompt="lofi", model="ace-step-1.5")
    assert v1.loaded is False and "unload" in v1.calls
    assert v15.loaded is True
    orch.evict.assert_called_once_with("audio_foundry:music")
    assert orch.request_vram.call_args.args[1] == 14400
    assert d.status()["music"]["backend"] == "ace_step_1.5_turbo"

    orch.reset_mock()
    d.generate(Intent.MUSIC, style_prompt="lofi", model="ace-step-1.5")
    orch.evict.assert_not_called()
    orch.request_vram.assert_not_called()


def test_an_unknown_model_is_refused_not_answered_by_v1(rig):
    d, v1, v15, _ = rig
    with pytest.raises(UnknownMusicModel):
        d.generate(Intent.MUSIC, style_prompt="lofi", model="ace-step-2")
    assert v1.calls == [] and v15.calls == []


def test_unload_releases_both(rig):
    d, v1, v15, _ = rig
    d.generate(Intent.MUSIC, style_prompt="lofi", model="ace-step-1.5")
    assert d.unload(Intent.MUSIC) is True
    assert v1.loaded is False and v15.loaded is False


def test_plan_selects_15_and_returns_its_arrangement(rig):
    d, v1, v15, _orch = rig
    seen = {}

    def _plan(**params):
        seen.update(params)
        return {"caption": "A quiet piano piece.", "bpm": 84}

    v15.plan = _plan
    arrangement = d.plan(Intent.MUSIC, style_prompt="quiet piano, 84 bpm")
    assert arrangement["bpm"] == 84
    assert v15.loaded is True and v1.loaded is False
    assert "model" not in seen
    assert seen["style_prompt"] == "quiet piano, 84 bpm"
    assert v1.calls == []


def test_the_15_backend_refuses_before_spawning_when_not_installed(tmp_path, monkeypatch):
    from backends import music_gen_acestep15 as m
    from backends.hub_weights import WeightsNotInstalled

    monkeypatch.setattr(m, "_PLUGIN_ROOT", tmp_path)
    monkeypatch.setattr(m, "_REPO_ROOT", tmp_path)
    spawned = []
    backend = m.ACEStep15Backend(output_root=tmp_path / "out")
    monkeypatch.setattr(backend, "_start_daemon", lambda *a, **k: spawned.append(a))
    with pytest.raises(WeightsNotInstalled, match="Python environment"):
        backend.load()

    cat = json.loads(m.CATALOG.read_text(encoding="utf-8"))
    venv = tmp_path / cat["environment"]
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    (venv / m.ENV_MARKER).write_text(cat["source"]["commit"] + "\n")
    with pytest.raises(WeightsNotInstalled, match="weights are not on this machine"):
        backend.load()
    assert spawned == []


def test_music_request_accepts_only_the_two_ace_step_models():
    from pydantic import ValidationError
    from service.app import MusicRequest

    assert MusicRequest(style_prompt="x").model is None
    assert MusicRequest(style_prompt="x", model="ace-step-1.5").model == "ace-step-1.5"
    with pytest.raises(ValidationError):
        MusicRequest(style_prompt="x", model="minimax-music3-int8")
