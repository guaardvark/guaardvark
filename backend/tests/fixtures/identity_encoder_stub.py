"""A stand-in for the identity check's image encoder: a fake install and a fake model.

``fake_install(root)`` lays out what a finished Install leaves under
``root/data/models/identity_encoder/<folder>`` — the declared files and an
install record for the pinned commit — without fetching anything.
``StubProcessor``/``StubModel`` take the place of the transformers classes
(``video_consistency_metrics._image_encoder_classes``): the embedding of a
picture is its mean colour, centred, so two pictures of one colour score 1.0 and
a red one against a blue one scores below zero. They record how they were
loaded, so a test can check the load asked for local files only.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.services.identity_method_data import IMAGE_ENCODERS
from backend.tests.fixtures.model_files import write_model_file

REPO = "facebook/dinov2-base"


def fake_install(root: Path, *, revision: str | None = None, sha256: str | None = None) -> Path:
    """The encoder folder as Install leaves it, under ``root`` (the install root)."""
    entry = IMAGE_ENCODERS[REPO]
    folder = Path(root) / entry["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    for name in entry["files"]:
        write_model_file(folder / name)
    (folder / "install.json").write_text(json.dumps({
        "repo": REPO,
        "revision": revision or entry["revision"],
        "weights_sha256": sha256 or entry["weights_sha256"],
    }), encoding="utf-8")
    return folder


class _Out:
    def __init__(self, vec):
        self.pooler_output = vec


class StubProcessor:
    loads: list = []

    @classmethod
    def from_pretrained(cls, name_or_path, **kwargs):
        cls.loads.append((name_or_path, kwargs))
        return cls()

    def __call__(self, images=None, return_tensors=None):
        import numpy as np
        import torch
        mean = np.asarray(images.convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(axis=0)
        return {"pixel_values": torch.tensor(mean / 255.0 - 0.5).reshape(1, 3)}


class StubModel:
    loads: list = []

    @classmethod
    def from_pretrained(cls, name_or_path, **kwargs):
        cls.loads.append((name_or_path, kwargs))
        return cls()

    def eval(self):
        return self

    def __call__(self, pixel_values=None):
        return _Out(pixel_values)


def stub_classes():
    """Fresh (processor, model) stand-ins with empty load records."""
    StubProcessor.loads = []
    StubModel.loads = []
    return StubProcessor, StubModel
