#!/usr/bin/env python3
"""
Agent Control API — REST endpoints for Agent Vision Control.

Provides start/stop/kill/status/capture endpoints for the agent control system.
Blueprint auto-discovered by blueprint_discovery.py.
"""

import logging
import threading
from flask import Blueprint, jsonify, request

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


# ---------------------------------------------------------------------------
# Learning endpoints
# ---------------------------------------------------------------------------

@agent_control_bp.route("/learn/start", methods=["POST"])
def learn_start():
    """Start learning mode — begin recording a demonstration."""
    try:
        data = request.get_json() or {}
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        result = service.start_learning(
            name=data.get("name"),
            description=data.get("description", ""),
            tags=data.get("tags"),
        )
        return jsonify(result), 200 if result["success"] else 409
    except Exception as e:
        logger.error(f"Error starting learning mode: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/stop", methods=["POST"])
def learn_stop():
    """Stop learning mode — finish recording and trigger clarification pass."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        result = service.stop_learning()
        return jsonify(result), 200 if result["success"] else 409
    except Exception as e:
        logger.error(f"Error stopping learning mode: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/status", methods=["GET"])
def learn_status():
    """Get current learning mode state."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        steps_count = 0
        if service.is_learning and service._demo_recorder:
            try:
                steps_count = len(service._demo_recorder.get_steps())
            except Exception:
                pass
        return jsonify({
            "success": True,
            "learning": service.is_learning,
            "demonstration_id": service._current_demonstration_id,
            "steps_count": steps_count,
        })
    except Exception as e:
        logger.error(f"Error getting learning status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations", methods=["GET"])
def learn_list_demonstrations():
    """List all completed demonstrations."""
    try:
        from backend.models import Demonstration
        demos = Demonstration.query.filter_by(is_complete=True).order_by(
            Demonstration.created_at.desc()
        ).all()
        return jsonify({"success": True, "demonstrations": [d.to_dict() for d in demos]})
    except Exception as e:
        logger.error(f"Error listing demonstrations: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>", methods=["GET"])
def learn_get_demonstration(demo_id):
    """Get a single demonstration with its steps."""
    try:
        from backend.models import db, Demonstration
        demo = db.session.get(Demonstration, demo_id)
        if not demo:
            return jsonify({"success": False, "error": "Not found"}), 404
        return jsonify({"success": True, "demonstration": demo.to_dict()})
    except Exception as e:
        logger.error(f"Error getting demonstration {demo_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>", methods=["DELETE"])
def learn_delete_demonstration(demo_id):
    """Delete a demonstration."""
    try:
        from backend.models import db, Demonstration
        demo = db.session.get(Demonstration, demo_id)
        if not demo:
            return jsonify({"success": False, "error": "Not found"}), 404
        db.session.delete(demo)
        db.session.commit()
        return jsonify({"success": True, "message": f"Demonstration {demo_id} deleted"})
    except Exception as e:
        logger.error(f"Error deleting demonstration {demo_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>", methods=["PATCH"])
def learn_update_demonstration(demo_id):
    """Update a demonstration's name, description, or tags."""
    try:
        from backend.models import db, Demonstration
        demo = db.session.get(Demonstration, demo_id)
        if not demo:
            return jsonify({"success": False, "error": "Not found"}), 404
        data = request.get_json() or {}
        if "name" in data:
            demo.name = data["name"]
        if "description" in data:
            demo.description = data["description"]
        if "tags" in data:
            demo.tags = data["tags"]
        db.session.commit()
        return jsonify({"success": True, "demonstration": demo.to_dict()})
    except Exception as e:
        logger.error(f"Error updating demonstration {demo_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>/steps", methods=["PUT"])
def learn_replace_steps(demo_id):
    """Replace all steps for a demonstration with the provided JSON array."""
    try:
        from backend.models import db, Demonstration, DemoStep
        demo = db.session.get(Demonstration, demo_id)
        if not demo:
            return jsonify({"success": False, "error": "Not found"}), 404
        data = request.get_json() or {}
        steps = data.get("steps")
        if not isinstance(steps, list):
            return jsonify({"success": False, "error": "'steps' must be a list"}), 400
        valid_actions = {"click", "type", "hotkey", "scroll"}
        for i, s in enumerate(steps):
            if s.get("action_type") not in valid_actions:
                return jsonify({"success": False, "error": f"Step {i}: invalid action_type '{s.get('action_type')}'"}), 400
        # Delete existing steps
        DemoStep.query.filter_by(demonstration_id=demo_id).delete()
        # Create new steps with enforced sequential indexing
        for i, step_data in enumerate(steps):
            step = DemoStep(
                demonstration_id=demo_id,
                step_index=i,
                action_type=step_data["action_type"],
                target_description=step_data.get("target_description", ""),
                element_context=step_data.get("element_context", ""),
                coordinates_x=step_data.get("coordinates_x"),
                coordinates_y=step_data.get("coordinates_y"),
                text=step_data.get("text"),
                keys=step_data.get("keys"),
                intent=step_data.get("intent"),
                precondition=step_data.get("precondition", ""),
                variability=step_data.get("variability", False),
                wait_condition=step_data.get("wait_condition"),
                is_mistake=step_data.get("is_mistake", False),
                screenshot_before=step_data.get("screenshot_before"),
                screenshot_after=step_data.get("screenshot_after"),
            )
            db.session.add(step)
        db.session.commit()
        return jsonify({"success": True, "demonstration": demo.to_dict()})
    except Exception as e:
        logger.error(f"Error replacing steps for demonstration {demo_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>/attempt", methods=["POST"])
