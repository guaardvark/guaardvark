"""A model file counts as installed only when it is whole (issue #378)."""
from __future__ import annotations

import os
import struct
import zipfile

import pytest

from backend.services import video_model_registry as vmr
from backend.tests.fixtures.model_files import gguf_bytes, safetensors_bytes
from backend.utils import model_file_integrity as mfi


def _gguf_with_tensor(data_bytes: int) -> bytes:
    """A GGUF v3 file holding one F32 tensor of ``data_bytes`` bytes."""
    def text(s: bytes) -> bytes:
        return struct.pack("<Q", len(s)) + s

    head = b"GGUF" + struct.pack("<IQQ", 3, 1, 2)
    head += text(b"general.name") + struct.pack("<I", 8) + text(b"stand-in")
    head += text(b"tokens") + struct.pack("<IIQ", 9, 8, 2) + text(b"a") + text(b"b")
    head += text(b"w") + struct.pack("<IQIQ", 1, data_bytes // 4, 0, 0)
    head += b"\0" * (-len(head) % 32)
    return head + b"\0" * data_bytes


def test_intact_safetensors_is_whole(tmp_path):
    path = tmp_path / "model.safetensors"
    path.write_bytes(safetensors_bytes(4096))
    assert mfi.file_problem(path) is None
    assert mfi.file_complete(path) and mfi.file_incomplete(path) is None


def test_truncated_safetensors_is_incomplete(tmp_path):
    path = tmp_path / "model.safetensors"
    path.write_bytes(safetensors_bytes(4096)[:-1000])
    assert mfi.file_problem(path).startswith("truncated")
    assert not mfi.file_complete(path)
    assert mfi.file_incomplete(path).startswith("truncated")


def test_safetensors_cut_inside_its_header_is_incomplete(tmp_path):
    path = tmp_path / "model.safetensors"
    path.write_bytes(safetensors_bytes(4096)[:20])
    assert mfi.file_problem(path) == "truncated: the file ends inside its header"
    path.write_bytes(b"\x10\0\0")
    assert mfi.file_problem(path).startswith("truncated")


@pytest.mark.parametrize("content", [
    struct.pack("<Q", 12) + b"not json at all",
    struct.pack("<Q", 2) + b"[]",
    struct.pack("<Q", 0),
    b"\xff" * 64,
    struct.pack("<Q", 14) + b'{"w": {"x":1}}',
])
def test_corrupt_safetensors_header_is_incomplete(tmp_path, content):
    path = tmp_path / "model.safetensors"
    path.write_bytes(content)
    assert mfi.file_problem(path).startswith("corrupt header")


def test_bytes_after_the_last_tensor_are_corrupt(tmp_path):
    path = tmp_path / "model.safetensors"
    path.write_bytes(safetensors_bytes(64) + b"extra")
    assert mfi.file_problem(path).startswith("corrupt")


def test_missing_and_empty_files(tmp_path):
    path = tmp_path / "model.safetensors"
    assert mfi.file_problem(path) == mfi.MISSING
    assert mfi.file_incomplete(path) is None and not mfi.file_complete(path)
    assert mfi.file_problem(tmp_path) == mfi.MISSING
    path.write_bytes(b"")
    assert mfi.file_problem(path) == mfi.EMPTY
    assert mfi.file_incomplete(path) == mfi.EMPTY


def test_gguf_whole_truncated_and_corrupt(tmp_path):
    path = tmp_path / "model.gguf"
    whole = _gguf_with_tensor(4096)
    path.write_bytes(whole)
    assert mfi.file_problem(path) is None
    path.write_bytes(whole[:-100])
    assert mfi.file_problem(path).startswith("truncated")
    path.write_bytes(whole[:40])
    assert mfi.file_problem(path) == "truncated: the file ends inside its GGUF header"
    path.write_bytes(b"nope" + whole[4:])
    assert mfi.file_problem(path).startswith("corrupt header")
    path.write_bytes(gguf_bytes())
    assert mfi.file_problem(path) is None


def test_zip_checkpoint_needs_its_end_record(tmp_path):
    path = tmp_path / "model.pth"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("archive/data.pkl", b"p" * 2000)
    assert mfi.file_problem(path) is None
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])
    assert mfi.file_problem(path).startswith("truncated")


