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
from backend.services import video_model_registry as vmr
from backend.services.batch_video_generator import (
    BatchVideoGenerator,
    BatchVideoRequest,
    BatchVideoItem,
)


def test_to_i2v_model_mapping():
    assert BatchVideoGenerator._to_i2v_model("wan22-14b") == "wan22-14b-i2v"
    assert BatchVideoGenerator._to_i2v_model("cogvideox-5b") == "cogvideox-5b-i2v"
    assert BatchVideoGenerator._to_i2v_model("wan22-14b-i2v") == "wan22-14b-i2v"  # already I2V
    # No model: the registry's default first-frame model (the 16 GB-native Wan 5B TI2V).
    assert BatchVideoGenerator._to_i2v_model(None) == vmr.DEFAULT_I2V_MODEL


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


# --- Batch image director pass ------------------------------------------------
# apply_enhance_to_prompts imports media_director.enhance_prompts at call time,
# so patching the module attribute reaches it.

from backend.services.batch_image_generator import (  # noqa: E402
    BatchImageGenerator,
    BatchImageRequest,
    BatchPrompt,
)


def _image_req(prompts, model="sd-xl", **kw):
    rows = [BatchPrompt(id=str(i), prompt=p, model=model) for i, p in enumerate(prompts)]
    return BatchImageRequest(batch_id="img1", prompts=rows, output_dir="/tmp/x", **kw)


def _image_self():
    # The pass only reaches ``self`` for the helper that records the typed prompt.
    return SimpleNamespace(_remember_typed_prompt=BatchImageGenerator._remember_typed_prompt)


def _image_director_stub(monkeypatch, rewrite):
    calls = []

    def fake_enhance(prompts, **k):
        calls.append(list(prompts))
        return rewrite(list(prompts))

    monkeypatch.setattr(md, "verbatim_prompts_enabled", lambda: False)
    monkeypatch.setattr(md, "enhance_prompts", fake_enhance)
    return calls


def test_image_director_fallback_is_called_once_and_keeps_enhancer(monkeypatch):
    calls = _image_director_stub(monkeypatch, lambda prompts: prompts)
    req = _image_req(["a red fox", "a quiet harbour"], director_mode=True)

    BatchImageGenerator._apply_director(_image_self(), req)

    assert len(calls) == 1
    assert [bp.prompt for bp in req.prompts] == ["a red fox", "a quiet harbour"]
    assert all(bp.auto_enhance is True for bp in req.prompts)
    assert req.auto_enhance is True


def test_image_director_turns_enhancer_off_only_where_it_rewrote(monkeypatch):
    calls = _image_director_stub(
        monkeypatch, lambda prompts: ["DIR:" + prompts[0]] + prompts[1:],
    )
    req = _image_req(["a red fox", "a quiet harbour"], director_mode=True)

    BatchImageGenerator._apply_director(_image_self(), req)

    assert len(calls) == 1
    first, second = req.prompts
    assert (first.prompt, first.auto_enhance) == ("DIR:a red fox", False)
    assert (second.prompt, second.auto_enhance) == ("a quiet harbour", True)
    assert req.auto_enhance is False


def test_image_director_natural_family_fallback_keeps_enhancer(monkeypatch):
    # Natural families take the director by default; their enhancer only adds
    # a style clause, so keeping it on after a fallback never stuffs tags.
    calls = _image_director_stub(monkeypatch, lambda prompts: prompts)
    req = _image_req(["a red fox"], model="zimage-turbo")

    BatchImageGenerator._apply_director(_image_self(), req)

    assert len(calls) == 1
    assert req.prompts[0].auto_enhance is True
    assert req.auto_enhance is True


def test_image_director_keeps_the_typed_prompt_for_the_image_name(monkeypatch):
    # Captions, the lightbox title and the registered document show what the
    # person typed; the rewrite goes to the generator and into the details.
    _image_director_stub(monkeypatch, lambda prompts: ["DIR:" + prompts[0]] + prompts[1:])
    req = _image_req(["a red fox", "a quiet harbour"], director_mode=True)

    BatchImageGenerator._apply_director(_image_self(), req)

    first, second = req.prompts
    assert first.prompt == "DIR:a red fox"
    assert first.metadata["typed_prompt"] == "a red fox"
    assert "typed_prompt" not in second.metadata
    assert BatchImageGenerator._prompt_record(first) == {
        "original_prompt": "a red fox",
        "rewritten_prompt": "DIR:a red fox",
    }
    assert BatchImageGenerator._prompt_record(second) == {"original_prompt": "a quiet harbour"}


def test_image_storyboard_keeps_the_typed_prompt_for_the_image_name(monkeypatch):
    monkeypatch.setattr(md, "storyboard_from_concept",
                        lambda concept, n, **k: {"treatment": "t",
                                                 "prompts": ["wide shot of the lighthouse", "the keeper"]})
    req = _image_req(["placeholder one", "placeholder two"],
                     storyboard_concept="a lighthouse at dawn")

    BatchImageGenerator._apply_director(_image_self(), req)

    assert [bp.metadata["typed_prompt"] for bp in req.prompts] == ["placeholder one", "placeholder two"]
    assert BatchImageGenerator._prompt_record(req.prompts[0])["rewritten_prompt"] == "wide shot of the lighthouse"


def test_image_storyboard_concept_fallback_keeps_enhancer(monkeypatch):
    # storyboard_from_concept's failure contract is {"prompts": [concept] * n}.
    monkeypatch.setattr(md, "storyboard_from_concept",
                        lambda concept, n, **k: {"treatment": None, "prompts": [concept] * n})
    req = _image_req(["placeholder one", "placeholder two"],
                     storyboard_concept="a lighthouse at dawn")

    BatchImageGenerator._apply_director(_image_self(), req)

    assert [bp.prompt for bp in req.prompts] == ["a lighthouse at dawn"] * 2
    assert all(bp.auto_enhance is True for bp in req.prompts)
    assert req.auto_enhance is True


def test_image_storyboard_turns_enhancer_off_only_on_real_shots(monkeypatch):
    monkeypatch.setattr(md, "storyboard_from_concept",
                        lambda concept, n, **k: {"treatment": "t",
                                                 "prompts": ["wide shot of the lighthouse", concept]})
    req = _image_req(["placeholder one", "placeholder two"],
                     storyboard_concept="a lighthouse at dawn")

    BatchImageGenerator._apply_director(_image_self(), req)

    first, second = req.prompts
    assert (first.prompt, first.auto_enhance) == ("wide shot of the lighthouse", False)
    assert (second.prompt, second.auto_enhance) == ("a lighthouse at dawn", True)
    assert req.auto_enhance is False
