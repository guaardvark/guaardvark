"""Unit tests for the voice_gen facade (Chatterbox + Kokoro routing).

We do NOT import chatterbox or kokoro packages here — the goal is to test the
routing logic itself, not the underlying TTS engines. The two inner backends
are replaced by stubs that record calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.base import AudioBackend, GenerationResult  # noqa: E402
from backends.voice_gen import VoiceGenBackend  # noqa: E402


class _StubInner(AudioBackend):
    """Test double for ChatterboxBackend / KokoroBackend."""

    def __init__(self, name: str, vram_mb: int, *,
                 fail_on_load: bool = False, fail_on_generate: bool = False) -> None:
        self.name = name
        self.vram_mb_estimate = vram_mb
        self._loaded = False
        self.load_calls = 0
        self.generate_calls = 0
        self.last_params: dict | None = None
        self._fail_on_load = fail_on_load
        self._fail_on_generate = fail_on_generate

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        self.load_calls += 1
        if self._fail_on_load:
            raise RuntimeError(f"{self.name} synthetic load failure")
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def generate(self, **params):
        self.generate_calls += 1
        self.last_params = params
        if self._fail_on_generate:
            raise RuntimeError(f"{self.name} synthetic generate failure")
        return GenerationResult(
            path=Path(f"/tmp/{self.name}.wav"),
            duration_s=1.0,
            sample_rate=24000,
            meta={"backend": self.name, "text": params.get("text")},
        )


@pytest.fixture
def voice_gen_with_stubs(tmp_path):
    """Build a VoiceGenBackend whose internals are stubs we can poke."""
    vg = VoiceGenBackend(output_root=tmp_path)
    chat = _StubInner("chatterbox", 2000)
    koko = _StubInner("kokoro", 600)
    vg._chatterbox = chat
    vg._kokoro = koko
    return vg, chat, koko


def test_vram_estimate_is_max_not_sum(voice_gen_with_stubs):
    vg, _, _ = voice_gen_with_stubs
    assert vg.vram_mb_estimate == 2000  # max(2000, 600)


def test_is_loaded_true_when_either_inner_is_loaded(voice_gen_with_stubs):
    vg, chat, koko = voice_gen_with_stubs
    assert vg.is_loaded is False
    chat._loaded = True
    assert vg.is_loaded is True
    chat._loaded = False
    koko._loaded = True
    assert vg.is_loaded is True


def test_load_pre_warms_chatterbox_only(voice_gen_with_stubs):
    vg, chat, koko = voice_gen_with_stubs
    vg.load()
    assert chat.load_calls == 1
    assert koko.load_calls == 0


def test_load_falls_back_to_kokoro_when_chatterbox_fails(tmp_path):
    """Chatterbox OOM at load -> Kokoro picks up the cold-start."""
    vg = VoiceGenBackend(output_root=tmp_path)
    vg._chatterbox = _StubInner("chatterbox", 2000, fail_on_load=True)
    vg._kokoro = _StubInner("kokoro", 600)

    vg.load()
    assert vg._chatterbox.load_calls == 1
    assert vg._kokoro.load_calls == 1
    assert vg._kokoro.is_loaded is True


def test_explicit_chatterbox_routes_to_chatterbox(voice_gen_with_stubs):
    vg, chat, koko = voice_gen_with_stubs
    vg.generate(text="hi", backend="chatterbox")
    assert chat.generate_calls == 1
    assert koko.generate_calls == 0


def test_explicit_kokoro_routes_to_kokoro(voice_gen_with_stubs):
    vg, chat, koko = voice_gen_with_stubs
    vg.generate(text="hi", backend="kokoro")
    assert koko.generate_calls == 1
    assert chat.generate_calls == 0


def test_auto_prefers_chatterbox_when_healthy(voice_gen_with_stubs):
    vg, chat, koko = voice_gen_with_stubs
    result = vg.generate(text="hi", backend="auto")
    assert chat.generate_calls == 1
    assert koko.generate_calls == 0
    assert result.meta["backend"] == "chatterbox"


def test_auto_falls_back_to_kokoro_when_chatterbox_generate_fails(tmp_path):
    vg = VoiceGenBackend(output_root=tmp_path)
    vg._chatterbox = _StubInner("chatterbox", 2000, fail_on_generate=True)
    vg._kokoro = _StubInner("kokoro", 600)

    result = vg.generate(text="hi", backend="auto")
    assert vg._chatterbox.generate_calls == 1
    assert vg._kokoro.generate_calls == 1
    assert result.meta["backend"] == "kokoro"


def test_explicit_chatterbox_failure_does_not_silently_fall_back(tmp_path):
    """If the user pinned Chatterbox, a failure must surface — not silent fallback."""
    vg = VoiceGenBackend(output_root=tmp_path)
    vg._chatterbox = _StubInner("chatterbox", 2000, fail_on_generate=True)
    vg._kokoro = _StubInner("kokoro", 600)

    with pytest.raises(RuntimeError, match="chatterbox synthetic generate failure"):
        vg.generate(text="hi", backend="chatterbox")
    assert vg._kokoro.generate_calls == 0


def test_lazy_load_inner_backend_when_picked(voice_gen_with_stubs):
    """Generate with backend='kokoro' on a never-loaded VoiceGen must load Kokoro on demand."""
    vg, chat, koko = voice_gen_with_stubs
    assert koko.is_loaded is False
    vg.generate(text="hi", backend="kokoro")
    assert koko.is_loaded is True
    assert koko.generate_calls == 1


def test_default_backend_is_auto_when_param_missing(voice_gen_with_stubs):
    vg, chat, koko = voice_gen_with_stubs
    vg.generate(text="hi")  # no `backend` param
    assert chat.generate_calls == 1


def test_params_pass_through_to_inner_backend(voice_gen_with_stubs):
    vg, chat, _ = voice_gen_with_stubs
    vg.generate(
        text="testing",
        backend="chatterbox",
        reference_clip_path="/tmp/ref.wav",
        emotion="happy",
        seed=42,
    )
    assert chat.last_params["text"] == "testing"
    assert chat.last_params["reference_clip_path"] == "/tmp/ref.wav"
    assert chat.last_params["emotion"] == "happy"
    assert chat.last_params["seed"] == 42
