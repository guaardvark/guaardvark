"""Extract N evenly-spaced frames from a video clip via ffmpeg.

Frames feed the Art Director (qwen3-vl) so it can read each clip's subject /
energy / palette / motion / mood. We use JPEG at quality 3 (very high) —
qwen3-vl tokenizes the image once so payload size doesn't matter much, and
crisp frames help the model on small details.

Output naming: <output_dir>/<source_stem>__f{i}.jpg. Caller is expected to
pass a per-clip subdir (e.g. data/outputs/videos/clip-scans/<hash>/) so
repeated samples don't collide.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SampledFrame:
    timestamp_seconds: float
    path: Path


def sample_frames(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    n_frames: int = 3,
    ffmpeg_path: str = "ffmpeg",
    timeout_s: float = 60.0,
    duration_seconds: float | None = None,
) -> list[SampledFrame]:
    """Extract `n_frames` JPEGs from `input_path`. Returns sorted by timestamp."""
    source = Path(input_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"input not found: {source}")
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = duration_seconds if duration_seconds is not None else _probe_duration(source)
    if duration <= 0:
        # Last-ditch: still try to extract frame 0.
        duration = 1.0

    # Pick timestamps: 1/(n+1), 2/(n+1), ..., n/(n+1) of the duration.
    # Avoids first frame (often a logo / black) and last frame (often credits).
    timestamps = [duration * (i + 1) / (n_frames + 1) for i in range(n_frames)]

    ff = shutil.which(ffmpeg_path) or ffmpeg_path
    out: list[SampledFrame] = []
    stem = source.stem
    for i, ts in enumerate(timestamps):
        target = out_dir / f"{stem}__f{i}.jpg"
        cmd = [
            ff, "-y",
            "-ss", f"{ts:.3f}",
            "-i", str(source),
            "-frames:v", "1",
            "-q:v", "3",
            str(target),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
        if proc.returncode != 0 or not target.exists():
            logger.warning("frame sample failed at ts=%.3f for %s: %s", ts, source.name, (proc.stderr or "")[-200:])
            continue
        out.append(SampledFrame(timestamp_seconds=ts, path=target))
    return out


def _probe_duration(path: Path) -> float:
    """ffprobe `format=duration`. Returns 0.0 on any failure."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return float(out.stdout.strip() or "0")
    except (subprocess.SubprocessError, ValueError):
        return 0.0
