"""Post-render quality gate: the frame checker, the batch record it writes and
the MCP status text that reads it.

The clips are real H.264 files written with PyAV (tests/fixtures/video_quality_clips.py).
"""
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("av")

from backend.services import video_consistency_metrics as vcm  # noqa: E402
from backend.tests.fixtures import video_quality_clips as clips  # noqa: E402

EXPECT = {"expected_width": clips.WIDTH, "expected_height": clips.HEIGHT, "expected_frames": clips.FRAMES}


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    root = tmp_path_factory.mktemp("clips")
    return {name: getattr(clips, name)(root / f"{name}.mp4")
            for name in ("clean", "black_tile", "blown_out", "washed_out", "frozen", "black_frames")}


def _codes(result):
    return [f["code"] for f in result["flags"]]


def _observed(result):
    return [f["code"] for f in result.get("observations") or []]


def test_a_clean_clip_is_not_flagged(made):
    result = vcm.inspect_video_frames(made["clean"], **EXPECT)
    assert result["readable"] and _codes(result) == []
    assert (result["width"], result["height"], result["frames"]) == (clips.WIDTH, clips.HEIGHT, clips.FRAMES)
    assert result["sampled"][0] == 0 and result["sampled"][-1] == clips.FRAMES - 1


@pytest.mark.parametrize("name,code", [
    ("blown_out", "clipped_highlights"),
    ("washed_out", "washed_out"),
    ("black_frames", "black_frames"),
])
def test_each_defect_is_flagged(made, name, code):
    result = vcm.inspect_video_frames(made[name], **EXPECT)
    assert code in _codes(result), result
    flag = next(f for f in result["flags"] if f["code"] == code)
    assert flag["message"].startswith(vcm._FLAG_TEXT[code])


@pytest.mark.parametrize("name,code", [
    ("black_tile", "black_tiles"),
    ("frozen", "frozen"),
])
def test_an_uncalibrated_check_is_an_observation_not_a_flag(made, name, code):
    result = vcm.inspect_video_frames(made[name], **EXPECT)
    assert code in _observed(result) and code not in _codes(result), result


def test_every_observed_only_check_says_what_it_misfired_on():
    assert set(vcm.OBSERVED_ONLY) <= set(vcm._FLAG_TEXT)
    assert all(why.strip() for why in vcm.OBSERVED_ONLY.values())


def test_washed_out_clip_also_reads_as_colourless(made):
    assert "desaturated" in _observed(vcm.inspect_video_frames(made["washed_out"], **EXPECT))


def test_a_black_tile_is_not_confused_with_a_dark_scene(made):
    result = vcm.inspect_video_frames(made["black_frames"], **EXPECT)
    codes = _codes(result) + _observed(result)
    assert "black_tiles" not in codes and "crushed_shadows" not in codes


def test_wrong_size_and_length_against_the_request(made):
    result = vcm.inspect_video_frames(made["clean"], expected_width=640, expected_height=384,
                                      expected_frames=49, frame_tolerance=4)
    assert _codes(result) == ["wrong_size", "wrong_frame_count"]
    assert "320x192, asked for 640x384" in result["flags"][0]["message"]


def test_a_length_on_the_neighbouring_grid_step_is_not_flagged(made):
    # 33 frames rendered for a request of 36 on a 4n+1 grid: one step away.
    result = vcm.inspect_video_frames(made["clean"], expected_frames=36, frame_tolerance=4)
    assert _codes(result) == []


def test_unreadable_and_missing_files(tmp_path):
    missing = vcm.inspect_video_frames(tmp_path / "gone.mp4")
    assert not missing["readable"] and _codes(missing) == ["unreadable"]
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video" * 100)
    assert _codes(vcm.inspect_video_frames(junk)) == ["unreadable"]


def test_rife_and_grid_arithmetic():
    # The 2026-09-24 case: 80 frames asked of Wan 14B (4n+1 grid) with RIFE x2 wrote 153.
    expected = vcm.expected_output_frames(80, 2)
    tolerance = vcm.frame_grid_step("4n+1") * 2
    assert expected == 159 and abs(153 - expected) <= tolerance
    assert vcm.expected_output_frames(49, 1) == 49
    assert vcm.frame_grid_step("17k+5") == 17 and vcm.frame_grid_step(None) == 1


