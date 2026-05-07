"""Production pipeline REST API. Read/write the Production state machine."""
from flask import Blueprint, request, jsonify
from backend.models import db, Production
from backend.services.production_service import ProductionService

bp = Blueprint("production_api", __name__, url_prefix="/api/production")


@bp.post("")
def create():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    script_text = body.get("script_text")
    if not name or not script_text:
        return jsonify({"error": "name and script_text are required"}), 400
    p = ProductionService(db.session).create(
        name=name, script_text=script_text,
        project_id=body.get("project_id"),
    )
    return jsonify({
        "id": p.id, "name": p.name,
        "status": p.status, "current_stage": p.current_stage,
        "project_id": p.project_id,
    }), 201


@bp.get("/<int:prod_id>")
def get_production(prod_id):
    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404
    shots = [
        {
            "id": s.id, "scene_number": s.scene_number, "shot_number": s.shot_number,
            "description": s.description, "approved": s.approved,
            "storyboard_image_path": s.storyboard_image_path,
            "video_clip_path": s.video_clip_path,
        }
        for s in p.shots
    ]
    return jsonify({
        "id": p.id, "name": p.name,
        "status": p.status, "current_stage": p.current_stage,
        "project_id": p.project_id,
        "script_text": p.script_text,
        "settings_json": p.settings_json,
        "shots": shots,
    })
