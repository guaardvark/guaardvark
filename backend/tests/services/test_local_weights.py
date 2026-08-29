"""Backend runtime loads never reach Hugging Face on their own.

2026-08-28 audit: four services called from_pretrained with bare hub ids and
no local-first check, so a cache miss during generation (or a chat request)
became a silent multi-GB download. Everything now goes through
local_weights.from_pretrained_local or passes local_files_only explicitly.
"""
import re
from pathlib import Path

import pytest

from backend.services import local_weights as lw

SERVICES = Path(__file__).resolve().parents[2] / "services"
GUARDED = [
    "anatomy_improvement_service.py",
    "offline_video_generator.py",
    "honesty_steering.py",
    "intent_service.py",
]


class _Loader:
    calls = []

    @classmethod
    def from_pretrained(cls, name, **kw):
        cls.calls.append((name, kw))
        if name == "missing/model":
            raise OSError("not found locally")
        if name == "broken/model":
            raise RuntimeError("CUDA out of memory")
        return "pipeline"


def test_forces_local_files_only_even_if_caller_says_otherwise():
    _Loader.calls.clear()
    out = lw.from_pretrained_local(_Loader, "org/ok", purpose="p", install_hint="h",
                                   local_files_only=False, torch_dtype="bf16")
    assert out == "pipeline"
    name, kw = _Loader.calls[-1]
    assert kw["local_files_only"] is True
    assert kw["torch_dtype"] == "bf16"


def test_cache_miss_becomes_one_clear_error():
    with pytest.raises(lw.WeightsNotInstalled) as ei:
        lw.from_pretrained_local(_Loader, "missing/model",
                                 purpose="OpenPose detector", install_hint="Use Install.")
    msg = str(ei.value)
    assert "OpenPose detector" in msg and "missing/model" in msg and "Use Install." in msg
    assert "never downloads" in msg


def test_other_failures_propagate_unchanged():
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        lw.from_pretrained_local(_Loader, "broken/model", purpose="p", install_hint="h")


def test_is_cached_is_false_on_an_empty_cache(tmp_path):
    assert lw.is_cached("THUDM/CogVideoX-5b", cache_dir=tmp_path) is False


def test_offline_video_generator_reports_empty_cache(tmp_path, monkeypatch):
    from backend.services.offline_video_generator import OfflineVideoGenerator
    gen = OfflineVideoGenerator.__new__(OfflineVideoGenerator)
    gen.models_dir = tmp_path
    assert gen.is_model_cached("cogvideox-5b") is False
    assert gen.is_model_cached("no-such-model") is False


HUB_LOAD = re.compile(r"\.from_pretrained\(")


def test_no_guarded_service_loads_from_the_hub_unguarded():
    """Every from_pretrained in the guarded modules is either the local helper
    or passes local_files_only explicitly within the next few lines."""
    for name in GUARDED:
        text = (SERVICES / name).read_text()
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not HUB_LOAD.search(line):
                continue
            window = "\n".join(lines[i:i + 8])
            assert ("from_pretrained_local(" in line
                    or "from_pretrained_local(" in lines[i - 1]
                    or "local_files_only" in window
                    or "Path(self.model_path)" in window or "str(tokenizer_path)" in line
                    or "self.model_path" in line), f"{name}:{i + 1}: unguarded hub load: {line.strip()}"


def test_is_cached_sees_a_real_hub_cache_layout(tmp_path):
    repo = tmp_path / "models--THUDM--CogVideoX-5b"
    sha = "a" * 40
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text(sha)
    (repo / "snapshots" / sha).mkdir(parents=True)
    (repo / "snapshots" / sha / "model_index.json").write_text("{}")
    assert lw.is_cached("THUDM/CogVideoX-5b", cache_dir=tmp_path) is True
    assert lw.is_cached("THUDM/CogVideoX-5b-I2V", cache_dir=tmp_path) is False


def test_download_status_tells_the_form_what_will_happen(tmp_path):
    # Not cached, looks like a hub id → the job will download.
    s = lw.download_status("unsloth/gemma-2-2b", cache_dir=tmp_path)
    assert s == {"name": "unsloth/gemma-2-2b", "looks_like_hub_id": True,
                 "cached": False, "will_download": True}
    # An Ollama tag is not something the hub can serve.
    s = lw.download_status("llama3:8b", cache_dir=tmp_path)
    assert s["looks_like_hub_id"] is False and s["will_download"] is False
    # Cached → nothing to download.
    repo = tmp_path / "models--unsloth--gemma-2-2b"
    sha = "b" * 40
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text(sha)
    (repo / "snapshots" / sha).mkdir(parents=True)
    (repo / "snapshots" / sha / "config.json").write_text("{}")
    s = lw.download_status("unsloth/gemma-2-2b", cache_dir=tmp_path)
    assert s["cached"] is True and s["will_download"] is False
    assert lw.download_status("")["name"] == ""