def test_every_threshold_says_why():
    for key, entry in vcm.QUALITY_THRESHOLDS.items():
        assert set(entry) == {"value", "why"}, key
        assert len(entry["why"]) > 40, key
    assert set(vcm._FLAG_TEXT) >= {"black_tiles", "clipped_highlights", "crushed_shadows", "washed_out",
                                   "desaturated", "oversaturated", "frozen", "wrong_size", "wrong_frame_count"}


# ── The batch record ─────────────────────────────────────────────────────────

def test_expected_output_follows_the_resolved_request():
    from backend.services.batch_video_generator import BatchVideoGenerator
    from backend.services.comfyui_video_generator import VideoGenerationRequest

    req = VideoGenerationRequest(model="wan22-14b", width=864, height=480, duration_frames=80,
                                 interpolation_multiplier=2, metadata={"upscale": True})
    assert BatchVideoGenerator._expected_output(req) == {
        "expected_width": 1728, "expected_height": 960, "expected_frames": 159, "frame_tolerance": 8,
    }


class _Renderer:
    """The video generator seam _run_batch_inner calls: it "renders" by copying
    a fixture clip into the item folder and answers with the batch-relative
    path, as ComfyUIVideoGenerator.generate_video does."""

    def __init__(self, source: Path):
        self.source = source
        self.service_available = True

    def generate_video(self, request):
        from backend.services.comfyui_video_generator import VideoGenerationResult

        item_dir = Path(request.output_dir) / request.metadata["item_id"] / "videos"
        item_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, item_dir / "clip.mp4")
        request.width, request.height = clips.WIDTH, clips.HEIGHT
        return VideoGenerationResult(success=True, video_path=str(Path(request.metadata["item_id"]) / "videos" / "clip.mp4"))


def _run_one(tmp_path, monkeypatch, source, **request_fields):
    import threading

    from flask import Flask

    from backend.services import batch_video_generator as bvg

    gen = bvg.BatchVideoGenerator.__new__(bvg.BatchVideoGenerator)
    gen.video_generator = _Renderer(source)
    gen.cancel_events = {"b1": threading.Event()}
    gen.batch_lock = threading.Lock()
    gen.active_batches = {}
    # Clip registration asks _get_batch_dir where the batch is, which reads this.
    gen.base_output_dir = tmp_path
    batch_dir = tmp_path / "batch"
    request = bvg.BatchVideoRequest(
        batch_id="b1", items=[bvg.BatchVideoItem(id="item1", prompt="a fox")], output_dir=str(batch_dir),
        model="wan22-5b", duration_frames=clips.FRAMES, width=clips.WIDTH, height=clips.HEIGHT,
        interpolation_multiplier=1, enhance_prompt=False, **request_fields,
    )
    status = bvg.BatchVideoStatus(batch_id="b1", status="queued", total_videos=1, output_dir=str(batch_dir))
    monkeypatch.chdir(tmp_path)  # a relative path read from the cwd would miss the clip
    with Flask(__name__).app_context():
        gen._run_batch_inner(request, status)
    return status, batch_dir


def test_a_completed_clip_is_checked_where_it_was_written(tmp_path, monkeypatch, made):
    status, batch_dir = _run_one(tmp_path, monkeypatch, made["clean"])
    [result] = status.results
    quality = result.metadata["quality"]
    assert result.success and status.status == "completed"
    assert quality["stats"]["exists"] is True
    assert quality["frames"]["readable"] and quality["frames"]["frames"] == clips.FRAMES
    assert quality["flags"] == [] and quality["flagged"] is False
    sidecar = batch_dir / "item1" / "videos" / "clip.mp4.metrics.json"
    assert json.loads(sidecar.read_text())["quality"]["frames"]["readable"] is True
    assert not list(Path(tmp_path).glob("item1*"))


def test_a_washed_out_clip_completes_with_its_flags_in_the_status(tmp_path, monkeypatch, made):
    status, batch_dir = _run_one(tmp_path, monkeypatch, made["washed_out"])
    [result] = status.results
    assert result.success and status.status == "completed"
    quality = result.metadata["quality"]
    assert quality["flagged"] and quality["flag_reasons"] == ["washed_out"]
    assert "desaturated" in [o["code"] for o in quality["frames"]["observations"]]
    saved = json.loads((batch_dir / "batch_metadata.json").read_text())
    assert saved["results"][0]["metadata"]["quality"]["flags"][0]["code"] == "washed_out"


