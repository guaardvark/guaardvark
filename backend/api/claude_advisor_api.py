"""REST API for Uncle Claude integration."""
import logging
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

claude_advisor_bp = Blueprint("claude_advisor", __name__, url_prefix="/api/claude")


@claude_advisor_bp.route("/status", methods=["GET"])
def get_status():
    """Check Claude API availability and usage."""
    from backend.services.claude_advisor_service import get_claude_advisor
    advisor = get_claude_advisor()
    return success_response(data={
        "available": advisor.is_available(),
        "usage": advisor.get_usage(),
        "escalation_mode": advisor._escalation_mode,
        "model": advisor._model,
    })


@claude_advisor_bp.route("/test-connection", methods=["POST"])
def test_connection():
    """Test Claude API connection."""
    from backend.services.claude_advisor_service import get_claude_advisor
    advisor = get_claude_advisor()
    if not advisor.is_available():
        return error_response("Claude API not configured. Set ANTHROPIC_API_KEY in .env", 400)
    result = advisor.escalate("Respond with 'Connection successful' and nothing else.", [])
    if result.get("available"):
        return success_response(data={"connected": True, "response": result["response"]})
    return error_response(result.get("reason", "Connection failed"), 503)


@claude_advisor_bp.route("/escalate", methods=["POST"])
def escalate():
    """Escalate a message to Uncle Claude."""
    from backend.services.claude_advisor_service import get_claude_advisor
    data = request.get_json()
    if not data or "message" not in data:
        return error_response("message is required", 400)

    advisor = get_claude_advisor()
    result = advisor.escalate(
        message=data["message"],
        conversation_history=data.get("history", []),
        system_context=data.get("system_context", ""),
    )
    if result.get("available"):
        return success_response(data=result)
    return error_response(result.get("reason", "Escalation failed"), 503)


@claude_advisor_bp.route("/advise", methods=["POST"])
def get_advice():
    """Get Uncle Claude's recommendations for system improvements."""
    from backend.services.claude_advisor_service import get_claude_advisor
    data = request.get_json() or {}
    advisor = get_claude_advisor()

    system_state = data.get("system_state", {})
    if not system_state:
        try:
            import subprocess
            gpu_info = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            system_state["gpu"] = gpu_info.stdout.strip() if gpu_info.returncode == 0 else "unavailable"
        except Exception:
            system_state["gpu"] = "unavailable"

    result = advisor.advise(system_state)
    if result.get("available"):
        return success_response(data=result)
    return error_response(result.get("reason", "Advisor unavailable"), 503)


@claude_advisor_bp.route("/usage", methods=["GET"])
def get_usage():
    """Get current token usage and budget."""
    from backend.services.claude_advisor_service import get_claude_advisor
    advisor = get_claude_advisor()
    return success_response(data=advisor.get_usage())


@claude_advisor_bp.route("/config", methods=["POST"])
def update_config():
    """Update Claude API configuration."""
    from backend.services.claude_advisor_service import get_claude_advisor
    data = request.get_json()
    if not data:
        return error_response("No configuration provided", 400)

    advisor = get_claude_advisor()

    if "escalation_mode" in data:
        mode = data["escalation_mode"]
        if mode not in ("manual", "smart", "always"):
            return error_response("Invalid escalation mode", 400)
        advisor._escalation_mode = mode
        _save_setting("claude_escalation_mode", mode)

    if "monthly_budget" in data:
        advisor._monthly_budget = int(data["monthly_budget"])
        _save_setting("claude_monthly_budget", str(advisor._monthly_budget))

    if "model" in data:
        advisor._model = data["model"]
        _save_setting("claude_model", advisor._model)

    return success_response(data={"updated": True})


def _save_setting(key: str, value: str):
    from backend.models import db, SystemSetting
    setting = db.session.query(SystemSetting).filter_by(key=key).first()
    if setting:
        setting.value = value
    else:
        db.session.add(SystemSetting(key=key, value=value))
    db.session.commit()
