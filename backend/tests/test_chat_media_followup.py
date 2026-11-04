"""A video queued from chat lands in the reply that queued it, and an
image-to-video clip takes the picture's shape."""
from types import SimpleNamespace

from PIL import Image

from backend.services import chat_media_followup as followup


def _status(state, results=(), error=None):
    return SimpleNamespace(batch_id="B1", status=state, results=list(results), error=error, error_kind=None)


def _result(**kw):
    base = dict(success=True, video_path="i/videos/a.mp4", thumbnail_path="i/thumbnails/a.jpg",
                error=None, error_kind=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_only_a_queued_video_is_pending():
    queued = {"queued": True, "batch_id": "B1", "prompt": "rain"}
    assert followup.pending_entry("generate_video", queued)["batch_id"] == "B1"
    assert followup.pending_entry("generate_image", queued) is None
    assert followup.pending_entry("generate_video", {"batch_id": "B1", "video_url": "/v.mp4"}) is None


def test_a_running_batch_settles_nothing():
    assert followup._settle(_status("running")) is None
    assert followup._settle(None) is None


def test_a_finished_batch_becomes_the_reply_clip():
    settled = followup._settle(_status("completed", [_result()]))
    assert settled["media"][0]["url"] == "/api/batch-video/video/B1/i/videos/a.mp4"
    assert settled["media"][0]["poster"].endswith("i/thumbnails/a.jpg")
    extra = followup._apply({"steps": [], "pendingMedia": [{"batch_id": "B1"}]}, "B1", settled)
    assert "pendingMedia" not in extra
    assert extra["generatedImages"] == settled["media"]


def test_a_failed_batch_keeps_its_reason_on_the_reply():
    settled = followup._settle(_status("error", [_result(success=False, video_path=None, error="CUDA out of memory")]))
    extra = followup._apply({"pendingMedia": [{"batch_id": "B1"}]}, "B1", settled)
    assert extra["pendingMedia"][0]["status"] == "failed"
    assert extra["pendingMedia"][0]["error"]


def test_a_square_picture_gets_a_square_wan_clip(tmp_path):
    from backend.tools.image_tools import _frame_for_picture

    square, wide = tmp_path / "sq.png", tmp_path / "wide.png"
    Image.new("RGB", (1024, 1024)).save(square)
    Image.new("RGB", (1920, 1080)).save(wide)
    assert _frame_for_picture(str(square), "wan22-5b") == (992, 992, "1:1")
    assert _frame_for_picture(str(wide), "wan22-5b") is None
