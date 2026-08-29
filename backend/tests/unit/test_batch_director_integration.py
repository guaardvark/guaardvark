"""Tests for the cinematic quality wiring in the batch video generator.

Covers the pure/near-pure pieces without spinning up the full generator or a GPU:
the T2V->I2V model mapping and the director/storyboard passes (prompt rewrite +
double-enhance off). Both are called unbound with a dummy ``self`` so no queue
worker / ComfyUI is needed.

The passes go through director_service.plan(), which calls media_director —
that is the seam these tests patch. (They used to patch video_director, which
the batch path stopped using when the Director layer was unified on
2026-06-23; the mocks never fired and the real planner ran.)"""

from types import SimpleNamespace

from backend.services import media_director as md
from backend.services.batch_video_generator import (
    BatchVideoGenerator,
    BatchVideoRequest,
    BatchVideoItem,
)


def test_to_i2v_model_mapping():
    assert BatchVideoGenerator._to_i2v_model("wan22-14b") == "wan22-14b-i2v"
    assert BatchVideoGenerator._to_i2v_model("cogvideox-5b") == "cogvideox-5b-i2v"
    assert BatchVideoGenerator._to_i2v_model("wan22-14b-i2v") == "wan22-14b-i2v"  # already I2V
    assert BatchVideoGenerator._to_i2v_model(None) == "wan22-14b-i2v"            # safe default


def _req(items, **kw):
    return BatchVideoRequest(batch_id="b1", items=items, output_dir="/tmp/x", **kw)


def test_apply_director_rewrites_prompts_and_disables_double_enhance(monkeypatch):
    monkeypatch.setattr(md, "enhance_prompts", lambda prompts, **k: [f"DIR:{p}" for p in prompts])

    items = [BatchVideoItem(id="1", prompt="a"), BatchVideoItem(id="2", prompt="b")]
    req = _req(items, director_mode=True, enhance_prompt=True, prompt_style="cinematic")

    BatchVideoGenerator._apply_director(SimpleNamespace(), req)

    assert [it.prompt for it in items] == ["DIR:a", "DIR:b"]
    # director output is already complete -> downstream light enhancer is turned off
    assert req.enhance_prompt is False


def test_apply_director_skips_image_only_items(monkeypatch):
    seen = {}

    def fake_enhance(prompts, **k):
        seen["prompts"] = list(prompts)
        return [f"DIR:{p}" for p in prompts]

    monkeypatch.setattr(md, "enhance_prompts", fake_enhance)

    items = [
        BatchVideoItem(id="1", prompt="text one"),
        BatchVideoItem(id="2", prompt=None, image_path="/img/a.png"),  # image-only, no prompt
    ]
    req = _req(items, director_mode=True)
    BatchVideoGenerator._apply_director(SimpleNamespace(), req)

    # only the text item was sent to the director; image-only item left untouched
    assert seen["prompts"] == ["text one"]
    assert items[0].prompt == "DIR:text one"
    assert items[1].prompt is None


def test_apply_director_keeps_enhancer_when_director_returns_originals(monkeypatch):
    # media_director's failure contract is "hand the originals back", not raise.
    monkeypatch.setattr(md, "enhance_prompts", lambda prompts, **k: list(prompts))
    items = [BatchVideoItem(id="1", prompt="a")]
    req = _req(items, director_mode=True, enhance_prompt=True)

    BatchVideoGenerator._apply_director(SimpleNamespace(), req)

    assert items[0].prompt == "a"
    assert req.enhance_prompt is True  # nothing was directed, so nothing to double-enhance


def test_apply_storyboard_expands_concept_into_items(monkeypatch):
    seen = {}

    def fake_storyboard(concept, n, **k):
        seen["concept"] = concept
        seen["n"] = n
        return {"treatment": "t", "prompts": [f"shot{i}" for i in range(n)]}

    monkeypatch.setattr(md, "storyboard_from_concept", fake_storyboard)
    # 3 placeholder items (the API creates N=shots placeholders carrying the concept)
    items = [BatchVideoItem(id=str(i), prompt="a lighthouse at dawn") for i in range(3)]
    req = _req(items, storyboard_concept="a lighthouse at dawn", enhance_prompt=True)

    BatchVideoGenerator._apply_storyboard(SimpleNamespace(), req)

    assert seen == {"concept": "a lighthouse at dawn", "n": 3}
    assert [it.prompt for it in items] == ["shot0", "shot1", "shot2"]
    assert req.enhance_prompt is False


def test_apply_storyboard_keeps_enhancer_on_concept_fallback(monkeypatch):
    # storyboard_from_concept's failure contract is {"prompts": [concept] * n}.
    monkeypatch.setattr(md, "storyboard_from_concept",
                        lambda concept, n, **k: {"treatment": None, "prompts": [concept] * n})
    items = [BatchVideoItem(id=str(i), prompt="a lighthouse at dawn") for i in range(2)]
    req = _req(items, storyboard_concept="a lighthouse at dawn", enhance_prompt=True)

    BatchVideoGenerator._apply_storyboard(SimpleNamespace(), req)

    assert [it.prompt for it in items] == ["a lighthouse at dawn"] * 2
    assert req.enhance_prompt is True


def test_apply_storyboard_noop_without_concept(monkeypatch):
    monkeypatch.setattr(md, "storyboard_from_concept",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    items = [BatchVideoItem(id="1", prompt="keep me")]
    req = _req(items, storyboard_concept=None)
    BatchVideoGenerator._apply_storyboard(SimpleNamespace(), req)
    assert items[0].prompt == "keep me"  # untouched


def test_apply_director_never_raises_on_director_failure(monkeypatch):
    def boom(prompts, **k):
        raise RuntimeError("director exploded")

    monkeypatch.setattr(md, "enhance_prompts", boom)
    items = [BatchVideoItem(id="1", prompt="a")]
    req = _req(items, director_mode=True, enhance_prompt=True)

    # must not raise; prompts and enhance flag unchanged on failure
    BatchVideoGenerator._apply_director(SimpleNamespace(), req)
    assert items[0].prompt == "a"
    assert req.enhance_prompt is True
