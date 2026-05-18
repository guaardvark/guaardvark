"""ffmpeg drawtext-based video text overlay.

Wraps `ffmpeg -vf drawtext=...` so the API/UI layer doesn't have to know
about ffmpeg filter-graph escaping rules. Single text element per call;
chain calls if you need multiple. Output is a new file; we never overwrite
the input.

Why not write the filter expression on the frontend? Because escaping
ffmpeg's drawtext is genuinely awful — single quotes, colons, commas,
and backslashes all need careful handling, and the rules differ between
the text= argument and the surrounding -vf chain. Doing it server-side
in one place beats getting bug reports about apostrophes breaking the
overlay.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Default font on Debian/Ubuntu/Mint — confirmed available at install time
# (`fc-list :lang=en family file` showed DejaVuSans-Bold.ttf in the standard
# location). If we ever ship to a distro without it, this needs a fallback.
_DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Named positions → (x, y) ffmpeg drawtext expressions. The text_w / text_h
# variables are populated by ffmpeg at filter eval time. 20px margin keeps
# text off the very edge so it survives YouTube/social-media safe-area crops.
_POSITION_EXPRESSIONS = {
    "top-left":      ("20",                "20"),
    "top-center":    ("(w-text_w)/2",      "20"),
    "top-right":     ("w-text_w-20",       "20"),
    "middle-left":   ("20",                "(h-text_h)/2"),
    "center":        ("(w-text_w)/2",      "(h-text_h)/2"),
    "middle-right":  ("w-text_w-20",       "(h-text_h)/2"),
    "bottom-left":   ("20",                "h-text_h-20"),
    "bottom-center": ("(w-text_w)/2",      "h-text_h-20"),
    "bottom-right":  ("w-text_w-20",       "h-text_h-20"),
}


class VideoOverlayError(RuntimeError):
    """ffmpeg refused to encode, or the input wasn't a usable video."""


def _ffmpeg_escape_text(text: str) -> str:
    """Escape user text for use inside drawtext's `text='...'` argument.

    drawtext's text parser treats `:`, `'`, `\\`, and `,` specially when
    they appear inside the value. Backslash-escape them, then wrap in
    single quotes when we build the filter string. We strip control chars
    while we're at it — the user can put a `\\n` in the field for newlines.
    """
    if text is None:
        return ""
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    # Order matters: backslash first, otherwise we double-escape later ones.
    cleaned = cleaned.replace("\\", "\\\\")
    cleaned = cleaned.replace("'", r"\'")
    cleaned = cleaned.replace(":", r"\:")
    cleaned = cleaned.replace(",", r"\,")
    cleaned = cleaned.replace("%", r"\%")
    return cleaned


def _validate_color(color: str) -> str:
    """Allow ffmpeg color names + #rrggbb/#rrggbbaa hex; reject anything else.

    ffmpeg accepts a wide vocabulary (`white`, `red`, `0xRRGGBB`, etc.) but
    we lock the input down so a stray quote can't break the filter chain.
    Default fallback is white — safer than silently passing invalid input.
    """
    if not color:
        return "white"
    if re.fullmatch(r"[a-zA-Z]{3,20}(@[01](?:\.\d+)?)?", color):
        return color
    if re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", color):
        return color.replace("#", "0x")
    logger.warning("Rejecting unknown color literal %r — falling back to white", color)
    return "white"


def add_text_to_video(
    input_path: Path,
    output_path: Path,
    text: str,
    font_size: int = 48,
    font_color: str = "white",
    position: str = "bottom-center",
    border: bool = True,
    border_width: int = 2,
    border_color: str = "black",
    box_background: bool = False,
    box_color: str = "black@0.5",
    box_border_width: int = 10,
    timeout_s: int = 300,
    font_path: Optional[str] = None,
) -> None:
    """Burn a single text element into a video via ffmpeg drawtext.

    Args:
        input_path: existing video file (must be a path ffmpeg can decode)
        output_path: where the new file goes (parent dir auto-created)
        text: the text to overlay (any printable string; escaped server-side)
        font_size: pixel height of the text
        font_color: ffmpeg color name or #rrggbb / #rrggbbaa hex
        position: one of the keys in _POSITION_EXPRESSIONS (e.g. bottom-center)
        border: if True, adds an outline so the text reads against any background
        border_width / border_color: outline tuning when border=True
        box_background: if True, draws a translucent box behind the text
        box_color / box_border_width: tune the background box when enabled
        timeout_s: ffmpeg subprocess timeout
        font_path: override the default DejaVu font

    Raises:
        VideoOverlayError on bad input, missing tools, ffmpeg failure, or timeout.
    """
    if shutil.which("ffmpeg") is None:
        raise VideoOverlayError("ffmpeg not found on PATH; cannot overlay text")
    if not input_path.is_file():
        raise VideoOverlayError(f"Input video not found: {input_path}")
    if not text or not text.strip():
        raise VideoOverlayError("Empty text — nothing to overlay")

    pos_key = position if position in _POSITION_EXPRESSIONS else "bottom-center"
    if pos_key != position:
        logger.warning("Unknown position %r; defaulting to bottom-center", position)
    x_expr, y_expr = _POSITION_EXPRESSIONS[pos_key]

    chosen_font = font_path or _DEFAULT_FONT
    if not Path(chosen_font).is_file():
        raise VideoOverlayError(
            f"Font file not found: {chosen_font}. "
            "Install fonts-dejavu-core or pass an explicit font_path."
        )

    # Build the drawtext filter argument. Each k=v pair is colon-separated;
    # the text value is single-quoted because it contains arbitrary user input.
    parts = [
        f"fontfile={chosen_font}",
        f"text='{_ffmpeg_escape_text(text)}'",
        f"fontsize={int(font_size)}",
        f"fontcolor={_validate_color(font_color)}",
        f"x={x_expr}",
        f"y={y_expr}",
    ]
    if border:
        parts.append(f"borderw={int(border_width)}")
        parts.append(f"bordercolor={_validate_color(border_color)}")
    if box_background:
        parts.append("box=1")
        parts.append(f"boxcolor={_validate_color(box_color)}")
        parts.append(f"boxborderw={int(box_border_width)}")

    drawtext_filter = "drawtext=" + ":".join(parts)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",                    # overwrite if output_path already exists
        "-i", str(input_path),
        "-vf", drawtext_filter,
        # Re-encode video; copy audio as-is so we don't gratuitously transcode.
        # libx264 + yuv420p keeps the result broadly compatible (browsers,
        # mobile, social) without dragging in NVENC config.
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info(
        "Running ffmpeg drawtext: input=%s output=%s text=%r position=%s size=%d",
        input_path, output_path, text[:40], pos_key, font_size,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        raise VideoOverlayError(f"ffmpeg timed out after {timeout_s}s") from e

    if result.returncode != 0:
        # Trim stderr — ffmpeg dumps a lot of header noise before the actual
        # error line. Last ~25 lines almost always contain the cause.
        tail = "\n".join(result.stderr.strip().splitlines()[-25:])
        logger.error("ffmpeg exit %d:\n%s", result.returncode, tail)
        raise VideoOverlayError(
            f"ffmpeg returned {result.returncode}. "
            f"Last lines:\n{tail}"
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise VideoOverlayError(
            f"ffmpeg succeeded but output is missing or empty: {output_path}"
        )

    logger.info("ffmpeg drawtext wrote %s (%d bytes)", output_path, output_path.stat().st_size)
