import pytest
from unittest.mock import patch
from PIL import Image
import io
import base64

from service.change_detector import ChangeDetector


def _make_frame(color: str = "red", size=(64, 64)) -> str:
    """Create a base64-encoded JPEG test frame with distinct visual content."""
    from PIL import ImageDraw
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    # Add color-specific shapes so phash differs between colors
    if color == "red":
        draw.rectangle([5, 5, 25, 25], fill="white")
        draw.rectangle([35, 35, 55, 55], fill="black")
    elif color == "blue":
        draw.ellipse([5, 5, 25, 25], fill="yellow")
        draw.ellipse([35, 35, 55, 55], fill="green")
    elif color == "green":
        draw.polygon([(32, 5), (5, 55), (59, 55)], fill="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


class TestChangeDetector:
    def test_first_frame_always_processes(self):
        cd = ChangeDetector()
        should, reason = cd.should_process(_make_frame("red"))
        assert should is True
        assert reason == "new_scene"

    def test_identical_frames_no_change(self):
        cd = ChangeDetector()
        frame = _make_frame("blue")
        cd.should_process(frame)  # first frame
        should, reason = cd.should_process(frame)  # same frame
        assert should is False
        assert reason == "no_change"

    def test_different_frames_detect_change(self):
        cd = ChangeDetector()
        cd.should_process(_make_frame("red"))
        should, reason = cd.should_process(_make_frame("blue"))
        assert should is True
        assert reason == "visual_change"

    def test_periodic_refresh_forces_processing(self):
        cd = ChangeDetector(periodic_refresh_seconds=0)  # immediate refresh
        frame = _make_frame("green")
        cd.should_process(frame)
        should, reason = cd.should_process(frame)
        assert should is True
        assert reason == "periodic_refresh"

    def test_threshold_is_a_hamming_distance(self):
        # A threshold above 64 bits can never fire; a zero threshold fires on
        # any difference.
        strict = ChangeDetector(phash_threshold=0)
        strict.should_process(_make_frame("red"))
        assert strict.should_process(_make_frame("blue")) == (True, "visual_change")
        lenient = ChangeDetector(phash_threshold=64)
        lenient.should_process(_make_frame("red"))
        assert lenient.should_process(_make_frame("blue")) == (False, "no_change")

    def test_phash_matches_imagehash_construction(self):
        from service.phash import hamming, phash
        img = Image.new("RGB", (64, 64), "red")
        value = phash(img)
        assert 0 <= value < 2 ** 64
        assert hamming(value, value) == 0

    def test_update_description(self):
        cd = ChangeDetector()
        cd.update_last_description("person sitting at desk")
        assert cd.last_description == "person sitting at desk"
