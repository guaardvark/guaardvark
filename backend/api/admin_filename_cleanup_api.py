"""Admin clean-filenames action.

Phase 6 of plans/2026-04-29-filename-structure.md. One-shot retroactive
cleanup of legacy rows produced before Phases 1-5 established the
filename invariant. Two patterns get fixed:

1. Upload rows with the legacy 'YYYYMMDD_HHMMSS_<name>' on-disk path
   while Document.filename held the clean name — the timestamp prefix
   gets stripped, the file renamed in place, Document.path updated.
2. Generator rows with hex-stem names (`video_<hex>.mp4`,
   `<uuid>.wav`) — flagged for the user but not auto-renamed since
   inferring a sensible new name needs human judgement.

Always supports dry-run first. The user reviews the proposed changes,
then commits. Every actual rename writes a row to retention_audit so
the operation is auditable.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from flask import Blueprint, request

from backend.models import Document as DBDocument, db
from backend.services.retention_audit_service import record_deletion
from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

admin_filename_cleanup_bp = Blueprint(
    "admin_filename_cleanup", __name__, url_prefix="/api/admin"
)

# YYYYMMDD_HHMMSS_<rest> pattern produced by the legacy upload path. The
# stripped 'rest' becomes the new on-disk basename.
_LEGACY_UPLOAD_PREFIX = re.compile(r"^(\d{8}_\d{6})_(.+)$")

# Hex-stem patterns generators used to produce. Names matching these are
# flagged but NOT auto-renamed — picking a sensible replacement needs
# context that the cleanup script doesn't have (which generator? what
# date? what session?).
_HEX_STEM_PATTERNS = [
    re.compile(r"^video_[0-9a-f]{16,}\."),
    re.compile(r"^batch_[0-9a-f]{16,}/"),
    re.compile(r"^item_[0-9a-f]{16,}/"),
    re.compile(r"^[0-9a-f]{32}\."),
]


def _classify(doc: DBDocument) -> dict[str, Any]:
    """Inspect one Document row, return a classification dict.

    Output shape: {kind: 'clean'|'legacy_upload'|'hex_stem'|'divergent_other',
                   reason: str, proposed_filename: str|None, proposed_path: str|None}
    """
    path = doc.path or ""
    filename = doc.filename or ""
    basename = Path(path).name

    # Already invariant? Nothing to do.
    if filename == basename and not any(p.search(path) for p in _HEX_STEM_PATTERNS):
        return {"kind": "clean", "reason": "filename matches basename(path)"}

    # Legacy upload pattern: timestamped basename, clean filename column
    m = _LEGACY_UPLOAD_PREFIX.match(basename)
    if m and filename and filename == m.group(2):
        new_basename = m.group(2)
        new_path = (Path(path).parent / new_basename).as_posix() if Path(path).parent.as_posix() != "." else new_basename
        return {
            "kind": "legacy_upload",
            "reason": f"timestamp prefix '{m.group(1)}_' on disk",
            "proposed_filename": new_basename,
            "proposed_path": new_path,
        }

    # Generator hex-stem pattern
    if any(p.search(path) for p in _HEX_STEM_PATTERNS):
        return {
            "kind": "hex_stem",
            "reason": "generator-produced hex stem; needs manual rename",
            "proposed_filename": None,
            "proposed_path": None,
        }

    # filename != basename(path) but no recognized pattern
    return {
        "kind": "divergent_other",
        "reason": f"filename={filename!r} disagrees with basename(path)={basename!r}",
        "proposed_filename": basename,
        "proposed_path": path,
    }


@admin_filename_cleanup_bp.route("/clean-filenames", methods=["GET", "POST"])
def clean_filenames():
    """Audit + execute the legacy filename cleanup.

    GET: dry-run. Returns counts + a sample of proposed renames per kind.
    POST: dry-run unless body has {"commit": true}. On commit, performs
    the renames in a single DB transaction with disk renames in the
    same loop. Writes a retention_audit row.
    """
    # Decide commit mode.
    commit = False
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        commit = bool(body.get("commit", False))

    rows = DBDocument.query.order_by(DBDocument.id).all()
    classifications: dict[str, list[dict]] = {
        "clean": [],
        "legacy_upload": [],
        "hex_stem": [],
        "divergent_other": [],
    }
    for doc in rows:
        c = _classify(doc)
        c["doc_id"] = doc.id
        c["filename"] = doc.filename
        c["path"] = doc.path
        classifications[c["kind"]].append(c)

    summary = {kind: len(items) for kind, items in classifications.items()}

    if not commit:
        # Dry-run — return sample for each non-clean bucket.
        sample = {
            kind: classifications[kind][:20]
            for kind in ("legacy_upload", "hex_stem", "divergent_other")
        }
        return success_response({
            "mode": "dry_run",
            "summary": summary,
            "sample": sample,
            "actionable_count": summary["legacy_upload"],
            "note": (
                "POST with {commit: true} to apply renames to legacy_upload "
                "rows. hex_stem rows need manual review (generator outputs "
                "without enough context to pick a clean name)."
            ),
        })

    # Commit mode — rename legacy_upload entries.
    renamed = 0
    skipped_no_file = 0
    errors: list[dict] = []

    try:
        from backend.api.files_api import get_physical_path
    except Exception:
        return error_response("get_physical_path import failed", 500, "INTERNAL_ERROR")

    for c in classifications["legacy_upload"]:
        doc = db.session.get(DBDocument, c["doc_id"])
        if doc is None:
            continue
        new_path = c["proposed_path"]
        new_filename = c["proposed_filename"]
        if not new_path or not new_filename:
            continue

        old_physical = get_physical_path(doc.path)
        new_physical = get_physical_path(new_path)

        if not old_physical.is_file():
            skipped_no_file += 1
            continue

        if new_physical.exists():
            errors.append({"doc_id": doc.id, "error": f"target already exists: {new_physical}"})
            continue

        try:
            old_physical.rename(new_physical)
            doc.path = new_path
            doc.filename = new_filename
            renamed += 1
        except Exception as e:
            errors.append({"doc_id": doc.id, "error": str(e)})

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return error_response(f"DB commit failed: {e}", 500, "DB_COMMIT_FAILED")

    # Retention audit — even though this isn't a deletion, it's a bulk
    # mutation worth recording for legal/business contexts.
    record_deletion(
        actor="user",
        kind="filename",
        operation="bulk_rename_legacy",
        item_count=renamed,
        bytes_freed=None,
        parameters={
            "skipped_no_file": skipped_no_file,
            "errors_count": len(errors),
            "summary": summary,
        },
        triggered_by="admin_clean_filenames",
    )

    return success_response({
        "mode": "committed",
        "renamed": renamed,
        "skipped_no_file": skipped_no_file,
        "errors": errors[:20],
        "errors_total": len(errors),
        "summary": summary,
    })
