"""Render a .mlt project to MP4 via the melt CLI.

Snap-confinement note: do NOT call /snap/shotcut/current/bin/melt directly —
that's the unwrapped binary that fails to load libmlt because LD_LIBRARY_PATH
isn't set. Use the top-level /snap/shotcut/current/melt wrapper script.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MeltNotFound(RuntimeError):
    """Raised when the configured melt binary isn't on disk or executable."""


@dataclass
class RenderResult:
    output_path: Path
    duration_seconds: float
    returncode: int
    stderr_tail: str


def render_mlt(
    mlt_path: str | Path,
    output_path: str | Path,
    *,
    melt_path: str,
    vcodec: str = "libx264",
    acodec: str = "aac",
    extra_args: Optional[list[str]] = None,
    timeout_s: float = 600.0,
) -> RenderResult:
    """Invoke `melt project.mlt -consumer avformat:out.mp4 vcodec=... acodec=...`."""
    mlt = Path(mlt_path).resolve()
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not mlt.exists():
        raise FileNotFoundError(f"input .mlt not found: {mlt}")

    melt_bin = _resolve_melt(melt_path)

    cmd = [
        str(melt_bin),
        str(mlt),
        "-consumer",
        f"avformat:{out}",
        f"vcodec={vcodec}",
        f"acodec={acodec}",
    ]
    if extra_args:
        cmd.extend(extra_args)

    logger.info("render: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )

    if proc.returncode != 0 or not out.exists():
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"melt render failed (rc={proc.returncode}): {tail}")

    duration = _probe_duration(out)
    return RenderResult(
        output_path=out,
        duration_seconds=duration,
        returncode=proc.returncode,
        stderr_tail=(proc.stderr or "")[-500:],
    )


def _resolve_melt(configured: str) -> Path:
    """Find an executable melt — honor the configured path, fall back to PATH."""
    p = Path(configured)
    if p.is_file():
        # Resolve snap "current" symlink so we don't break mid-render on a snap refresh.
        return p.resolve()
    found = shutil.which(configured) or shutil.which("melt")
    if found:
        return Path(found).resolve()
    raise MeltNotFound(
        f"melt binary not found (tried '{configured}' and $PATH). "
        "Install Shotcut or `apt install melt`."
    )


def _probe_duration(mp4: Path) -> float:
    """Best-effort duration probe via ffprobe; returns 0.0 if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp4),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return float(out.stdout.strip() or "0")
    except (subprocess.SubprocessError, ValueError):
        return 0.0
