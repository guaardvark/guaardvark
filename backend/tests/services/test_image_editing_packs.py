"""The image-editing pack table: rows, readiness and the one refusal sentence."""

from __future__ import annotations

import pytest

from backend.services import image_editing_packs as packs
from backend.services import video_model_registry as vmr


PHOTO_TOOLS = ("edit_image", "inpaint_image", "outpaint_image", "generate_identity", "remove_background")


def test_every_pack_is_a_registry_entry():
    for pack in packs.PACKS:
        assert pack["id"] in vmr.VIDEO_MODEL_REGISTRY, pack["id"]


def test_every_photo_tool_has_a_pack():
    for tool in PHOTO_TOOLS:
        assert packs.packs_for_tool(tool), tool


def test_pulid_pack_pulls_its_companions(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_IDENTITY_TOOL", "1")
    plan = packs.pack_plan(packs.pack_by_id("pulid-flux"))
    for eid in ("pulid-flux", "pulid-antelopev2", "eva02-clip", "facexlib-face", "flux-dev"):
        assert eid in plan


def test_missing_message_names_the_image_modal():
    msg = packs.missing_message("remove_background")
    assert msg.startswith("Background removal needs BiRefNet general or u2net, which is not installed.")
    assert "Manage Image Models" in msg
    assert "Video" not in msg
    assert packs.missing_message("generate_identity").startswith(
        "Identity generation needs the PuLID identity pack"
    )
    assert packs.missing_message("edit_image").startswith(
        "Image editing needs Qwen-Image-Edit or FLUX.1 Kontext"
    )


def test_identity_pack_is_listed_without_any_flag(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_IDENTITY_TOOL", raising=False)
    assert packs.pack_by_id("pulid-flux") is not None
    assert "pulid-flux" in {p["id"] for p in packs.enabled_packs()}
    assert "PuLID" in packs.missing_message("generate_identity")


def test_pack_rows_report_install_state(monkeypatch):
    from backend.api import batch_video_generation_api as video_api

    monkeypatch.setenv("GUAARDVARK_IDENTITY_TOOL", "1")
    monkeypatch.setattr(video_api, "_check_model_downloaded", lambda mid: mid == "bgremove-u2net")
    monkeypatch.setattr(video_api, "_missing_check_files", lambda mid: [f"{mid}:x"])
    rows = {r["id"]: r for r in packs.pack_rows()}
    assert rows["bgremove-u2net"]["installed"] is True
    assert rows["bgremove-u2net"]["is_downloaded"] is True
    assert rows["bgremove-u2net"]["missing_files"] == []
    assert rows["bgremove-birefnet"]["installed"] is False
    assert rows["bgremove-birefnet"]["missing_files"] == ["bgremove-birefnet:x"]
    assert rows["bgremove-u2net"]["path"] == "comfy:bgremove-u2net"
    assert rows["bgremove-u2net"]["tools"] == ["remove_background"]
    assert rows["pulid-flux"]["size_gb"] > vmr.VIDEO_MODEL_REGISTRY["pulid-flux"]["size_gb"]
    assert packs.tool_ready("remove_background") is True
    assert packs.tool_ready("generate_identity") is False


@pytest.mark.parametrize("dest,expected_tail", [
    ("bgremove", ("data", "models", "background_removal")),
    (None, ("models", "loras")),
])
def test_resolve_entry_dir(dest, expected_tail):
    entry = {"local_subdir": "loras"}
    if dest:
        entry["dest"] = dest
    path = vmr.resolve_entry_dir(entry)
    assert tuple(path.parts[-len(expected_tail):]) == expected_tail


def test_hf_cache_entry_checks_the_cache(monkeypatch, tmp_path):
    from backend.services import local_weights

    entry = vmr.VIDEO_MODEL_REGISTRY["eva02-clip"]
    name = entry["files"][0]["src"]
    cached = tmp_path / name
    cached.write_bytes(b"legacy pickle bytes")
    asked = []

    def in_cache(repo, filename, cache_dir=None):
        asked.append((repo, filename))
        return str(cached)

    monkeypatch.setattr(local_weights, "cached_path", in_cache)
    # Whole at the size the entry publishes for it.
    whole = dict(entry, file_bytes={name: cached.stat().st_size})
    assert vmr.entry_files_present(whole) is True
    assert asked == [(entry["hf_repo"], name)]
    # In the cache but far short of the registry's published size.
    assert vmr.entry_files_present(entry) is False
    monkeypatch.setattr(local_weights, "cached_path", lambda repo, filename, cache_dir=None: None)
    assert vmr.entry_files_present(whole) is False
