"""The record an identity scene render leaves, so its faces can be placed later.

generate_identity with others=true renders the scene with every identity at
weight 0 and finds its faces. Placing a person on one of them has to repeat
that render exactly (prompt, size, seed, photos), so the record is stored
under OUTPUT_DIR/identity_scenes/<scene file>.json, and the second call
names only the scene and a face number.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Union


def _record_path(scene_path: str) -> str:
    from backend.config import OUTPUT_DIR
    return os.path.join(OUTPUT_DIR, "identity_scenes", os.path.basename(scene_path) + ".json")


def save(scene_path: str, *, prompt: str, width: int, height: int, steps: int, seed: int,
         weight: Optional[float], references: List[str], person2: List[str]) -> List[Dict[str, Any]]:
    """Find the scene's faces, store the record, return the faces largest first."""
    from backend.services.face_reference import find_faces
    faces = find_faces(scene_path)["faces"]
    record = {
        "scene": os.path.abspath(scene_path), "prompt": prompt, "width": width, "height": height,
        "steps": steps, "seed": seed, "weight": weight,
        "references": [os.path.abspath(p) for p in references],
        "person2": [os.path.abspath(p) for p in person2],
        "faces": faces,
    }
    path = _record_path(scene_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    os.replace(tmp, path)
    return faces


def load(scene_path: Optional[str]) -> Optional[Dict[str, Any]]:
    """The record for a scene this install rendered, with its photos still on disk; else None."""
    if not scene_path:
        return None
    path = _record_path(scene_path)
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    if os.path.abspath(scene_path) != record.get("scene"):
        return None
    if not record.get("references") or not all(
            os.path.isfile(p) for p in record["references"] + record.get("person2", [])):
        return None
    return record


def pick(faces: List[Dict[str, Any]], number, label: str) -> Union[list, str]:
    """The box of face ``number`` (counted from 1, as the scene call listed them), or why not."""
    if not faces:
        return "No face was found in that scene. Render it again with a new seed or a closer shot."
    if number in (None, ""):
        if len(faces) == 1:
            return faces[0]["box"]
        return f"The scene has {len(faces)} faces; say which one with {label} (1 to {len(faces)})."
    try:
        index = int(number) - 1
    except (TypeError, ValueError):
        return f"{label} must be a face number from 1 to {len(faces)}."
    if not 0 <= index < len(faces):
        return f"{label} must be a face number from 1 to {len(faces)}."
    return faces[index]["box"]