@pytest.mark.parametrize("name", ["legacy.pth", "model.onnx", "weights.bin", "old.ckpt"])
def test_formats_without_a_length_pass_when_non_empty(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"\x80\x02 some bytes")
    assert mfi.file_problem(path) is None


def test_verdict_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "model.safetensors"
    path.write_bytes(safetensors_bytes(64))
    calls = []
    real = mfi._examine
    monkeypatch.setattr(mfi, "_examine", lambda p, size: calls.append(p) or real(p, size))
    assert mfi.file_problem(path) is None
    assert mfi.file_problem(path) is None
    assert len(calls) == 1
    path.write_bytes(safetensors_bytes(64)[:-8])
    assert mfi.file_problem(path).startswith("truncated")
    assert len(calls) == 2
    # Same size, new mtime: read again.
    path.write_bytes(safetensors_bytes(56))
    os.utime(path, ns=(1, 1))
    mfi.file_problem(path)
    assert len(calls) == 3


# ── the registry reads a truncated file as not installed ─────────────────────

WAN = "wan22-5b"


def _wan_file(root):
    entry = vmr.VIDEO_MODEL_REGISTRY[WAN]
    return vmr.resolve_entry_dir(entry) / entry["check_files"][0]


@pytest.fixture
def models(tmp_path, monkeypatch):
    root = tmp_path / "models"
    monkeypatch.setattr(vmr, "comfyui_models_dir", lambda: root)
    return root


def test_registry_counts_only_a_whole_file_as_installed(models):
    path = _wan_file(models)
    label = f"{WAN}:{vmr.VIDEO_MODEL_REGISTRY[WAN]['check_files'][0]}"
    assert vmr.is_model_installed(WAN) is False
    assert label in vmr.missing_plan_files(WAN)
    assert vmr.incomplete_plan_files(WAN) == {}

    path.parent.mkdir(parents=True)
    path.write_bytes(safetensors_bytes(4096)[:-1000])
    assert vmr.is_model_installed(WAN) is False
    assert label in vmr.missing_plan_files(WAN)
    assert vmr.incomplete_plan_files(WAN)[label].startswith("truncated")

    path.write_bytes(safetensors_bytes(4096))
    assert vmr.is_model_installed(WAN) is True
    assert label not in vmr.missing_plan_files(WAN)
    assert vmr.incomplete_plan_files(WAN) == {}


def test_install_fetches_a_truncated_file_again(models, monkeypatch):
    import huggingface_hub

    entry = vmr.VIDEO_MODEL_REGISTRY[WAN]
    path = _wan_file(models)
    path.parent.mkdir(parents=True)
    path.write_bytes(safetensors_bytes(4096)[:-1000])
    fetched = []

    def fake_hf_download(repo_id, filename, local_dir=None, **_kw):
        fetched.append(filename)
        got = models.parent / "fetched" / filename
        got.parent.mkdir(parents=True, exist_ok=True)
        got.write_bytes(safetensors_bytes(4096))
        return str(got)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_download)
    vmr.download_entry_files(entry, vmr.resolve_entry_dir(entry))
    assert len(fetched) == 1
    assert vmr.is_model_installed(WAN) is True
    vmr.download_entry_files(entry, vmr.resolve_entry_dir(entry))
    assert len(fetched) == 1


def test_models_api_reports_the_incomplete_file(models):
    from backend.api import batch_video_generation_api as api

    path = _wan_file(models)
    path.parent.mkdir(parents=True)
    path.write_bytes(safetensors_bytes(4096)[:-1000])
    assert api._check_model_downloaded(WAN) is False
    reasons = api._incomplete_check_files(WAN)
    assert list(reasons) == [f"{WAN}:{path.name}"]
    assert set(reasons) <= set(api._missing_check_files(WAN))


def test_interrupted_direct_download_leaves_nothing_under_the_final_name(models, monkeypatch):
    import urllib.request

    def dies_part_way(url, dest):
        with open(dest, "wb") as f:
            f.write(b"front part")
        raise OSError("connection reset")

    monkeypatch.setattr(urllib.request, "urlretrieve", dies_part_way)
    entry = vmr.VIDEO_MODEL_REGISTRY["codeformer"]
    local = vmr.resolve_entry_dir(entry)
    with pytest.raises(OSError):
        vmr.download_entry_files(entry, local)
    assert not (local / "codeformer.pth").exists()
    assert vmr.is_model_installed("codeformer") is False
