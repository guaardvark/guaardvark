"""REST API for self-improvement management and kill switch controls."""
import json
import logging
import os
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

self_improvement_bp = Blueprint("self_improvement", __name__, url_prefix="/api/self-improvement")


@self_improvement_bp.route("/status", methods=["GET"])
def get_status():
    """Get self-improvement system status."""
    from backend.models import db, SystemSetting, SelfImprovementRun

    enabled_setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
    locked_setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()

    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")

    last_run = db.session.query(SelfImprovementRun).order_by(
        SelfImprovementRun.timestamp.desc()
    ).first()

    total_fixes = db.session.query(SelfImprovementRun).filter_by(status="success").count()

    return success_response(data={
        "enabled": enabled_setting.value.lower() == "true" if enabled_setting else False,
        "codebase_locked": (
            (locked_setting and locked_setting.value.lower() == "true") or
            os.path.exists(lock_file)
        ),
        "last_run": last_run.to_dict() if last_run else None,
        "total_fixes": total_fixes,
    })


@self_improvement_bp.route("/toggle", methods=["POST"])
def toggle_self_improvement():
    """Enable or disable self-improvement."""
    from backend.models import db, SystemSetting
    data = request.get_json()
    if not data or "enabled" not in data:
        return error_response("enabled field is required", 400)

    enabled = str(data["enabled"]).lower() == "true"
    setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
    if setting:
        setting.value = str(enabled).lower()
    else:
        db.session.add(SystemSetting(key="self_improvement_enabled", value=str(enabled).lower()))
    db.session.commit()

    logger.info(f"Self-improvement {'enabled' if enabled else 'disabled'} by user")
    return success_response(data={"enabled": enabled})


@self_improvement_bp.route("/lock-codebase", methods=["POST"])
def lock_codebase():
    """Lock or unlock the codebase."""
    from backend.models import db, SystemSetting
    data = request.get_json()
    if not data or "locked" not in data:
        return error_response("locked field is required", 400)

    locked = str(data["locked"]).lower() == "true"

    setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
    if setting:
        setting.value = str(locked).lower()
    else:
        db.session.add(SystemSetting(key="codebase_locked", value=str(locked).lower()))
    db.session.commit()

    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
    if locked:
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(f"LOCKED_BY=user\nTIMESTAMP={__import__('datetime').datetime.now().isoformat()}\n")
    else:
        if os.path.exists(lock_file):
            os.remove(lock_file)

    logger.info(f"Codebase {'locked' if locked else 'unlocked'} by user")
    return success_response(data={"locked": locked})


@self_improvement_bp.route("/runs", methods=["GET"])
def get_runs():
    """Get self-improvement run history."""
    from backend.models import db, SelfImprovementRun
    limit = request.args.get("limit", 20, type=int)
    offset = request.args.get("offset", 0, type=int)

    runs = db.session.query(SelfImprovementRun).order_by(
        SelfImprovementRun.timestamp.desc()
    ).offset(offset).limit(limit).all()

    total = db.session.query(SelfImprovementRun).count()

    return success_response(data={
        "runs": [r.to_dict() for r in runs],
        "total": total,
    })


@self_improvement_bp.route("/task", methods=["POST"])
def submit_task():
    """Submit a directed improvement task (async via Celery)."""
    data = request.get_json() or {}
    description = data.get("description", "")
    if not description:
        return error_response("Task description is required", 400)
    try:
        from backend.celery_app import celery
        task = celery.send_task("self_improvement.run_directed_async", args=[description])
        return success_response(data={"task_id": task.id, "status": "dispatched"},
                                message="Directed task dispatched")
    except Exception as e:
        logger.error(f"Failed to dispatch directed task: {e}", exc_info=True)
        return error_response(str(e), 500)


@self_improvement_bp.route("/trigger", methods=["POST"])
def trigger_check():
    """Trigger a self-improvement check (async via Celery)."""
    try:
        from backend.celery_app import celery
        task = celery.send_task("self_improvement.run_check_async")
        return success_response(data={"task_id": task.id, "status": "dispatched"},
                                message="Self-improvement check dispatched")
    except Exception as e:
        logger.error(f"Failed to dispatch self-check: {e}", exc_info=True)
        return error_response(str(e), 500)


@self_improvement_bp.route("/pending-fixes", methods=["GET"])
def list_pending_fixes():
    """List all pending fixes, optionally filtered by status."""
    from backend.models import db, PendingFix
    status_filter = request.args.get("status")
    query = db.session.query(PendingFix).order_by(PendingFix.created_at.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)
    limit = min(int(request.args.get("limit", 50)), 100)
    fixes = query.limit(limit).all()
    return success_response(data=[f.to_dict() for f in fixes])


