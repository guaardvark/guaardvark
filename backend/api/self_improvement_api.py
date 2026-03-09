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
    """Submit a directed improvement task."""
    data = request.get_json()
    if not data or "description" not in data:
        return error_response("description is required", 400)

    try:
        from backend.services.self_improvement_service import get_self_improvement_service
        service = get_self_improvement_service()
        result = service.submit_directed_task(
            description=data["description"],
            target_files=data.get("target_files", []),
            priority=data.get("priority", "medium"),
        )
        return success_response(data=result)
    except Exception as e:
        logger.error(f"Failed to submit improvement task: {e}", exc_info=True)
        return error_response(str(e), 500)


@self_improvement_bp.route("/trigger", methods=["POST"])
def trigger_run():
    """Manually trigger a self-improvement run."""
    try:
        from backend.services.self_improvement_service import get_self_improvement_service
        service = get_self_improvement_service()
        result = service.run_self_check()
        return success_response(data=result)
    except Exception as e:
        logger.error(f"Failed to trigger self-improvement: {e}", exc_info=True)
        return error_response(str(e), 500)
