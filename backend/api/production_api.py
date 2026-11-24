"""Production pipeline REST API. Read/write the Production state machine."""
import logging
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

from backend.models import db, Production, Project
from backend.services.pipeline_service import dispatch_report
from backend.services.production_service import ProductionService
from backend.services.swarm.script_markup import effective_cast_required

# Repo root for resolving relative lora_path values (stored as "data/training/loras/x.safetensors"
# or as absolute paths). backend/api/production_api.py -> parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _lora_on_disk(lora_path: str | None) -> bool:
    """Pre-cast freshness gate: a subject claiming 'trained' must have a real LoRA file on disk.
    Mirrors scripts/verify_training_outputs' >100KB check (catches a missing file or the ~8-byte
    mock-trainer stub). This is what would have blocked Subjects 8 (file missing) and 10 (stale)
    from being cast while falsely marked trained."""
    if not lora_path:
        return False
    p = Path(lora_path) if Path(lora_path).is_absolute() else _REPO_ROOT / lora_path
    try:
        return p.exists() and p.stat().st_size > 100_000
    except OSError:
        return False

bp = Blueprint("production_api", __name__, url_prefix="/api/production")
log = logging.getLogger(__name__)


def _production_cast(prod_id: int) -> list:
    """(Subject, ProductionSubject) for every subject linked to the production."""
    from backend.models import Subject, ProductionSubject
    return (
        db.session.query(Subject, ProductionSubject)
        .join(ProductionSubject, ProductionSubject.subject_id == Subject.id)
        .filter(ProductionSubject.production_id == prod_id)
        .all()
    )


def _cast_required(subject, link) -> bool:
    """Whether this production needs a trained LoRA for ``subject``.

    The production's own script decides, through the pin the screenwriter stored
    on the link; a link with no pin defers to the Subject, then to its kind.
    """
    pinned = getattr(link, "cast_required", None)
    return effective_cast_required(
        pinned if pinned is not None else subject.cast_required, subject.kind
    )


VALID_CAST_ACTIONS = {"use_existing_lora", "train_from_uploads", "train_from_generated", "as_described"}


