#!/usr/bin/env python3
"""
Grid Overlay — Draws labeled grids on screenshots for coordinate identification.

The grid divides the screen into labeled cells (A1-H8, chess notation).
The vision model identifies which cell(s) contain target elements, then
sub-cell refinement narrows to a 3x3 position within the cell.

Effective precision: 8x8 grid * 3x3 sub-cell = 24x24 (576 positions).
"""

import logging
from typing import Dict, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Column labels
COL_LABELS = "ABCDEFGHIJKLMNOP"


def create_grid_spec(
    width: int,
    height: int,
    cols: int = 8,
    rows: int = 8
) -> Dict[str, Dict]:
    """
    Create a grid specification mapping cell labels to pixel coordinates.

    Args:
        width: Screenshot width in pixels
        height: Screenshot height in pixels
        cols: Number of columns (default 8)
        rows: Number of rows (default 8)

    Returns:
        Dict mapping cell labels (e.g., "A1") to:
            center: (x, y) center pixel coordinates
            bounds: (x1, y1, x2, y2) bounding box
    """
    cell_w = width // cols
    cell_h = height // rows
    spec = {}

    for col in range(cols):
        for row in range(rows):
            label = f"{COL_LABELS[col]}{row + 1}"
            x1 = col * cell_w
            y1 = row * cell_h
            x2 = x1 + cell_w if col < cols - 1 else width
            y2 = y1 + cell_h if row < rows - 1 else height
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            spec[label] = {
                "center": (cx, cy),
                "bounds": (x1, y1, x2, y2),
            }

    return spec


def overlay_grid(
    screenshot: Image.Image,
    cols: int = 8,
    rows: int = 8,
    line_color: Tuple[int, int, int, int] = (255, 255, 0, 128),
    label_color: Tuple[int, int, int, int] = (255, 255, 0, 200),
) -> Tuple[Image.Image, Dict[str, Dict]]:
    """
    Draw a labeled grid overlay on a screenshot.

    Args:
        screenshot: RGB PIL Image
        cols: Number of columns
        rows: Number of rows
        line_color: RGBA color for grid lines
        label_color: RGBA color for cell labels

    Returns:
        Tuple of (RGB image with grid, grid spec dict)
    """
    width, height = screenshot.size
    spec = create_grid_spec(width, height, cols, rows)
    cell_w = width // cols
    cell_h = height // rows

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw vertical lines
    for col in range(1, cols):
        x = col * cell_w
        draw.line([(x, 0), (x, height)], fill=line_color, width=1)

    # Draw horizontal lines
    for row in range(1, rows):
        y = row * cell_h
        draw.line([(0, y), (width, y)], fill=line_color, width=1)

    # Draw cell labels
    font_size = max(10, min(cell_w, cell_h) // 8)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except (IOError, OSError):
        font = ImageFont.load_default()

    for label, cell in spec.items():
        x1, y1 = cell["bounds"][0], cell["bounds"][1]
        draw.text((x1 + 2, y1 + 2), label, fill=label_color, font=font)

    # Composite overlay onto screenshot
    result = screenshot.copy().convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB"), spec


def crop_grid_cell(
    screenshot: Image.Image,
    cell_label: str,
    spec: Dict[str, Dict]
) -> Image.Image:
    """
    Crop a single grid cell from the screenshot (pre-grid, original image).

    Args:
        screenshot: Original RGB screenshot (without grid overlay)
        cell_label: Cell label (e.g., "D4")
        spec: Grid spec from create_grid_spec()

    Returns:
        Cropped RGB PIL Image of the cell
    """
    cell = spec[cell_label]
    return screenshot.crop(cell["bounds"])


def refine_coordinates(
    cell_label: str,
    position: str,
    spec: Dict[str, Dict]
) -> Tuple[int, int]:
    """
    Refine click coordinates within a cell using sub-cell position.

    The cell is divided into a 3x3 sub-grid:
        top-left     top-center     top-right
        center-left  center         center-right
        bottom-left  bottom-center  bottom-right

    Args:
        cell_label: Cell label (e.g., "A1")
        position: Sub-cell position string (e.g., "top-left", "center")
        spec: Grid spec from create_grid_spec()

    Returns:
        (x, y) refined pixel coordinates
    """
    cell = spec[cell_label]
    x1, y1, x2, y2 = cell["bounds"]
    w = x2 - x1
    h = y2 - y1

    # 3x3 sub-grid: each sub-cell is w/3 by h/3
    # Position centers within each third
    position_map = {
        "top-left":      (x1 + w // 6,     y1 + h // 6),
        "top-center":    (x1 + w // 2,      y1 + h // 6),
        "top-right":     (x1 + 5 * w // 6,  y1 + h // 6),
        "center-left":   (x1 + w // 6,      y1 + h // 2),
        "center":        (x1 + w // 2,      y1 + h // 2),
        "center-right":  (x1 + 5 * w // 6,  y1 + h // 2),
        "bottom-left":   (x1 + w // 6,      y1 + 5 * h // 6),
        "bottom-center": (x1 + w // 2,      y1 + 5 * h // 6),
        "bottom-right":  (x1 + 5 * w // 6,  y1 + 5 * h // 6),
    }

    coords = position_map.get(position.lower().strip())
    if coords is None:
        logger.warning(f"Unknown sub-cell position '{position}', falling back to cell center")
        coords = cell["center"]

    return coords
