"""
Brain API — Health check and refresh endpoints for the AgentBrain system.
"""

import logging
import os

from flask import Blueprint, current_app, jsonify

logger = logging.getLogger(__name__)

brain_bp = Blueprint("brain", __name__, url_prefix="/api/brain")


@brain_bp.route("/health", methods=["GET"])
def brain_health():
    """GET /api/brain/health — Current BrainState health and tier availability."""
    brain_state = getattr(current_app, "brain_state", None)
    if not brain_state:
        return jsonify({
            "enabled": False,
            "reason": "AgentBrain not initialized",
        })

    return jsonify({
        "enabled": True,
        "initialized": brain_state._initialized,
        "is_ready": brain_state.is_ready,
        "active_model": brain_state.active_model,
        "model_caps": {
            "name": brain_state.model_caps.name,
            "native_tools": brain_state.model_caps.supports_native_tools,
            "thinking": brain_state.model_caps.is_thinking_model,
            "vision": brain_state.model_caps.is_vision_model,
            "context_window": brain_state.model_caps.context_window,
        },
        "health": brain_state.health.to_dict(),
        "reflexes_count": len(brain_state.reflexes),
        "tools_count": len(brain_state.tool_registry.list_tools()) if brain_state.tool_registry else 0,
        "system_prompts": list(brain_state.system_prompts.keys()),
        "lite_mode": brain_state.lite_mode,
    })


@brain_bp.route("/refresh", methods=["POST"])
def brain_refresh():
    """POST /api/brain/refresh — Rebuild pre-computed state after config change."""
    brain_state = getattr(current_app, "brain_state", None)
    if not brain_state:
        return jsonify({"success": False, "error": "AgentBrain not initialized"}), 503

    try:
        brain_state.refresh()
        return jsonify({
            "success": True,
            "health": brain_state.health.to_dict(),
            "active_model": brain_state.active_model,
            "reflexes_count": len(brain_state.reflexes),
        })
    except Exception as e:
        logger.error(f"Brain refresh failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@brain_bp.route("/telemetry", methods=["GET"])
def brain_telemetry():
    """GET /api/brain/telemetry — Recent tier telemetry entries."""
    try:
        from backend.config import LOG_DIR
        path = os.path.join(LOG_DIR, "tier_telemetry.jsonl")
    except Exception:
        path = "logs/tier_telemetry.jsonl"

    if not os.path.exists(path):
        return jsonify({"entries": [], "total": 0})

    import json
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Return last 50 entries
        for line in lines[-50:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    except Exception as e:
        logger.error(f"Failed to read telemetry: {e}")

    return jsonify({
        "entries": entries,
        "total": len(lines) if 'lines' in dir() else 0,
    })
