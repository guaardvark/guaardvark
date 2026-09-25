"""Small H.264 clips with one defect each, for the post-render quality checker.

Written with PyAV (backend/requirements.txt) as yuv420p MP4, the format
VHS_VideoCombine writes, so the checker reads them the way it reads a render.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

WIDTH, HEIGHT, FPS, FRAMES = 320, 192, 16, 33


def _scene(i: int, width: int = WIDTH, height: int = HEIGHT) -> np.ndarray:
    """A coloured, full-contrast frame that moves: a sky-to-grass gradient,
    a sun that slides across, and texture."""
    y = np.linspace(0, 1, height)[:, None]
    x = np.linspace(0, 1, width)[None, :]
    sky = np.stack([40 + 60 * y, 90 + 90 * y, 200 - 60 * y], axis=-1) * np.ones((1, width, 1))
    grass = np.stack([30 + 40 * x, 120 + 60 * x, 40 + 10 * x], axis=-1) * np.ones((height, 1, 1))
    frame = np.where(y[..., None] < 0.55, sky, grass)
    cx = int((0.15 + 0.7 * i / max(1, FRAMES - 1)) * width)
    yy, xx = np.ogrid[:height, :width]
    sun = (yy - height * 0.25) ** 2 + (xx - cx) ** 2 < (height * 0.09) ** 2
    frame[sun] = (235, 205, 90)
    shade = ((xx // 12 + yy // 12 + i) % 2) * 25
    frame = frame - shade[..., None] * (y[..., None] >= 0.55)
    rng = np.random.default_rng(i)
    frame = frame + rng.normal(0, 6, frame.shape)
    frame[:8] = 12          # a dark band keeps real shadows in the shot
    frame[-8:, :40] = 245   # and a small highlight
    return np.clip(frame, 0, 255).astype(np.uint8)


def write_clip(path: Path, frames, fps: int = FPS) -> Path:
    import av

    frames = list(frames)
    height, width = frames[0].shape[:2]
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"crf": "18", "preset": "ultrafast"}
        for rgb in frames:
            for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def clean(path: Path) -> Path:
    return write_clip(path, (_scene(i) for i in range(FRAMES)))


def black_tile(path: Path) -> Path:
    """A NaN patch: one rectangle decoded to black in the middle frames."""
    def frame(i):
        f = _scene(i)
        if 8 <= i <= 24:
            f[60:120, 100:180] = 0
        return f
    return write_clip(path, (frame(i) for i in range(FRAMES)))


def blown_out(path: Path) -> Path:
    return write_clip(path, (np.clip(_scene(i).astype(np.int16) * 3 + 60, 0, 255).astype(np.uint8)
                             for i in range(FRAMES)))


def washed_out(path: Path) -> Path:
    """Lifted blacks, dimmed whites, most colour gone: the grey-veil look."""
    def frame(i):
        f = _scene(i).astype(np.float32)
        grey = f.mean(axis=2, keepdims=True)
        f = 0.25 * f + 0.75 * grey
        return np.clip(150 + (f - 128) * 0.18, 0, 255).astype(np.uint8)
    return write_clip(path, (frame(i) for i in range(FRAMES)))


def frozen(path: Path) -> Path:
    still = _scene(0)
    return write_clip(path, (still.copy() for _ in range(FRAMES)))


def black_frames(path: Path) -> Path:
    return write_clip(path, (np.zeros((HEIGHT, WIDTH, 3), np.uint8) if 10 <= i <= 20 else _scene(i)
                             for i in range(FRAMES)))