def learn_attempt_demonstration(demo_id):
    """Start an agent attempt of a demonstration."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        result = service.attempt_demonstration(demo_id)
        return jsonify(result), 200 if result["success"] else 409
    except Exception as e:
        logger.error(f"Error attempting demonstration {demo_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>/feedback", methods=["POST"])
def learn_demonstration_feedback(demo_id):
    """Submit success/failure feedback for a demonstration attempt."""
    try:
        from backend.models import db, Demonstration
        demo = db.session.get(Demonstration, demo_id)
        if not demo:
            return jsonify({"success": False, "error": "Not found"}), 404
        data = request.get_json() or {}
        if data.get("success"):
            demo.success_count += 1
        else:
            demo.success_count = 0
        demo.attempt_count += 1
        db.session.commit()
        return jsonify({"success": True, "demonstration": demo.to_dict()})
    except Exception as e:
        logger.error(f"Error recording feedback for demonstration {demo_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/answer", methods=["POST"])
def learn_answer():
    """Answer a learning clarification question."""
    try:
        data = request.get_json() or {}
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        service._learning_answer_queue.put(data)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error submitting learning answer: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/learn/input", methods=["POST"])
def learn_input():
    """Forward user input to the virtual display during training.

    Accepts: {action: "click"|"type"|"hotkey"|"scroll", x, y, text, keys}
    Executes the action on display :99 via LocalScreenBackend and feeds
    the event directly to DemoRecorder for step capture.
    """
    try:
        data = request.get_json() or {}
        action = data.get("action")
        if not action:
            return jsonify({"success": False, "error": "Missing 'action' field"}), 400

        from backend.services.local_screen_backend import LocalScreenBackend
        screen = LocalScreenBackend()

        if action == "click":
            x = int(data.get("x", 0))
            y = int(data.get("y", 0))
            button = data.get("button", "left")
            result = screen.click(x, y, button=button)
        elif action == "type":
            text = data.get("text", "")
            if not text:
                return jsonify({"success": False, "error": "Missing 'text' for type action"}), 400
            result = screen.type_text(text)
        elif action == "hotkey":
            keys = data.get("keys", "")
            if not keys:
                return jsonify({"success": False, "error": "Missing 'keys' for hotkey action"}), 400
            key_list = keys.split("+")
            result = screen.hotkey(*key_list)
        elif action == "scroll":
            x = int(data.get("x", 640))
            y = int(data.get("y", 360))
            amount = int(data.get("amount", -3))
            result = screen.scroll(x, y, amount=amount)
        else:
            return jsonify({"success": False, "error": f"Unknown action: {action}"}), 400

        # Feed the event to DemoRecorder if recording is active
        if result.get("success"):
            try:
                from backend.services.agent_control_service import get_agent_control_service
                service = get_agent_control_service()
                if service.is_learning and service._demo_recorder:
                    service._demo_recorder.record_event(
                        action=action,
                        x=int(data.get("x", 0)),
                        y=int(data.get("y", 0)),
                        text=data.get("text", ""),
                        keys=data.get("keys", ""),
                    )
            except Exception as e:
                logger.warning(f"DemoRecorder event capture failed (non-fatal): {e}")

        return jsonify({"success": result.get("success", False), "result": result})
    except Exception as e:
        logger.error(f"Error forwarding input: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/capture/raw", methods=["POST"])
def capture_raw():
    """Return a raw JPEG screenshot of the virtual display — no vision analysis."""
    try:
        from backend.services.local_screen_backend import LocalScreenBackend
        from io import BytesIO

        data = request.get_json() or {}
        try:
            quality = int(data.get("quality", 70))
        except (TypeError, ValueError):
            quality = 70
        quality = max(1, min(100, quality))

        screen = LocalScreenBackend()
        screenshot, _ = screen.capture()

        buf = BytesIO()
        screenshot.save(buf, format="JPEG", quality=quality)
        buf.seek(0)

        # Validate the JPEG is non-empty and has valid header
        jpeg_bytes = buf.getvalue()
        if len(jpeg_bytes) < 100 or jpeg_bytes[:2] != b'\xff\xd8':
            logger.error(f"Capture produced invalid JPEG ({len(jpeg_bytes)} bytes)")
            return jsonify({"success": False, "error": "Capture produced invalid image"}), 500
        buf.seek(0)

        from flask import send_file
        return send_file(buf, mimetype="image/jpeg")

    except IndexError:
        logger.error("No monitors available on agent display — is Xvfb running?")
        return jsonify({"success": False, "error": "Agent display not running"}), 503
    except Exception as e:
        logger.error(f"Error in raw capture: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
