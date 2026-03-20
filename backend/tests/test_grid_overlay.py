#!/usr/bin/env python3

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestGridSpec(unittest.TestCase):

    def test_create_grid_spec_8x8(self):
        from backend.utils.grid_overlay import create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        self.assertEqual(len(spec), 64)
        self.assertIn("A1", spec)
        self.assertIn("H8", spec)

    def test_grid_spec_cell_center_A1(self):
        from backend.utils.grid_overlay import create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        cell = spec["A1"]
        # A1 is top-left cell. Cell width = 1920/8 = 240, height = 1080/8 = 135
        # Center should be at (120, 67)
        self.assertEqual(cell["center"], (120, 67))
        self.assertEqual(cell["bounds"], (0, 0, 240, 135))

    def test_grid_spec_cell_center_H8(self):
        from backend.utils.grid_overlay import create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        cell = spec["H8"]
        # H8 is bottom-right cell
        self.assertEqual(cell["center"], (1800, 1012))
        self.assertEqual(cell["bounds"], (1680, 945, 1920, 1080))

    def test_grid_spec_columns_labeled_A_through_H(self):
        from backend.utils.grid_overlay import create_grid_spec
        spec = create_grid_spec(800, 600, cols=8, rows=8)
        col_labels = sorted(set(k[0] for k in spec.keys()))
        self.assertEqual(col_labels, ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'])

    def test_grid_spec_rows_labeled_1_through_8(self):
        from backend.utils.grid_overlay import create_grid_spec
        spec = create_grid_spec(800, 600, cols=8, rows=8)
        row_labels = sorted(set(k[1] for k in spec.keys()))
        self.assertEqual(row_labels, ['1', '2', '3', '4', '5', '6', '7', '8'])


class TestGridOverlay(unittest.TestCase):

    def test_overlay_grid_returns_rgb_image(self):
        from backend.utils.grid_overlay import overlay_grid
        from PIL import Image
        screenshot = Image.new("RGB", (800, 600), color=(128, 128, 128))
        result, spec = overlay_grid(screenshot, cols=8, rows=8)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (800, 600))
        self.assertEqual(len(spec), 64)

    def test_overlay_grid_draws_lines(self):
        from backend.utils.grid_overlay import overlay_grid
        from PIL import Image
        screenshot = Image.new("RGB", (800, 600), color=(128, 128, 128))
        result, _ = overlay_grid(screenshot, cols=8, rows=8)
        # The grid line at x=100 (800/8=100) should differ from input
        original_pixel = screenshot.getpixel((100, 300))
        grid_pixel = result.getpixel((100, 300))
        self.assertNotEqual(original_pixel, grid_pixel, "Grid line should be visible")


class TestCropGridCell(unittest.TestCase):

    def test_crop_grid_cell_returns_correct_size(self):
        from backend.utils.grid_overlay import crop_grid_cell, create_grid_spec
        from PIL import Image
        screenshot = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        crop = crop_grid_cell(screenshot, "A1", spec)
        self.assertEqual(crop.size, (240, 135))

    def test_crop_grid_cell_D4(self):
        from backend.utils.grid_overlay import crop_grid_cell, create_grid_spec
        from PIL import Image
        screenshot = Image.new("RGB", (1920, 1080), color=(50, 50, 50))
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        crop = crop_grid_cell(screenshot, "D4", spec)
        self.assertEqual(crop.size, (240, 135))


class TestSubCellRefinement(unittest.TestCase):

    def test_refine_coordinates_center(self):
        from backend.utils.grid_overlay import refine_coordinates, create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        coords = refine_coordinates("A1", "center", spec)
        # A1 center = (120, 67), same as cell center
        self.assertEqual(coords, (120, 67))

    def test_refine_coordinates_top_left(self):
        from backend.utils.grid_overlay import refine_coordinates, create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        coords = refine_coordinates("A1", "top-left", spec)
        # A1 bounds = (0, 0, 240, 135), top-left quadrant center = (40, 22)
        self.assertEqual(coords, (40, 22))

    def test_refine_coordinates_bottom_right(self):
        from backend.utils.grid_overlay import refine_coordinates, create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        coords = refine_coordinates("A1", "bottom-right", spec)
        # A1 bounds = (0, 0, 240, 135), bottom-right = (200, 112)
        self.assertEqual(coords, (200, 112))

    def test_refine_coordinates_fallback(self):
        from backend.utils.grid_overlay import refine_coordinates, create_grid_spec
        spec = create_grid_spec(1920, 1080, cols=8, rows=8)
        coords = refine_coordinates("A1", "unknown_position", spec)
        # Should fall back to cell center
        self.assertEqual(coords, (120, 67))


if __name__ == "__main__":
    unittest.main()
