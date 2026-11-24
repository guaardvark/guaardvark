import os

import pytest
import service.model_manager as model_manager_module
from service.model_manager import ModelManager, ModelNotInstalledError, MODEL_REGISTRY


def _forbid_downloads(monkeypatch):
    def _no_download(*_args, **_kwargs):
        raise AssertionError("using a model must not download it")

    monkeypatch.setattr(model_manager_module, "urlretrieve", _no_download)


def _registry_size(name):
    return next(m["size_bytes"] for m in MODEL_REGISTRY if m["name"] == name)


def _write_whole(path, name, head=b"\x80\x02\x8a\nlegacy"):
    """A stand-in of the registry model's published size (sparse, no disk used)."""
    with open(path, "wb") as f:
        f.write(head)
        f.truncate(_registry_size(name))


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
    _write_whole(fake_model, "RealESRGAN_x4plus")
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

    _write_whole(tmp_path / "RealESRGAN_x2plus.pth", "RealESRGAN_x2plus")
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
        _write_whole(dest, "RealESRGAN_x2plus")

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


def _zip_checkpoint(path):
    """A small zip archive, the container torch.save writes."""
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("archive/data.pkl", b"\x80\x02}q\x00.")
        z.writestr("archive/data/0", b"\x00" * 4096)


def _cut_in_half(path):
    with open(path, "r+b") as f:
        f.truncate(os.path.getsize(path) // 2)


def test_truncated_zip_checkpoint_reads_as_not_installed(tmp_path):
    """A registry model whose archive lost its end is offered for install, with the reason."""
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    path = tmp_path / "RealESRGAN_x2plus.pth"
    _zip_checkpoint(path)
    assert mm.is_downloaded("RealESRGAN_x2plus")
    assert mm.incomplete_reason("RealESRGAN_x2plus") is None

    _cut_in_half(path)

    assert not mm.is_downloaded("RealESRGAN_x2plus")
    assert mm.incomplete_reason("RealESRGAN_x2plus").startswith("truncated")
    assert "incomplete" in mm.missing_model_message("RealESRGAN_x2plus")
    listed = mm.list_models()
    assert "RealESRGAN_x2plus" not in [m["name"] for m in listed["downloaded"]]
    row = next(m for m in listed["available"] if m["name"] == "RealESRGAN_x2plus")
    assert row["incomplete"].startswith("truncated")
    with pytest.raises(ModelNotInstalledError, match="incomplete"):
        mm.load_model("RealESRGAN_x2plus")


def test_truncated_user_model_is_not_listed(tmp_path):
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    path = tmp_path / "my_custom_model.pth"
    _zip_checkpoint(path)
    _cut_in_half(path)

    listed = mm.list_models()

    assert "my_custom_model" not in [m["name"] for m in listed["downloaded"]]
    assert "incomplete" in mm.missing_model_message("my_custom_model")


def test_install_replaces_a_truncated_file(tmp_path, monkeypatch):
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    path = tmp_path / "RealESRGAN_x2plus.pth"
    _zip_checkpoint(path)
    _cut_in_half(path)
    monkeypatch.setattr(model_manager_module, "urlretrieve", lambda url, dest: _zip_checkpoint(dest))

    mm.download_model("RealESRGAN_x2plus")

    assert mm.is_downloaded("RealESRGAN_x2plus")


def test_legacy_pickle_checkpoint_is_checked_against_its_published_size(tmp_path):
    """A non-zip .pth has no length of its own: a registry model is compared
    with the size the registry records, and a model a person dropped in only
    has to be non-empty."""
    mm = ModelManager(models_dir=str(tmp_path), precision="fp32", compile_enabled=False)
    path = tmp_path / "RealESRGAN_x2plus.pth"
    _write_whole(path, "RealESRGAN_x2plus")
    assert mm.is_downloaded("RealESRGAN_x2plus")

    path.write_bytes(b"\x80\x02\x8a\nfront part of a download")
    assert not mm.is_downloaded("RealESRGAN_x2plus")
    assert mm.incomplete_reason("RealESRGAN_x2plus").startswith("truncated")

    path.write_bytes(b"")
    assert not mm.is_downloaded("RealESRGAN_x2plus")
    assert mm.incomplete_reason("RealESRGAN_x2plus") == "empty"

    own = tmp_path / "my-own-model.pth"
    own.write_bytes(b"\x80\x02\x8a\nlegacy")
    assert mm.is_downloaded("my-own-model")


def test_safetensors_check_matches_the_header(tmp_path):
    import json
    import struct

    from service.model_file_integrity import file_problem

    header = json.dumps({"w": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]}}).encode()
    whole = struct.pack("<Q", len(header)) + header + b"\x00" * 16
    path = tmp_path / "model.safetensors"

    path.write_bytes(whole)
    assert file_problem(path) is None
    path.write_bytes(whole[:-8])
    assert file_problem(path).startswith("truncated")
    path.write_bytes(whole + b"\x00")
    assert file_problem(path).startswith("corrupt")
    assert file_problem(tmp_path / "absent.safetensors") == "missing"
