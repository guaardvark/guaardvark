"""Sampling provenance reaches results without running a renderer."""
from types import SimpleNamespace

import pytest

from backend.services import offline_image_generator as offline
from backend.services import stills_pipeline
from backend.services.stills_defaults import _FAMILY_DEFAULTS


@pytest.mark.parametrize("model, family, steps, default", [
    ("zimage-turbo", "zimage", 0, 9),
    ("zimage-turbo", "zimage", 1, 2),     # below the declared floor
    ("zimage-turbo", "zimage", 2, 2),     # at the floor: no longer reset to 9
    ("zimage-turbo", "zimage", 40, 9),
    ("krea2-turbo", "krea2", 2, 8),
    ("krea2-turbo", "krea2", 40, 8),
    ("krea2-raw", "krea2", 4, 52),
    ("krea2-raw", "krea2", 90, 52),
])
@pytest.mark.parametrize("explicit", [False, True])
def test_soft_clamp_respects_typed_steps(model, family, steps, default, explicit):
    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)
    gen.available_models = {}
    request = offline.ImageGenerationRequest(
        prompt="a key", model=model, num_inference_steps=steps,
        steps_explicit=explicit, guidance_scale=99,
    )
    gen._soft_clamp_family_sampling(request, family)
    assert request.num_inference_steps == (steps if explicit else default)
    assert request.guidance_scale != 99


@pytest.mark.parametrize("explicit, requested, actual", [(False, 4, 8), (True, 2, 2), (False, 40, 9)])
def test_pipeline_reports_renderer_steps(monkeypatch, explicit, requested, actual):
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    calls = []
    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)

    def render(request):
        calls.append(request)
        gen._soft_clamp_family_sampling(request, "zimage")
        return offline.ImageGenerationResult(
            success=True, image_path="image.png", metadata={"steps": request.num_inference_steps},
        )

    monkeypatch.setattr(offline, "get_image_generator", lambda: SimpleNamespace(
        generate_image=render, missing_model_message=lambda model: None))
    result = stills_pipeline.run_stills_pipeline(
        ["a brass key"], model="zimage-turbo", steps=requested, steps_explicit=explicit,
        verbatim=True, hold_gpu=False, keep_pipeline=True,
    )[0]
    assert result.success
    assert result.steps == result.metadata["steps"] == actual
    assert result.metadata["steps_requested"] == requested
    assert calls[0].steps_explicit is explicit
    assert bool(result.metadata["steps_notice"]) is (not explicit and requested == 4)


def test_batch_file_reports_actual_steps_and_original_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(offline.torch.cuda, "is_available", lambda: False)
    from backend.services.batch_image_generator import BatchImageGenerator, BatchPrompt
    image = tmp_path / "source.png"
    image.write_bytes(b"mock renderer output")
    (tmp_path / "images").mkdir()
    notice = "Z-Image Turbo needs at least 8 steps; raised 4 to 8."
    prompt = BatchPrompt(
        id="one", prompt="a key", model="zimage-turbo", steps=8,
        metadata={"steps_requested": 4, "steps_notice": notice, "steps_explicit": False},
    )
    calls = []

    def render(*args, **kwargs):
        calls.append(kwargs)
        return [stills_pipeline.StillResult(
            success=True, image_path=str(image), steps=9, metadata={"steps": 9},
        )]

    monkeypatch.setattr(stills_pipeline, "run_stills_pipeline", render)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.progress_system = None
    status = SimpleNamespace(generate_thumbnails=False)
    result = gen._generate_single_image("batch", prompt, tmp_path, status)
    assert result.success
    assert result.metadata["steps"] == 9
    assert result.metadata["steps_requested"] == 4
    assert result.metadata["steps_notice"] == notice
    assert calls[0]["steps_explicit"] is False


@pytest.mark.parametrize("explicit, expected", [(False, 8), (True, 4)])
def test_character_still_uses_resolved_steps(monkeypatch, tmp_path, explicit, expected):
    from backend.services.character_still_pipeline import render_character_still
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    dest = tmp_path / "character.png"
    calls = []

    def render(request):
        calls.append(request)
        dest.write_bytes(b"mock image")
        return offline.ImageGenerationResult(
            success=True, image_path=str(dest), metadata={"steps": request.num_inference_steps},
        )

    monkeypatch.setattr(offline, "get_image_generator", lambda: SimpleNamespace(generate_image=render))
    result = render_character_still(
        "a key", steps=4, steps_explicit=explicit, output_path=str(dest),
        apply_subject_loras=False,
    )
    assert result.success
    assert calls[0].num_inference_steps == result.steps == result.metadata["steps"] == expected
    assert calls[0].steps_explicit is explicit
    assert result.metadata["steps_requested"] == 4
    assert bool(result.metadata["steps_notice"]) is (not explicit)


