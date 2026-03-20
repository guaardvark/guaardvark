#!/usr/bin/env python3
"""
Cursor Overlay — Generates and composites a bullseye reticle onto screenshots.

The bullseye is composited at the cursor position before vision analysis so
the vision model can always identify where the cursor is. This is applied on
the master side — the client never knows about it.

Design: sniper scope reticle with:
- Outer ring (black stroke + white inner)
- Inner ring (black stroke + white inner)
- Crosshair lines (N/S/E/W with gap at center)
- Transparent center hole so target pixel shows through
"""

import logging
from typing import Dict, Tuple

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# Cache generated bullseyes by size
_bullseye_cache: Dict[int, Image.Image] = {}


def generate_bullseye(size: int = 48) -> Image.Image:
    """
    Generate a bullseye reticle image with transparent background.

    Args:
        size: Diameter in pixels (default 48)

    Returns:
        RGBA PIL Image of the bullseye
    """
    if size in _bullseye_cache:
        return _bullseye_cache[size]

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    center = size // 2
    outer_stroke = max(3, size // 16)
    inner_stroke = max(2, size // 24)
    hole_radius = max(4, size // 12)

    # Outer ring — black stroke
    outer_radius = center - 2
    draw.ellipse(
        [center - outer_radius, center - outer_radius,
         center + outer_radius, center + outer_radius],
        outline=(0, 0, 0, 255), width=outer_stroke
    )
    # Outer ring — white inner stroke
    draw.ellipse(
        [center - outer_radius + outer_stroke, center - outer_radius + outer_stroke,
         center + outer_radius - outer_stroke, center + outer_radius - outer_stroke],
        outline=(255, 255, 255, 255), width=inner_stroke
    )

    # Inner ring — black stroke
    inner_radius = center // 2
    draw.ellipse(
        [center - inner_radius, center - inner_radius,
         center + inner_radius, center + inner_radius],
        outline=(0, 0, 0, 255), width=outer_stroke
    )
    # Inner ring — white inner stroke
    draw.ellipse(
        [center - inner_radius + outer_stroke, center - inner_radius + outer_stroke,
         center + inner_radius - outer_stroke, center + inner_radius - outer_stroke],
        outline=(255, 255, 255, 255), width=inner_stroke
    )

    # Crosshair lines (with gap around center for the hole)
    gap = hole_radius + 2
    line_width = max(2, size // 24)

    # North
    draw.line([(center, 0), (center, center - gap)],
              fill=(0, 0, 0, 255), width=line_width)
    # South
    draw.line([(center, center + gap), (center, size)],
              fill=(0, 0, 0, 255), width=line_width)
    # West
    draw.line([(0, center), (center - gap, center)],
              fill=(0, 0, 0, 255), width=line_width)
    # East
    draw.line([(center + gap, center), (size, center)],
              fill=(0, 0, 0, 255), width=line_width)

    # Clear center hole — make transparent
    draw.ellipse(
        [center - hole_radius, center - hole_radius,
         center + hole_radius, center + hole_radius],
        fill=(0, 0, 0, 0)
    )

    _bullseye_cache[size] = img
    logger.debug(f"Generated bullseye reticle: {size}x{size}px")
    return img


def composite_bullseye(
    screenshot: Image.Image,
    cursor_pos: Tuple[int, int],
    size: int = 48
) -> Image.Image:
    """
    Composite the bullseye reticle onto a screenshot at cursor position.

    Args:
        screenshot: RGB PIL Image of the screen
        cursor_pos: (x, y) pixel coordinates of cursor
        size: Bullseye diameter in pixels

    Returns:
        RGB PIL Image with bullseye composited
    """
    bullseye = generate_bullseye(size)
    result = screenshot.copy().convert("RGBA")
    half = size // 2
    paste_x = cursor_pos[0] - half
    paste_y = cursor_pos[1] - half
    sw, sh = screenshot.size

    # Crop bullseye to fit within screenshot bounds
    bx1 = max(0, -paste_x)
    by1 = max(0, -paste_y)
    bx2 = min(size, sw - paste_x)
    by2 = min(size, sh - paste_y)
    if bx1 >= bx2 or by1 >= by2:
        return result.convert("RGB")  # Cursor entirely off-screen

    cropped = bullseye.crop((bx1, by1, bx2, by2))
    result.paste(cropped, (max(0, paste_x), max(0, paste_y)), cropped)
    return result.convert("RGB")
