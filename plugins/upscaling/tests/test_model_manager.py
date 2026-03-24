import pytest
from service.model_manager import ModelManager, MODEL_REGISTRY


def test_model_registry_has_default_models():
    """Registry contains the five standard Real-ESRGAN models."""
    names = [m["name"] for m in MODEL_REGISTRY]
    assert "RealESRGAN_x4plus" in names
    assert "RealESRGAN_x2plus" in names
    assert "RealESRGAN_x4plus_anime_6B" in names
    assert "realesr-animevideov3" in names
    assert "realesr-general-x4v3" in names


def test_model_registry_has_urls():
    """Each registry entry has a download URL."""
    for entry in MODEL_REGISTRY:
        assert "url" in entry, f"{entry['name']} missing url"
        assert entry["url"].startswith("https://"), f"{entry['name']} has invalid url"


def test_model_manager_list_available(tmp_path):
    """ModelManager.list_models returns available and downloaded lists."""
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    result = mm.list_models()
    assert "downloaded" in result
    assert "available" in result
    assert len(result["available"]) == len(MODEL_REGISTRY)
    assert len(result["downloaded"]) == 0


def test_model_manager_list_downloaded(tmp_path):
    """Downloaded .pth files appear in downloaded list."""
    fake_model = tmp_path / "RealESRGAN_x4plus.pth"
    fake_model.write_bytes(b"fake")
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    result = mm.list_models()
    downloaded_names = [m["name"] for m in result["downloaded"]]
    assert "RealESRGAN_x4plus" in downloaded_names


def test_model_manager_current_model_none(tmp_path):
    """No model loaded initially."""
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    assert mm.current_model_name is None
