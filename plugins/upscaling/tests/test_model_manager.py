import os

import pytest
import service.model_manager as model_manager_module
from service.model_manager import ModelManager, ModelNotInstalledError, MODEL_REGISTRY


def _forbid_downloads(monkeypatch):
    def _no_download(*_args, **_kwargs):
        raise AssertionError("using a model must not download it")

    monkeypatch.setattr(model_manager_module, "urlretrieve", _no_download)


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


def test_load_model_refuses_a_registry_model_that_is_not_installed(tmp_path, monkeypatch):
    """Loading never downloads: the refusal names the model and where to install it."""
    _forbid_downloads(monkeypatch)
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)

    with pytest.raises(ModelNotInstalledError) as refused:
        mm.load_model("RealESRGAN_x4plus")

    message = str(refused.value)
    assert "'RealESRGAN_x4plus' is not installed" in message
    assert "Manage Upscaling Models" in message
    assert not (tmp_path / "RealESRGAN_x4plus.pth").exists()
    assert mm.current_model_name is None


def test_missing_model_message_tells_registry_and_unknown_models_apart(tmp_path):
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    assert "is not installed" in mm.missing_model_message("RealESRGAN_x2plus")
    assert "was not found" in mm.missing_model_message("my-own-model")

    (tmp_path / "RealESRGAN_x2plus.pth").write_bytes(b"fake")
    (tmp_path / "my-own-model.pth").write_bytes(b"fake")
    assert mm.missing_model_message("RealESRGAN_x2plus") is None
    assert mm.missing_model_message("my-own-model") is None


def test_an_unknown_model_is_refused_without_a_download(tmp_path, monkeypatch):
    _forbid_downloads(monkeypatch)
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    with pytest.raises(FileNotFoundError, match="my-own-model"):
        mm.load_model("my-own-model")


def test_download_model_is_the_install_path(tmp_path, monkeypatch):
    """The explicit install still fetches the registry file."""
    fetched = []

    def _fake_retrieve(url, dest):
        fetched.append(url)
        with open(dest, "wb") as f:
            f.write(b"weights")

    monkeypatch.setattr(model_manager_module, "urlretrieve", _fake_retrieve)
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)

    path = mm.download_model("RealESRGAN_x2plus")

    assert fetched and fetched[0].endswith("RealESRGAN_x2plus.pth")
    assert os.path.isfile(path)
    assert mm.missing_model_message("RealESRGAN_x2plus") is None


def test_interrupted_download_leaves_no_model_file(tmp_path, monkeypatch):
    """A fetch that dies part-way must not read as an installed model."""
    def _dies_part_way(url, dest):
        with open(dest, "wb") as f:
            f.write(b"front part")
        raise OSError("connection reset")

    monkeypatch.setattr(model_manager_module, "urlretrieve", _dies_part_way)
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)

    with pytest.raises(OSError):
        mm.download_model("RealESRGAN_x2plus")

    assert not mm.is_downloaded("RealESRGAN_x2plus")
    assert mm.missing_model_message("RealESRGAN_x2plus")
