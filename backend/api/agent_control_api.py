#!/usr/bin/env python3
"""
Agent Control API — REST endpoints for Agent Vision Control.

Provides start/stop/kill/status/capture endpoints for the agent control system.
Blueprint auto-discovered by blueprint_discovery.py.
"""

import logging
import os
import threading
from flask import Blueprint, jsonify, request

# Agent operations use the virtual display
AGENT_DISPLAY = os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")

logger = logging.getLogger(__name__)

agent_control_bp = Blueprint("agent_control", __name__, url_prefix="/api/agent-control")


@agent_control_bp.route("/status", methods=["GET"])
def get_status():
    """Get agent control system status."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        return jsonify({"success": True, "status": service.get_status()})
    except Exception as e:
        logger.error(f"Error getting agent status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/kill", methods=["POST"])
def kill():
    """Emergency stop — immediately halt all agent operations."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        service.kill()
        logger.warning("Agent kill switch activated via API")
        return jsonify({"success": True, "message": "All agent operations halted"})
    except Exception as e:
        logger.error(f"Error activating kill switch: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/execute", methods=["POST"])
def execute_task():
    """Execute a task using vision-based agent control."""
    try:
        data = request.get_json() or {}
        task = data.get("task", "")
        if not task:
            return jsonify({"success": False, "error": "task is required"}), 400

        from backend.services.agent_control_service import get_agent_control_service
        from backend.services.local_screen_backend import LocalScreenBackend

        service = get_agent_control_service()
        if service.is_active:
            return jsonify({"success": False, "error": "Agent already active"}), 409

        screen = LocalScreenBackend()

        # Run in background thread so the API doesn't block
        def run_task():
            os.environ["DISPLAY"] = AGENT_DISPLAY
            result = service.execute_task(task, screen)
            logger.info(f"Task completed: success={result.success}, reason={result.reason}, "
                       f"steps={len(result.steps)}, time={result.total_time_seconds:.1f}s")

        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"Task started: {task}",
            "note": "Use GET /api/agent-control/status to monitor progress"
        })

    except Exception as e:
        logger.error(f"Error starting agent task: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/capture", methods=["POST"])
def capture_and_analyze():
    """Take a screenshot and analyze it with a vision model."""
    try:
        data = request.get_json() or {}
        prompt = data.get("prompt", "Describe what is currently on the screen.")

        os.environ["DISPLAY"] = AGENT_DISPLAY
        from backend.services.local_screen_backend import LocalScreenBackend
        from backend.utils.vision_analyzer import VisionAnalyzer

        screen = LocalScreenBackend()
        screenshot, cursor_pos = screen.capture()

        analyzer = VisionAnalyzer()
        result = analyzer.analyze(screenshot, prompt=prompt)

        return jsonify({
            "success": result.success,
            "description": result.description,
            "cursor": cursor_pos,
            "model": result.model_used,
            "inference_ms": result.inference_ms,
            "error": result.error,
        })

    except Exception as e:
        logger.error(f"Error in capture/analyze: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
