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
from where the facexlib-face pack installs them and never downloaded.

The node batches several references and averages their identity embeddings.
ComfyUI's batch node resizes every picture to the first one's size, which would
squash a face that arrived in another shape, so ``batch_references`` letterboxes
them onto one square canvas first.
"""

from __future__ import annotations

import logging
import os
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
# The most references one identity render takes; Dean's own test case had three.
MAX_REFERENCES = 3

_lock = threading.Lock()
_detector = None


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
    import torch
    from PIL import Image, ImageOps

    detector = _load_detector()
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

