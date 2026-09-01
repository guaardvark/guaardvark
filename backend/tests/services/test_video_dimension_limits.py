#!/usr/bin/env python3
"""Video dimension guard rails (2026-08-08, aspect-selector regression).

Full HD + Square on the video page sent 1920×1920 (3.7 MPx) to the Wan
workflows — both the 5B and 14B ran until the watchdog timeout, which read as
"the aspect selector is broken". The frontend now caps pixel area per model,
and the backend mirrors it here as defense-in-depth because batch retry_data
replays old width/height verbatim.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator  # noqa: E402


class TestClampPixelArea(unittest.TestCase):

    def test_proven_wan_dims_pass_untouched(self):
        # 1280×736 completed 22/22 on wan22-5b — must never be scaled down
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(1280, 736, "wan22-5b"),
            (1280, 736),
        )
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(832, 480, "wan22-14b-i2v"),
            (832, 480),
        )

    def test_fullhd_square_is_scaled_down(self):
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "wan22-5b")
        self.assertLessEqual(w * h, 1_050_000)
        # aspect preserved (square stays square)
        self.assertEqual(w, h)

    def test_fullhd_widescreen_preserves_aspect(self):
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1080, "wan22-14b")
        self.assertLessEqual(w * h, 1_050_000)
        self.assertAlmostEqual(w / h, 1920 / 1080, places=2)

    def test_ltx_budget(self):
        # LTX native (and the frontend's aspect-refit dims) stay untouched
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(768, 512, "ltx23-distilled-fp8"),
            (768, 512),
        )
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "ltx23-distilled-fp8")
        self.assertLessEqual(w * h, 1_050_000)

    def test_ltx25_budget(self):
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(768, 512, "ltx25-distilled-int8"),
            (768, 512),
        )
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "ltx25-distilled-int8")
        self.assertLessEqual(w * h, 1_050_000)

    def test_minimax_budget_is_the_native_canvas(self):
        # 864×480 (template default) and the native 1344×768 both pass; a
        # 1920×1920 request is pulled under the 768×1344 canvas cap.
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(864, 480, "minimax-h3-int8"),
            (864, 480),
        )
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(1344, 768, "minimax-h3-int8"),
            (1344, 768),
        )
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "minimax-h3-int8")
        self.assertLessEqual(w * h, 768 * 1344)

    def test_unbudgeted_family_is_untouched(self):
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "cogvideox-5b"),
            (1920, 1920),
        )

    def test_clamp_then_align_stays_within_budget(self):
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "wan22-5b")
        w, h = ComfyUIVideoGenerator._align_dimensions(w, h, "wan22-5b")
        self.assertEqual(w % 32, 0)
        self.assertEqual(h % 32, 0)
        # alignment rounding must not blow meaningfully past the cap
        self.assertLessEqual(w * h, 1_100_000)


if __name__ == "__main__":
    unittest.main()


def test_h3_longer_clips_are_capped_at_their_measured_tier():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator as G
    # 5 s keeps the native canvas; 10 s and 15 s tiers were measured at 480p.
    assert G._clamp_pixel_area(1344, 768, "minimax-h3-int8", 124) == (1344, 768)
    w, h = G._clamp_pixel_area(1344, 768, "minimax-h3-int8", 243)
    assert w * h <= 864 * 480 and abs(w / h - 1344 / 768) < 0.02
    assert G._clamp_pixel_area(864, 480, "minimax-h3-int8", 362) == (864, 480)
    assert G._clamp_pixel_area(1344, 768, "minimax-h3-int8") == (1344, 768)
