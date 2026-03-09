
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

def _make_relative(path: str, base_dir: str) -> str:
    try:
        abs_base = os.path.abspath(base_dir)
        abs_path = os.path.abspath(path)
        rel = os.path.relpath(abs_path, abs_base)
    except Exception:
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


def _rewrite_path(path: str, field: str, base_dir: str) -> Tuple[str, str]:
    rel = _make_relative(path, base_dir)
    filename = os.path.basename(path)
    return rel, filename


def _collect_file_metadata(
    entity_type: str, entity_id: Any, original_name: str, rel_path: str, base_dir: str
) -> Dict[str, Any]:
    abs_path = (
        os.path.join(base_dir, rel_path) if not os.path.isabs(rel_path) else rel_path
    )
    meta = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "original_file_name": original_name,
        "relative_path": rel_path,
    }
    if not os.path.exists(abs_path):
        meta["warning"] = "file missing"
    return meta


def _process_list(
    entity_type: str,
    items: List[Dict[str, Any]],
    base_dir: str,
    metadata: List[Dict[str, Any]],
) -> None:
    for obj in items:
        entity_id = obj.get("id")
        for key, value in list(obj.items()):
            if not isinstance(value, str):
                continue
            if key.endswith("_path") or key == "path":
                new_rel, original = _rewrite_path(value, key, base_dir)
                obj[key] = new_rel
                metadata.append(
                    _collect_file_metadata(
                        entity_type, entity_id, original, new_rel, base_dir
                    )
                )


def process_backup(data: Dict[str, Any], uploads_dir: str) -> Dict[str, Any]:

    metadata: List[Dict[str, Any]] = []

    if isinstance(data.get("clients"), list):
        _process_list("client", data["clients"], uploads_dir, metadata)

    if isinstance(data.get("projects"), list):
        _process_list("project", data["projects"], uploads_dir, metadata)

    if isinstance(data.get("websites"), list):
        _process_list("website", data["websites"], uploads_dir, metadata)

    if isinstance(data.get("documents"), list):
        _process_list("document", data["documents"], uploads_dir, metadata)

    data["file_metadata"] = metadata
    return data


def process_backup_file(input_path: str, output_path: str, uploads_dir: str) -> None:
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = process_backup(data, uploads_dir)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rewrite paths in guaardvark backup JSON")
    parser.add_argument("input", help="Path to original backup JSON")
    parser.add_argument("output", help="Where to write updated JSON")
    parser.add_argument(
        "--uploads-dir", default="backend/uploads", help="Base uploads directory"
    )

    args = parser.parse_args()
    process_backup_file(args.input, args.output, args.uploads_dir)
    print(f"Rewrote backup JSON saved to {args.output}")
