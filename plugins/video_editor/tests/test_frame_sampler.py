"""Frame sampler tests — ffmpeg subprocess against a real-or-fixture file."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mlt.frame_sampler import sample_frames


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required")


@pytest.fixture
def short_clip(tmp_path: Path) -> Path:
    """Generate a 3s solid-color clip via ffmpeg's lavfi source."""
    import subprocess
    out = tmp_path / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=30:d=3",
         "-c:v", "libx264", "-t", "3", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_sample_default_three_frames(short_clip: Path, tmp_path: Path):
    out_dir = tmp_path / "frames"
    frames = sample_frames(short_clip, out_dir, n_frames=3)
    assert len(frames) == 3
    for f in frames:
        assert f.path.exists()
        assert f.path.suffix == ".jpg"
    # Timestamps strictly increasing.
    ts = [f.timestamp_seconds for f in frames]
    assert ts == sorted(ts)
    # Spread across [0, duration].
    assert ts[0] > 0
    assert ts[-1] < 3.0


def test_sample_n_frames_param(short_clip: Path, tmp_path: Path):
    out_dir = tmp_path / "frames"
    frames = sample_frames(short_clip, out_dir, n_frames=5)
    assert len(frames) == 5


def test_sample_missing_input_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        sample_frames(tmp_path / "nope.mp4", tmp_path)


def test_sampled_jpegs_are_readable(short_clip: Path, tmp_path: Path):
    """Make sure the bytes we'd send to the vision model are actually JPEG."""
    out_dir = tmp_path / "frames"
    frames = sample_frames(short_clip, out_dir, n_frames=2)
    for f in frames:
        with open(f.path, "rb") as fh:
            head = fh.read(3)
        # JPEG SOI marker
        assert head[:3] == b"\xff\xd8\xff", f"not a JPEG: {head!r}"
