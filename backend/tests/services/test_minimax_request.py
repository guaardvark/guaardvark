"""A request becomes the H3 graph through the capability contract: speed
profiles, step floors, style tokens, frames and guides are resolved against
what the registry declares, and a wrong ask fails with one sentence."""
from pathlib import Path

import pytest

from backend.services import video_model_registry as vmr
from backend.services.comfyui_video_generator import (
    ComfyUIVideoGenerator,
    VideoGenerationRequest,
)

MODEL = "minimax-h3-int8"


class _Gen(ComfyUIVideoGenerator):
    """No live ComfyUI: uploads return the basename, audio cutting is recorded."""

    def __init__(self):  # noqa: D401 — skip the network probe in the real __init__
        self.uploads = []
        self.cuts = []

    def _upload_input_file(self, path, kind="image"):
        self.uploads.append((kind, Path(path).name))
        return Path(path).name

    def _prepare_guide_audio(self, path, seek_s=0.0, duration_s=0.0, max_s=None):
        self.cuts.append((Path(path).name, seek_s, duration_s, round(max_s or 0, 2)))
        return path


def _build(gen, tmp_path, **kw):
    req = VideoGenerationRequest(model=MODEL, prompt="a dog", width=864, height=480,
                                 duration_frames=124, fps=24, **kw)
    return gen._build_minimax_request(req, MODEL, None, 42, 1)


@pytest.fixture
def gen():
    return _Gen()


@pytest.fixture
def lora_installed(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: m == "minimax-h3-fl2v-turbo-8step")


def test_default_request_is_the_template_at_the_floor(gen, tmp_path):
    wf, err = _build(gen, tmp_path, num_inference_steps=25)
    assert err is None and wf["9"]["inputs"]["steps"] == 25
    wf, err = _build(gen, tmp_path, num_inference_steps=10)
    assert err is None and wf["9"]["inputs"]["steps"] == 20  # preset raised to the floor
    wf, err = _build(gen, tmp_path, num_inference_steps=10, metadata={"steps_explicit": "1"})
    assert err is None and wf["9"]["inputs"]["steps"] == 10  # typed value wins


def test_speed_profile_loads_its_lora_and_steps(gen, tmp_path, lora_installed):
    wf, err = _build(gen, tmp_path, speed_profile="turbo-8", num_inference_steps=25)
    assert err is None
    assert wf["15"]["inputs"]["lora_name"] == "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    assert wf["9"]["inputs"]["steps"] == 8  # the profile's count, not the API's generic 25
    assert wf["9"]["inputs"]["model"] == ["15", 0]
    wf, err = _build(gen, tmp_path, speed_profile="standard")
    assert err is None and "15" not in wf and wf["9"]["inputs"]["steps"] == 20


def test_missing_lora_and_unknown_profile_fail_plainly(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: False)
    wf, err = _build(gen, tmp_path, speed_profile="turbo-8")
    assert wf is None and "Manage Video Models" in err and "8-step" in err
    wf, err = _build(gen, tmp_path, speed_profile="warp")
    assert wf is None and "Unknown speed profile 'warp'" in err


