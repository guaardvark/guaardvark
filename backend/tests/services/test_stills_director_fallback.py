"""A director pass that rewrites nothing leaves the stills enhancer on.

Seams: ``media_director.enhance_prompts`` (stills_policy imports it at call time)
and ``offline_image_generator.get_image_generator`` (stills_pipeline imports it
at call time). No renderer or chat model runs.
"""
from types import SimpleNamespace

import pytest

from backend.services import media_director as md
from backend.services import offline_image_generator as offline
from backend.services import stills_pipeline


@pytest.fixture
def renders(monkeypatch):
    calls = []

    def render(request):
        calls.append(request)
        return offline.ImageGenerationResult(success=True, image_path="image.png", metadata={})

    monkeypatch.setattr(offline, "get_image_generator",
                        lambda: SimpleNamespace(generate_image=render, style_configs={},
                                                missing_model_message=lambda model: None))
    return calls


def _director(monkeypatch, rewrite):
    monkeypatch.setattr(md, "enhance_prompts", lambda prompts, **k: rewrite(list(prompts)))


def _run(prompts, **kw):
    return stills_pipeline.run_stills_pipeline(
        prompts, model="zimage-turbo", director=True, verbatim=False,
        hold_gpu=False, keep_pipeline=True, **kw,
    )


def test_director_fallback_keeps_the_enhancer_on(monkeypatch, renders):
    _director(monkeypatch, lambda prompts: prompts)

    results = _run(["a red fox in snow"])

    assert renders[0].prompt == "a red fox in snow"
    assert renders[0].auto_enhance is True
    assert results[0].enhance_mode == "offline"


def test_a_rewritten_prompt_turns_the_enhancer_off(monkeypatch, renders):
    _director(monkeypatch, lambda prompts: [f"DIR {p}" for p in prompts])

    results = _run(["a red fox in snow"])

    assert renders[0].prompt == "DIR a red fox in snow"
    assert renders[0].auto_enhance is False
    assert results[0].enhance_mode == "director"


def test_only_rewritten_prompts_lose_the_enhancer(monkeypatch, renders):
    _director(monkeypatch, lambda prompts: [f"DIR {prompts[0]}"] + prompts[1:])

    results = _run(["a red fox in snow", "a quiet harbour"])

    assert [(r.prompt, r.auto_enhance) for r in renders] == [
        ("DIR a red fox in snow", False),
        ("a quiet harbour", True),
    ]
    assert [r.enhance_mode for r in results] == ["director", "offline"]


def test_a_caller_who_turned_the_enhancer_off_keeps_it_off(monkeypatch, renders):
    _director(monkeypatch, lambda prompts: prompts)

    results = _run(["a red fox in snow"], auto_enhance=False)

    assert renders[0].auto_enhance is False
    assert results[0].enhance_mode == "none"


def test_director_negatives_are_kept_on_fallback(monkeypatch, renders):
    _director(monkeypatch, lambda prompts: prompts)

    _run(["a red fox in snow"])

    assert "low quality" in renders[0].negative_prompt
