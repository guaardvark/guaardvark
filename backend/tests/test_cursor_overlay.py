#!/usr/bin/env python3

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestCursorOverlay(unittest.TestCase):

    def test_generate_bullseye_returns_rgba_image(self):
        from backend.utils.cursor_overlay import generate_bullseye
        img = generate_bullseye(size=48)
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.size, (48, 48))

    def test_generate_bullseye_has_transparency(self):
        from backend.utils.cursor_overlay import generate_bullseye
        img = generate_bullseye(size=48)
        # Center pixel should be transparent (the hole)
        center = img.getpixel((24, 24))
        self.assertEqual(center[3], 0, "Center should be fully transparent")

    def test_generate_bullseye_has_opaque_ring(self):
        from backend.utils.cursor_overlay import generate_bullseye
        img = generate_bullseye(size=48)
        # A pixel on the outer ring should be opaque
        # The outer ring is at radius ~20 from center (48/2 - 4 = 20)
        ring_pixel = img.getpixel((24, 4))  # Top of ring
        self.assertEqual(ring_pixel[3], 255, "Ring pixel should be opaque")

    def test_generate_bullseye_caches(self):
        from backend.utils.cursor_overlay import generate_bullseye
        img1 = generate_bullseye(size=48)
        img2 = generate_bullseye(size=48)
        self.assertIs(img1, img2, "Same size should return cached instance")

    def test_generate_bullseye_different_sizes(self):
        from backend.utils.cursor_overlay import generate_bullseye
        img48 = generate_bullseye(size=48)
        img64 = generate_bullseye(size=64)
        self.assertIsNot(img48, img64)
        self.assertEqual(img64.size, (64, 64))

    def test_composite_bullseye_on_screenshot(self):
        from backend.utils.cursor_overlay import composite_bullseye
        from PIL import Image
        # Create a 200x200 red screenshot
        screenshot = Image.new("RGB", (200, 200), color=(255, 0, 0))
        cursor_pos = (100, 100)
        result = composite_bullseye(screenshot, cursor_pos, size=48)
        # Result should be RGB (composited back)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (200, 200))
        # The center should still be red (transparent hole)
        center = result.getpixel((100, 100))
        self.assertEqual(center, (255, 0, 0))

    def test_composite_bullseye_clamps_to_edges(self):
        from backend.utils.cursor_overlay import composite_bullseye
        from PIL import Image
        screenshot = Image.new("RGB", (100, 100), color=(0, 0, 255))
        # Cursor at edge — should not crash
        result = composite_bullseye(screenshot, (5, 5), size=48)
        self.assertEqual(result.size, (100, 100))

    def test_composite_bullseye_cursor_at_origin(self):
        from backend.utils.cursor_overlay import composite_bullseye
        from PIL import Image
        screenshot = Image.new("RGB", (100, 100), color=(0, 255, 0))
        result = composite_bullseye(screenshot, (0, 0), size=48)
        self.assertEqual(result.size, (100, 100))


if __name__ == "__main__":
    unittest.main()
