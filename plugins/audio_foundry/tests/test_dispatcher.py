"""Unit tests for the dispatcher's load lifecycle.

We don't import service.app here — it carries the singleton dispatcher whose
state may be shaped by other tests. Instead we instantiate Dispatcher fresh
with a stub orchestrator and a stub backend so the assertions are about the
Dispatcher's own behavior, not the FastAPI wiring around it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.base import AudioBackend, GenerationResult  # noqa: E402
from service.dispatcher import Dispatcher, Intent, NotWired  # noqa: E402


class _StubBackend(AudioBackend):
    """Lightweight backend whose load/generate are observable."""

    name = "stub"
    vram_mb_estimate = 1234

    def __init__(self, *, fail_on_load: bool = False) -> None:
        self._loaded = False
        self.load_calls = 0
        self.generate_calls = 0
        self._fail_on_load = fail_on_load

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        self.load_calls += 1
        if self._fail_on_load:
            raise RuntimeError("synthetic load failure")
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def generate(self, **params):
        self.generate_calls += 1
        return GenerationResult(
            path=Path("/tmp/stub.wav"),
            duration_s=1.0,
            sample_rate=44100,
            meta={"stub": True, **params},
        )


def _make_orch_mock():
    orch = MagicMock()
    orch.request_vram.return_value = True
    orch.mark_loaded.return_value = True
    orch.evict.return_value = True
    orch.release.return_value = True
    return orch


def test_unwired_intent_raises_NotWired():
    d = Dispatcher(orchestrator=_make_orch_mock())
    with pytest.raises(NotWired):
        d.generate(Intent.FX, prompt="x")


def test_cold_load_calls_orchestrator_in_request_then_load_then_mark_order():
    """Cold path: request_vram BEFORE load, mark_loaded AFTER. evict NOT called."""
    orch = _make_orch_mock()
    backend = _StubBackend()
    d = Dispatcher(orchestrator=orch)
    d.register(Intent.FX, backend)

    result = d.generate(Intent.FX, prompt="rain")

    assert result.meta["stub"] is True
    # Order: request_vram, mark_loaded
    method_order = [c[0] for c in orch.method_calls]
    assert method_order == ["request_vram", "mark_loaded"]
    orch.evict.assert_not_called()
    # Backend was actually loaded once
    assert backend.load_calls == 1
    assert backend.is_loaded is True
    # request_vram got the right slot id and vram estimate
    rv_args = orch.request_vram.call_args
    assert rv_args.args[0] == "audio_foundry:fx"
    assert rv_args.args[1] == 1234


def test_hot_backend_skips_orchestrator_entirely():
    """If the backend is already loaded, the dispatcher does NOT touch the orchestrator."""
    orch = _make_orch_mock()
    backend = _StubBackend()
    backend.load()  # pre-warm
    assert backend.is_loaded
    d = Dispatcher(orchestrator=orch)
    d.register(Intent.FX, backend)

    d.generate(Intent.FX, prompt="x")

    orch.request_vram.assert_not_called()
    orch.mark_loaded.assert_not_called()
    orch.evict.assert_not_called()
    # And load() was not called a second time
    assert backend.load_calls == 1


def test_load_failure_calls_evict_and_reraises():
    """If backend.load() raises, the dispatcher must clean up the LOADING slot."""
    orch = _make_orch_mock()
    backend = _StubBackend(fail_on_load=True)
    d = Dispatcher(orchestrator=orch)
    d.register(Intent.VOICE, backend)

    with pytest.raises(RuntimeError, match="synthetic load failure"):
        d.generate(Intent.VOICE, text="hi")

    orch.request_vram.assert_called_once()
    orch.evict.assert_called_once_with("audio_foundry:voice")
    orch.mark_loaded.assert_not_called()


def test_dispatcher_works_without_orchestrator():
    """Default constructor (no orchestrator) must still allow generation."""
    backend = _StubBackend()
    d = Dispatcher()  # no orchestrator passed
    d.register(Intent.FX, backend)

    result = d.generate(Intent.FX, prompt="x")

    assert result.meta["stub"] is True
    assert backend.is_loaded is True
