"""Production pipeline REST API. Read/write the Production state machine."""
import logging
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

from backend.models import db, Production, Project
from backend.services.production_service import ProductionService

bp = Blueprint("production_api", __name__, url_prefix="/api/production")
log = logging.getLogger(__name__)


VALID_CAST_ACTIONS = {"use_existing_lora", "train_from_uploads", "train_from_generated"}


def _dispatch_lora_train(subject_id: int) -> str | None:
    """Dispatch a LoRA training Celery task."""
    from backend.celery_app import celery
    task = celery.send_task("lora_trainer.train_lora", args=[subject_id])
    return task.id


def _dispatch_storyboard_regen(shot_id: int, prompt_override: str | None) -> str | None:
    """Dispatch a single-shot storyboard regeneration via Celery."""
    from backend.celery_app import celery
    task = celery.send_task("production.regen_storyboard_shot", args=[shot_id, prompt_override])
    return task.id


def _shot_to_dict(shot):
    image_url = None
    if shot.storyboard_image_path:
        image_url = f"/api/production/{shot.production_id}/storyboard/shot/{shot.id}/image"
    return {
        "id": shot.id, "scene_number": shot.scene_number, "shot_number": shot.shot_number,
        "description": shot.description, "approved": shot.approved,
        "storyboard_image_path": shot.storyboard_image_path,
        "storyboard_image_url": image_url,
        "video_clip_path": shot.video_clip_path,
        "regen_count": shot.regen_count,
    }


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


