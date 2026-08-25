"""A model must not be handed an aspect ratio it cannot render.

The UI no longer offers one, but /api/batch-video still accepts whatever it is
sent and old batch retry_data replays stored dimensions verbatim.

What counts as unrenderable has to come from evidence. Wan was originally declared
landscape-and-transpose only, on the grounds that a square frame "comes back
warped" -- but the output directory holds seven 1:1 Wan I2V renders (512x512 and
736x736), one of them the project's own demo. Square is native enough; the clamp
now guards genuinely off-ratio requests such as 4:3 and 3:2.
"""
from __future__ import annotations

import pytest

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator as G


def _ratio(width: int, height: int) -> float:
    return width / height


@pytest.mark.parametrize("model", ["wan22-5b", "wan22-14b", "wan22-14b-i2v"])
def test_wan_declares_landscape_its_transpose_and_square(model):
    assert G._supported_aspect_ratios(model) == ["16:9", "9:16", "1:1"]


@pytest.mark.parametrize("model", ["wan22-5b", "wan22-14b", "wan22-14b-i2v"])
@pytest.mark.parametrize("width,height", [(512, 512), (736, 736)])
def test_square_is_rendered_not_reshaped(model, width, height):
    """Both sizes are taken from real Wan I2V output; square renders fine."""
    assert G._clamp_aspect_ratio(width, height, model) == (width, height)


def test_a_model_declaring_nothing_is_left_alone():
    assert G._supported_aspect_ratios("cogvideox-5b") == []
    assert G._clamp_aspect_ratio(1024, 1024, "cogvideox-5b") == (1024, 1024)


@pytest.mark.parametrize(
    "width,height",
    [(1280, 704), (1280, 736), (1344, 736), (736, 416), (704, 1280), (736, 1280), (512, 288)],
)
def test_supported_frames_pass_through_untouched(width, height):
    """Includes native 1280x704, which a 32px snap leaves 2.3% off exact 16:9."""
    assert G._clamp_aspect_ratio(width, height, "wan22-5b") == (width, height)


@pytest.mark.parametrize("width,height", [(1280, 960), (1200, 800)])
def test_unsupported_frames_are_reshaped(width, height):
    new_w, new_h = G._clamp_aspect_ratio(width, height, "wan22-5b")
    assert (new_w, new_h) != (width, height)
    assert new_w != new_h, "a square frame must never survive the clamp"


def test_reshaping_preserves_pixel_area():
    """Area is the VRAM/compute budget — the clamp reshapes, it does not resize."""
    new_w, new_h = G._clamp_aspect_ratio(1280, 960, "wan22-5b")
    assert new_w * new_h == pytest.approx(1280 * 960, rel=0.01)


def test_it_snaps_within_the_requested_orientation():
    """Orientation is what a caller notices, so it wins over raw ratio distance.

    4:3 sits exactly as far from 16:9 as from 1:1, so choosing purely by distance
    let a landscape request come back square.
    """
    landscape = G._clamp_aspect_ratio(1280, 960, "wan22-5b")      # 4:3, landscape
    portrait = G._clamp_aspect_ratio(960, 1280, "wan22-5b")       # 3:4, portrait
    assert _ratio(*landscape) > 1, "a landscape request must stay landscape"
    assert _ratio(*portrait) < 1, "a portrait request must stay portrait"


def test_degenerate_dimensions_do_not_raise():
    assert G._clamp_aspect_ratio(0, 0, "wan22-5b") == (0, 0)
    assert G._clamp_aspect_ratio(-1, 100, "wan22-5b") == (-1, 100)
