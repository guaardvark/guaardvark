"""GET /api/batch-video/models carries the capability contract and the tier
defaults resolved for this card, so the page and the tools read data."""

from __future__ import annotations

import pytest

try:
    from flask import Flask
    from backend.api import batch_video_generation_api as api
    from backend.api.batch_video_generation_api import batch_video_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(batch_video_bp)
    # No disk, no GPU: every model reads as not downloaded on a 16 GB card.
    monkeypatch.setattr(api, "_check_model_downloaded", lambda _m: False)
    monkeypatch.setattr(api, "_missing_check_files", lambda _m: [])
    monkeypatch.setattr(api, "_detected_total_vram_mb", lambda: 16376)
    monkeypatch.setattr(api, "resolve_active_video_model", lambda role, explicit=None, surface=None, comfyui_down_ok=False: (None, "none"))
    return app.test_client()


def _stub_video_inspect(monkeypatch, *, hf_repo, files, unwired=None):
    listed = list(files)

    def fake(_url):
        return {
            "hf_repo": hf_repo,
            "revision": "main",
            "src": listed[0]["src"] if listed else None,
            "files": listed,
            "has_model_index": False,
            "gated": False,
            "truncated": False,
            "warnings": [],
            "matches": [],
            "unwired": unwired,
        }

    monkeypatch.setattr(api, "preview_hf_url", fake)


def _rows(client):
    payload = client.get("/api/batch-video/models").get_json()
    assert payload["success"]
    return {m["id"]: m for m in payload["data"]["models"]}


def test_generation_rows_carry_capabilities_and_tier_defaults(client):
    rows = _rows(client)
    h3 = rows["minimax-h3-int8"]
    caps = h3["capabilities"]
    assert caps["modes"] == ["t2v", "i2v", "l2v", "flf2v"]
    assert caps["audio_out"] is True and caps["cfg"] is False
    assert caps["min_steps"] == 20 and "turbo-8" in caps["speed_profiles"]
    assert caps["aspect_ratios"][0] == "21:9"
    assert h3["tier_defaults"] == {
        "tier": "16", "width": 864, "height": 480, "speed_profile": "turbo-8", "frames": 124,
    }
    assert h3["license"]["attribution"] == "MiniMax H3"
    assert h3["license"]["form_url"].startswith("https://")


def test_older_families_get_defaults_and_companions_get_nothing(client):
    rows = _rows(client)
    wan = rows["wan22-5b"]
    assert wan["capabilities"]["modes"] == ["t2v", "i2v"]
    assert wan["capabilities"]["cfg"] is True and wan["license"] is None
    assert wan["tier_defaults"] == {}
    vae = rows["minimax-h3-vae"]
    assert vae["capabilities"] == {} and vae["tier_defaults"] == {}


def test_bigger_builds_have_no_defaults_for_a_smaller_card(client):
    rows = _rows(client)
    assert rows["minimax-h3-int8-full"]["tier_defaults"] == {}
    assert rows["minimax-h3-bf16"]["tier_defaults"] == {}


def _rows_on_card(client, monkeypatch, total_mb):
    from backend.services import comfyui_version
    monkeypatch.setattr(api, "_detected_total_vram_mb", lambda: total_mb)
    monkeypatch.setattr(comfyui_version, "offered", lambda *_a, **_k: True)
    payload = client.get("/api/batch-video/models").get_json()
    assert payload["success"]
    return payload["data"], {m["id"]: m for m in payload["data"]["models"]}


def test_rows_carry_the_preflight_floor_beside_the_booking(client, monkeypatch):
    data, rows = _rows_on_card(client, monkeypatch, 12282)
    assert data["total_vram_mb"] == 12282
    # Undeclared floor falls back to the family's; declared ones win.
    assert (rows["wan22-14b"]["vram_mb"], rows["wan22-14b"]["min_vram_gb"]) == (11000, 16)
    assert rows["wan22-14b-i2v"]["min_vram_gb"] == 8
    assert rows["wan22-5b"]["min_vram_gb"] == 6
    # A row outside the capability contract keeps its own declared floor.
    assert rows["minimax-h3-t2i"]["min_vram_gb"] == 16
    assert rows["wan-vae"]["min_vram_gb"] == 0 and rows["flux-dev"]["min_vram_gb"] == 0


def test_a_12gb_card_is_marked_under_the_16gb_floor_only(client, monkeypatch):
    _, rows = _rows_on_card(client, monkeypatch, 12282)
    for model_id in ("wan22-14b", "hunyuan-t2v", "hunyuan-i2v", "ltx25-distilled-int8", "minimax-h3-t2i"):
        assert rows[model_id]["below_card_floor"] is True, model_id
    # 12282 MB is within the half-GB grace of a 12 GB floor.
    for model_id in ("wan22-5b", "wan22-14b-i2v", "minimax-music3-int8", "flux-dev", "wan-vae"):
        assert rows[model_id]["below_card_floor"] is False, model_id