@pytest.mark.parametrize("explicit, expected", [(False, 8), (True, 4)])
def test_api_parses_step_intent(monkeypatch, explicit, expected):
    from backend.api import batch_image_generation_api as api
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    monkeypatch.setattr(api, "settings_validator_available", False)
    params, _ = api._parse_generation_params({
        "model": "zimage-turbo", "steps": 4, "steps_explicit": explicit,
    })
    assert params["steps"] == expected
    assert params["steps_requested"] == 4
    assert params["steps_explicit"] is explicit
    assert bool(params["steps_notice"]) is (not explicit)


@pytest.mark.parametrize("explicit, expected", [(False, 8), (True, 4)])
def test_celery_carries_step_intent(monkeypatch, explicit, expected):
    from backend.services import batch_image_generator as batch
    from backend.services.task_handlers.batch_image_handler import BatchImageHandler
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    calls = []

    def start(request):
        calls.append(request)
        return request.batch_id

    monkeypatch.setattr(batch, "get_batch_image_generator", lambda: SimpleNamespace(
        start_batch_generation=start, get_batch_status=lambda _: None,
    ))
    BatchImageHandler().execute(
        SimpleNamespace(id=1),
        {"prompts": ["a key", {"prompt": "a lock"}], "model": "zimage-turbo",
         "steps": 4, "steps_explicit": explicit},
        lambda *args: None,
    )
    assert len(calls) == 1
    for prompt in calls[0].prompts:
        assert prompt.steps == expected
        assert prompt.metadata["steps_requested"] == 4
        assert prompt.metadata["steps_explicit"] is explicit
        assert bool(prompt.metadata["steps_notice"]) is (not explicit)



def test_celery_keeps_resolved_form_provenance(monkeypatch):
    from backend.services import batch_image_generator as batch
    from backend.services.task_handlers.batch_image_handler import BatchImageHandler
    calls = []
    monkeypatch.setattr(batch, "get_batch_image_generator", lambda: SimpleNamespace(
        start_batch_generation=lambda request: calls.append(request) or request.batch_id,
        get_batch_status=lambda _: None,
    ))
    notice = "Z-Image Turbo needs at least 8 steps; raised 4 to 8."
    BatchImageHandler().execute(
        SimpleNamespace(id=1),
        {"prompts": ["a key", {"prompt": "a lock"}], "model": "zimage-turbo",
         "steps": 8, "steps_requested": 4, "steps_notice": notice},
        lambda *args: None,
    )
    assert len(calls) == 1
    for prompt in calls[0].prompts:
        assert prompt.steps == 8
        assert prompt.metadata["steps_requested"] == 4
        assert prompt.metadata["steps_notice"] == notice


@pytest.mark.parametrize("model, steps, expected", [
    ("flux-dev", 2, 4), ("flux-schnell", 28, 8), ("sdxl", 25, 25),
])
@pytest.mark.parametrize("explicit", [False, True])
def test_comfy_reports_submitted_steps(monkeypatch, tmp_path, model, steps, expected, explicit):
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    gen = ComfyUIImageGenerator(model=model)
    monkeypatch.setattr(gen, "_available", lambda: True)
    monkeypatch.setattr(gen, "_preflight_loras", lambda _: None)
    monkeypatch.setattr(gen, "_queue", lambda _: "mock_prompt")
    monkeypatch.setattr(gen, "_wait", lambda _, **kw: {})
    monkeypatch.setattr(gen, "_fetch_first_image", lambda outputs, path: path)
    gen.generate_image(
        prompt="a key", output_path=str(tmp_path / "key.png"), steps=steps, steps_explicit=explicit,
    )
    assert gen.last_steps == (steps if explicit else expected)


def test_flux_pipeline_reports_comfy_sampler_count(monkeypatch):
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    calls = []

    def render(self, **kwargs):
        calls.append(kwargs)
        self.last_steps = 8
        return "mock.png"

    monkeypatch.setattr(ComfyUIImageGenerator, "generate_image", render)
    result = stills_pipeline.run_stills_pipeline(
        ["a key"], model="flux-schnell", steps=28, verbatim=True, hold_gpu=False,
    )[0]
    assert result.success
    assert result.steps == result.metadata["steps"] == 8
    assert result.metadata["steps_requested"] == 28
    assert calls[0]["steps_explicit"] is False
