"""A user ComfyUI generation model installs and is ready with its companions.

The UNET a person adds renders on its family's shipped graph, with the encoder
and VAE that shipped entry (``like``) requires. Install fetches them beside the
UNET through the registry downloader, readiness needs them on disk, and the
models list says which files are still missing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import user_image_models as uim
from backend.services import video_model_registry as vmr


def _generator(tmp_path):
    return SimpleNamespace(
        models_dir=tmp_path / "stable_diffusion",
        available_models={"zimage-turbo": "Tongyi-MAI/Z-Image-Turbo"},
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
    """A throwaway ComfyUI models/ tree every registry path resolves into."""
    root = tmp_path / "comfy_models"
    monkeypatch.setattr(vmr, "comfyui_models_dir", lambda: root)
    return root


def _add_qwen(gen):
    return uim.add_user_model(
        gen, role="generation", family="qwen-image-21",
        hf_repo="abenzerps/Qwen-Image-2.1-Uncensored-GGUF",
        files=[{"src": "qwen-image-2.1-UC-fp8.safetensors", "size": 7_120_000_000}],
        has_model_index=False, name="Qwen UC fp8",
    )


def _write(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_companions_follow_the_graph_the_model_renders_on(catalog_dir, tmp_path, comfy_models):
    gen = _generator(tmp_path)
    mid, _ = _add_qwen(gen)
    assert uim.companion_ids(gen, mid) == vmr.VIDEO_MODEL_REGISTRY["qwen-image-21"]["requires"]
    assert uim.companion_ids(gen, mid) == ["qwen-image-21-clip", "qwen-image-21-vae"]
    assert uim.companion_dirs(gen, mid) == [comfy_models / "text_encoders", comfy_models / "vae"]
    st_mid, _ = uim.add_user_model(
        gen, role="generation", family="flux", hf_repo="someone/flux-finetune",
        files=[{"src": "flux1-dev-fp8.safetensors"}], has_model_index=False, name="Flux FP8",
    )
    assert uim.companion_ids(gen, st_mid) == vmr.VIDEO_MODEL_REGISTRY["flux-dev"]["requires"]
    assert "flux-t5-fp16" in uim.companion_ids(gen, st_mid)
    # A .gguf renders on the schnell graph, which loads the fp8 T5, not dev's fp16.
    gguf_mid, _ = uim.add_user_model(
        gen, role="generation", family="flux", hf_repo="city96/flux-mirror",
        files=[{"src": "flux1-dev-Q8_0.gguf"}], has_model_index=False, name="Flux Q8",
    )
    assert uim.companion_ids(gen, gguf_mid) == vmr.VIDEO_MODEL_REGISTRY["flux-schnell"]["requires"]
    assert "flux-t5-fp8" in uim.companion_ids(gen, gguf_mid)
    assert "flux-t5-fp16" not in uim.companion_ids(gen, gguf_mid)
    lora_mid, _ = uim.add_user_model(
        gen, role="lora", family="zimage", hf_repo="x/y", files=[{"src": "l.safetensors"}], name="L",
    )
    assert uim.companion_ids(gen, lora_mid) == []


def test_dispatch_and_companions_share_one_graph_rule(catalog_dir, tmp_path):
    # The entry comfy_t2i_target renders on is the entry companion_ids reads
    # `requires` from, for every user ComfyUI generation model.
    from backend.services.user_model_families import comfy_graph_model

    gen = _generator(tmp_path)
    added = [
        _add_qwen(gen)[0],
        uim.add_user_model(gen, role="generation", family="flux", hf_repo="city96/flux-mirror",
                           files=[{"src": "flux1-dev-Q8_0.gguf"}], has_model_index=False, name="Q8")[0],
        uim.add_user_model(gen, role="generation", family="flux", hf_repo="someone/flux-finetune",
                           files=[{"src": "flux1-dev-fp8.safetensors"}], has_model_index=False, name="FP8")[0],
    ]
    for mid in added:
        target = uim.comfy_t2i_target(gen, mid)
        assert target["tag"] == comfy_graph_model(target["family"], target["unet"])
        assert uim.companion_ids(gen, mid) == vmr.VIDEO_MODEL_REGISTRY[target["tag"]]["requires"]
    assert comfy_graph_model("flux", "x.gguf") == "flux-schnell"
    assert comfy_graph_model("flux", "x.safetensors") == "flux-dev"
    assert comfy_graph_model("qwen-image-21", "x.safetensors") == "qwen-image-21"
    assert comfy_graph_model("qwen-image-edit", "x.safetensors") is None
    assert comfy_graph_model("sdxl", "x.safetensors") is None


def test_a_user_flux_gguf_is_ready_with_the_schnell_companions(catalog_dir, tmp_path, comfy_models):
    gen = _generator(tmp_path)
    mid, _ = uim.add_user_model(
        gen, role="generation", family="flux", hf_repo="city96/flux-mirror",
        files=[{"src": "flux1-dev-Q8_0.gguf"}], has_model_index=False, name="Flux Q8",
    )
    sentinel = uim.user_sentinel(mid)
    _write(comfy_models / "unet" / "flux1-dev-Q8_0.gguf")
    for eid in vmr.VIDEO_MODEL_REGISTRY["flux-schnell"]["requires"]:
        entry = vmr.VIDEO_MODEL_REGISTRY[eid]
        _write(vmr.resolve_entry_dir(entry) / entry["check_files"][0])
    assert uim.user_model_ready(gen, sentinel) is True, "the fp16 T5 of the dev graph is not needed"
    assert uim.user_missing_files(gen, mid) == []
    assert uim.missing_companion_size_gb(gen, mid) == 0.0


def test_ready_needs_the_unet_and_every_companion(catalog_dir, tmp_path, comfy_models):
    gen = _generator(tmp_path)
    mid, _ = _add_qwen(gen)
    sentinel = uim.user_sentinel(mid)
    unet = comfy_models / "diffusion_models" / "qwen-image-2.1-UC-fp8.safetensors"
    clip = comfy_models / "text_encoders" / "qwen3vl_8b_int8_convrot.safetensors"
    vae = comfy_models / "vae" / "qwen_image_2.1_vae_bf16.safetensors"

    assert uim.user_model_ready(gen, sentinel) is False
    assert uim.user_missing_files(gen, mid) == [
        f"{mid}:qwen-image-2.1-UC-fp8.safetensors",
        "qwen-image-21-clip:qwen3vl_8b_int8_convrot.safetensors",
        "qwen-image-21-vae:qwen_image_2.1_vae_bf16.safetensors",
    ]

    _write(unet)
    assert uim.user_files_present(gen, sentinel) is True
    assert uim.user_model_ready(gen, sentinel) is False, "the UNET alone is not a render-ready model"
    assert uim.missing_companions(gen, mid) == ["qwen-image-21-clip", "qwen-image-21-vae"]
    assert uim.missing_companion_size_gb(gen, mid) == pytest.approx(9.35 + 0.68)

    _write(clip)
    _write(vae)
    assert uim.user_model_ready(gen, sentinel) is True
    assert uim.user_missing_files(gen, mid) == []


def test_install_pulls_the_missing_companions_through_the_registry_downloader(
    catalog_dir, tmp_path, comfy_models, monkeypatch,
):
    gen = _generator(tmp_path)
    mid, _ = _add_qwen(gen)
    sentinel = uim.user_sentinel(mid)
    _write(comfy_models / "vae" / "qwen_image_2.1_vae_bf16.safetensors")   # already there

    def fake_hf_download(repo_id, filename, revision="main", local_dir=None, **_kw):
        got = Path(local_dir) / Path(filename).name
        _write(got)
        return str(got)

    pulled = []

    def fake_download_entry_files(entry, local_dir, *, check=None):
        pulled.append((entry["files"][0]["dst"], Path(local_dir)))
        _write(Path(local_dir) / entry["files"][0]["dst"])

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_download)
    monkeypatch.setattr(vmr, "download_entry_files", fake_download_entry_files)

    ok, err = uim.download_user_files(gen, sentinel)
    assert (ok, err) == (True, None)
    assert pulled == [("qwen3vl_8b_int8_convrot.safetensors", comfy_models / "text_encoders")]
    assert uim.user_model_ready(gen, sentinel) is True


def test_a_companion_pull_that_leaves_no_file_fails_the_install(catalog_dir, tmp_path, comfy_models, monkeypatch):
    gen = _generator(tmp_path)
    mid, _ = _add_qwen(gen)
    sentinel = uim.user_sentinel(mid)
    _write(comfy_models / "diffusion_models" / "qwen-image-2.1-UC-fp8.safetensors")
    monkeypatch.setattr(vmr, "download_entry_files", lambda entry, local_dir, *, check=None: None)

    ok, err = uim.download_companions(gen, sentinel)
    assert ok is False and "qwen-image-21-clip" in err
    assert uim.user_model_ready(gen, sentinel) is False


def test_comfy_t2i_target_names_the_graph_and_the_users_unet(catalog_dir, tmp_path):
    gen = _generator(tmp_path)
    mid, _ = _add_qwen(gen)
    assert uim.comfy_t2i_target(gen, mid) == {
        "family": "qwen-image-21", "tag": "qwen-image-21", "unet": "qwen-image-2.1-UC-fp8.safetensors",
    }
    gguf_mid, _ = uim.add_user_model(
        gen, role="generation", family="flux", hf_repo="city96/flux-mirror",
        files=[{"src": "flux1-dev-Q8_0.gguf"}], has_model_index=False, name="Flux Q8",
    )
    assert uim.comfy_t2i_target(gen, gguf_mid) == {"family": "flux", "tag": "flux-schnell", "unet": "flux1-dev-Q8_0.gguf"}
    st_mid, _ = uim.add_user_model(
        gen, role="generation", family="flux", hf_repo="someone/flux-finetune",
        files=[{"src": "flux1-dev-fp8.safetensors"}], has_model_index=False, name="Flux FP8",
    )
    assert uim.comfy_t2i_target(gen, st_mid) == {"family": "flux", "tag": "flux-dev", "unet": "flux1-dev-fp8.safetensors"}
    # Shipped ids pass through; the graph loads its registry file.
    assert uim.comfy_t2i_target(gen, "flux-dev") == {"family": "flux", "tag": "flux-dev", "unet": None}
    assert uim.comfy_t2i_target(gen, "qwen-image-21") == {"family": "qwen-image-21", "tag": "qwen-image-21", "unet": None}
    assert uim.comfy_t2i_target(gen, "zimage-turbo") is None


# ── the registry side: plan, readiness and the one downloader ────────────────

def test_qwen_image_21_entries_declare_their_files_and_nodes():
    entry = vmr.VIDEO_MODEL_REGISTRY["qwen-image-21"]
    assert entry["requires"] == ["qwen-image-21-clip", "qwen-image-21-vae"]
    assert entry["comfy_nodes"] == ["TextEncodeQwenImage21", "QwenImage21Cache"]
    assert (entry["default_steps"], entry["default_cfg"]) == (25, 1.0)
    assert "min_steps" not in entry, "no floor until a card measures one"
    assert "non-commercial" in entry["description"].lower()
    assert vmr.install_plan("qwen-image-21") == ["qwen-image-21", "qwen-image-21-clip", "qwen-image-21-vae"]
    for eid in vmr.install_plan("qwen-image-21"):
        assert vmr.VIDEO_MODEL_REGISTRY[eid]["hf_repo"] == "Comfy-Org/Qwen-Image-2.1"
    assert not [p for p in vmr.verify_registry() if p.startswith("qwen-image-21")]


def test_plan_readiness_needs_every_entry(tmp_path, monkeypatch):
    root = tmp_path / "models"
    monkeypatch.setattr(vmr, "comfyui_models_dir", lambda: root)
    _write(root / "diffusion_models" / "qwen_image_2.1_int8_convrot.safetensors")
    assert vmr.is_model_installed("qwen-image-21") is True
    assert vmr.is_plan_installed("qwen-image-21") is False
    assert vmr.missing_plan_files("qwen-image-21") == [
        "qwen-image-21-clip:qwen3vl_8b_int8_convrot.safetensors",
        "qwen-image-21-vae:qwen_image_2.1_vae_bf16.safetensors",
    ]
    _write(root / "text_encoders" / "qwen3vl_8b_int8_convrot.safetensors")
    _write(root / "vae" / "qwen_image_2.1_vae_bf16.safetensors")
    assert vmr.is_plan_installed("qwen-image-21") is True
    assert vmr.missing_plan_files("qwen-image-21") == []
    assert vmr.is_plan_installed("not-a-model") is False


def test_download_entry_files_lands_each_file_at_its_dst(tmp_path, monkeypatch):
    import huggingface_hub

    seen = []

    def fake_hf_download(repo_id, filename, local_dir=None, **_kw):
        seen.append((repo_id, filename))
        got = Path(local_dir) / filename
        _write(got)
        return str(got)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_download)
    local = tmp_path / "text_encoders"
    local.mkdir()
    vmr.download_entry_files(vmr.VIDEO_MODEL_REGISTRY["qwen-image-21-clip"], local)
    assert seen == [("Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_int8_convrot.safetensors")]
    assert (local / "qwen3vl_8b_int8_convrot.safetensors").is_file()
    # A second pull keeps the file and asks the Hub for nothing.
    vmr.download_entry_files(vmr.VIDEO_MODEL_REGISTRY["qwen-image-21-clip"], local)
    assert len(seen) == 1


def test_download_entry_files_stops_when_the_check_raises(tmp_path, monkeypatch):
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda **_kw: pytest.fail("must not pull"))

    def stop():
        raise RuntimeError("superseded")

    with pytest.raises(RuntimeError, match="superseded"):
        vmr.download_entry_files(vmr.VIDEO_MODEL_REGISTRY["qwen-image-21-vae"], tmp_path, check=stop)
