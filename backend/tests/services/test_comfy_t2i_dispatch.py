"""One predicate routes ComfyUI stills: shipped and user FLUX and Qwen-Image 2.1
ids reach the ComfyUI branch in batch and chat; the FLUX routes behave as before.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.services import stills_pipeline as sp
from backend.services.batch_image_generator import BatchImageGenerator, BatchPrompt

USER_ENTRIES = {
    "user-qwen-image-21-uc-fp8": {
        "family": "qwen-image-21", "role": "generation", "engine": "comfy", "kind": "comfy_files",
        "files": [{"src": "qwen-image-2.1-UC-fp8.safetensors", "dst": "qwen-image-2.1-UC-fp8.safetensors"}],
    },
    "user-flux-q8": {
        "family": "flux", "role": "generation", "engine": "comfy", "kind": "comfy_files",
        "files": [{"src": "flux1-dev-Q8_0.gguf", "dst": "flux1-dev-Q8_0.gguf"}],
    },
    "user-flux-fp8": {
        "family": "flux", "role": "generation", "engine": "comfy", "kind": "comfy_files",
        "files": [{"src": "flux1-dev-fp8.safetensors", "dst": "flux1-dev-fp8.safetensors"}],
    },
}


class _RecordingGen:
    """Stands in for ComfyUIImageGenerator; records how it was built and called."""
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.call = None
        _RecordingGen.instances.append(self)

    def generate_image(self, **kwargs):
        self.call = kwargs
        p = Path(kwargs["output_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return str(p)


@pytest.fixture
def recording(monkeypatch):
    _RecordingGen.instances = []
    monkeypatch.setattr("backend.services.comfyui_image_generator.ComfyUIImageGenerator", _RecordingGen)
    return _RecordingGen


def _batch_gen():
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.image_generator = SimpleNamespace(user_entries=dict(USER_ENTRIES))
    return gen


# ── the predicate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,family", [
    ("flux-dev", "flux"), ("flux", "flux"), ("user-flux-fp8", "flux"),
    ("qwen-image-21", "qwen-image-21"), ("user-qwen-image-21-uc-fp8", "qwen-image-21"),
    ("user-qwen-image-edit-x", None), ("zimage-turbo", None), ("sd-xl", None), ("auto", None),
])
def test_batch_predicate_and_comfy_route(model, family):
    gen = _batch_gen()
    assert gen._comfy_t2i_family(model) == family
    assert gen._should_use_comfy_stills(BatchPrompt(id="p", prompt="x", model=model)) is (family is not None)


def test_comfy_prices_come_from_the_family_rows():
    gen = _batch_gen()
    assert gen._comfy_t2i_price("flux") == (12000, 16.0)
    from backend.services.video_model_registry import vram_mb_for_model
    assert gen._comfy_t2i_price("qwen-image-21") == (vram_mb_for_model("qwen-image-21"), 16.0)
    assert gen._comfy_t2i_price("qwen-image-21")[0] == 14000


# ── batch dispatch ────────────────────────────────────────────────────────────

def test_shipped_flux_batch_renders_on_the_dev_graph_as_before(recording, tmp_path):
    gen = _batch_gen()
    prompt = BatchPrompt(id="p1", prompt="a lighthouse", model="flux-dev", steps=9, guidance=0.0)
    prompt.steps = None
    prompt.guidance = None
    result = gen._generate_with_comfy_flux(prompt, "batch-1")
    assert result.success
    inst = recording.instances[-1]
    assert inst.kwargs["model"] == "flux-dev"
    assert inst.kwargs["flux_dev_unet"] is None and inst.kwargs["flux_unet"] is None
    assert inst.kwargs["qwen21_unet"] is None
    assert inst.call["model"] == "flux-dev"
    assert (inst.call["steps"], inst.call["cfg"]) == (28, 3.5)


def test_user_flux_gguf_batch_renders_on_schnell_with_its_file(recording):
    gen = _batch_gen()
    prompt = BatchPrompt(id="p1", prompt="a lighthouse", model="user-flux-q8")
    gen._generate_with_comfy_flux(prompt, "batch-1")
    inst = recording.instances[-1]
    assert inst.kwargs["model"] == "flux-schnell"
    assert inst.kwargs["flux_unet"] == "flux1-dev-Q8_0.gguf" and inst.kwargs["flux_dev_unet"] is None


def test_user_flux_safetensors_batch_renders_on_dev_with_its_file(recording):
    gen = _batch_gen()
    gen._generate_with_comfy_flux(BatchPrompt(id="p1", prompt="a lighthouse", model="user-flux-fp8"), "b")
    inst = recording.instances[-1]
    assert inst.kwargs["model"] == "flux-dev"
    assert inst.kwargs["flux_dev_unet"] == "flux1-dev-fp8.safetensors"


def test_shipped_qwen_batch_renders_on_its_graph_with_template_defaults(recording):
    gen = _batch_gen()
    prompt = BatchPrompt(id="p1", prompt="a lighthouse", model="qwen-image-21", width=1024, height=1024)
    prompt.steps = None
    prompt.guidance = None
    result = gen._generate_with_comfy_flux(prompt, "batch-1")
    assert result.success and result.model_used == "qwen-image-21"
    inst = recording.instances[-1]
    assert inst.kwargs["model"] == "qwen-image-21" and inst.kwargs["qwen21_unet"] is None
    assert inst.call["model"] == "qwen-image-21"
    assert (inst.call["steps"], inst.call["cfg"]) == (25, 1.0)
    assert (inst.call["width"], inst.call["height"]) == (1024, 1024)


def test_user_qwen_batch_renders_with_its_unet_and_keeps_typed_values(recording):
    gen = _batch_gen()
    prompt = BatchPrompt(id="p1", prompt="a lighthouse", model="user-qwen-image-21-uc-fp8",
                         steps=12, guidance=2.0, width=1536, height=1024)
    prompt.metadata["steps_explicit"] = True
    gen._generate_with_comfy_flux(prompt, "batch-1")
    inst = recording.instances[-1]
    assert inst.kwargs["model"] == "qwen-image-21"
    assert inst.kwargs["qwen21_unet"] == "qwen-image-2.1-UC-fp8.safetensors"
    assert inst.kwargs["flux_unet"] is None and inst.kwargs["flux_dev_unet"] is None
    assert (inst.call["steps"], inst.call["cfg"]) == (12, 2.0)
    assert inst.call["steps_explicit"] is True
    assert (inst.call["width"], inst.call["height"]) == (1536, 1024)


def test_qwen_batch_canvas_is_held_to_its_family_limits(recording):
    gen = _batch_gen()
    prompt = BatchPrompt(id="p1", prompt="a lighthouse", model="qwen-image-21", width=3000, height=3000)
    gen._generate_with_comfy_flux(prompt, "batch-1")
    inst = recording.instances[-1]
    assert inst.call["width"] <= 2048 and inst.call["height"] <= 2048
    assert inst.call["width"] % 32 == 0 and inst.call["height"] % 32 == 0


def test_sdxl_lora_batch_route_is_unchanged(recording):
    gen = _batch_gen()
    prompt = BatchPrompt(id="p1", prompt="a lighthouse", model="sd-xl", adapter_loras=["style.safetensors"])
    gen._generate_with_comfy_flux(prompt, "batch-1")
    inst = recording.instances[-1]
    assert inst.kwargs["model"] == "sdxl" and inst.call["model"] == "sdxl"


# ── chat dispatch ─────────────────────────────────────────────────────────────

def _one(monkeypatch, model, hold_gpu=False):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return sp.StillResult(success=True, steps=kwargs["steps"])

    monkeypatch.setattr(sp, "_generate_comfy_flux", fake)
    sp._generate_one(
        prompt="p", negative="", model=model, width=1024, height=1024, steps=25, steps_explicit=False,
        guidance=1.0, style="realistic", seed=None, source="chat", enhance_mode="none", auto_enhance=False,
        loras=None, lora_scale=1.0, keep_pipeline=False, output="none", output_dir=None,
        content_preset=None, enhance_anatomy=False, enhance_faces=False, enhance_hands=False,
        restore_faces=False, face_restoration_weight=0.5, remove_background=False, hold_gpu=hold_gpu,
    )
    return seen


@pytest.mark.parametrize("model", ["flux-dev", "flux", "user-flux-fp8", "qwen-image-21", "user-qwen-image-21-uc-fp8"])
def test_chat_routes_every_comfy_stills_id_to_the_comfy_branch(monkeypatch, model):
    seen = _one(monkeypatch, model, hold_gpu=True)
    assert seen["model"] == model and seen["hold_gpu"] is True


def test_chat_leaves_an_edit_entry_and_offline_models_alone(monkeypatch):
    monkeypatch.setattr(sp, "_generate_comfy_flux", lambda **kw: pytest.fail("not a ComfyUI stills model"))
    monkeypatch.setattr(
        "backend.services.offline_image_generator.get_image_generator",
        lambda: (_ for _ in ()).throw(RuntimeError("offline path reached")),
    )
    for model in ("user-qwen-image-edit-x", "zimage-turbo"):
        result = sp._generate_one(
            prompt="p", negative="", model=model, width=1024, height=1024, steps=9, steps_explicit=False,
            guidance=0.0, style="realistic", seed=None, source="chat", enhance_mode="none", auto_enhance=False,
            loras=None, lora_scale=1.0, keep_pipeline=False, output="none", output_dir=None,
            content_preset=None, enhance_anatomy=False, enhance_faces=False, enhance_hands=False,
            restore_faces=False, face_restoration_weight=0.5, remove_background=False, hold_gpu=False,
        )
        assert result.success is False and "offline path reached" in (result.error or "")


def test_chat_user_qwen_resolves_its_unet_and_is_admitted(recording, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.services.offline_image_generator.get_image_generator",
        lambda: SimpleNamespace(user_entries=dict(USER_ENTRIES)),
    )
    sp._generate_comfy_flux(
        prompt="p", negative="", model="user-qwen-image-21-uc-fp8", width=1024, height=1024, steps=25,
        guidance=1.0, seed=1, enhance_mode="none", output="path", output_dir=str(tmp_path), hold_gpu=True,
    )
    inst = recording.instances[-1]
    assert inst.kwargs == {"qwen21_unet": "qwen-image-2.1-UC-fp8.safetensors"}
    assert inst.call["model"] == "qwen-image-21"
    assert inst.call["admit_vram_mb"] == 14000


def test_chat_shipped_qwen_is_admitted_and_flux_is_not(recording, tmp_path):
    sp._generate_comfy_flux(
        prompt="p", negative="", model="qwen-image-21", width=1024, height=1024, steps=25,
        guidance=1.0, seed=1, enhance_mode="none", output="path", output_dir=str(tmp_path), hold_gpu=True,
    )
    qwen = recording.instances[-1]
    assert qwen.kwargs == {} and qwen.call["model"] == "qwen-image-21" and qwen.call["admit_vram_mb"] == 14000
    sp._generate_comfy_flux(
        prompt="p", negative="", model="flux-dev", width=1024, height=1024, steps=28,
        guidance=3.5, seed=1, enhance_mode="none", output="path", output_dir=str(tmp_path), hold_gpu=True,
    )
    flux = recording.instances[-1]
    assert flux.kwargs == {} and flux.call["model"] == "flux-dev" and flux.call["admit_vram_mb"] is None


def test_chat_user_flux_gguf_resolves_to_schnell_with_its_file(recording, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.services.offline_image_generator.get_image_generator",
        lambda: SimpleNamespace(user_entries=dict(USER_ENTRIES)),
    )
    sp._generate_comfy_flux(
        prompt="p", negative="", model="user-flux-q8", width=1024, height=1024, steps=8,
        guidance=1.0, seed=1, enhance_mode="none", output="path", output_dir=str(tmp_path),
    )
    inst = recording.instances[-1]
    assert inst.kwargs == {"flux_unet": "flux1-dev-Q8_0.gguf"} and inst.call["model"] == "flux-schnell"


def test_chat_defaults_for_qwen_come_from_its_family_row():
    from backend.services.stills_defaults import resolve_stills_defaults
    d = resolve_stills_defaults("qwen-image-21")
    assert (d["family"], d["steps"], d["guidance"], d["width"], d["height"]) == ("qwen-image-21", 25, 1.0, 1024, 1024)
    assert d["steps_floor"] is None
    d = resolve_stills_defaults("user-qwen-image-21-uc-fp8", steps=40, guidance=2.0)
    assert (d["steps"], d["guidance"]) == (40, 2.0)
