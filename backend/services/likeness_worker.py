"""Face similarity between a render and reference photos, run in ComfyUI's Python.

InsightFace is installed where ComfyUI runs (the PuLID node needs it), not in
the backend, so likeness_score.py runs this file as a subprocess with
ComfyUI's interpreter. It imports nothing from the backend.

stdin:  {"root": <folder holding models/antelopev2>, "image": <render>,
         "people": [[<photo>, ...], ...]}
stdout: {"refs_found": [n, ...], "faces": [{"box": [x0, y0, x1, y1],
         "scores": [<cosine to person 1>, ...]}, ...]}

Each person's photos are reduced to one mean ArcFace embedding of the largest
face in each, as ApplyPulidFlux does; faces are detected at 640, then 320 and
256 px, the sizes the node tries.
"""

import json
import sys


def _faces(app, path):
    import cv2
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"cannot read {path}")
    for size in (640, 320, 256):
        app.det_model.input_size = (size, size)
        found = app.get(img)
        if found:
            return found
    return []


def main():
    import numpy as np
    from insightface.app import FaceAnalysis

    job = json.load(sys.stdin)
    app = FaceAnalysis(name="antelopev2", root=job["root"], providers=["CPUExecutionProvider"],
                       allowed_modules=["detection", "recognition"])
    app.prepare(ctx_id=-1, det_size=(640, 640))

    means, refs_found = [], []
    for photos in job["people"]:
        embeddings = []
        for path in photos:
            found = _faces(app, path)
            if found:
                largest = max(found, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                embeddings.append(largest.normed_embedding)
        refs_found.append(len(embeddings))
        if embeddings:
            mean = np.mean(embeddings, axis=0)
            means.append(mean / np.linalg.norm(mean))
        else:
            means.append(None)

    faces = []
    for face in _faces(app, job["image"]):
        faces.append({
            "box": [round(float(v)) for v in face.bbox[:4]],
            "scores": [None if m is None else round(float(np.dot(face.normed_embedding, m)), 3) for m in means],
        })
    json.dump({"refs_found": refs_found, "faces": faces}, sys.stdout)


if __name__ == "__main__":
    main()
