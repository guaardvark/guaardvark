"""Production pipeline REST API. Read/write the Production state machine."""
import logging

from flask import Blueprint, request, jsonify

from backend.models import db, Production, Project
from backend.services.production_service import ProductionService

bp = Blueprint("production_api", __name__, url_prefix="/api/production")
log = logging.getLogger(__name__)


@bp.post("")
def create():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    script_text = body.get("script_text")
    project_id = body.get("project_id")

    if not name or not script_text:
        return jsonify({"error": "name and script_text are required"}), 400

    # M5: validate project_id BEFORE inserting, so a bad ref is a 400 not a 500.
    if project_id is not None and db.session.get(Project, project_id) is None:
        return jsonify({"error": f"project_id {project_id} not found"}), 400

    svc = ProductionService(db.session)
    p = svc.create(name=name, script_text=script_text, project_id=project_id)

    # C1: advance to screenwriting and dispatch the agent so the pipeline
    # actually starts. Tolerate NotImplementedError (swarm not wired yet) —
    # state still moved forward so the next boot's resume_all picks it up.
    if svc.advance_if_predecessor(p.id, expected_predecessor="draft"):
        try:
            svc.dispatch_agent(p.id, "screenwriter")
        except NotImplementedError:
            log.debug("Screenwriter dispatch deferred (swarm not yet wired)")
        except Exception as e:
            log.warning(f"Screenwriter dispatch failed for production {p.id}: {e}")
        db.session.refresh(p)

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
