"""Video editor timeline → ffmpeg filter_complex.

Converts a TimelineState dict from the Video Editor frontend into a single
ffmpeg invocation that produces the final mp4. Lives separately from
video_text_overlay.py because the timeline render needs a richer filter
graph (trim, multi-element drawtext with time ranges, optional audio
replacement) where the simple overlay path stays purpose-built for one
text element + no trim.

Per plans/2026-04-29-video-editor.md §5.3.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.services.video_text_overlay import (
    _DEFAULT_FONT,
    _ffmpeg_escape_text,
    _validate_color,
    VideoOverlayError,
)

logger = logging.getLogger(__name__)

# Names for the input streams in the filter_complex graph. [0] is video,
# [1] (when present) is the audio overlay track.
_VIDEO_INPUT_LABEL = "0:v"
_VIDEO_AUDIO_LABEL = "0:a"
_AUDIO_OVERLAY_LABEL = "1:a"


def _build_drawtext_filter(text_el: dict, label_in: str, label_out: str) -> str:
    """One drawtext per text element. Non-rotation only for v1; rotation is
    a §10 decision in the editor plan (overlay-PNG render path, deferred)."""
    text = text_el.get("text") or ""
    if not text.strip():
        return None
    parts = [
        f"fontfile={_DEFAULT_FONT}",
        f"text='{_ffmpeg_escape_text(text)}'",
        f"fontsize={int(text_el.get('fontSize', 48))}",
        f"fontcolor={_validate_color(text_el.get('fontColor', 'white'))}",
        f"x={int(text_el.get('x', 320))}",
        f"y={int(text_el.get('y', 240))}",
        "borderw=2",
        "bordercolor=black",
    ]
    start = text_el.get("startSeconds")
    end = text_el.get("endSeconds")
    if start is not None and end is not None:
        parts.append(f"enable='between(t,{float(start)},{float(end)})'")
    elif start is not None:
        parts.append(f"enable='gte(t,{float(start)})'")
    drawtext = "drawtext=" + ":".join(parts)
    return f"[{label_in}]{drawtext}[{label_out}]"


def render_timeline(
    *,
    video_input_path: Path,
    output_path: Path,
    text_elements: list[dict],
    video_trim_start: float | None = None,
    video_trim_end: float | None = None,
    audio_input_path: Path | None = None,
    audio_volume: float = 1.0,
    timeout_s: int = 600,
) -> None:
    """Render a timeline to mp4 via a single ffmpeg invocation.

    Layout of the filter_complex graph:
        [0:v] [trim if needed] -> v0
        v0 -> [drawtext element 1] -> v1
        v1 -> [drawtext element 2] -> v2
        ...
        vN -> output video stream
        Audio: either [0:a] (passthrough) or [1:a] (replacement) → output audio stream

    Trim is applied via input -ss / -to flags (faster than the trim filter
    for plain trim). drawtext chain happens in -filter_complex.

    Raises VideoOverlayError on missing tools, missing inputs, ffmpeg
    failure, or timeout. Per the existing error contract in
    video_text_overlay.py.
    """
    if shutil.which("ffmpeg") is None:
        raise VideoOverlayError("ffmpeg not found on PATH")
    if not video_input_path.is_file():
        raise VideoOverlayError(f"video input not found: {video_input_path}")
    if audio_input_path is not None and not audio_input_path.is_file():
        raise VideoOverlayError(f"audio input not found: {audio_input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = ["ffmpeg", "-y"]

    # Trim with input flags — faster than filter trim for plain cuts.
    if video_trim_start is not None and video_trim_start > 0:
        cmd.extend(["-ss", str(float(video_trim_start))])
    if video_trim_end is not None and video_trim_end > 0:
        cmd.extend(["-to", str(float(video_trim_end))])

    cmd.extend(["-i", str(video_input_path)])

    if audio_input_path is not None:
        cmd.extend(["-i", str(audio_input_path)])

    # Build the filter_complex. Each drawtext chains the previous output as
    # its input label, producing v0 → v1 → v2 → ... → vN.
    filter_chain: list[str] = []
    label_in = _VIDEO_INPUT_LABEL
    next_label_idx = 1
    valid_text_elements = [t for t in text_elements if t.get("text", "").strip()]

    for i, text_el in enumerate(valid_text_elements):
        label_out = f"v{next_label_idx}" if i < len(valid_text_elements) - 1 else "vout"
        filter_str = _build_drawtext_filter(text_el, label_in, label_out)
        if filter_str:
            filter_chain.append(filter_str)
            label_in = label_out
            next_label_idx += 1

    if filter_chain:
        cmd.extend(["-filter_complex", ";".join(filter_chain), "-map", "[vout]"])
    else:
        # No text elements — straight passthrough video.
        cmd.extend(["-map", _VIDEO_INPUT_LABEL])

    # Audio routing: if a separate audio input was supplied, replace the
    # video's audio with it (volume-adjusted). Otherwise pass video audio.
    if audio_input_path is not None:
        # Apply volume via a simple filter on the audio input.
        if audio_volume != 1.0:
            cmd.extend([
                "-filter:a:1", f"volume={float(audio_volume):.3f}",
                "-map", _AUDIO_OVERLAY_LABEL,
            ])
        else:
            cmd.extend(["-map", _AUDIO_OVERLAY_LABEL])
    else:
        cmd.extend(["-map", f"{_VIDEO_AUDIO_LABEL}?"])  # `?` = optional

    cmd.extend([
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ])

    logger.info(
        "render_timeline: ffmpeg cmd len=%d, %d text elements, audio=%s, trim=(%s..%s)",
        len(cmd), len(valid_text_elements),
        "replace" if audio_input_path else "passthrough",
        video_trim_start, video_trim_end,
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise VideoOverlayError(f"ffmpeg timed out after {timeout_s}s") from e

    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-25:])
        logger.error("render_timeline: ffmpeg exit %d:\n%s", result.returncode, tail)
        raise VideoOverlayError(f"ffmpeg returned {result.returncode}. Last lines:\n{tail}")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise VideoOverlayError(f"ffmpeg succeeded but output is missing/empty: {output_path}")

    logger.info("render_timeline wrote %s (%d bytes)", output_path, output_path.stat().st_size)