# ── What MCP and chat read ───────────────────────────────────────────────────

def test_status_text_names_the_flags(monkeypatch):
    from backend.tools import image_tools

    quality = {"flagged": True, "frames": {"readable": True},
               "flags": [{"code": "black_frames", "message": "black frames: 3 of 9 sampled frames"}]}
    clean = {"flagged": False, "frames": {"readable": True}, "flags": []}
    body = {"status": "completed", "stage": "done", "completed_videos": 2, "total_videos": 2, "results": [
        {"success": True, "video_path": "a/videos/a.mp4", "metadata": {"quality": quality}},
        {"success": True, "video_path": "b/videos/b.mp4", "metadata": {"quality": clean}},
    ]}

    def fake_http(method, path, *args, **kwargs):
        if path.startswith("/api/batch-video/status/"):
            return body
        raise RuntimeError("404 not found")

    monkeypatch.setattr(image_tools, "_http_json", fake_http)
    tool = image_tools.GenerationStatusTool()
    tool._context = {"transport": "mcp"}
    out = tool.execute(batch_id="VideoBatch_x")
    assert out.success
    assert "Quality: flagged — black frames: 3 of 9 sampled frames" in out.output
    assert "Quality: no problems found in the sampled frames" in out.output
    assert out.metadata["files"][0]["quality"]["flags"][0]["code"] == "black_frames"


# ── The vision review's "not reviewed" state ─────────────────────────────────

def _attach(made, **kwargs):
    from backend.services import batch_video_generator as bvg

    gen = bvg.BatchVideoGenerator.__new__(bvg.BatchVideoGenerator)
    result = bvg.BatchVideoResult(item_id="item1", success=True, video_path="clip.mp4")
    gen._attach_quality_metrics(result, video_path=str(made["clean"]), cinematic=True, **kwargs)
    return result.metadata["quality"]