def test_768p_profile_refuses_a_small_canvas(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    wf, err = _build(gen, tmp_path, speed_profile="turbo-4-768p")
    assert wf is None and "768px short edge" in err
    req = VideoGenerationRequest(model=MODEL, prompt="p", width=1344, height=768,
                                 duration_frames=124, speed_profile="turbo-4-768p")
    wf, err = gen._build_minimax_request(req, MODEL, None, 1, 1)
    assert err is None and wf["9"]["inputs"]["steps"] == 4


def test_style_embedding_token_is_appended(gen, tmp_path):
    wf, err = _build(gen, tmp_path, style_embedding="bullet_time")
    assert err is None
    assert wf["6"]["inputs"]["prompt"] == "a dog embedding:minimaxh3_bullet_time"
    wf, err = _build(gen, tmp_path, style_embedding="nope")
    assert wf is None and "Unknown style embedding 'nope'" in err


def test_frames_and_guides_are_uploaded(gen, tmp_path):
    first, last, line = tmp_path / "first.png", tmp_path / "last.png", tmp_path / "line.wav"
    for f in (first, last, line):
        f.write_bytes(b"x")
    wf, err = _build(
        gen, tmp_path, first_frame_path=str(first), last_frame_path=str(last),
        guides=[{"kind": "audio", "path": str(line), "frame_idx": 24, "seek_s": 1.5}],
    )
    assert err is None
    assert wf["6"]["inputs"]["first_frame"] == ["5", 0]
    assert wf["6"]["inputs"]["last_frame"] == ["16", 0]
    assert wf["17"]["inputs"]["audio"] == "line.wav" and wf["18"]["inputs"]["frame_idx"] == 24
    assert gen.uploads == [("image", "first.png"), ("image", "last.png"), ("audio", "line.wav")]
    # The slice is capped at what remains of the clip after the anchor frame.
    assert gen.cuts == [("line.wav", 1.5, 0.0, round(100 / 24, 2))]


def test_missing_files_and_reference_inputs_fail_plainly(gen, tmp_path):
    wf, err = _build(gen, tmp_path, last_frame_path=str(tmp_path / "nope.png"))
    assert wf is None and "Last frame not found" in err
    wf, err = _build(gen, tmp_path, ref_images=[str(tmp_path / "a.png")])
    assert wf is None and "Reference build" in err
    wf, err = _build(gen, tmp_path, guides=[{"kind": "audio", "path": str(tmp_path / "x.wav")}])
    assert wf is None and "Guide file not found" in err


# ── reference-to-video build ─────────────────────────────────────────────────

REF = "minimax-h3-ref2va-int8"


def _ref(gen, tmp_path, **kw):
    req = VideoGenerationRequest(model=REF, prompt="<Picture 1> walks in", width=864, height=480,
                                 duration_frames=124, fps=24, **kw)
    return gen._build_minimax_request(req, REF, None, 7, 1)


def test_reference_build_wires_images_clips_and_audio(gen, tmp_path):
    a, b, clip, track, voice = (tmp_path / n for n in ("a.png", "b.png", "clip.mp4", "track.wav", "voice.wav"))
    for f in (a, b, clip, track, voice):
        f.write_bytes(b"x")
    wf, err = _ref(
        gen, tmp_path,
        ref_images=[str(a), str(b)],
        ref_videos=[{"path": str(clip), "audio_path": str(track)}],
        ref_audios=[str(voice)],
    )
    assert err is None
    node = wf["6"]
    assert node["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert node["inputs"]["ref_images.ref_image_0"] == ["20", 0]
    assert node["inputs"]["ref_images.ref_image_1"] == ["21", 0]
    assert node["inputs"]["ref_videos.ref_video_0"] == ["30", 0]
    assert node["inputs"]["ref_video_audios.ref_video_audio_0"] == ["33", 0]
    assert node["inputs"]["ref_audios.ref_audio_0"] == ["36", 0]
    assert wf["30"]["inputs"]["force_rate"] == 24 and wf["30"]["inputs"]["video"] == "clip.mp4"
    assert wf["33"] == {"class_type": "LoadAudio", "inputs": {"audio": "track.wav"}}
    assert wf["14"]["inputs"]["filename_prefix"] == "minimax_h3_r2v"
    assert wf["1"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    assert gen.uploads == [("image", "a.png"), ("image", "b.png"), ("clip", "clip.mp4"),
                           ("audio", "track.wav"), ("audio", "voice.wav")]


def test_reference_clip_uses_its_own_soundtrack_by_default(gen, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    wf, err = _ref(gen, tmp_path, ref_videos=[{"path": str(clip)}])
    assert err is None
    assert wf["6"]["inputs"]["ref_video_audios.ref_video_audio_0"] == ["30", 2]
    wf, err = _ref(gen, tmp_path, ref_videos=[{"path": str(clip), "include_audio": False}])
    assert err is None and "ref_video_audios.ref_video_audio_0" not in wf["6"]["inputs"]


def test_reference_limits_and_misuse_fail_plainly(gen, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    wf, err = _ref(gen, tmp_path, ref_images=[str(img)] * 10)
    assert wf is None and "at most 9 reference images" in err
    wf, err = _ref(gen, tmp_path, ref_audios=[str(img)])
    assert wf is None and "cannot be the only reference" in err
    wf, err = _ref(gen, tmp_path, ref_images=[str(img)], first_frame_path=str(img))
    assert wf is None and "takes references, not first/last frames" in err
    wf, err = _ref(gen, tmp_path, ref_images=[str(tmp_path / "missing.png")])
    assert wf is None and "Reference image not found" in err


def test_reference_turbo_profile_is_the_ref2v_lora(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda m: True)
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    wf, err = _ref(gen, tmp_path, ref_images=[str(img)], speed_profile="turbo-4")
    assert err is None
    assert wf["15"]["inputs"]["lora_name"] == "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
    assert wf["9"]["inputs"]["steps"] == 4 and wf["9"]["inputs"]["model"] == ["15", 0]


def test_registry_floors_win_over_the_class_tables():
    assert ComfyUIVideoGenerator._min_vram_gb_for("minimax-h3-int8-full") == 24
    assert ComfyUIVideoGenerator._min_vram_gb_for("minimax-h3-bf16") == 48
    assert ComfyUIVideoGenerator._min_vram_gb_for("minimax-h3-int8") == 16
    assert ComfyUIVideoGenerator._min_vram_gb_for("wan22-5b") == 11


def test_wide_and_portrait_ratios_are_known():
    ratios = ComfyUIVideoGenerator._ASPECT_RATIOS
    assert abs(ratios["21:9"] - 21 / 9) < 1e-9 and abs(ratios["3:4"] - 0.75) < 1e-9
    assert ComfyUIVideoGenerator._supported_aspect_ratios(MODEL) == [
        "21:9", "16:9", "4:3", "1:1", "3:4", "9:16",
    ]
