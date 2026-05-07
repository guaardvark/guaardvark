"""Cast Library — CRUD over Subjects. Reusable across Productions."""
import logging
import os
from pathlib import Path

from flask import Blueprint, current_app, request, jsonify
from werkzeug.utils import secure_filename

from backend.models import db, Subject

bp = Blueprint("cast_library_api", __name__, url_prefix="/api/cast-library")
log = logging.getLogger(__name__)

VALID_KINDS = {"character", "environment", "prop"}

# Standard image formats — anything else is rejected at upload time.
_ALLOWED_REF_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_MAX_REF_BYTES = 25 * 1024 * 1024  # 25 MB per image — generous, but caps runaway uploads.


def _cast_ref_dir(subject_id: int) -> Path:
    """Where reference images for a Subject live on disk. Created lazily."""
    base = Path(current_app.config.get("DATA_DIR") or "data")
    target = base / "cast_refs" / str(subject_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _serialize(s: Subject) -> dict:
    return {
        "id": s.id, "kind": s.kind, "name": s.name,
        "description": s.description,
        "ref_image_paths": s.ref_image_paths or [],
        "lora_path": s.lora_path,
        "lora_version": s.lora_version,
        "training_status": s.training_status,
    }


@bp.get("")
def list_subjects():
    subjects = Subject.query.order_by(Subject.created_at.desc()).all()
    return jsonify({"subjects": [_serialize(s) for s in subjects]})


@bp.post("/subjects")
def create_subject():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    name = body.get("name")
    if kind not in VALID_KINDS:
        return jsonify({"error": f"kind must be one of {sorted(VALID_KINDS)}"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400
    s = Subject(
        kind=kind, name=name,
        description=body.get("description") or "",
        ref_image_paths=body.get("ref_image_paths") or [],
    )
    db.session.add(s); db.session.commit()
    return jsonify(_serialize(s)), 201


@bp.delete("/subjects/<int:subject_id>")
def delete_subject(subject_id):
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(s); db.session.commit()
    return "", 204


@bp.post("/subjects/<int:subject_id>/upload-refs")
def upload_subject_refs(subject_id):
    """Drag-and-drop receiver for reference images. Accepts one or more
    multipart files under the ``files`` field, saves them under
    ``data/cast_refs/<subject_id>/``, appends the resolved paths onto
    ``Subject.ref_image_paths``, and returns the updated subject.

    The user-facing flow expects no path-typing — the frontend drops images
    here and the server owns persistence.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "subject not found"}), 404

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files (expected multipart field 'files')"}), 400

    target_dir = _cast_ref_dir(subject_id)
    saved_paths: list[str] = []
    skipped: list[dict] = []

    for f in files:
        if not f or not f.filename:
            continue
        safe_name = secure_filename(f.filename) or ""
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_REF_EXTS:
            skipped.append({"name": f.filename, "reason": f"unsupported extension {ext!r}"})
            continue

        # Resolve collisions by appending -1, -2, … so multiple uploads with
        # the same source filename don't clobber each other.
        stem = Path(safe_name).stem or "ref"
        candidate = target_dir / f"{stem}{ext}"
        n = 1
        while candidate.exists():
            candidate = target_dir / f"{stem}-{n}{ext}"
            n += 1

        # Stream-write with a per-file size cap so a malicious / runaway
        # upload can't fill disk. Anything that goes wrong inside the loop
        # (network drop, disk full, write error) must clean up the partial
        # file — otherwise we leave half-written turds in cast_refs/.
        written = 0
        oversized = False
        write_error = None
        try:
            with open(candidate, "wb") as out:
                while True:
                    chunk = f.stream.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_REF_BYTES:
                        oversized = True
                        break
                    out.write(chunk)
        except OSError as e:
            write_error = e
        if oversized or write_error is not None:
            candidate.unlink(missing_ok=True)
            reason = "too large" if oversized else f"write failed: {write_error}"
            skipped.append({"name": f.filename, "reason": reason})
            continue
        saved_paths.append(str(candidate))

    s.ref_image_paths = list(s.ref_image_paths or []) + saved_paths
    db.session.commit()

    return jsonify({
        "subject": _serialize(s),
        "saved": saved_paths,
        "skipped": skipped,
    })