def test_a_cinematic_clip_without_the_review_model_reads_not_reviewed(made, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_VIDEO_REVIEW_MODEL", "not-pulled-vlm:7b")
    monkeypatch.setattr(vcm, "_installed_ollama_tags", lambda: {"gemma4:e2b"})
    monkeypatch.setattr(vcm, "_extract_frames_b64", lambda *a, **k: pytest.fail("frames sampled"))

    quality = _attach(made)

    review = quality["vlm_review"]
    assert review["status"] == "not_reviewed"
    assert review["reason"] == "model_not_installed"
    assert "not-pulled-vlm:7b" in review["message"]
    assert "low_vlm_score" not in quality["flag_reasons"]


def test_a_review_that_raises_is_recorded_not_dropped(made, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ffmpeg vanished")

    monkeypatch.setattr(vcm, "review_video_quality", boom)
    quality = _attach(made)
    assert quality["vlm_review"]["status"] == "not_reviewed"
    assert quality["vlm_review"]["reason"] == "review_error"
    assert "ffmpeg vanished" in quality["vlm_review"]["message"]


def test_a_reviewed_clip_keeps_its_score_and_low_score_flag(made, monkeypatch):
    monkeypatch.setattr(vcm, "review_video_quality", lambda *a, **k: {
        "status": "reviewed", "available": True, "model": "m", "reason": None, "message": None,
        "review": {"quality_score": 3},
    })
    quality = _attach(made)
    assert quality["vlm_review"]["review"]["quality_score"] == 3
    assert "low_vlm_score:3" in quality["flag_reasons"]


def test_status_text_says_when_a_clip_was_not_reviewed(monkeypatch):
    from backend.tools import image_tools

    quality = {"flagged": False, "frames": {"readable": True}, "flags": [],
               "vlm_review": vcm.not_reviewed("model_not_installed", model="minicpm-v4.5:latest")}
    body = {"status": "completed", "stage": "done", "completed_videos": 1, "total_videos": 1, "results": [
        {"success": True, "video_path": "a/videos/a.mp4", "metadata": {"quality": quality}},
    ]}
    def fake_http(method, path, *args, **kwargs):
        if path.startswith("/api/batch-video/status/"):
            return body
        raise RuntimeError("404 not found")

    monkeypatch.setattr(image_tools, "_http_json", fake_http)
    tool = image_tools.GenerationStatusTool()
    tool._context = {"transport": "mcp"}
    out = tool.execute(batch_id="VideoBatch_x")
    assert "Vision review: not reviewed — the review model minicpm-v4.5:latest is not installed" in out.output


# ── The keyframe colour match ────────────────────────────────────────────────

def _keyframe(tmp_path, rgb):
    from PIL import Image
    path = tmp_path / "keyframe.png"
    Image.fromarray(rgb).save(path)
    return str(path)


def test_colour_match_reads_the_middle_frame_and_says_what_it_is(made, tmp_path):
    match = vcm.colour_match(_keyframe(tmp_path, clips._scene(0)), made["clean"])
    assert match["label"] == "colour match"
    assert match["frame_index"] == clips.FRAMES // 2 and match["frames"] == clips.FRAMES
    assert 0.5 < match["score"] <= 1.0


def test_a_cinematic_clip_records_a_colour_match_not_an_identity_score(made, tmp_path, monkeypatch):
    monkeypatch.setattr(vcm, "review_video_quality", lambda *a, **k: vcm.not_reviewed("no_frames"))
    quality = _attach(made, keyframe_path=_keyframe(tmp_path, clips._scene(0)))
    assert "identity" not in quality
    assert quality["colour_match"]["label"] == "colour match"
    assert quality["colour_match"]["frame_index"] == clips.FRAMES // 2
    assert not any(code.startswith("low_colour_match") for code in quality["flag_reasons"])


def test_a_clip_whose_palette_left_the_keyframe_is_flagged_as_colour_drift(made, tmp_path, monkeypatch):
    import numpy as np

    monkeypatch.setattr(vcm, "review_video_quality", lambda *a, **k: vcm.not_reviewed("no_frames"))
    magenta = np.zeros((clips.HEIGHT, clips.WIDTH, 3), dtype=np.uint8)
    magenta[..., 0] = magenta[..., 2] = 255
    quality = _attach(made, keyframe_path=_keyframe(tmp_path, magenta))
    flag = next(f for f in quality["flags"] if f["code"] == "low_colour_match")
    assert flag["message"].startswith("colours drift from the keyframe: colour match")
    assert f"at frame {clips.FRAMES // 2} of {clips.FRAMES}" in flag["message"]
    assert "identity" not in flag["message"]


# ── The keyframe match, once the image encoder is installed ──────────────────

@pytest.fixture
def encoder(tmp_path, monkeypatch):
    """A verified install of the pinned encoder with stand-in weights (the
    embedding of a picture is its mean colour)."""
    from backend.services import identity_encoder
    from backend.tests.fixtures import identity_encoder_stub as stub

    root = tmp_path / "identity_encoder"
    monkeypatch.setattr(identity_encoder, "install_root", lambda: root)
    stub.fake_install(root)
    monkeypatch.setattr(vcm, "_image_encoder_classes", stub.stub_classes)
    monkeypatch.setattr(vcm, "review_video_quality", lambda *a, **k: vcm.not_reviewed("no_frames"))
    vcm._ENCODER_CACHE.clear()
    yield
    vcm._ENCODER_CACHE.clear()


def test_keyframe_match_is_none_without_the_encoder(made, tmp_path):
    """None tells the caller to fall back to the colour match; nothing is loaded."""
    assert vcm.keyframe_match(_keyframe(tmp_path, clips._scene(0)), made["clean"]) is None


def test_with_the_encoder_a_clip_records_a_keyframe_match_instead_of_colour(made, tmp_path, encoder):
    quality = _attach(made, keyframe_path=_keyframe(tmp_path, clips._scene(0)))
    match = quality["keyframe_match"]
    assert "colour_match" not in quality
    assert match["label"] == "keyframe match" and match["method"] == "embed"
    assert match["frame_index"] == clips.FRAMES // 2 and match["frames"] == clips.FRAMES
    assert match["threshold"] == vcm.QUALITY_THRESHOLDS["keyframe_match_floor"]["value"]
    assert match["score"] >= match["threshold"]
    assert not any(code.startswith("low_keyframe_match") for code in quality["flag_reasons"])


def test_a_clip_that_no_longer_shows_its_keyframe_is_held(made, tmp_path, encoder):
    import numpy as np

    magenta = np.zeros((clips.HEIGHT, clips.WIDTH, 3), dtype=np.uint8)
    magenta[..., 0] = magenta[..., 2] = 255
    quality = _attach(made, keyframe_path=_keyframe(tmp_path, magenta))
    flag = next(f for f in quality["flags"] if f["code"] == "low_keyframe_match")
    assert flag["message"].startswith("the middle of the clip no longer shows what the keyframe showed")
    assert f"at frame {clips.FRAMES // 2} of {clips.FRAMES}" in flag["message"]
    assert vcm.review_hold(quality)["codes"] == ["low_keyframe_match"]


def test_an_encoder_that_cannot_score_falls_back_to_the_colour_match(made, tmp_path, encoder, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("out of memory")

    monkeypatch.setattr(vcm, "_image_embedding", broken)
    quality = _attach(made, keyframe_path=_keyframe(tmp_path, clips._scene(0)))
    assert quality["keyframe_match"]["score"] is None
    assert "could not run" in quality["keyframe_match"]["reason"]
    assert quality["colour_match"]["label"] == "colour match"


# ── What a failed check does (T014: needs review, no automatic re-render) ────

def test_every_flag_has_a_declared_outcome():
    flags = (set(vcm._FLAG_TEXT) - set(vcm.OBSERVED_ONLY)) | {"low_vlm_score"}
    assert flags <= set(vcm.QUALITY_FLAG_OUTCOME)
    assert set(vcm.QUALITY_FLAG_OUTCOME.values()) == {vcm.NEEDS_REVIEW}


def test_a_check_with_no_declared_outcome_still_holds_the_clip():
    hold = vcm.review_hold({"flags": [{"code": "a_new_check", "message": "something new"}]})
    assert hold == {"state": "needs_review", "codes": ["a_new_check"], "reasons": ["something new"]}
    assert vcm.review_hold({"flags": [], "observations": [{"code": "frozen"}]}) is None


@pytest.fixture
def registered(monkeypatch):
    """What reached the Documents registration seam, patched where it is read."""
    calls = []
    monkeypatch.setattr("backend.services.output_registration.ensure_subfolder", lambda *a, **k: None)
    monkeypatch.setattr("backend.services.output_registration.ensure_folder_for_dir", lambda *a, **k: None)
    monkeypatch.setattr("backend.services.output_registration.register_file",
                        lambda **kw: calls.append(Path(kw["physical_path"]).resolve()))
    return calls


def test_a_flagged_clip_is_held_for_review_and_not_registered(tmp_path, monkeypatch, made, registered):
    status, batch_dir = _run_one(tmp_path, monkeypatch, made["washed_out"])
    [result] = status.results
    assert result.success and status.status == "completed"
    assert result.review["state"] == "needs_review"
    assert result.review["codes"] == ["washed_out"]
    assert result.review["reasons"][0].startswith("washed out")
    clip = (batch_dir / "item1" / "videos" / "clip.mp4").resolve()
    assert clip not in registered
    saved = json.loads((batch_dir / "batch_metadata.json").read_text())
    assert saved["results"][0]["review"]["state"] == "needs_review"


def test_a_held_clip_is_not_rendered_again_on_its_own(tmp_path, monkeypatch, made, registered):
    from backend.services import batch_video_generator as bvg

    renders = []
    real = _Renderer.generate_video

    def counted(self, request):
        renders.append(request.metadata["item_id"])
        return real(self, request)

    monkeypatch.setattr(_Renderer, "generate_video", counted)
    monkeypatch.setattr(bvg.BatchVideoGenerator, "start_batch_from_prompts",
                        lambda *a, **k: pytest.fail("a held clip started a new batch"))
    status, _ = _run_one(tmp_path, monkeypatch, made["washed_out"])
    assert renders == ["item1"]
    assert status.results[0].review["state"] == "needs_review"


def test_a_clean_clip_is_not_held_and_is_registered(tmp_path, monkeypatch, made, registered):
    status, batch_dir = _run_one(tmp_path, monkeypatch, made["clean"])
    [result] = status.results
    assert result.review is None
    assert (batch_dir / "item1" / "videos" / "clip.mp4").resolve() in registered


def _held_batch(tmp_path, made):
    """A finished batch whose one clip a check held, as the generator keeps it."""
    import threading

    from backend.services import batch_video_generator as bvg

    batch_dir = tmp_path / "VideoBatch_held"
    clip = batch_dir / "item1" / "videos" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    shutil.copyfile(made["washed_out"], clip)
    gen = bvg.BatchVideoGenerator.__new__(bvg.BatchVideoGenerator)
    gen.batch_lock = threading.Lock()
    gen.base_output_dir = tmp_path
    result = bvg.BatchVideoResult(
        item_id="item1", success=True, video_path="item1/videos/clip.mp4",
        review={"state": "needs_review", "codes": ["washed_out"], "reasons": ["washed out: spread 13"]},
    )
    status = bvg.BatchVideoStatus(
        batch_id="VideoBatch_held", status="completed", total_videos=1, completed_videos=1,
        results=[result], output_dir=str(batch_dir),
        retry_data={"mode": "text", "prompts": ["a fox in snow"], "item_ids": ["item1"],
                    "params": {"model": "wan22-5b", "seed": 42, "metadata": {"high_consistency": True}}},
    )
    gen.active_batches = {"VideoBatch_held": status}
    return gen, status, clip


def test_approving_a_held_clip_registers_it(tmp_path, made, registered):
    from flask import Flask

    gen, status, clip = _held_batch(tmp_path, made)
    with Flask(__name__).app_context():
        result = gen.approve_item("VideoBatch_held", "item1")
    assert result.review["state"] == "approved"
    assert result.review["reasons"] == ["washed out: spread 13"]
    assert registered == [clip.resolve()]
    saved = json.loads((Path(status.output_dir) / "batch_metadata.json").read_text())
    assert saved["results"][0]["review"]["state"] == "approved"


def test_re_render_is_one_new_batch_with_the_same_settings_and_a_fresh_seed(tmp_path, made, monkeypatch):
    from types import SimpleNamespace

    gen, status, _ = _held_batch(tmp_path, made)
    started = []

    def fake_start(prompts, **params):
        started.append((prompts, params))
        return SimpleNamespace(batch_id="VideoBatch_new", status="queued")

    monkeypatch.setattr(gen, "start_batch_from_prompts", fake_start)
    new = gen.rerender_item("VideoBatch_held", "item1")

    assert new.batch_id == "VideoBatch_new"
    [(prompts, params)] = started
    assert prompts == ["a fox in snow"]
    assert params["model"] == "wan22-5b" and params["seed"] is None
    assert params["metadata"]["high_consistency"] is True
    assert params["metadata"]["rerender_of"] == {"batch_id": "VideoBatch_held", "item_id": "item1"}
    assert status.results[0].review["state"] == "rerendered"
    assert status.results[0].review["rerender_batch_id"] == "VideoBatch_new"


def test_re_render_refuses_a_batch_that_kept_no_per_clip_settings(tmp_path, made):
    gen, status, _ = _held_batch(tmp_path, made)
    status.retry_data = {"mode": "text", "prompts": ["a fox in snow"], "params": {}}
    with pytest.raises(ValueError):
        gen.rerender_item("VideoBatch_held", "item1")
    assert status.results[0].review["state"] == "needs_review"


def test_status_text_says_a_held_clip_waits_for_a_person(monkeypatch):
    from backend.tools import image_tools

    body = {"status": "completed", "stage": "done", "completed_videos": 1, "total_videos": 1, "results": [
        {"success": True, "video_path": "a/videos/a.mp4",
         "metadata": {"quality": {"flagged": True, "frames": {"readable": True},
                                  "flags": [{"code": "washed_out", "message": "washed out: spread 13"}]}},
         "review": {"state": "needs_review", "codes": ["washed_out"], "reasons": ["washed out: spread 13"]}},
    ]}

    def fake_http(method, path, *args, **kwargs):
        if path.startswith("/api/batch-video/status/"):
            return body
        raise RuntimeError("404 not found")

    monkeypatch.setattr(image_tools, "_http_json", fake_http)
    tool = image_tools.GenerationStatusTool()
    tool._context = {"transport": "mcp"}
    out = tool.execute(batch_id="VideoBatch_x")
    assert "Review: needs review — held until a person approves or re-renders it" in out.output
