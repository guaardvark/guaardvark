"""Models whose ComfyUI nodes cannot draw sampler previews are queued without them.

CogVideoXWrapper's latent format lacks latent_rgb_factors_reshape, so ComfyUI's
Latent2RGB previewer raises on the first sampler step when previews are on. The
registry marks those entries live_preview=False and the prompt carries a
per-prompt preview_method override; every other model keeps the launch default.
"""

from backend.services import comfyui_video_generator as cvg
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
from backend.services.video_model_registry import (
    VIDEO_MODEL_REGISTRY, live_preview_for_model,
)


def test_cogvideox_entries_opt_out_of_live_preview():
    cog = [mid for mid, e in VIDEO_MODEL_REGISTRY.items() if e.get("type") == "cogvideox"]
    assert set(cog) >= {"cogvideox-5b", "cogvideox-5b-i2v"}
    assert not any(live_preview_for_model(mid) for mid in cog)


def test_other_models_and_unknown_ids_keep_previews():
    assert live_preview_for_model("wan22-5b") is True
    assert live_preview_for_model("no-such-model") is True


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"prompt_id": "p1", "number": 0, "node_errors": {}}


def _queued_payload(monkeypatch, **kw):
    sent = {}

    def post(url, json=None, timeout=None):
        sent["url"], sent["json"] = url, json
        return _Resp()

    monkeypatch.setattr(cvg.requests, "post", post)
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    gen.comfy_url = "http://comfyui.test"
    assert gen._queue_prompt({"1": {}}, client_id="c1", **kw) == "p1"
    return sent["json"]


def test_opted_out_prompt_carries_the_preview_override(monkeypatch):
    payload = _queued_payload(monkeypatch, live_preview=False)
    assert payload["extra_data"] == {"preview_method": "none"}
    assert payload["client_id"] == "c1"


def test_default_prompt_leaves_the_launch_preview_alone(monkeypatch):
    payload = _queued_payload(monkeypatch)
    assert "extra_data" not in payload
