"""Background removal on this machine: u2net or BiRefNet ONNX through onnxruntime.

The weights are the ONNX files the rembg project publishes, installed from
Manage Image Models -> Image editing (registry entries ``bgremove-birefnet``
and ``bgremove-u2net``). Nothing here downloads. Pre- and post-processing
mirror rembg's sessions (scale by the image maximum, ImageNet mean and std,
LANCZOS resize, min-max on the prediction) so the same file gives the same
mask rembg would, without rembg's own dependency set (opencv-python-headless,
pymatting) next to the OpenCV build and numpy floor this backend pins.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Preference order: BiRefNet's 1024 px matting first, u2net's 320 px second.
# ``keep_loaded`` says whether the session stays in memory between cut-outs.
# Resident memory of this process after two cut-outs of a 1344x768 picture,
# measured 2026-11-25 with onnxruntime 1.30.0 on the CPU provider, a fresh
# process per run:
#   bgremove-birefnet  session kept, onnxruntime's CPU memory arena on: over
#                      12.5 GB (stopped by a 12 GB cap) and none of it returned,
#                      because the arena keeps its peak for the session's life
#                      session kept, arena off: 1.67 GB, peak 7.74 GB
#                      session dropped after each cut-out, arena off: 0.82 GB,
#                      peak 7.68 GB, 16-17 s per cut-out with the reload
#   bgremove-u2net     session kept, arena on: 1.04 GB; arena off: 0.50 GB,
#                      peak 0.78 GB, 0.5 s per cut-out
# So the arena is off for both, and BiRefNet is loaded for the cut-out only.
MODELS = {
    "bgremove-birefnet": {
        "file": "BiRefNet-general-epoch_244.onnx",
        "size": (1024, 1024),
        "sigmoid": True,
        "keep_loaded": False,
    },
    "bgremove-u2net": {
        "file": "u2net.onnx",
        "size": (320, 320),
        "sigmoid": False,
        "keep_loaded": True,
    },
}
PREFERENCE = ("bgremove-birefnet", "bgremove-u2net")

# Cut-outs run on the CPU provider, whichever onnxruntime build is installed. A
# CUDA session here would hold VRAM with no booking (gpu_resource_policy), next
# to renders and a resident chat model that were sized without it.
#
# Measured 2026-09-30 on an 8-core / 16-thread desktop CPU: one 1024x1024 PNG, a
# fresh process per run, onnxruntime 1.30.0 CPUExecutionProvider.
#   bgremove-u2net     0.13 s to load the session; 0.10-0.15 s per cut-out after
#                      that (resize, inference, alpha); 0.4-0.6 s for the whole
#                      remove_background call, PNG read and write included
#   bgremove-birefnet  not measured: the file was not installed on that machine
# There is no GPU timing to set against these. On that machine the CUDA provider
# onnxruntime-gpu 1.30.0 lists did not load (it needs CUDA 13, torch 2.6 brings
# CUDA 12.4), and requirements.txt pins the CPU build, which has none. Before a
# GPU path is added, record its timing and peak VRAM here and claim the card
# through gpu_resource_policy.gpu_session, so it runs only when the card is free.
PROVIDERS = ("CPUExecutionProvider",)

_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)

_sessions: dict = {}
_lock = threading.Lock()
# One cut-out at a time for a model that is not kept loaded: each run holds
# its own session, and two BiRefNet sessions at once are 15 GB.
_run_lock = threading.Lock()


class BackgroundRemovalNotInstalled(RuntimeError):
    """No background-removal weights on this machine; this code will not fetch them."""


def model_path(model_id: str) -> Path:
    from backend.services.video_model_registry import background_removal_dir
    return background_removal_dir() / MODELS[model_id]["file"]


def installed_model() -> Optional[str]:
    """The best installed model id, or None."""
    from backend.utils.model_file_integrity import file_complete
    for mid in PREFERENCE:
        if file_complete(model_path(mid)):
            return mid
    return None


def _providers() -> list:
    return list(PROVIDERS)


def device_used(model_id: Optional[str] = None) -> str:
    """'CPU' or 'GPU': what the loaded session for ``model_id`` runs on, read from
    the session itself; before one is loaded, what ``PROVIDERS`` asks for."""
    sess = _sessions.get(model_id or installed_model() or "")
    provider = sess.get_providers()[0] if sess is not None else PROVIDERS[0]
    return "CPU" if provider == "CPUExecutionProvider" else "GPU"


def _session(model_id: str):
    with _lock:
        sess = _sessions.get(model_id)
        if sess is not None:
            return sess
        import onnxruntime as ort
        path = model_path(model_id)
        if not path.is_file():
            raise BackgroundRemovalNotInstalled(_missing_message())
        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False
        sess = ort.InferenceSession(str(path), sess_options=options, providers=_providers())
        if MODELS[model_id]["keep_loaded"]:
            _sessions[model_id] = sess
        logger.info("Background removal loaded %s (%s)", model_id, sess.get_providers()[0])
        return sess


def _missing_message() -> str:
    from backend.services.image_editing_packs import missing_message
    return missing_message("remove_background")


def preprocess(img: Image.Image, size: tuple) -> np.ndarray:
    """rembg's normalize(): resize, scale by the max, ImageNet mean/std, NCHW float32."""
    im = img.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    arr = np.asarray(im, dtype=np.float32)
    arr = arr / max(float(arr.max()), 1e-6)
    out = np.empty_like(arr)
    for c in range(3):
        out[:, :, c] = (arr[:, :, c] - _MEAN[c]) / _STD[c]
    return np.expand_dims(out.transpose((2, 0, 1)), 0).astype(np.float32)


def postprocess(pred: np.ndarray, original_size: tuple, *, sigmoid: bool) -> Image.Image:
    """rembg's mask step: first channel, optional sigmoid, min-max to 0..1, L image."""
    pred = np.asarray(pred, dtype=np.float32)[:, 0, :, :]
    if sigmoid:
        pred = 1.0 / (1.0 + np.exp(-pred))
    lo, hi = float(pred.min()), float(pred.max())
    pred = (pred - lo) / max(hi - lo, 1e-6)
    pred = np.squeeze(pred)
    mask = Image.fromarray((np.clip(pred, 0, 1) * 255).astype("uint8"), mode="L")
    return mask.resize(original_size, Image.Resampling.LANCZOS)


def predict_mask(img: Image.Image, model_id: Optional[str] = None) -> Image.Image:
    mid = model_id or installed_model()
    if not mid:
        raise BackgroundRemovalNotInstalled(_missing_message())
    spec = MODELS[mid]
    tensor = preprocess(img, spec["size"])
    if spec["keep_loaded"]:
        sess = _session(mid)
        outputs = sess.run(None, {sess.get_inputs()[0].name: tensor})
    else:
        with _run_lock:
            sess = _session(mid)
            outputs = sess.run(None, {sess.get_inputs()[0].name: tensor})
            del sess
    return postprocess(outputs[0], img.size, sigmoid=spec["sigmoid"])


def remove_background(img: Image.Image, model_id: Optional[str] = None) -> Image.Image:
    """The subject on a transparent background (RGBA)."""
    mask = predict_mask(img, model_id)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out