def test_an_unread_card_marks_nothing(client, monkeypatch):
    data, rows = _rows_on_card(client, monkeypatch, 0)
    assert data["total_vram_mb"] == 0
    assert not any(r["below_card_floor"] for r in rows.values())


def test_loras_name_the_models_they_apply_to(client):
    rows = _rows(client)
    lora = rows["minimax-h3-fl2v-turbo-8step"]
    assert lora["type"] == "lora"
    assert "minimax-h3-int8" in lora["applies_to"]
    assert rows["minimax-h3-int8"]["applies_to"] == []


def test_from_hf_rejects_non_hf(client):
    payload = client.post("/api/batch-video/models/from-hf", json={"url": "https://civitai.com/x"}).get_json()
    assert payload["success"] is False
    assert "Hugging Face" in ((payload.get("error") or {}).get("message") or "")


def test_user_add_and_delete(client, tmp_path, monkeypatch):
    from backend.services import user_video_models as uvm
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_video_models.json")
    src = "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    _stub_video_inspect(
        monkeypatch,
        hf_repo="Comfy-Org/MiniMax-H3",
        files=[{"src": src, "size": 1000}],
    )
    body = {
        "role": "lora",
        "like": "minimax-h3-int8",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "files": [{"src": src}],
        "name": "API turbo",
        "install": False,
    }
    payload = client.post("/api/batch-video/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert mid.startswith("user-")
        rows = _rows(client)
        assert rows[mid]["user"] is True
        assert rows[mid]["type"] == "lora"
        forbidden = client.delete("/api/batch-video/models/user/minimax-h3-int8").get_json()
        assert forbidden["success"] is False
        gone = client.delete(f"/api/batch-video/models/user/{mid}").get_json()
        assert gone["success"] is True
        assert mid not in _rows(client)
    finally:
        try:
            uvm.remove_user_model(mid, delete_files=False)
        except Exception:
            pass


def test_user_add_install_true_does_not_500(client, tmp_path, monkeypatch):
    from backend.services import user_video_models as uvm
    from backend.utils.response_utils import success_response
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_video_models.json")
    monkeypatch.setattr(api, "start_video_model_download", lambda _mid: success_response({"message": "already installed"}))
    src = "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    _stub_video_inspect(
        monkeypatch,
        hf_repo="Comfy-Org/MiniMax-H3",
        files=[{"src": src, "size": 1000}],
    )
    body = {
        "role": "lora",
        "like": "minimax-h3-int8",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "files": [{"src": src}],
        "name": "Install path",
        "install": True,
    }
    payload = client.post("/api/batch-video/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert payload["data"]["download"]["message"] == "already installed"
    finally:
        try:
            uvm.remove_user_model(mid, delete_files=False)
        except Exception:
            pass


def test_user_add_install_409_still_succeeds(client, tmp_path, monkeypatch):
    from backend.services import user_video_models as uvm
    from backend.utils.response_utils import error_response
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_video_models.json")
    monkeypatch.setattr(
        api, "start_video_model_download",
        lambda _mid: error_response("Already downloading model: other", 409),
    )
    src = "loras/unique_turbo.safetensors"
    _stub_video_inspect(
        monkeypatch,
        hf_repo="Comfy-Org/MiniMax-H3",
        files=[{"src": src, "size": 1000}],
    )
    body = {
        "role": "lora",
        "like": "minimax-h3-int8",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "files": [{"src": src}],
        "name": "While busy",
        "install": True,
    }
    payload = client.post("/api/batch-video/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert payload["data"]["download"]["status"] == 409
        assert "Already downloading" in (payload["data"]["download"]["error"] or "")
    finally:
        try:
            uvm.remove_user_model(mid, delete_files=False)
        except Exception:
            pass


def test_user_add_without_url_refuses_unwired(client, tmp_path, monkeypatch):
    from backend.services import user_video_models as uvm
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_video_models.json")
    _stub_video_inspect(
        monkeypatch,
        hf_repo="HiDream-ai/HiDream-I1-Full",
        files=[{"src": "model.safetensors", "size": 100}],
        unwired={"family": "hidream", "reason": "HiDream is not wired yet."},
    )
    body = {
        "role": "generation",
        "like": "minimax-h3-int8",
        "hf_repo": "HiDream-ai/HiDream-I1-Full",
        "files": [{"src": "model.safetensors"}],
        "name": "HiDream",
        "install": False,
    }
    payload = client.post("/api/batch-video/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "not wired" in ((payload.get("error") or {}).get("message") or "").lower()


def test_install_of_a_model_already_on_disk_says_so(client, monkeypatch):
    # The model dialogs skip progress tracking on this flag.
    monkeypatch.setattr(api, "_check_model_downloaded", lambda _m: True)
    monkeypatch.setattr("backend.services.comfyui_version.refusal", lambda *_a, **_k: None)
    model_id = next(iter(api.VIDEO_MODEL_REGISTRY))
    res = client.post("/api/batch-video/models/download", json={"model_id": model_id})
    assert res.status_code == 200
    assert res.get_json()["data"]["already_installed"] is True

