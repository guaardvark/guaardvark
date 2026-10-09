"""generate_video floors a step count at the speed profile's floor when one is named,
and takes only video models.

A turbo or lightning profile's LoRA is distilled for its own step count, so the
model-wide floor does not apply to it. The song model has a capability record
but is not a video model. Pure: the registry is read, nothing is rendered.
"""
import pytest

from backend.services import video_model_registry as vmr
from backend.services import video_render_limits as limits
from backend.tools.image_tools import VideoGeneratorTool

resolve = VideoGeneratorTool.resolve_request


def _rendered(model: str, profile: str | None, asked):
    """(what the tool sends, what the generator's step resolver renders)."""
    # A reference build refuses a request without a reference picture or clip.
    refs = ["fox.png"] if vmr.VIDEO_MODEL_REGISTRY[model].get("ref_limits") else None
    params, err = resolve("a fox", model=model, speed_profile=profile, num_inference_steps=asked,
                          reference_images=refs)
    assert err is None, err
    spec = dict(vmr.model_capabilities(model)["speed_profiles"][profile]) if profile else None
    explicit = bool(params["metadata"].get("steps_explicit"))
    return params.get("num_inference_steps"), limits.resolve_steps(
        model, params.get("num_inference_steps"), explicit=explicit, profile=spec, strict=False)


def _profiles_below_the_model_floor():
    for model, entry in vmr.VIDEO_MODEL_REGISTRY.items():
        if entry.get("type") not in vmr.GENERATION_TYPES or model.startswith("user-"):
            continue
        for profile, spec in (entry.get("speed_profiles") or {}).items():
            if spec.get("min_steps") and entry.get("min_steps") and spec["min_steps"] < entry["min_steps"]:
                yield model, profile


@pytest.mark.parametrize("model,profile", sorted(set(_profiles_below_the_model_floor())))
def test_a_profile_step_count_is_rendered_not_the_model_floor(model, profile):
    spec = vmr.VIDEO_MODEL_REGISTRY[model]["speed_profiles"][profile]
    sent, rendered = _rendered(model, profile, spec["steps"])
    assert sent == rendered == spec["steps"]
    # Below the profile's own floor the count is raised to that floor, no further.
    sent, rendered = _rendered(model, profile, 1)
    assert sent == rendered == spec["min_steps"]
    # No count given: the profile brings its own.
    assert _rendered(model, profile, None)[1] == spec["steps"]


def test_the_registry_still_declares_a_profile_below_its_model_floor():
    assert ("minimax-h3-int8", "turbo-8") in set(_profiles_below_the_model_floor())


def test_a_count_above_the_profile_floor_stands():
    assert _rendered("minimax-h3-int8", "turbo-8", 12) == (12, 12)


def test_without_a_profile_the_model_floor_applies():
    floor = vmr.VIDEO_MODEL_REGISTRY["minimax-h3-int8"]["min_steps"]
    assert _rendered("minimax-h3-int8", None, floor - 12) == (floor, floor)
    assert _rendered("minimax-h3-int8", "standard", floor - 12) == (floor, floor)
    assert _rendered("minimax-h3-int8", None, floor + 10) == (floor + 10, floor + 10)


def test_the_song_model_is_refused_and_not_listed_as_a_video_model():
    assert vmr.VIDEO_MODEL_REGISTRY["minimax-music3-int8"]["type"] == "audio"
    params, err = resolve("a song", model="minimax-music3-int8")
    assert params is None and "audio, not video" in err and "generate_music" in err
    _, err = resolve("a fox", model="no-such-model")
    known = err.split("Known: ")[1].rstrip(".").split(", ")
    assert "minimax-music3-int8" not in known and "minimax-h3-int8" in known and "wan22-5b" in known
    assert all(vmr.VIDEO_MODEL_REGISTRY[k]["type"] in vmr.GENERATION_TYPES for k in known)
    # A companion keeps its own sentence.
    assert "companion" in resolve("a fox", model="minimax-h3-vae")[1]
