"""Background removal runs the installed ONNX file and refuses when there is none."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from backend.services import background_removal as bg
from backend.tests.fixtures.model_files import onnx_bytes, write_model_file


class _FakeInput:
    name = "input"


class _FakeSession:
    """Answers a bright centre square: the mask must follow it."""

    def __init__(self, size):
        self.size = size
        self.seen = None

    def get_inputs(self):
        return [_FakeInput()]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, _outputs, feeds):
        self.seen = feeds["input"]
        h, w = self.size
        pred = np.full((1, 1, h, w), -6.0, dtype=np.float32)
        pred[:, :, h // 4: 3 * h // 4, w // 4: 3 * w // 4] = 6.0
        return [pred]


def test_preprocess_matches_rembg_shape_and_range():
    img = Image.new("RGB", (64, 32), (255, 128, 0))
    t = bg.preprocess(img, (320, 320))
    assert t.shape == (1, 3, 320, 320) and t.dtype == np.float32
    # red channel: (1.0 - 0.485) / 0.229
    assert abs(float(t[0, 0, 0, 0]) - (1.0 - 0.485) / 0.229) < 1e-4


def test_remove_background_uses_the_installed_model(monkeypatch, tmp_path):
    fake = _FakeSession((320, 320))
    monkeypatch.setattr(bg, "installed_model", lambda: "bgremove-u2net")
    monkeypatch.setattr(bg, "_session", lambda mid: fake)
    img = Image.new("RGB", (80, 80), (10, 200, 30))
    out = bg.remove_background(img)
    assert out.mode == "RGBA" and out.size == (80, 80)
    alpha = np.asarray(out.getchannel("A"))
    assert alpha[40, 40] > 200      # centre kept
    assert alpha[2, 2] < 30         # corner cut
    assert fake.seen.shape == (1, 3, 320, 320)


def test_refuses_without_weights(monkeypatch):
    monkeypatch.setattr(bg, "installed_model", lambda: None)
    monkeypatch.setattr(bg, "_missing_message", lambda: "Background removal needs X. Open Manage Image Models.")
    with pytest.raises(bg.BackgroundRemovalNotInstalled, match="Manage Image Models"):
        bg.remove_background(Image.new("RGB", (8, 8)))


def test_installed_model_prefers_birefnet(monkeypatch, tmp_path):
    monkeypatch.setattr(bg, "model_path", lambda mid: tmp_path / bg.MODELS[mid]["file"])
    assert bg.installed_model() is None
    write_model_file(tmp_path / "u2net.onnx")
    assert bg.installed_model() == "bgremove-u2net"
    write_model_file(tmp_path / "BiRefNet-general-epoch_244.onnx")
    assert bg.installed_model() == "bgremove-birefnet"


def test_a_cut_off_model_file_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(bg, "model_path", lambda mid: tmp_path / bg.MODELS[mid]["file"])
    whole = onnx_bytes(graph_bytes=64)
    (tmp_path / "u2net.onnx").write_bytes(whole[:-20])
    assert bg.installed_model() is None
    (tmp_path / "u2net.onnx").write_bytes(whole)
    assert bg.installed_model() == "bgremove-u2net"
