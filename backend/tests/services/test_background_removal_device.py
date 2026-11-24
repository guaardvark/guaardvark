"""Background removal asks for the CPU provider only, and says which device ran.

A CUDA session would hold VRAM no GPU booking knows about, so the session is
created on the CPU provider even when the installed onnxruntime build lists
CUDA. The tool's result names the device the session reports. onnxruntime and
the model file are stand-ins: no GPU, network or database.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
from PIL import Image

from backend.services import background_removal as bg
from backend.tests.fixtures.model_files import write_model_file
from backend.tools import image_tools as it


class _Session:
    def __init__(self, path=None, providers=None):
        self.asked = list(providers or [])

    def get_providers(self):
        return self.asked or ["CPUExecutionProvider"]

    def get_inputs(self):
        return [types.SimpleNamespace(name="input")]

    def run(self, _outputs, feeds):
        _, _, h, w = feeds["input"].shape
        pred = np.full((1, 1, h, w), -6.0, dtype=np.float32)
        pred[:, :, h // 4: 3 * h // 4, w // 4: 3 * w // 4] = 6.0
        return [pred]


@pytest.fixture
def cuda_build(monkeypatch, tmp_path):
    """An onnxruntime build that lists CUDA, and an installed u2net file."""
    created = []
    fake = types.ModuleType("onnxruntime")
    fake.get_available_providers = lambda: ["TensorrtExecutionProvider", "CUDAExecutionProvider",
                                            "CPUExecutionProvider"]
    fake.InferenceSession = lambda path, providers=None: created.append(_Session(path, providers)) or created[-1]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(bg, "_sessions", {})
    monkeypatch.setattr(bg, "model_path", lambda mid: tmp_path / bg.MODELS[mid]["file"])
    write_model_file(tmp_path / "u2net.onnx")
    return created


def test_the_session_is_created_on_the_cpu_provider_only(cuda_build):
    assert bg.PROVIDERS == ("CPUExecutionProvider",)
    sess = bg._session("bgremove-u2net")
    assert sess.asked == ["CPUExecutionProvider"] and len(cuda_build) == 1
    assert bg._session("bgremove-u2net") is sess and len(cuda_build) == 1     # kept, not reloaded


def test_device_used_reads_the_loaded_session(cuda_build, monkeypatch):
    assert bg.device_used("bgremove-u2net") == "CPU"                          # nothing loaded yet
    bg._session("bgremove-u2net")
    assert bg.device_used("bgremove-u2net") == "CPU" and bg.device_used() == "CPU"
    # Whatever provider a session ends up on is what gets reported.
    monkeypatch.setitem(bg._sessions, "bgremove-u2net",
                        _Session(providers=["CUDAExecutionProvider", "CPUExecutionProvider"]))
    assert bg.device_used("bgremove-u2net") == "GPU"


def test_the_tool_result_names_the_model_and_the_device(cuda_build, monkeypatch, tmp_path):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (64, 64), (10, 200, 30)).save(photo)
    monkeypatch.setattr(it, "_chat_png_path", lambda prefix: (str(tmp_path / f"{prefix}.png"), f"{prefix}.png"))
    res = it.RemoveBackgroundTool._cut_out(str(photo))
    assert res.success and res.output.startswith("Background removed (u2net, on the CPU).")
    assert res.metadata["device"] == "cpu" and res.metadata["backend"] == "bgremove-u2net"
    with Image.open(tmp_path / "nobg.png") as out:
        alpha = np.asarray(out.getchannel("A"))
    assert out.mode == "RGBA" and alpha[32, 32] > 200 and alpha[2, 2] < 30
