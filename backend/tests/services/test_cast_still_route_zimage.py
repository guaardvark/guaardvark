"""Cast still routing for Z-Image — the ComfyUI opt-in must reach the route.

``_resolve_cast_still_route`` drives both the generator build and the GPU session
in ``generate_samples``. Historically Z-Image always resolved to ``offline``, so
``GUAARDVARK_ZIMAGE_USE_COMFYUI=1`` never reached Cast sample generation (the
offline Diffusers path is CUDA-only). These tests pin the flag into the route.
"""
import pytest

try:
    from backend.services import media_model_registry as mmr
    from backend.tasks.character_generation_tasks import _resolve_cast_still_route
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _zimage_profile():
    return {
        "family": "zimage",
        "inference_engine": "offline",
        "offline_model_key": "zimage-turbo",
        "base_model_id": "zimage-turbo",
        "profile": {"id": "zimage-turbo", "family": "zimage", "vram_infer_mb": 11000},
    }


def test_cast_route_zimage_is_offline_by_default(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", raising=False)
    monkeypatch.setattr(mmr, "resolve_inference_for_loras", lambda paths: _zimage_profile())

    route = _resolve_cast_still_route(None, ["/tmp/does-not-matter.safetensors"])

    assert route["engine"] == "offline"
    assert route["family"] == "zimage"


def test_cast_route_zimage_uses_comfy_when_opted_in(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_ZIMAGE_USE_COMFYUI", "1")
    monkeypatch.setattr(mmr, "resolve_inference_for_loras", lambda paths: _zimage_profile())

    route = _resolve_cast_still_route(None, ["/tmp/does-not-matter.safetensors"])

    assert route["engine"] == "comfy"
    assert route["comfy_model"] == "zimage"
    assert route["family"] == "zimage"
