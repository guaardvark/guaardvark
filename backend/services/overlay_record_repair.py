"""Repair Library records of text overlays made before register_file kept the
file's real location.

Such a record was stored under a folder-style path built from its display
name ("Videos/Text Overlay/clip-text.mp4") while the file on disk is
"<output>/videos/text-overlay/clip_001.mp4", so it never downloaded; and a
second overlay of the same clip was merged into the first one's record.

`plan_repair` only reads: it takes the records as plain dicts and looks at the
overlay folders, and says what it would change. `apply_plan` makes those
changes through the database session it is given. `flask repair-overlay-records`
prints the plan; `--apply` carries it out.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

OLD_PATH_PREFIX = "Videos/Text Overlay/"
_DISPLAY_SUFFIX = "-text"


@dataclass
class Repair:
    """What to do about one record."""

    doc_id: int
    filename: str
    old_path: str
    action: str  # "repoint", "choose", "missing", "ok"
    new_path: Optional[str] = None
    candidates: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id, "filename": self.filename, "old_path": self.old_path,
            "action": self.action, "new_path": self.new_path, "candidates": self.candidates, "note": self.note,
        }


def _metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("file_metadata")
    if isinstance(raw, dict):
        return raw
    try:
        loaded = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _source_stem(row: Dict[str, Any]) -> str:
    """The stem the overlay's file was named from: the source clip's, as the
    record's metadata has it, else the display name without "-text"."""
    source = _metadata(row).get("source_filename")
    if source:
        return Path(str(source)).stem
    stem = Path(str(row.get("filename") or "")).stem
    stem = re.sub(r" \(\d+\)$", "", stem)
    return stem[: -len(_DISPLAY_SUFFIX)] if stem.endswith(_DISPLAY_SUFFIX) else stem


def _stored_path(root: Path, file: Path) -> str:
    """The path register_file stores for a file outside uploads: relative to
    the install root, absolute when the output folder lies outside it."""
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(file.resolve())


def plan_repair(
    rows: Iterable[Dict[str, Any]],
    *,
    root: Path,
    upload_dir: Path,
    overlay_dirs: Iterable[Path],
) -> List[Repair]:
    """One Repair per record stored under the old overlay path.

    ``rows`` are document records as dicts with id, filename, path and
    file_metadata; pass every record, not only the overlays, so files another
    record already names are not offered twice. Nothing is written.

    - "ok": the record already reaches a file.
    - "repoint": exactly one unclaimed overlay file fits; ``new_path`` is it.
    - "choose": several fit (a second overlay of the same clip was merged into
      this record); they are listed oldest first and nothing is picked.
    - "missing": no file on disk fits; the overlay is gone.
    """
    rows = list(rows)
    root, upload_dir = Path(root), Path(upload_dir)
    folders = [Path(d) for d in overlay_dirs if Path(d).is_dir()]
    claimed = {str(r.get("path") or "") for r in rows}
    plan: List[Repair] = []
    offered: set[str] = set()

    for row in sorted(rows, key=lambda r: int(r["id"])):
        path = str(row.get("path") or "")
        if not path.startswith(OLD_PATH_PREFIX):
            continue
        item = Repair(doc_id=int(row["id"]), filename=str(row.get("filename") or ""), old_path=path, action="missing")
        if (upload_dir / path).is_file() or (root / path).is_file():
            item.action, item.note = "ok", "the record's path reaches a file"
            plan.append(item)
            continue
        stem = _source_stem(row)
        pattern = re.compile(rf"^{re.escape(stem)}_(?:\d{{3}}|[0-9a-f]{{8}})\.mp4$")
        found: List[Path] = []
        for folder in folders:
            for file in folder.iterdir():
                stored = _stored_path(root, file)
                if file.is_file() and pattern.match(file.name) and stored not in claimed and stored not in offered:
                    found.append(file)
        found.sort(key=lambda f: (f.stat().st_mtime, f.name))
        item.candidates = [_stored_path(root, f) for f in found]
        if len(found) == 1:
            item.action, item.new_path = "repoint", item.candidates[0]
            offered.add(item.new_path)
            item.note = "one overlay file fits this record"
        elif found:
            item.action = "choose"
            item.note = (f"{len(found)} overlay files fit: later overlays of the same clip were merged into this "
                         "record. Not changed; pick one, or register the others as records of their own.")
        else:
            item.note = f"no file named {stem}_NNN.mp4 in the overlay folder"
        plan.append(item)
    return plan


def apply_plan(plan: Iterable[Repair], session, document_model) -> Dict[str, int]:
    """Carry out the "repoint" items. Records marked "choose", "missing" or
    "ok" are left as they are. Returns counts. Commits once at the end."""
    changed = skipped = 0
    for item in plan:
        if item.action != "repoint" or not item.new_path:
            continue
        doc = session.get(document_model, item.doc_id)
        if doc is None or doc.path != item.old_path:
            skipped += 1  # changed since the plan was made
            continue
        if session.query(document_model).filter(document_model.path == item.new_path).first() is not None:
            skipped += 1  # another record has taken that file since
            continue
        doc.path = item.new_path
        changed += 1
    session.commit()
    return {"repointed": changed, "skipped": skipped}


def overlay_dirs(root: Path) -> List[Path]:
    """Where overlays have been written: the configured output folder, and the
    install's own data/outputs, which the route used while it wrote relative
    to the working directory."""
    from backend import config

    seen: List[Path] = []
    for folder in (Path(config.OUTPUT_DIR) / "videos" / "text-overlay",
                   Path(root) / "data" / "outputs" / "videos" / "text-overlay"):
        if folder.resolve() not in [s.resolve() for s in seen]:
            seen.append(folder)
    return seen


def format_plan(plan: List[Repair]) -> str:
    counts: Dict[str, int] = {}
    for item in plan:
        counts[item.action] = counts.get(item.action, 0) + 1
    lines = [
        f"{len(plan)} text-overlay record(s) stored under the old path: "
        + ", ".join(f"{counts.get(k, 0)} {k}" for k in ("repoint", "choose", "missing", "ok"))
    ]
    for item in plan:
        if item.action == "repoint":
            lines.append(f"  #{item.doc_id} {item.filename}: {item.old_path} -> {item.new_path}")
        elif item.action == "choose":
            lines.append(f"  #{item.doc_id} {item.filename}: {len(item.candidates)} files fit, not changed: "
                         + ", ".join(item.candidates))
        elif item.action == "missing":
            lines.append(f"  #{item.doc_id} {item.filename}: {item.note}")
    return "\n".join(lines)