def _dispatch_lora_train(subject_id: int) -> str | None:
    """Dispatch LoRA training with unified progress (same path as Cast Studio)."""
    from backend.services.lora_train_dispatch import dispatch_lora_train

    result = dispatch_lora_train(subject_id)
    return result["job_id"]


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
        "approved_by": shot.approved_by,
        "curator_advice": shot.curator_advice,
        "voice_record": shot.voice_record,
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

    settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    video_model = settings.get("video_model")
    if video_model:
        from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, model_capabilities
        if video_model not in VIDEO_MODEL_REGISTRY or not model_capabilities(video_model):
            return jsonify({"error": f"video_model '{video_model}' is not a video model"}), 400
    aspect_ratio = settings.get("aspect_ratio")
    if aspect_ratio is not None:
        from backend.services.production_service import FILM_ASPECT_RATIOS
        if aspect_ratio not in FILM_ASPECT_RATIOS:
            return jsonify({"error": f"aspect_ratio must be one of {list(FILM_ASPECT_RATIOS)}"}), 400

    svc = ProductionService(db.session)
    p = svc.create(name=name, script_text=script_text, project_id=project_id, settings=settings)

    # C1: advance to screenwriting and dispatch the agent so the pipeline
    # actually starts. A dispatch failure is reported, not fatal: state still
    # moved forward so the next boot's resume_all picks it up.
    dispatch = {}
    if svc.advance_if_predecessor(p.id, expected_predecessor="draft"):
        dispatch = dispatch_report(svc.try_dispatch(p.id, "screenwriter"))
        db.session.refresh(p)

    return jsonify({
        "id": p.id, "name": p.name,
        "status": p.status, "current_stage": p.current_stage,
        "project_id": p.project_id,
        **dispatch,
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
    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404

    out = []
    for s, link in _production_cast(prod_id):
        out.append({
            "id": s.id, "name": s.name, "kind": s.kind,
            "description": s.description,
            # What this production's script said about the subject; the library
            # description above is shared with every production that casts it.
            "script_description": link.script_description,
            "ref_image_paths": s.ref_image_paths or [],
            "lora_path": s.lora_path,
            "training_status": s.training_status,
            # Resolved cast requirement: True = identity-locked, needs a LoRA
            # before casting can be confirmed; False = generated inline.
            "cast_required": _cast_required(s, link),
        })

    return jsonify({"subjects": out})


@bp.get("/<int:prod_id>")
def get_production(prod_id):
    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404
    shots = [_shot_to_dict(s) for s in sorted(p.shots, key=lambda s: (s.scene_number or 0, s.shot_number or 0))]
    return jsonify({
        "id": p.id, "name": p.name,
        "status": p.status, "current_stage": p.current_stage,
        "project_id": p.project_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "script_text": p.script_text,
        "settings_json": p.settings_json,
        "error_blob": p.error_blob,
        "final_video": _final_video(p),
        "shots": shots,
    })


def _final_video(p):
    """The newest finished film of a production, or None before the first one."""
    from backend.services.production_documents import production_outputs
    films = production_outputs(p, "final")
    if not films:
        return None
    doc = films[0]
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "path": doc.path,
        "url": f"/api/files/document/{doc.id}/download",
        "created_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
    }


def _poster_url(p):
    """The first storyboard frame in script order, or None when nothing was drawn."""
    shots = sorted(p.shots, key=lambda s: (s.scene_number or 0, s.shot_number or 0))
    first = next((s for s in shots if s.storyboard_image_path), None)
    if first is None:
        return None
    return f"/api/production/{p.id}/storyboard/shot/{first.id}/image"


@bp.get("/films")
def list_films():
    """Every production's finished film, newest first. The Media Library lists
    these beside the Video Gen batches; the films themselves are filed in
    Documents under productions/<id>/final."""
    films = []
    for p in Production.query.all():
        film = _final_video(p)
        if film is None:
            continue
        films.append({
            "production_id": p.id,
            "name": p.name,
            "poster_url": _poster_url(p),
            **film,
        })
    films.sort(key=lambda f: f["created_at"] or "", reverse=True)
    return jsonify({"films": films})


@bp.delete("/<int:prod_id>")
def delete_production(prod_id):
    """Delete a production and cascaded shots / join rows. Subjects are kept."""
    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404
    name = p.name
    db.session.delete(p)
    db.session.commit()
    log.info("Deleted production %s (%s)", prod_id, name)
    return jsonify({"deleted": prod_id, "name": name})


@bp.post("/<int:prod_id>/retry")
def retry_production(prod_id):
    """Clear failed_* status and re-dispatch the agent for current_stage.

    User-gated stages (casting, awaiting_approval) only clear the failed flag.
    Non-failed idle stages (e.g. stuck screenwriting) also re-dispatch.
    """
    from backend.services.production_service import STAGE_TO_AGENT

    p = db.session.get(Production, prod_id)
    if p is None:
        return jsonify({"error": "not_found"}), 404

    stage = p.current_stage or "draft"
    status = p.status or ""
    was_failed = status.startswith("failed")

    # Restore active status for this stage
    p.status = stage
    p.error_blob = None
    db.session.commit()

    agent = STAGE_TO_AGENT.get(stage)
    dispatched = False
    if agent:
        warning = ProductionService(db.session).try_dispatch(p.id, agent)
        if warning:
            return jsonify({
                "id": p.id,
                "status": p.status,
                "current_stage": p.current_stage,
                **dispatch_report(warning),
            }), 200
        dispatched = True

    return jsonify({
        "id": p.id,
        "status": p.status,
        "current_stage": p.current_stage,
        "dispatched": dispatched,
        "agent": agent,
        "was_failed": was_failed,
        "message": (
            f"Re-dispatched {agent}" if dispatched
            else f"Cleared failure; stage '{stage}' is user-gated — continue in the UI"
        ),
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
    needs_dispatch = False

    if action == "use_existing_lora":
        existing_id = body.get("existing_lora_id")
        if existing_id is None:
            return jsonify({"error": "existing_lora_id is required for use_existing_lora"}), 400
        existing = db.session.get(Subject, existing_id)
        if existing is None or not existing.lora_path:
            return jsonify({"error": "existing_lora_id not found or has no trained LoRA"}), 404
        subj.lora_path = existing.lora_path
        subj.training_status = "trained"
        subj.training_error = None

    elif action == "train_from_uploads":
        refs = body.get("ref_image_paths") or []
        if not refs:
            return jsonify({"error": "ref_image_paths is required for train_from_uploads"}), 400
        subj.ref_image_paths = refs
        subj.training_status = "training"
        needs_dispatch = True

    elif action == "train_from_generated":
        subj.training_status = "training"
        needs_dispatch = True

    elif action == "as_described":
        # Drawn from the script's description in each shot, with no LoRA: an
        # unseen voice, a crowd, or a figure whose face may vary between shots.
        # The choice is stored on this production's link, so the Cast Library
        # subject other films share keeps its own setting.
        from backend.models import ProductionSubject
        link = ProductionSubject.query.filter_by(
            production_id=prod_id, subject_id=subj.id
        ).first()
        if link is None:
            return jsonify({"error": "subject is not part of this production"}), 404
        link.cast_required = False

    # Commit the status transition BEFORE dispatching the Celery task. The
    # trainer reads training_status in a separate worker process/connection and
    # skips anything not already committed as 'training' (idempotency guard in
    # lora_trainer_tasks.py). Dispatching pre-commit raced the worker against
    # this web transaction and produced zombie 'training' rows whose job
    # silently skipped on a stale status. Commit first, then dispatch.
    db.session.commit()

    warning = None
    if needs_dispatch:
        try:
            training_job_id = _dispatch_lora_train(subj.id)
        except NotImplementedError:
            log.debug("LoRA train dispatch deferred (lora_trainer not yet wired)")
        except Exception as e:
            # dispatch_lora_train has put the subject back to untrained.
            from backend.celery_dispatch import TaskNotStarted

            log.warning(f"LoRA train dispatch failed for subject {subj.id}: {e}")
            warning = (
                f"LoRA training was not started: {e.why}. Start Redis (./start.sh starts it) and try again."
                if isinstance(e, TaskNotStarted)
                else f"LoRA training was not started: {e}"
            )

    return jsonify({
        "subject_id": subj.id,
        "training_status": subj.training_status,
        "training_job_id": training_job_id,
        **({"dispatched": False, "warning": warning} if warning else {}),
    })


@bp.post("/<int:prod_id>/casting/confirm")
def confirm_casting(prod_id):
    """User-gated transition from casting to cinematography after all subjects have a cast plan."""
    prod = db.session.get(Production, prod_id)
    if prod is None:
        return jsonify({"error": "production not found"}), 404
    if prod.current_stage != "casting":
        return jsonify({"error": f"production is at stage '{prod.current_stage}', not casting"}), 409

    cast = _production_cast(prod_id)
    if not cast:
        return jsonify({"error": "production has no subjects to cast"}), 400

    # Only identity-locked cast members (cast_required) must have a trained
    # LoRA. Props/environments are generated inline from their description and
    # never block casting — that is what kept a "Microphone" prop from being
    # confirmable when the screenwriter over-extracted it.
    incomplete = [
        {"id": s.id, "name": s.name, "training_status": s.training_status}
        for s, link in cast
        if _cast_required(s, link)
        and not (s.lora_path or s.training_status in {"training", "trained"})
    ]
    if incomplete:
        return jsonify({"error": "all production subjects must be cast before continuing", "incomplete_subjects": incomplete}), 400

    # Pre-cast freshness gate: a cast member marked 'trained' must have a real LoRA file on disk.
    # Without this, a stale/missing artifact (Subjects 8 & 10 were exactly this) sails through and
    # the render silently produces an off-model character.
    stale = [
        {"id": s.id, "name": s.name, "lora_path": s.lora_path}
        for s, link in cast
        if _cast_required(s, link)
        and s.training_status == "trained"
        and not _lora_on_disk(s.lora_path)
    ]
    if stale:
        return jsonify({
            "error": "some cast members are marked 'trained' but their LoRA file is missing or "
                     "stale on disk — retrain before casting",
            "stale_subjects": stale,
        }), 400

    svc = ProductionService(db.session)
    dispatch = {}
    if svc.advance_if_predecessor(prod_id, expected_predecessor="casting"):
        dispatch = dispatch_report(svc.try_dispatch(prod_id, "cinematographer"))

    db.session.refresh(prod)
    return jsonify({
        "production_id": prod_id,
        "current_stage": prod.current_stage,
        "status": prod.status,
        "subjects_confirmed": len(cast),
        **dispatch,
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

    # A frame the curator flagged is approved only when the caller says so
    # explicitly, after the person has seen the list.
    flagged = [s for s in shots if _curator_flagged(s)]
    body = request.get_json(silent=True) or {}
    if flagged and body.get("confirm_flagged") is not True:
        listed = ", ".join(f"{s.scene_number}.{s.shot_number}" for s in flagged)
        return jsonify({
            "error": (
                f"{len(flagged)} shot(s) were flagged by the curator: {listed}. "
                "Approve again with confirm_flagged to render them anyway."
            ),
            "flagged_shots": [
                {
                    "id": s.id, "scene_number": s.scene_number, "shot_number": s.shot_number,
                    "reason": (s.curator_advice or {}).get("reason"),
                }
                for s in flagged
            ],
        }), 409

    for s in shots:
        s.approved = True
        s.approved_by = "person"
    db.session.commit()

    svc = ProductionService(db.session)
    dispatch = {}
    if svc.advance_if_predecessor(prod_id, expected_predecessor="awaiting_approval"):
        dispatch = dispatch_report(svc.try_dispatch(prod_id, "editor"))

    db.session.refresh(prod)
    return jsonify({
        "production_id": prod_id,
        "current_stage": prod.current_stage,
        "shots_approved": len(shots),
        "flagged_approved": [s.id for s in flagged],
        **dispatch,
    })


def _curator_flagged(shot) -> bool:
    """True when the curator flagged this frame and no person has approved it."""
    if (shot.curator_advice or {}).get("verdict") != "flag":
        return False
    return not (shot.approved and shot.approved_by == "person")


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
    shot.approved_by = None
    # The advice was about the frame being replaced.
    shot.curator_advice = None
    db.session.commit()

    regen_job_id: str | None = None
    warning: str | None = None
    from backend.celery_app import celery
    from backend.celery_dispatch import TaskNotStarted
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
        warning = (
            f"The shot was not regenerated: {e.why}. Start Redis (./start.sh starts it) and try again."
            if isinstance(e, TaskNotStarted)
            else f"The shot was not regenerated: {str(e) or type(e).__name__}."
        )

    return jsonify({
        "shot_id": shot_id,
        "regen_count": shot.regen_count,
        "regen_job_id": regen_job_id,
        **({"dispatched": False, "warning": warning} if warning else {}),
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
