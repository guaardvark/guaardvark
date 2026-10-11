"""Image models list + user-add API, mirroring the video suite."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    from flask import Flask
    from backend.api import batch_image_generation_api as api
    from backend.api.batch_image_generation_api import batch_image_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


def _generator(tmp_path):
    return SimpleNamespace(
        models_dir=tmp_path / "stable_diffusion",
        available_models={"zimage-turbo": "Tongyi-MAI/Z-Image-Turbo", "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0"},
        model_meta={},
        hidden_models=set(),
    )


def _stub_image_inspect(monkeypatch, *, hf_repo, files, has_model_index=False, unwired=None):
    from backend.services import user_image_models as uim

    listed = list(files)

    def fake(_url):
        return {
            "hf_repo": hf_repo,
            "revision": "main",
            "src": listed[0]["src"] if listed else None,
            "files": listed,
            "has_model_index": has_model_index,
            "gated": False,
            "truncated": False,
            "warnings": [],
            "matches": [],
            "unwired": unwired,
        }

    monkeypatch.setattr(uim, "preview_hf_url", fake)
    _stub_lora_header(monkeypatch, None)


def _stub_lora_header(monkeypatch, problem):
    """The Z-Image LoRA add check reads the file header from the Hub; keep tests offline."""
    from backend.services import zimage_lora_check
    monkeypatch.setattr(zimage_lora_check, "hf_lora_problem", lambda *_a, **_k: problem)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.services import user_image_models as uim
    monkeypatch.setattr(uim, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_image_models.json")
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(batch_image_bp)
    monkeypatch.setattr(api, "service_available", True, raising=False)
    gen = _generator(tmp_path)
    wrapper = MagicMock()
    wrapper.image_generator = gen
    monkeypatch.setattr(api, "get_batch_image_generator", lambda: wrapper)
    return app.test_client()


def test_from_hf_rejects_non_hf(client):
    payload = client.post("/api/batch-image/models/from-hf", json={"url": "https://civitai.com/x"}).get_json()
    assert payload["success"] is False
    assert "Hugging Face" in ((payload.get("error") or {}).get("message") or "")


def test_from_hf_rejects_dataset(client):
    payload = client.post(
        "/api/batch-image/models/from-hf",
        json={"url": "https://huggingface.co/datasets/org/repo"},
    ).get_json()
    assert payload["success"] is False
    assert "dataset" in ((payload.get("error") or {}).get("message") or "").lower()


def test_user_add_and_delete(client, tmp_path, monkeypatch):
    from backend.services import user_image_models as uim
    _stub_image_inspect(
        monkeypatch,
        hf_repo="someone/zimage-lora",
        files=[{"src": "realism.safetensors", "size": 32_000_000}],
    )
    body = {
        "role": "lora",
        "family": "zimage",
        "hf_repo": "someone/zimage-lora",
        "files": [{"src": "realism.safetensors"}],
        "name": "API lora",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert mid.startswith("user-zimage-")
        assert payload["data"]["entry"]["role"] == "lora"
        assert payload["data"]["entry"]["files"][0]["size"] == 32_000_000
        assert payload["data"]["entry"]["size_gb"] == round(32_000_000 / (1024 ** 3), 3)
        forbidden = client.delete("/api/batch-image/models/user/zimage-turbo").get_json()
        assert forbidden["success"] is False
        gone = client.delete(f"/api/batch-image/models/user/{mid}").get_json()
        assert gone["success"] is True
    finally:
        try:
            wrapper = api.get_batch_image_generator()
            uim.remove_user_model(wrapper.image_generator, mid)
        except Exception:
            pass


def test_user_add_ignores_forged_model_index_when_url_inspected(client, monkeypatch):
    from backend.services import user_image_models as uim

    def fake_preview(_url):
        return {
            "hf_repo": "x/y",
            "revision": "main",
            "src": None,
            "files": [{"src": "merged.safetensors", "size": 100}],
            "has_model_index": False,
            "gated": False,
            "truncated": False,
            "warnings": [],
            "matches": [],
            "unwired": None,
        }

    monkeypatch.setattr(uim, "preview_hf_url", fake_preview)
    monkeypatch.setattr(api, "preview_hf_image_model", api.preview_hf_image_model)
    body = {
        "url": "https://huggingface.co/x/y",
        "role": "generation",
        "family": "zimage",
        "has_model_index": True,
        "files": [{"src": "merged.safetensors"}],
        "name": "Forged",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "diffusers" in ((payload.get("error") or {}).get("message") or "").lower()


def test_user_add_install_409_still_succeeds(client, monkeypatch):
    from backend.utils.response_utils import error_response
    from backend.services import user_image_models as uim
    _stub_image_inspect(
        monkeypatch,
        hf_repo="someone/zimage-lora",
        files=[{"src": "busy.safetensors", "size": 1000}],
    )
    monkeypatch.setattr(
        api, "_start_image_model_download",
        lambda _mid: error_response("Already downloading model: other", 409),
    )
    body = {
        "role": "lora",
        "family": "zimage",
        "hf_repo": "someone/zimage-lora",
        "files": [{"src": "busy.safetensors"}],
        "name": "While busy",
        "install": True,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert payload["data"]["download"]["status"] == 409
    finally:
        try:
            wrapper = api.get_batch_image_generator()
            uim.remove_user_model(wrapper.image_generator, mid)
        except Exception:
            pass


def test_user_add_refuses_zimage_lora_the_engine_cannot_load(client, monkeypatch, tmp_path):
    _stub_image_inspect(
        monkeypatch,
        hf_repo="someone/zimage-lokr",
        files=[{"src": "style.safetensors", "size": 4_000_000}],
    )
    _stub_lora_header(monkeypatch, "It is a LoKr adapter.")
    body = {
        "role": "lora",
        "family": "zimage",
        "hf_repo": "someone/zimage-lokr",
        "files": [{"src": "style.safetensors"}],
        "install": True,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    message = (payload.get("error") or {}).get("message") or ""
    assert message == "style.safetensors can't be added as a Z-Image LoRA. It is a LoKr adapter."
    assert not (tmp_path / "user_image_models.json").exists()


def test_user_add_without_url_refuses_unwired(client, monkeypatch):
    _stub_image_inspect(
        monkeypatch,
        hf_repo="Qwen/Qwen-Image",
        files=[{"src": "model.safetensors", "size": 100}],
        unwired={"family": "qwen-image", "reason": "Qwen-Image is not wired yet."},
    )
    body = {
        "role": "generation",
        "family": "zimage",
        "hf_repo": "Qwen/Qwen-Image",
        "files": [{"src": "model.safetensors"}],
        "name": "Qwen",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "not wired" in ((payload.get("error") or {}).get("message") or "").lower()


def test_user_add_without_url_refuses_forged_snapshot(client, monkeypatch):
    _stub_image_inspect(
        monkeypatch,
        hf_repo="x/y",
        files=[{"src": "merged.safetensors", "size": 100}],
        has_model_index=False,
    )
    body = {
        "role": "generation",
        "family": "zimage",
        "hf_repo": "x/y",
        "has_model_index": True,
        "files": [{"src": "merged.safetensors"}],
        "name": "Forged no url",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "diffusers" in ((payload.get("error") or {}).get("message") or "").lower()


def test_list_includes_undownloaded_user_flux(client, monkeypatch):
    from backend.services import user_image_models as uim

    gen = api.get_batch_image_generator().image_generator
    gen.get_available_models = lambda probe_remote=True: {
        "user-flux-fp8": {
            "id": "user:user-flux-fp8",
            "downloaded": False,
            "availability": "downloadable",
            "selectable": True,
            "label": "Flux FP8",
            "description": "",
            "recommended": False,
            "user": True,
            "family": "flux",
            "kind": "comfy_files",
            "order": 1,
            "size_gb": 11.2,
        }
    }
    monkeypatch.setattr(uim, "catalog_rows", lambda _g: [])
    monkeypatch.setattr(uim, "family_choices", lambda: [])
    payload = client.get("/api/batch-image/models").get_json()
    assert payload["success"], payload
    ids = [m["id"] for m in payload["data"]["models"]]
    assert "user-flux-fp8" in ids
    assert "user-flux-fp8" not in [m["id"] for m in payload["data"]["unavailable_models"]]


def test_list_carries_missing_files_for_comfy_rows(client, monkeypatch):
    # A ComfyUI model that lacks its companions says which files Install still
    # fetches, in the editing packs' "<entry>:<file>" shape; offline rows say [].
    from backend.services import user_image_models as uim

    gen = api.get_batch_image_generator().image_generator
    base = {"downloaded": False, "availability": "downloadable", "selectable": True,
            "description": "", "recommended": False, "kind": "comfy_files", "order": 1}
    gen.get_available_models = lambda probe_remote=True: {
        "qwen-image-21": {**base, "id": "comfy:qwen-image-21", "label": "Qwen-Image 2.1", "family": "qwen-image-21",
                          "user": False, "missing_files": ["qwen-image-21-clip:qwen3vl_8b_int8_convrot.safetensors"]},
        "user-qwen-image-21-ft": {**base, "id": "user:user-qwen-image-21-ft", "label": "FT", "family": "qwen-image-21",
                                  "user": True, "missing_files": ["user-qwen-image-21-ft:qwen-image-2.1-FT-fp8.safetensors",
                                                                  "qwen-image-21-vae:qwen_image_2.1_vae_bf16.safetensors"]},
        "zimage-turbo": {**base, "id": "Tongyi-MAI/Z-Image-Turbo", "label": "Z", "family": "zimage", "user": False},
    }
    monkeypatch.setattr(uim, "catalog_rows", lambda _g: [])
    monkeypatch.setattr(uim, "family_choices", lambda: [])
    payload = client.get("/api/batch-image/models").get_json()
    assert payload["success"], payload
    rows = {m["id"]: m for m in payload["data"]["models"]}
    assert rows["qwen-image-21"]["missing_files"] == ["qwen-image-21-clip:qwen3vl_8b_int8_convrot.safetensors"]
    assert rows["user-qwen-image-21-ft"]["missing_files"][0].startswith("user-qwen-image-21-ft:")
    assert rows["zimage-turbo"]["missing_files"] == []
    assert rows["qwen-image-21"]["prompt_style"] == "natural"


def test_generator_missing_files_reads_the_registry_plan(monkeypatch, tmp_path):
    from backend.services import video_model_registry as vmr
    from backend.services.offline_image_generator import OfflineImageGenerator as G

    monkeypatch.setattr(vmr, "comfyui_models_dir", lambda: tmp_path / "models")
    gen = G.__new__(G)
    gen.available_models = {"qwen-image-21": "comfy:qwen-image-21", "flux-dev": "comfy:flux-dev",
                            "zimage-turbo": "Tongyi-MAI/Z-Image-Turbo"}
    gen.comfy_only_models = {"flux-dev", "qwen-image-21", "comfyui"}
    gen.user_entries, gen.user_files, gen.family_overrides = {}, {}, {}
    assert gen.missing_files_for("qwen-image-21") == [
        "qwen-image-21:qwen_image_2.1_int8_convrot.safetensors",
        "qwen-image-21-clip:qwen3vl_8b_int8_convrot.safetensors",
        "qwen-image-21-vae:qwen_image_2.1_vae_bf16.safetensors",
    ]
    assert gen.missing_files_for("zimage-turbo") == []
    assert gen._is_model_downloaded("comfy:qwen-image-21") is False
    for eid in vmr.install_plan("qwen-image-21"):
        entry = vmr.VIDEO_MODEL_REGISTRY[eid]
        p = vmr.resolve_entry_dir(entry) / entry["check_files"][0]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert gen._is_model_downloaded("comfy:qwen-image-21") is True
    assert gen.missing_files_for("qwen-image-21") == []


def test_list_reports_prompt_style_per_model_and_for_auto(client, monkeypatch):
    # The page hides the content-type and anatomy controls for models that
    # read prompts as prose; the list says which ones those are.
    from backend.services import user_image_models as uim

    gen = api.get_batch_image_generator().image_generator
    base = {
        "downloaded": True, "availability": "installed", "selectable": True,
        "description": "", "recommended": False, "user": False, "kind": "diffusers",
    }
    gen.get_available_models = lambda probe_remote=True: {
        "zimage-turbo": {**base, "id": "Tongyi-MAI/Z-Image-Turbo", "label": "Z-Image", "family": "zimage", "order": 1},
        "sd-xl": {**base, "id": "stabilityai/sdxl", "label": "SDXL", "family": "sdxl", "order": 2},
        "user-krea-thing": {**base, "id": "user:krea", "label": "Krea", "family": None, "order": 3},
    }
    monkeypatch.setattr(uim, "catalog_rows", lambda _g: [])
    monkeypatch.setattr(uim, "family_choices", lambda: [])
    payload = client.get("/api/batch-image/models").get_json()
    assert payload["success"], payload
    styles = {m["id"]: m["prompt_style"] for m in payload["data"]["models"]}
    assert styles["zimage-turbo"] == "natural"
    assert styles["sd-xl"] == "tags"
    # No family on the row: the id decides (Krea 2 is a tags family until A/B'd).
    assert styles["user-krea-thing"] == "tags"
    assert payload["data"]["auto_prompt_style"] in ("natural", "tags")


def test_list_auto_prompt_style_follows_what_auto_renders_with(client, monkeypatch):
    # Not the Settings model's family: the model the router would pick for a
    # neutral prompt on this box (Krea 2 on a roomy card, SDXL when Z-Image is
    # not installed), or the Settings default when the generator can honour it.
    from backend.services import user_image_models as uim

    gen = api.get_batch_image_generator().image_generator
    gen.get_available_models = lambda probe_remote=True: {}
    monkeypatch.setattr(uim, "catalog_rows", lambda _g: [])
    monkeypatch.setattr(uim, "family_choices", lambda: [])

    gen._auto_select_model = lambda prompt="", style="realistic": "sd-xl"
    data = client.get("/api/batch-image/models").get_json()["data"]
    assert (data["auto_model"], data["auto_prompt_style"]) == ("sd-xl", "tags")

    gen.configured_default_for_auto = lambda: "zimage-turbo"
    data = client.get("/api/batch-image/models").get_json()["data"]
    assert (data["auto_model"], data["auto_prompt_style"]) == ("zimage-turbo", "natural")

    # Nothing on disk: the router answers None and the family default stands in.
    gen.configured_default_for_auto = lambda: None
    gen._auto_select_model = lambda prompt="", style="realistic": None
    data = client.get("/api/batch-image/models").get_json()["data"]
    assert data["auto_model"] is None
    assert data["auto_prompt_style"] in ("natural", "tags")


def test_dir_bytes_counts_dest_including_incomplete(tmp_path):
    dest = tmp_path / "unet"
    dest.mkdir()
    (dest / "file.safetensors").write_bytes(b"x" * 100)
    (dest / "file.safetensors.incomplete").write_bytes(b"y" * 50)
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "huge.incomplete").write_bytes(b"z" * 1_000_000)
    assert api._dir_bytes(dest) == 150
    assert api._dir_bytes(hub) == 1_000_000


def test_download_409_while_thread_still_marked_running(client, monkeypatch):
    monkeypatch.setattr(
        api, "_resolve_catalog_model",
        lambda _ref: ("zimage-turbo", "Tongyi-MAI/Z-Image-Turbo"),
    )
    gen = api.get_batch_image_generator().image_generator
    gen._is_model_downloaded = lambda _m: False
    with api.model_download_lock:
        api.model_download_status.update({
            "is_downloading": True,
            "current_model": "other/repo",
            "status": "failed",
            "updated_at": time.time() - 10_000,
            "epoch": 1,
        })
    try:
        payload = client.post(
            "/api/batch-image/models/download",
            json={"model_path": "zimage-turbo"},
        ).get_json()
        assert payload["success"] is False
        assert "Already downloading" in ((payload.get("error") or {}).get("message") or "")
    finally:
        with api.model_download_lock:
            api.model_download_status["is_downloading"] = False
