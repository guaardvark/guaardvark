"""generate_video resolves its arguments against the model's capability
record: a wrong ask fails with one sentence, a typed value keeps priority."""
from backend.tools.image_tools import VideoGeneratorTool, _dims_for_ratio
from backend.services import video_model_registry as vmr

resolve = VideoGeneratorTool.resolve_request


def test_default_model_and_legacy_frames():
    params, err = resolve("a dog")
    assert err is None
    caps = vmr.model_capabilities(params["model"])
    assert caps.get("supports_t2v")
    assert params["duration_frames"] == 49
    assert params["metadata"] == {"source": "chat"}


def test_h3_with_audio_and_seconds():
    params, err = resolve("a dog barks", model="minimax-h3-int8", audio=True, duration_s=5, aspect_ratio="9:16")
    assert err is None
    assert params["duration_frames"] == 120 and params["fps"] == 24
    assert params["num_inference_steps"] == 20   # the model's declared default
    assert params["metadata"]["aspect_ratio"] == "9:16"
    assert params["height"] > params["width"] and params["width"] % 32 == 0


def test_audio_on_a_silent_family_is_refused():
    params, err = resolve("a dog", model="wan22-5b", audio=True)
    assert params is None and "silent" in err and "minimax-h3-int8" in err


def test_references_need_the_reference_build():
    params, err = resolve("a dog", model="minimax-h3-int8", reference_images=["/x.png"])
    assert params is None and "minimax-h3-ref2va-int8" in err
    params, err = resolve("<Picture 1> walks", model="minimax-h3-ref2va-int8", reference_images=["/x.png"])
    assert err is None and params["model"] == "minimax-h3-ref2va-int8"


def test_last_frame_needs_a_first_plus_last_model():
    params, err = resolve("a dog", model="wan22-5b", last_image="/end.png")
    assert params is None and "last-frame" in err
    params, err = resolve("a dog", model="minimax-h3-int8", last_image="/end.png")
    assert err is None


def test_first_frame_on_a_text_only_model_uses_its_sibling():
    params, err = resolve("a dog", model="wan22-14b", first_image="/start.png")
    assert err is None and params["model"] == "wan22-14b-i2v"


def test_steps_floor_and_explicit_flag():
    params, err = resolve("a dog", model="minimax-h3-int8", num_inference_steps=8)
    assert err is None and params["num_inference_steps"] == 20 and params["metadata"]["steps_explicit"] is True
    params, err = resolve("a dog", model="minimax-h3-int8", num_inference_steps=30)
    assert params["num_inference_steps"] == 30


def test_duration_is_clamped_to_the_model():
    params, err = resolve("a dog", model="minimax-h3-int8", duration_s=40)
    assert err is None and params["duration_frames"] == 362
    params, err = resolve("a dog", model="minimax-h3-int8", duration_s=1)
    assert params["duration_frames"] == 72   # the 3 s floor at 24 fps


def test_unknown_things_fail_plainly():
    assert resolve("a dog", model="nope")[1].startswith("Unknown video model")
    assert "companion" in resolve("a dog", model="minimax-h3-vae")[1]
    assert "3:2 is not one of them" in resolve("a dog", model="minimax-h3-int8", aspect_ratio="3:2")[1]
    assert "speed profile 'warp'" in resolve("a dog", model="minimax-h3-int8", speed_profile="warp")[1]
    params, _ = resolve("a dog", model="minimax-h3-int8", speed_profile="turbo-8")
    assert params["speed_profile"] == "turbo-8"


def test_dims_follow_alignment_and_budget():
    w, h = _dims_for_ratio("16:9", vmr.model_capabilities("minimax-h3-int8"))
    assert (w, h) == (864, 480)
    w, h = _dims_for_ratio("1:1", vmr.model_capabilities("wan22-5b"))
    assert w == h and w % 32 == 0
    assert _dims_for_ratio("bad", {}) == (None, None)
