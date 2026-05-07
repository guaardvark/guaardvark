"""Cast Library — CRUD over Subjects. Reusable across Productions."""
from flask import Blueprint, request, jsonify
from backend.models import db, Subject

bp = Blueprint("cast_library_api", __name__, url_prefix="/api/cast-library")

VALID_KINDS = {"character", "environment", "prop"}


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
