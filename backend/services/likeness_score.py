"""How much the face in an identity render looks like the person's photos.

A PuLID render can finish without the identity: the node skips a reference
its detector misses, and a node fault once left every render untouched while
reporting success. Only someone comparing faces by eye would notice. After
each identity render the result is scored with the ArcFace model the node
builds its identity from (InsightFace antelopev2, installed with the PuLID
pack), and the score goes back with the picture.

InsightFace lives in ComfyUI's environment, so the scoring runs there
(likeness_worker.py) on the CPU. No score is given when that environment or
the model is missing; the render itself is unaffected. Its pretrained models
are licensed for non-commercial research, which is why video_consistency_metrics
does not use them; this scores only renders PuLID made with that same model,
so the identity feature runs nothing it did not already run.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cosine similarity of ArcFace (antelopev2) embeddings: the render's face
# against the mean of the person's photos. Scored 2026-10-31 over the identity
# renders on file. PuLID applied: 0.56-0.78 (20 faces in 19 renders: weights
# 0.8-1.3, starts 0 and 0.2, portrait, 16:9, Full HD, group scenes). Applied,
# but FLUX added glasses: 0.44. FLUX's own Marilyn Monroe against photos of
# her: 0.43. Scenes rendered at weight 0: 0.03-0.19. The September renders the
# node left untouched: -0.04 to 0.05.
LIKENESS_BANDS = (
    (0.55, "strong"),
    (0.30, "partial"),
)
BELOW_BANDS = "not_taken"
NO_FACE = "no_face"
# None of the person's photos had a face the scorer could read, so there is
# nothing to compare the render with; it says nothing about the render.
NO_REFERENCE = "no_reference_face"
# A face counts as carrying the likeness from the lowest band up; strangers
# stayed under 0.2 in every scene scored.
CARRIES_LIKENESS = LIKENESS_BANDS[-1][0]
# The chosen face is the detected one overlapping the box it was given most;
# below this overlap nothing was rendered where the face was asked for.
MIN_OVERLAP = 0.1
TIMEOUT_S = 120

_WORKER = Path(__file__).with_name("likeness_worker.py")


def band(score: Optional[float]) -> str:
    if score is None:
        return NO_FACE
    for floor, name in LIKENESS_BANDS:
        if score >= floor:
            return name
    return BELOW_BANDS


def _model_root() -> Optional[str]:
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, resolve_entry_dir
    entry = VIDEO_MODEL_REGISTRY.get("pulid-antelopev2")
    if not entry:
        return None
    folder = resolve_entry_dir(entry)
    if not folder.is_dir() or not any(folder.glob("*.onnx")):
        return None
    # FaceAnalysis reads <root>/models/antelopev2.
    return str(folder.parent.parent)


def _overlap(a: List[float], b: List[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _measure(image_path: str, people: List[List[str]]) -> Optional[Dict[str, Any]]:
    root = _model_root()
    if root is None:
        logger.info("likeness: antelopev2 is not installed; no score")
        return None
    from backend.services.comfyui_update_service import comfyui_python
    job = {"root": root, "image": os.path.abspath(image_path),
           "people": [[os.path.abspath(p) for p in photos] for photos in people]}
    try:
        done = subprocess.run(
            [str(comfyui_python()), "-I", str(_WORKER)], input=json.dumps(job),
            capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("likeness: scoring did not finish: %s", e)
        return None
    if done.returncode != 0:
        logger.warning("likeness: scoring failed: %s", done.stderr.strip()[-500:])
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        logger.warning("likeness: unreadable scoring output: %s", done.stdout[-300:])
        return None


def score_render(image_path: str, people: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Score each person's face in the render, or None when it cannot be scored.

    ``people`` is ``[{"paths": [photo, ...], "box": [x0, y0, x1, y1] or None}]``.
    A person with a box is scored on the face found there; without one (a
    single render, where every face gets the identity) on the face closest to
    the photos, with ``faces_with_likeness`` counting the faces that carry it.
    ``faces`` lists every face found with its score against each person.
    """
    measured = _measure(image_path, [p["paths"] for p in people])
    if measured is None:
        return None
    faces = measured.get("faces") or []
    refs_found = measured.get("refs_found") or []
    report = []
    for i, person in enumerate(people):
        if i < len(refs_found) and not refs_found[i]:
            report.append({"person": i + 1, "score": None, "band": NO_REFERENCE, "box": None,
                           "faces_with_likeness": 0})
            continue
        scored = [(f, f["scores"][i]) for f in faces if f["scores"][i] is not None]
        if person.get("box"):
            near = [(f, s) for f, s in scored if _overlap(f["box"], person["box"]) >= MIN_OVERLAP]
            chosen = max(near, key=lambda fs: _overlap(fs[0]["box"], person["box"]), default=None)
        else:
            chosen = max(scored, key=lambda fs: fs[1], default=None)
        score = round(chosen[1], 2) if chosen else None
        report.append({
            "person": i + 1,
            "score": score,
            "band": band(score),
            "box": chosen[0]["box"] if chosen else None,
            "faces_with_likeness": sum(1 for _, s in scored if s >= CARRIES_LIKENESS),
        })
    return {"people": report, "faces_found": len(faces), "faces": faces}


_BAND_WORDS = {
    "strong": "strong",
    "partial": "partial, some features carried over",
    BELOW_BANDS: "the face did not take",
}


def describe(likeness: Optional[Dict[str, Any]]) -> str:
    """One line for the tool's answer, so chat can say when the face did not take."""
    if not likeness:
        return ""
    parts = []
    for person in likeness["people"]:
        who = f"person {person['person']} " if len(likeness["people"]) > 1 else ""
        if person["band"] == NO_FACE:
            parts.append(f"{who}no face in the result to compare with the photos")
        elif person["band"] == NO_REFERENCE:
            parts.append(f"{who}not measured, the scorer found no face in the photos")
        else:
            parts.append(f"{who}{person['score']:.2f} ({_BAND_WORDS[person['band']]})")
    line = "Likeness to the photos (ArcFace similarity; 0.55 and up is strong): " + "; ".join(parts) + "."
    first = likeness["people"][0]
    if len(likeness["people"]) == 1 and first["faces_with_likeness"] > 1:
        line += (f" The face is on {first['faces_with_likeness']} people in the picture; with others=true "
                 "the scene is rendered first and the face goes on the one chosen.")
    return line
