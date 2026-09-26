"""LoRA linking into ComfyUI's models/loras for the Z-Image ComfyUI route.

``ensure_lora_in_comfyui`` symlinks a trained LoRA so ``LoraLoaderModelOnly`` can
resolve it by basename. It must honour ``GUAARDVARK_COMFYUI_LORAS_DIR`` and heal a
dangling symlink (a moved/removed target reads as absent and would otherwise raise
on the next link attempt).
"""
from pathlib import Path

import pytest

try:
    from backend.services import comfyui_image_generator as cig
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def test_loras_dir_prefers_env_override(monkeypatch, tmp_path):
    loras = tmp_path / "loras"
    loras.mkdir()
    monkeypatch.setenv("GUAARDVARK_COMFYUI_LORAS_DIR", str(loras))

    assert cig._comfyui_loras_dir() == loras


def test_ensure_lora_is_inert_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.delenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", raising=False)
    loras = tmp_path / "loras"
    loras.mkdir()
    monkeypatch.setenv("GUAARDVARK_COMFYUI_LORAS_DIR", str(loras))
    lora = tmp_path / "char.safetensors"
    lora.write_bytes(b"x")

    assert cig.ensure_lora_in_comfyui(str(lora)) is False
    assert not (loras / "char.safetensors").exists()


def test_ensure_lora_links_when_opted_in(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")
    loras = tmp_path / "loras"
    loras.mkdir()
    monkeypatch.setenv("GUAARDVARK_COMFYUI_LORAS_DIR", str(loras))
    lora = tmp_path / "char.safetensors"
    lora.write_bytes(b"x")

    assert cig.ensure_lora_in_comfyui(str(lora)) is True
    link = loras / "char.safetensors"
    assert link.is_symlink()
    assert link.resolve() == lora.resolve()


def test_ensure_lora_heals_dangling_symlink(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")
    loras = tmp_path / "loras"
    loras.mkdir()
    monkeypatch.setenv("GUAARDVARK_COMFYUI_LORAS_DIR", str(loras))

    # A stale link pointing at a path that no longer exists.
    link = loras / "char.safetensors"
    link.symlink_to(tmp_path / "gone" / "char.safetensors")
    assert link.is_symlink() and not link.exists()

    lora = tmp_path / "char.safetensors"
    lora.write_bytes(b"x")

    assert cig.ensure_lora_in_comfyui(str(lora)) is True
    assert link.is_symlink()
    assert link.resolve() == lora.resolve()
