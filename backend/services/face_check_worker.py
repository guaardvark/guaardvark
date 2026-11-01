"""facexlib's RetinaFace face check, run in ComfyUI's Python.

face_reference.find_faces runs the detector in the backend when facexlib is
installed there (an optional extra, backend/requirements-cv.txt). Where it is
not, ComfyUI's environment has it, because the PuLID node needs it, and
find_faces runs this file there with ComfyUI's interpreter. It imports nothing
from the backend.

stdin:  {"weights": <detection_Resnet50_Final.pth>, "image": <photo>, "threshold": 0.97}
stdout: {"boxes": [[x0, y0, x1, y1, score], ...], "width": w, "height": h}
"""

import json
import sys
from copy import deepcopy


def main():
    out = sys.stdout
    # Anything the libraries print goes to stderr; stdout carries only the answer.
    sys.stdout = sys.stderr
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    from facexlib.detection.retinaface import RetinaFace

    job = json.load(sys.stdin)
    model = RetinaFace(network_name="resnet50", half=False, device="cpu")
    state = torch.load(job["weights"], map_location="cpu", weights_only=True)
    for key, value in deepcopy(state).items():
        if key.startswith("module."):
            state[key[7:]] = value
            state.pop(key)
    model.load_state_dict(state, strict=True)
    model.eval()
    with Image.open(job["image"]) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    bgr = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
    with torch.no_grad():
        boxes = model.detect_faces(bgr, job["threshold"])
    json.dump({"boxes": [[float(v) for v in row[:5]] for row in boxes],
               "width": image.width, "height": image.height}, out)


if __name__ == "__main__":
    main()
