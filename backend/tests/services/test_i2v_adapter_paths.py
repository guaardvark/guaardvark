"""P0.4 — i2v adapter output_dir path-resolution fix.

generate_video returns result.video_path RELATIVE to request.output_dir; the
Svd/Wan22 adapters used to copyfile that bare relative path → ENOENT. These tests
lock the shared resolver and prove both adapters now resolve correctly.
"""
from pathlib import Path

import pytest

try:
    import backend.services.comfyui_video_generator as cvg
    from backend.services.comfyui_video_generator import (
        resolve_generated_video_path, Wan22I2VGenerator, SvdI2VGenerator,
    )
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


class _Result:
    def __init__(self, video_path):
        self.success = True
        self.video_path = video_path
        self.error = None


def test_resolve_relative_joins_output_dir(tmp_path):
    r = _Result("item_5/clip.mp4")
    assert resolve_generated_video_path(r, tmp_path) == tmp_path / "item_5" / "clip.mp4"


def test_resolve_absolute_passthrough(tmp_path):
    abs_p = tmp_path / "x.mp4"
    r = _Result(str(abs_p))
    assert resolve_generated_video_path(r, tmp_path) == abs_p


@pytest.mark.parametrize("Adapter", [Wan22I2VGenerator, SvdI2VGenerator])
def test_adapter_resolves_relative_video_path(tmp_path, monkeypatch, Adapter):
    captured = {}

    class _FakeGen:
        def generate_video(self, req):
            captured["output_dir"] = req.output_dir
            captured["model"] = req.model
            captured["frames"] = req.duration_frames
            captured["fps"] = req.fps
            # ComfyUI writes the real file under output_dir/<item>/…; return the REL path.
            rel = "item_0/out.mp4"
            real = Path(req.output_dir) / rel
            real.parent.mkdir(parents=True, exist_ok=True)
            real.write_bytes(b"VIDEO")
            return _Result(rel)

    monkeypatch.setattr(cvg, "get_video_generator", lambda: _FakeGen())

    out_path = tmp_path / "clips" / "final.mp4"
    returned = Adapter().i2v_from_image(
        image_path="/tmp/still.png", prompt="x", loras=[],
        duration_seconds=2.0, output_path=str(out_path),
    )
    assert returned == str(out_path)
    assert out_path.exists()                 # no ENOENT — the fix
    assert out_path.read_bytes() == b"VIDEO"
    # The adapter set output_dir to the output_path's parent (a known base).
    assert Path(captured["output_dir"]) == out_path.parent


def test_wan_adapter_snaps_frames_to_4n_plus_1(tmp_path, monkeypatch):
    captured = {}

    class _FakeGen:
        def generate_video(self, req):
            captured["frames"] = req.duration_frames
            captured["model"] = req.model
            rel = "item_0/out.mp4"
            real = Path(req.output_dir) / rel
            real.parent.mkdir(parents=True, exist_ok=True)
            real.write_bytes(b"VIDEO")
            return _Result(rel)

    monkeypatch.setattr(cvg, "get_video_generator", lambda: _FakeGen())
    out_path = tmp_path / "clips" / "final.mp4"
    Wan22I2VGenerator(model="wan22-5b", fps=24).i2v_from_image(
        image_path="/tmp/still.png", prompt="x", loras=[],
        duration_seconds=2.0, output_path=str(out_path),
    )
    assert captured["model"] == "wan22-5b"
    assert captured["frames"] % 4 == 1
    assert 17 <= captured["frames"] <= 49
