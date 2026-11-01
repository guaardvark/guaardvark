"""Face photos for identity renders: is there a face, and one batch from several.

PuLID-FLUX crops each reference with facexlib's RetinaFace before it reads the
face, and a reference where that finds nothing is skipped without a word: with
no usable reference at all the node hands back the plain model, and the render
comes out as an ordinary FLUX picture of a stranger. ``find_faces`` runs the
same detector on the same weights at the same 0.97 threshold the node's
FaceRestoreHelper uses (custom_nodes/ComfyUI-PuLID-Flux/pulidflux.py,
facexlib.utils.face_restoration_helper.get_face_landmarks_5), on the picture the
node is handed, so a photo it refuses is one the node would have dropped.

The detector is facexlib's (MIT). InsightFace, which the node also runs, is not
loaded here: its pretrained models are licensed for non-commercial research
only (see video_consistency_metrics._identity_via_embed). The weights are read
from where the facexlib-face pack installs them and never downloaded. facexlib
is an optional extra in the backend's environment; without it the check runs
in ComfyUI's (face_check_worker.py), which has it for the node.

The node batches several references and averages their identity embeddings.
ComfyUI's batch node resizes every picture to the first one's size, which would
squash a face that arrived in another shape, so ``batch_references`` letterboxes
them onto one square canvas first.

A photo of several people is cut down to its largest face (``crop_to_face``):
the node keeps the largest face for one half of the identity and the most
central for the other, and in a group photo those can be two people.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DETECTOR_FILE = "detection_Resnet50_Final.pth"
# FaceRestoreHelper.get_face_landmarks_5 calls detect_faces(img, 0.97).
NODE_THRESHOLD = 0.97
# The longest side a batched reference is scaled to. The node crops the face to
# 512 px and InsightFace detects at 640 px at most, so a larger photo adds time
# and no detail.
MAX_SIDE = 1024
# The most references one identity render takes.
MAX_REFERENCES = 3
# A photo with several faces is cut to a square this many face-widths across,
# centred on the face it keeps, and scaled up to at least CROP_MIN_SIDE so the
# node's detectors still find a small face.
CROP_FACE_WIDTHS = 2.5
CROP_MIN_SIDE = 512

_lock = threading.Lock()
_detector = None
_WORKER = Path(__file__).with_name("face_check_worker.py")
WORKER_TIMEOUT_S = 120


class FaceCheckUnavailable(RuntimeError):
    """The detector weights are not on this machine; the message says what to install."""


def detector_path() -> Optional[Path]:
    """The installed RetinaFace weights, from the folders the facexlib-face pack fills."""
    from backend.services.video_model_registry import facexlib_weight_dirs
    for folder in facexlib_weight_dirs():
        candidate = folder / DETECTOR_FILE
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _load_detector():
    global _detector
    with _lock:
        if _detector is not None:
            return _detector
        path = detector_path()
        if path is None:
            from backend.services.image_editing_packs import missing_message
            raise FaceCheckUnavailable(missing_message("generate_identity"))
        import torch
        from facexlib.detection.retinaface import RetinaFace

        # init_detection_model would fetch the file from GitHub when it is
        # missing; this reads the installed copy and nothing else.
        model = RetinaFace(network_name="resnet50", half=False, device="cpu")
        state = torch.load(str(path), map_location="cpu", weights_only=True)
        for key, value in deepcopy(state).items():
            if key.startswith("module."):
                state[key[7:]] = value
                state.pop(key)
        model.load_state_dict(state, strict=True)
        model.eval()
        _detector = model
        logger.info("face check: RetinaFace loaded on the CPU from %s", path)
        return _detector


def _bgr(image) -> "object":
    import numpy as np
    rgb = image.convert("RGB")
    return np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])


def _scaled(image):
    from PIL import Image
    longest = max(image.size)
    if longest <= MAX_SIDE:
        return image
    ratio = MAX_SIDE / float(longest)
    return image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                        Image.LANCZOS)


def find_faces(image_path: str) -> Dict[str, object]:
    """Faces the node's detector finds in the photo, largest first.

    ``{"count", "faces": [{"box": [x0, y0, x1, y1], "score"}], "width",
    "height"}`` in the photo's own pixels. Raises FaceCheckUnavailable when the
    weights are not installed.
    """
    try:
        detector = _load_detector()
    except ImportError:
        boxes, width, height = _detect_in_comfyui(image_path)
    else:
        import torch
        from PIL import Image, ImageOps

        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
        width, height = image.size
        with torch.no_grad():
            boxes = detector.detect_faces(_bgr(image), NODE_THRESHOLD)
    faces = []
    for row in boxes:
        x0, y0, x1, y1, score = (float(v) for v in row[:5])
        faces.append({"box": [round(x0), round(y0), round(x1), round(y1)], "score": round(score, 3)})
    faces.sort(key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]), reverse=True)
    return {"count": len(faces), "faces": faces, "width": width, "height": height}


def _detect_in_comfyui(image_path: str):
    """find_faces's detection, run in ComfyUI's Python: (boxes, width, height)."""
    from backend.services.comfyui_update_service import comfyui_python
    job = {"weights": str(detector_path()), "image": os.path.abspath(image_path), "threshold": NODE_THRESHOLD}
    try:
        done = subprocess.run(
            [str(comfyui_python()), "-I", str(_WORKER)], input=json.dumps(job),
            capture_output=True, text=True, timeout=WORKER_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FaceCheckUnavailable(f"The face check could not run in ComfyUI's environment: {e}")
    if done.returncode != 0:
        logger.warning("face check in ComfyUI's environment failed: %s", done.stderr.strip()[-500:])
        raise FaceCheckUnavailable(
            "The face check needs facexlib, which is installed neither in the backend's environment nor in "
            "ComfyUI's. Reinstall the PuLID-FLUX pack from Manage Image Models.")
    try:
        found = json.loads(done.stdout)
        return found["boxes"], found["width"], found["height"]
    except (ValueError, KeyError):
        raise FaceCheckUnavailable(f"The face check gave an unreadable answer: {done.stdout[-200:]}")


def batch_references(paths: List[str], out_dir: str) -> List[str]:
    """The references on one square canvas each, ready for a single batch.

    One path comes back unchanged. Several are scaled so the longest side of
    the largest is at most MAX_SIDE and centred on a grey square of that size,
    so ComfyUI's batch node has nothing left to resize.
    """
    if len(paths) <= 1:
        return list(paths)
    from PIL import Image, ImageOps

    images = []
    for path in paths:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        images.append(_scaled(image))
    side = max(max(image.size) for image in images)
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for image in images:
        canvas = Image.new("RGB", (side, side), (127, 127, 127))
        canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        target = os.path.join(out_dir, f"identity_ref_{uuid.uuid4().hex[:12]}.png")
        canvas.save(target)
        out.append(target)
    return out


def crop_to_face(path: str, box, out_dir: str) -> str:
    """The photo cut down to one face, which sits at the centre of a square.

    ApplyPulidFlux reads a reference twice: InsightFace keeps the largest face
    for the identity embedding, facexlib the face nearest the centre for the
    EVA-CLIP features. In a photo of several people those can be two people,
    and the likeness mixes them. Centred on the face find_faces ranked largest,
    with grey where the photo ends, it is both. ``box`` is in the photo's own
    (EXIF-upright) pixels, as find_faces reports it.
    """
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    x0, y0, x1, y1 = (float(v) for v in box)
    side = max(1, int(round(CROP_FACE_WIDTHS * max(x1 - x0, y1 - y0))))
    left = int(round((x0 + x1) / 2 - side / 2))
    top = int(round((y0 + y1) / 2 - side / 2))
    canvas = Image.new("RGB", (side, side), (127, 127, 127))
    region = image.crop((max(0, left), max(0, top), min(image.width, left + side), min(image.height, top + side)))
    canvas.paste(region, (max(0, -left), max(0, -top)))
    if side < CROP_MIN_SIDE:
        canvas = canvas.resize((CROP_MIN_SIDE, CROP_MIN_SIDE), Image.LANCZOS)
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, f"identity_face_{uuid.uuid4().hex[:12]}.png")
    canvas.save(target)
    return target


def face_mask(box, width: int, height: int, out_dir: str) -> str:
    """A soft white oval over one face (head and hair) on black, at render size.

    ``box`` is the face as find_faces reports it, in the render's pixels. The
    oval reaches past the face box to the hair and jaw, where likeness also
    lives, and fades out so the identity blends into the rest of the head.
    """
    from PIL import Image, ImageDraw, ImageFilter

    x0, y0, x1, y1 = (float(v) for v in box)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - 0.12 * h
    rx, ry = 0.85 * w, 1.0 * h
    mask = Image.new("L", (int(width), int(height)), 0)
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(4.0, 0.18 * max(w, h))))
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, f"identity_mask_{uuid.uuid4().hex[:12]}.png")
    mask.save(target)
    return target