@self_improvement_bp.route("/pending-fixes/<int:fix_id>/approve", methods=["POST"])
def approve_fix(fix_id):
    """Approve a pending fix for application."""
    from backend.models import db, PendingFix
    from datetime import datetime
    fix = db.session.get(PendingFix, fix_id)
    if not fix:
        return error_response("Fix not found", 404)
    if fix.status not in ("proposed", "triaged"):
        return error_response(f"Cannot approve fix in status: {fix.status}", 400)
    data = request.get_json() or {}
    fix.status = "approved"
    fix.reviewed_by = data.get("reviewer", "user")
    fix.review_notes = data.get("notes", "")
    fix.reviewed_at = datetime.now()
    db.session.commit()
    return success_response(data=fix.to_dict(), message="Fix approved")


@self_improvement_bp.route("/pending-fixes/<int:fix_id>/reject", methods=["POST"])
def reject_fix(fix_id):
    """Reject a pending fix."""
    from backend.models import db, PendingFix
    from datetime import datetime
    fix = db.session.get(PendingFix, fix_id)
    if not fix:
        return error_response("Fix not found", 404)
    data = request.get_json() or {}
    fix.status = "rejected"
    fix.reviewed_by = data.get("reviewer", "user")
    fix.review_notes = data.get("notes", "")
    fix.reviewed_at = datetime.now()
    db.session.commit()
    return success_response(data=fix.to_dict(), message="Fix rejected")


@self_improvement_bp.route("/pending-fixes/<int:fix_id>/apply", methods=["POST"])
def apply_fix(fix_id):
    """Apply an approved fix to the filesystem."""
    from backend.models import db, PendingFix
    from datetime import datetime
    fix = db.session.get(PendingFix, fix_id)
    if not fix:
        return error_response("Fix not found", 404)
    if fix.status != "approved":
        return error_response(f"Fix must be approved before applying (current: {fix.status})", 400)
    try:
        filepath = fix.file_path
        if not os.path.exists(filepath):
            return error_response(f"File not found: {filepath}", 404)
        if fix.original_content and fix.proposed_new_content:
            from backend.tools.llama_code_tools import edit_code
            result = edit_code(filepath, fix.original_content, fix.proposed_new_content)
            if result.startswith("ERROR"):
                return error_response(f"Failed to apply: {result}", 500)
        else:
            return error_response("Fix is missing original or new content", 400)
        fix.status = "applied"
        fix.applied_at = datetime.now()
        db.session.commit()
        return success_response(data=fix.to_dict(), message="Fix applied successfully")
    except Exception as e:
        logger.error(f"Failed to apply fix {fix_id}: {e}", exc_info=True)
        return error_response(str(e), 500)


@self_improvement_bp.route("/servo/rotate-archive", methods=["POST"])
def rotate_servo_archive():
    """Rotate the servo archive — move poisoned data to backup, start clean."""
    try:
        from backend.services.servo_knowledge_store import get_servo_archive
        archive = get_servo_archive()
        data = request.get_json() or {}
        reason = data.get("reason", "manual_cleanup")
        backup_path = archive.rotate_archive(reason=reason)
        if backup_path:
            return success_response(
                data={"backup_path": backup_path},
                message="Archive rotated — fresh start. Go click some stuff."
            )
        return success_response(data={}, message="No archive to rotate (already clean)")
    except Exception as e:
        logger.error(f"Archive rotation failed: {e}")
        return error_response(str(e), 500)


@self_improvement_bp.route("/servo/optimize", methods=["POST"])
def trigger_servo_optimization():
    """Trigger servo optimization — analyze archive and propose scale corrections."""
    try:
        from backend.services.self_improvement_service import get_self_improvement_service
        service = get_self_improvement_service()
        result = service.optimize_servo()
        return success_response(data=result)
    except Exception as e:
        logger.error(f"Servo optimization failed: {e}")
        return error_response(str(e), 500)


@self_improvement_bp.route("/distill", methods=["POST"])
def trigger_distillation():
    """Manually trigger learning distillation.

    Body (optional):
        task: str — task description to distill
        steps: list — step dicts [{action_type, target, text, keys, failed, ...}]

    If no body provided, distills the most recent successful agent task.
    """
    try:
        data = request.get_json(silent=True) or {}
        task = data.get("task", "")
        steps = data.get("steps", [])

        if not task or not steps:
            # Pull from the most recent agent task
            from backend.services.agent_control_service import get_agent_control_service
            service = get_agent_control_service()
            last_result = getattr(service, '_last_result', None)
            if not last_result or not last_result.success:
                return error_response("No recent successful task to distill", 404)
            task = getattr(service, '_current_task', '') or "unknown task"
            steps = [
                {
                    "iteration": s.iteration,
                    "action_type": s.action.action_type,
                    "target": s.action.target_description,
                    "text": s.action.text,
                    "keys": s.action.keys,
                    "failed": s.failed,
                    "result_success": s.result.get("success", False),
                }
                for s in last_result.steps
            ]

        from backend.services.self_improvement_service import get_self_improvement_service
        service = get_self_improvement_service()
        service.distill_task_learning(task, steps, data.get("model_name", ""))
        return success_response(message="Distillation complete", data={"task": task, "steps_count": len(steps)})
    except Exception as e:
        logger.error(f"Manual distillation failed: {e}", exc_info=True)
        return error_response(str(e), 500)