@bp.get("")
def list_productions():
    productions = Production.query.order_by(Production.created_at.desc()).all()
    return jsonify({
        "productions": [
            {
                "id": p.id, "name": p.name, "status": p.status,
                "current_stage": p.current_stage, "project_id": p.project_id,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in productions
        ]
    })


@bp.get("/<int:prod_id>/subjects")
def get_production_subjects(prod_id):
    """Return the Subjects this production cares about — i.e. what the
    Screenwriter agent extracted from the script. The CastingPanel uses this
    to know which Subjects need a cast action.
    """
    from backend.models import Subject, ProductionSubject
    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404

    # Look up the actual Subject rows via the ProductionSubject join table.
    subjects = (
        db.session.query(Subject)
        .join(ProductionSubject)
        .filter(ProductionSubject.production_id == prod_id)
        .all()
    )

    out = []
    for s in subjects:
        out.append({
            "id": s.id, "name": s.name, "kind": s.kind,
            "description": s.description,
            "ref_image_paths": s.ref_image_paths or [],
            "lora_path": s.lora_path,
            "training_status": s.training_status,
        })

    return jsonify({"subjects": out})


@bp.get("/<int:prod_id>")
def get_production(prod_id):
    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404
    shots = [_shot_to_dict(s) for s in p.shots]
    return jsonify({
        "id": p.id, "name": p.name,
        "status": p.status, "current_stage": p.current_stage,
        "project_id": p.project_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "script_text": p.script_text,
        "settings_json": p.settings_json,
        "error_blob": p.error_blob,
        "shots": shots,
    })


@bp.post("/<int:prod_id>/cast/<int:subject_id>")
def cast_subject(prod_id, subject_id):
    body = request.get_json(silent=True) or {}
    action = body.get("action")

    if action not in VALID_CAST_ACTIONS:
        return jsonify({"error": f"action must be one of {sorted(VALID_CAST_ACTIONS)}"}), 400

    prod = db.session.get(Production, prod_id)
    if prod is None:
        return jsonify({"error": "production not found"}), 404

    from backend.models import Subject
    subj = db.session.get(Subject, subject_id)
    if subj is None:
        return jsonify({"error": "subject not found"}), 404

    training_job_id: str | None = None

    if action == "use_existing_lora":
        existing_id = body.get("existing_lora_id")
        if existing_id is None:
            return jsonify({"error": "existing_lora_id is required for use_existing_lora"}), 400
        existing = db.session.get(Subject, existing_id)
        if existing is None or not existing.lora_path:
            return jsonify({"error": "existing_lora_id not found or has no trained LoRA"}), 404
        subj.lora_path = existing.lora_path
        subj.training_status = "trained"

    elif action == "train_from_uploads":
        refs = body.get("ref_image_paths") or []
        if not refs:
            return jsonify({"error": "ref_image_paths is required for train_from_uploads"}), 400
        subj.ref_image_paths = refs
        subj.training_status = "training"
        try:
            training_job_id = _dispatch_lora_train(subj.id)
        except NotImplementedError:
            log.debug("LoRA train dispatch deferred (lora_trainer not yet wired)")
        except Exception as e:
            log.warning(f"LoRA train dispatch failed for subject {subj.id}: {e}")

    elif action == "train_from_generated":
        subj.training_status = "training"
        try:
            training_job_id = _dispatch_lora_train(subj.id)
        except NotImplementedError:
            log.debug("LoRA train dispatch deferred (lora_trainer not yet wired)")
        except Exception as e:
            log.warning(f"LoRA train dispatch failed for subject {subj.id}: {e}")

    db.session.commit()
    return jsonify({
        "subject_id": subj.id,
        "training_status": subj.training_status,
        "training_job_id": training_job_id,
    })


@bp.post("/<int:prod_id>/casting/confirm")
def confirm_casting(prod_id):
    """User-gated transition from casting to cinematography after all subjects have a cast plan."""
    from backend.models import Subject, ProductionSubject

    prod = db.session.get(Production, prod_id)
    if prod is None:
        return jsonify({"error": "production not found"}), 404
    if prod.current_stage != "casting":
        return jsonify({"error": f"production is at stage '{prod.current_stage}', not casting"}), 409

    subjects = (
        db.session.query(Subject)
        .join(ProductionSubject)
        .filter(ProductionSubject.production_id == prod_id)
        .all()
    )
    if not subjects:
        return jsonify({"error": "production has no subjects to cast"}), 400

    incomplete = [
        {"id": s.id, "name": s.name, "training_status": s.training_status}
        for s in subjects
        if not (s.lora_path or s.training_status in {"training", "trained"})
    ]
    if incomplete:
        return jsonify({"error": "all production subjects must be cast before continuing", "incomplete_subjects": incomplete}), 400

    svc = ProductionService(db.session)
    advanced = svc.advance_if_predecessor(prod_id, expected_predecessor="casting")
    if advanced:
        try:
            svc.dispatch_agent(prod_id, "cinematographer")
        except NotImplementedError:
            log.debug("Cinematographer dispatch deferred (swarm not yet wired)")
        except Exception as e:
            log.warning(f"Cinematographer dispatch failed for production {prod_id}: {e}")

    db.session.refresh(prod)
    return jsonify({
        "production_id": prod_id,
        "current_stage": prod.current_stage,
        "status": prod.status,
        "subjects_confirmed": len(subjects),
    })


@bp.post("/<int:prod_id>/storyboard/approve")
def approve_storyboard(prod_id):
    prod = db.session.get(Production, prod_id)
    if prod is None:
        return jsonify({"error": "production not found"}), 404
    if prod.current_stage != "awaiting_approval":
        return jsonify({"error": f"production is at stage '{prod.current_stage}', not awaiting_approval"}), 409

    from backend.models import ProductionShot
    shots = ProductionShot.query.filter_by(production_id=prod_id).all()
    for s in shots:
        s.approved = True
    db.session.commit()

    svc = ProductionService(db.session)
    if svc.advance_if_predecessor(prod_id, expected_predecessor="awaiting_approval"):
        try:
            svc.dispatch_agent(prod_id, "editor")
        except NotImplementedError:
            log.debug("Editor dispatch deferred (swarm not yet wired)")
        except Exception as e:
            log.warning(f"Editor dispatch failed for production {prod_id}: {e}")

    db.session.refresh(prod)
    return jsonify({
        "production_id": prod_id,
        "current_stage": prod.current_stage,
        "shots_approved": len(shots),
    })


@bp.post("/<int:prod_id>/storyboard/shot/<int:shot_id>/regenerate")
def regenerate_shot(prod_id, shot_id):
    from backend.models import ProductionShot
    shot = db.session.get(ProductionShot, shot_id)
    if shot is None or shot.production_id != prod_id:
        return jsonify({"error": "shot not found in this production"}), 404

    body = request.get_json(silent=True) or {}
    feedback = body.get("feedback")
    prompt_override = body.get("prompt_override")

    shot.regen_count = (shot.regen_count or 0) + 1
    shot.approved = False
    db.session.commit()

    regen_job_id: str | None = None
    from backend.celery_app import celery
    try:
        if feedback:
            task = celery.send_task("production.regen_shot_plan", args=[shot_id, feedback])
            regen_job_id = task.id
        else:
            regen_job_id = _dispatch_storyboard_regen(shot_id, prompt_override)
    except NotImplementedError:
        log.debug("Regen dispatch deferred (Celery task not yet wired)")
    except Exception as e:
        log.warning(f"Regen dispatch failed for shot {shot_id}: {e}")

    return jsonify({
        "shot_id": shot_id,
        "regen_count": shot.regen_count,
        "regen_job_id": regen_job_id,
    })


@bp.get("/<int:prod_id>/storyboard/shot/<int:shot_id>/image")
def storyboard_shot_image(prod_id, shot_id):
    from backend.config import STORAGE_DIR
    from backend.models import ProductionShot

    shot = db.session.get(ProductionShot, shot_id)
    if shot is None or shot.production_id != prod_id:
        return jsonify({"error": "shot not found in this production"}), 404
    if not shot.storyboard_image_path:
        return jsonify({"error": "shot has no storyboard image"}), 404

    image_path = Path(shot.storyboard_image_path).resolve()
    storage_root = Path(STORAGE_DIR).resolve()
    try:
        image_path.relative_to(storage_root)
    except ValueError:
        return jsonify({"error": "storyboard image is outside storage"}), 403

    if not image_path.is_file():
        return jsonify({"error": "storyboard image file not found"}), 404
    return send_file(image_path)
