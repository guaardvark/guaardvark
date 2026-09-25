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


def test_a_clean_clip_is_not_flagged(made):
    result = vcm.inspect_video_frames(made["clean"], **EXPECT)
    assert result["readable"] and _codes(result) == []
    assert (result["width"], result["height"], result["frames"]) == (clips.WIDTH, clips.HEIGHT, clips.FRAMES)
    assert result["sampled"][0] == 0 and result["sampled"][-1] == clips.FRAMES - 1


@pytest.mark.parametrize("name,code", [
    ("black_tile", "black_tiles"),
    ("blown_out", "clipped_highlights"),
    ("washed_out", "washed_out"),
    ("frozen", "frozen"),
    ("black_frames", "black_frames"),
])
def test_each_defect_is_flagged(made, name, code):
    result = vcm.inspect_video_frames(made[name], **EXPECT)
    assert code in _codes(result), result
    flag = next(f for f in result["flags"] if f["code"] == code)
    assert flag["message"].startswith(vcm._FLAG_TEXT[code])


def test_washed_out_clip_also_reads_as_colourless(made):
    assert "desaturated" in _codes(vcm.inspect_video_frames(made["washed_out"], **EXPECT))


def test_a_black_tile_is_not_confused_with_a_dark_scene(made):
    codes = _codes(vcm.inspect_video_frames(made["black_frames"], **EXPECT))
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
    assert quality["flagged"] and {"washed_out", "desaturated"} <= set(quality["flag_reasons"])
    saved = json.loads((batch_dir / "batch_metadata.json").read_text())
    assert saved["results"][0]["metadata"]["quality"]["flags"][0]["code"] == "washed_out"


# ── What MCP and chat read ───────────────────────────────────────────────────

def test_status_text_names_the_flags(monkeypatch):
    from backend.tools import image_tools

    quality = {"flagged": True, "frames": {"readable": True},
               "flags": [{"code": "black_tiles", "message": "black tiles (NaN latents decode as black rectangles): in 3 of 9 sampled frames"}]}
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
    assert "Quality: flagged — black tiles (NaN latents decode as black rectangles)" in out.output
    assert "Quality: no problems found in the sampled frames" in out.output
    assert out.metadata["files"][0]["quality"]["flags"][0]["code"] == "black_tiles"
