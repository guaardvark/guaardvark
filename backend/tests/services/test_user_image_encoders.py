"""User text encoders for FLUX: catalog entries, header checks, render-time resolution."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import pytest

from backend.services import user_image_models as uim
from backend.services import user_model_families as umf


def _generator(tmp_path):
    return SimpleNamespace(
        models_dir=tmp_path / "stable_diffusion",
        available_models={"flux-dev": "comfy:flux-dev"},
        model_meta={},
        hidden_models=set(),
    )


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch):
    path = tmp_path / "user_image_models.json"
    monkeypatch.setattr(uim, "_CATALOG_PATH_OVERRIDE", path)
    return path


@pytest.fixture
def comfy_models(tmp_path, monkeypatch):
    root = tmp_path / "comfy_models"
    monkeypatch.setattr("backend.services.video_model_registry.comfyui_models_dir", lambda: root)
    return root


def _write_header(path, tensors):
    header = {name: {"dtype": "F16", "shape": shape, "data_offsets": [0, 0]} for name, shape in tensors.items()}
    raw = json.dumps(header).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)


T5 = {"shared.weight": [32128, 4096], "encoder.block.0.layer.0.SelfAttention.q.weight": [4096, 4096]}
CLIP_L = {
    "text_model.embeddings.token_embedding.weight": [49408, 768],
    "text_model.encoder.layers.0.self_attn.q_proj.weight": [768, 768],
}


def _add_t5(gen, **overrides):
    kwargs = dict(
        role="encoder", family="flux", slot="t5", hf_repo="someone/flan-t5-xxl-fp16",
        files=[{"src": "flan_t5_xxl_fp16.safetensors", "size": 9_000_000_000}], name="Flan T5",
    )
    kwargs.update(overrides)
    return uim.add_user_model(gen, **kwargs)


def test_flux_declares_both_dual_clip_slots():
    slots = umf.encoder_slots("flux")
    assert slots["t5"]["arch"] == "t5-xxl" and slots["clip_l"]["arch"] == "clip-l"
    assert slots["t5"]["replaces"] == {"flux-dev": "flux-t5-fp16", "flux-schnell": "flux-t5-fp8"}
    # Every companion a slot replaces is one the graph actually requires.
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
    for slot in slots.values():
        for graph, companion in slot["replaces"].items():
            assert companion in VIDEO_MODEL_REGISTRY[graph]["requires"]
    assert umf.encoder_slots("sdxl") == {} and umf.encoder_slots("zimage") == {}


def test_encoder_entry_installs_in_its_own_folder_and_stays_out_of_the_picker(catalog_dir, comfy_models, tmp_path):
    gen = _generator(tmp_path)
    mid, entry = _add_t5(gen)
    assert mid.startswith("user-flux-")
    assert entry["kind"] == "encoder" and entry["engine"] == "comfy" and entry["local_subdir"] == "clip"
    assert entry["files"][0]["dst"] == f"user/{mid}/flan_t5_xxl_fp16.safetensors"
    assert entry["slot"] == "t5" and entry["arch"] == "t5-xxl" and entry["shared_with"] is None
    assert mid in gen.hidden_models
    assert uim.user_download_dir(gen, mid) == comfy_models / "clip"
    row = uim.encoder_rows(gen)[0]
    assert row["id"] == mid and row["slot_label"] == "T5-XXL" and row["is_downloaded"] is False
    assert uim.catalog_rows(gen) == []


@pytest.mark.parametrize("overrides, fragment", [
    ({"slot": None}, "Pick which text encoder"),
    ({"slot": "vae"}, "Pick which text encoder"),
    ({"files": [{"src": "a.safetensors"}, {"src": "b.safetensors"}]}, "one file"),
    ({"files": [{"src": "t5-xxl-Q8_0.gguf"}]}, "pick a .safetensors file"),
    ({"family": "sdxl"}, "does not take a replacement text encoder"),
])
def test_encoder_entries_refused_at_add(catalog_dir, tmp_path, overrides, fragment):
    with pytest.raises(ValueError, match=fragment):
        _add_t5(_generator(tmp_path), **overrides)


def test_a_shipped_companion_file_is_shared_not_fetched_again(catalog_dir, comfy_models, tmp_path):
    gen = _generator(tmp_path)
    mid, entry = _add_t5(
        gen, hf_repo="comfyanonymous/flux_text_encoders",
        files=[{"src": "t5xxl_fp8_e4m3fn.safetensors"}], name="fp8 T5 on dev",
    )
    # FLUX schnell's companion, not CogVideoX's renamed copy of the same file.
    assert entry["shared_with"] == "flux-t5-fp8"
    assert entry["files"][0]["dst"] == "t5/t5xxl_fp8_e4m3fn.safetensors"
    shipped = comfy_models / "clip" / "t5" / "t5xxl_fp8_e4m3fn.safetensors"
    _write_header(shipped, T5)
    result = uim.remove_user_model(gen, mid, delete_files=True)
    assert result["deleted_paths"] == [] and shipped.is_file()


def test_remove_deletes_the_encoder_and_its_folder(catalog_dir, comfy_models, tmp_path):
    gen = _generator(tmp_path)
    mid, entry = _add_t5(gen)
    path = comfy_models / "clip" / entry["files"][0]["dst"]
    _write_header(path, T5)
    result = uim.remove_user_model(gen, mid, delete_files=True)
    assert result["deleted_paths"] == [str(path)]
    assert not path.parent.exists() and (comfy_models / "clip").is_dir()


def test_download_lands_in_a_staging_folder_not_on_a_shipped_name(catalog_dir, comfy_models, tmp_path, monkeypatch):
    gen = _generator(tmp_path)
    mid, entry = _add_t5(gen, hf_repo="someone/better-clip", slot="clip_l",
                         files=[{"src": "clip_l.safetensors"}], name="Better CLIP-L")
    shipped = comfy_models / "clip" / "clip_l.safetensors"
    _write_header(shipped, CLIP_L)
    before = shipped.read_bytes()
    seen = {}

    def _fake_download(repo_id, filename, revision, local_dir):
        seen["local_dir"] = local_dir
        out = tmp_path.joinpath(local_dir, filename)
        _write_header(out, CLIP_L)
        return str(out)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", _fake_download)
    ok, err = uim.download_user_files(gen, uim.user_sentinel(mid))
    assert (ok, err) == (True, None)
    assert seen["local_dir"] == str(comfy_models / "clip" / "user" / mid / ".download")
    assert shipped.read_bytes() == before
    assert (comfy_models / "clip" / entry["files"][0]["dst"]).is_file()
    assert not (comfy_models / "clip" / "user" / mid / ".download").exists()


def test_resolve_returns_the_loader_filename_per_slot(catalog_dir, comfy_models, tmp_path):
    gen = _generator(tmp_path)
    t5_id, t5 = _add_t5(gen)
    clip_id, clip = _add_t5(gen, hf_repo="someone/better-clip", slot="clip_l",
                            files=[{"src": "ViT-L-14-text.safetensors"}], name="Better CLIP-L")
    _write_header(comfy_models / "clip" / t5["files"][0]["dst"], T5)
    _write_header(comfy_models / "clip" / clip["files"][0]["dst"], CLIP_L)
    for model in ("flux-dev", "flux-schnell", "user-flux-my-unet"):
        chosen, err = uim.resolve_user_encoders(gen, model, [t5_id, {"id": clip_id}])
        assert err is None
        assert chosen == {"t5": t5["files"][0]["dst"], "clip_l": clip["files"][0]["dst"]}
    assert uim.resolve_user_encoders(gen, "flux-dev", []) == ({}, None)


def test_resolve_refuses_with_a_reason(catalog_dir, comfy_models, tmp_path):
    gen = _generator(tmp_path)
    t5_id, t5 = _add_t5(gen)
    _, err = uim.resolve_user_encoders(gen, "flux-dev", [t5_id])
    assert "is not installed" in err
    # A file that turned out to be the wrong architecture is caught before ComfyUI sees it.
    _write_header(comfy_models / "clip" / t5["files"][0]["dst"], CLIP_L)
    _, err = uim.resolve_user_encoders(gen, "flux-dev", [t5_id])
    assert "is a CLIP-L" in err and "T5-XXL" in err
    _write_header(comfy_models / "clip" / t5["files"][0]["dst"], T5)
    _, err = uim.resolve_user_encoders(gen, "zimage-turbo", [t5_id])
    assert "does not take a replacement text encoder" in err
    _, err = uim.resolve_user_encoders(gen, "flux-dev", ["user-flux-missing"])
    assert "is not a text encoder you added" in err
    other_id, other = _add_t5(gen, hf_repo="someone/other-t5", files=[{"src": "other.safetensors"}], name="Other")
    _write_header(comfy_models / "clip" / other["files"][0]["dst"], T5)
    _, err = uim.resolve_user_encoders(gen, "flux-dev", [t5_id, other_id])
    assert "Two text encoders were picked for the T5-XXL slot" in err


@pytest.mark.parametrize("role, slot, tensors, fragment", [
    ("encoder", "t5", CLIP_L, "is a CLIP-L"),
    ("encoder", "t5", {"double_blocks.0.x": [1]}, "is a diffusion model"),
    ("generation", None, T5, "is a T5-XXL text encoder, not a FLUX.1 model"),
    ("lora", None, CLIP_L, "is a CLIP-L text encoder, not a FLUX.1 LoRA"),
    ("lora", None, {"double_blocks.0.x": [1]}, "is a full diffusion model, not a LoRA"),
])
def test_header_problem_turns_the_wrong_file_away_before_download(monkeypatch, role, slot, tensors, fragment):
    monkeypatch.setattr("backend.services.text_encoder_check.hf_tensors", lambda *a, **k: tensors)
    problem = uim.header_problem(role=role, family="flux", slot=slot, hf_repo="o/r", src="x.safetensors")
    assert fragment in problem


def test_header_problem_passes_a_fitting_or_unreadable_file(monkeypatch):
    monkeypatch.setattr("backend.services.text_encoder_check.hf_tensors", lambda *a, **k: T5)
    assert uim.header_problem(role="encoder", family="flux", slot="t5", hf_repo="o/r", src="x.safetensors") is None
    monkeypatch.setattr("backend.services.text_encoder_check.hf_tensors", lambda *a, **k: None)
    assert uim.header_problem(role="generation", family="flux", slot=None, hf_repo="o/r", src="x.safetensors") is None


def test_preview_suggests_the_encoder_role_and_slot_from_the_header(monkeypatch):
    monkeypatch.setattr(uim, "inspect_hf_repo", lambda url: {
        "hf_repo": "someone/encoders", "revision": "main", "src": None,
        "files": [{"src": "a.safetensors", "size": 1}, {"src": "b.safetensors", "size": 1}],
    })
    by_file = {"a.safetensors": CLIP_L, "b.safetensors": T5}
    monkeypatch.setattr(
        "backend.services.text_encoder_check.hf_tensors", lambda repo, src, revision="main": by_file[src],
    )
    preview = uim.preview_hf_url("https://huggingface.co/someone/encoders")
    assert [f.get("arch") for f in preview["files"]] == ["clip-l", "t5-xxl"]
    assert preview["files"][1]["arch_label"] == "T5-XXL"

    monkeypatch.setattr(uim, "inspect_hf_repo", lambda url: {
        "hf_repo": "someone/flan", "revision": "main", "src": "flan.safetensors",
        "files": [{"src": "flan.safetensors", "size": 1}],
    })
    monkeypatch.setattr("backend.services.text_encoder_check.hf_tensors", lambda *a, **k: T5)
    preview = uim.preview_hf_url("https://huggingface.co/someone/flan/blob/main/flan.safetensors")
    assert (preview["suggested_role"], preview["suggested_family"], preview["suggested_slot"]) == ("encoder", "flux", "t5")


def test_preview_says_when_no_model_takes_that_encoder(monkeypatch):
    monkeypatch.setattr(uim, "inspect_hf_repo", lambda url: {
        "hf_repo": "Comfy-Org/z_image_turbo", "revision": "main",
        "src": "split_files/text_encoders/qwen_3_4b.safetensors",
        "files": [{"src": "split_files/text_encoders/qwen_3_4b.safetensors", "size": 1}],
    })
    llm = {"model.embed_tokens.weight": [151936, 2560], "model.layers.0.self_attn.q_proj.weight": [4096, 2560]}
    monkeypatch.setattr("backend.services.text_encoder_check.hf_tensors", lambda *a, **k: llm)
    preview = uim.preview_hf_url("https://huggingface.co/Comfy-Org/z_image_turbo/blob/main/x")
    assert preview["suggested_role"] is None
    assert any("language-model encoder" in w and "FLUX.1 takes a T5-XXL or a CLIP-L" in w for w in preview["warnings"])


def test_encoder_file_names_now_suggest_the_encoder_role_for_images():
    matches = umf.match_families(
        domain="image", files=[], src="t5xxl_flan_fp16.safetensors", hf_repo="someone/flan-t5", has_model_index=False,
    )
    assert matches[0]["family"] == "flux" and matches[0]["role"] == "encoder"
