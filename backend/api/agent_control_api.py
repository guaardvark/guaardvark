#!/usr/bin/env python3
"""
Agent Control API — REST endpoints for Agent Vision Control.

Provides start/stop/kill/status/capture endpoints for the agent control system.
Blueprint auto-discovered by blueprint_discovery.py.
"""

import contextlib
import logging
import threading
from flask import Blueprint, jsonify, request

from backend.utils.privileged_apt import (
    apt_install_script,
    desktop_session_available,
    manual_apt_command,
    passwordless_sudo_available,
    run_privileged_apt,
)

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


@agent_control_bp.route("/runs", methods=["GET"])
def list_task_runs():
    """The agent's episodes, newest first: one row per screen task (no steps)."""
    try:
        from backend.models import AgentTaskRun
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
        rows = (AgentTaskRun.query.order_by(AgentTaskRun.ended_at.desc())
                .limit(limit).all())
        return jsonify({"success": True, "runs": [r.to_dict() for r in rows]})
    except Exception as e:
        logger.error(f"Error listing task runs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/runs/<run_id>", methods=["GET"])
def get_task_run(run_id):
    """One episode with its steps in order."""
    try:
        from backend.models import db, AgentTaskRun, AgentTaskStep
        run = db.session.get(AgentTaskRun, run_id)
        if run is None:
            return jsonify({"success": False, "error": "run not found"}), 404
        steps = (AgentTaskStep.query.filter_by(run_id=run.id)
                 .order_by(AgentTaskStep.iteration.asc(), AgentTaskStep.created_at.asc()).all())
        payload = run.to_dict()
        payload["steps"] = [s.to_dict() for s in steps]
        return jsonify({"success": True, "run": payload})
    except Exception as e:
        logger.error(f"Error reading task run {run_id}: {e}")
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


@agent_control_bp.route("/steer", methods=["POST"])
def steer():
    """A note for the running task, read at its next step; the task keeps going.

    Body: {"message": str, "session_id": str}. Answers {"queued": true} when
    the running task took the note (also when the note was "stop", which ends
    the task as the kill switch does), {"queued": false, "reason": ...} when
    there is no running task for that chat. A queued note is saved to the
    chat's history so it shows after a reload.
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = (data.get("session_id") or "").strip() or None
    if not message:
        return jsonify({"success": False, "error": "message is required"}), 400
    try:
        from backend.services.agent_control_service import get_agent_control_service
        out = get_agent_control_service().add_steer_note(message, session_id=session_id)
    except Exception as e:
        logger.error(f"Error queuing a note for the agent: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    if out.get("queued") and session_id:
        _save_note_to_history(session_id, message, stopping=bool(out.get("stopping")))
    return jsonify({"success": True, **out})


def _save_note_to_history(session_id: str, message: str, stopping: bool = False) -> None:
    """The note as a user turn in the chat, marked so the UI shows it as one."""
    try:
        from datetime import datetime
        from backend.models import db, LLMMessage, LLMSession
        if db.session.get(LLMSession, session_id) is None:
            return
        db.session.add(LLMMessage(
            session_id=session_id,
            role="user",
            content=message,
            extra_data={"messageType": "agent_note", "agentNote": "stopping" if stopping else "sent"},
            timestamp=datetime.now(),
        ))
        db.session.commit()
    except Exception as e:
        logger.warning(f"Agent note not saved to chat history: {e}")
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass


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

        # The display starts on demand and stops after a restart or idle; the
        # chat tool and outreach start it the same way before using the screen.
        from backend.utils.agent_display_utils import start_agent_display_if_needed
        if not start_agent_display_if_needed():
            return jsonify({"success": False, "error": "Agent display could not be started"}), 503

        screen = LocalScreenBackend()

        mouse_only = data.get("mouse_only", False)
        training_mode = data.get("training_mode", False)
        # Servo correction loop for this task only (off | shadow | on | auto);
        # absent means the environment/reflex precedence decides.
        correction_mode = data.get("correction_mode")
        if correction_mode is not None:
            correction_mode = str(correction_mode).strip().lower()
            if correction_mode not in ("off", "shadow", "on", "auto"):
                return jsonify({"success": False,
                                "error": "correction_mode must be off, shadow, on or auto"}), 400

        # Capture the Flask app so the worker thread can push an app context.
        # Without this, anything inside execute_task that touches db.session
        # (Phase 4 belief-update memory writes, future DB-backed lessons) fails
        # with "Working outside of application context". Existing in-loop
        # writes wrap themselves in current_app.app_context(); pushing once
        # at the thread boundary makes that pattern optional rather than
        # required for everything downstream.
        from flask import current_app
        flask_app = current_app._get_current_object()

        # Run in background thread so the API doesn't block
        def run_task():
            with flask_app.app_context():
                result = service.execute_task(task, screen, mouse_only=mouse_only, training_mode=training_mode,
                                              correction_mode=correction_mode)
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


@agent_control_bp.route("/calibrate", methods=["POST"])
def calibrate_vision():
    """Run the Optician interactive calibration routine."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        from backend.services.local_screen_backend import LocalScreenBackend
        from backend.services.servo_controller import ServoController
        
        service = get_agent_control_service()
        screen = LocalScreenBackend()
        # We need an analyzer, usually provided by the service
        analyzer = service.vision_analyzer
        servo = ServoController(screen, analyzer)
        
        result = servo.calibrate()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in vision calibration: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/self-improve", methods=["POST"])
def run_self_improvement():
    """Run the Archive Miner to find and suggest vision improvements."""
    try:
        from backend.services.servo_self_improvement import ServoSelfImprovement
        miner = ServoSelfImprovement()
        proposals = miner.suggest_reflex_updates()
        return jsonify({"success": True, "proposals": proposals})
    except Exception as e:
        logger.error(f"Error in self-improvement: {e}")
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
    """Get current learning mode state.

    Always returns a 200 with safe defaults on any internal error so that
    the AgentScreenViewer (and any training UI) polling does not spam the
    browser console with 500s. The viewer starts this poll as soon as it
    opens (to react to backend learning mode being toggled externally).
    """
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()

        # Prefer the public get_status() for the common fields (uses internal
        # _learning / _current_demonstration_id which are always set in __init__).
        # This is more robust than reaching for the @property + private attrs
        # and survives partial init or stale .pyc scenarios.
        base = service.get_status() or {}
        learning = bool(base.get("learning", False))
        demo_id = base.get("current_demonstration_id")

        steps_count = 0
        recorder = getattr(service, "_demo_recorder", None)
        if learning and recorder:
            try:
                steps_count = len(recorder.get_steps())
            except Exception:
                pass

        return jsonify({
            "success": True,
            "learning": learning,
            "demonstration_id": demo_id,
            "steps_count": steps_count,
        })
    except Exception as e:
        logger.error(f"Error getting learning status: {e}", exc_info=True)
        # Safe idle response — prevents console spam from the viewer's 2s poll.
        # The frontend already treats any failure as "not training".
        return jsonify({
            "success": True,
            "learning": False,
            "demonstration_id": None,
            "steps_count": 0,
        })


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


def _step_coordinates(step_data):
    """(x, y) for a step written back through the steps API, or None if malformed.

    Two shapes arrive: ``coordinates_x`` / ``coordinates_y``, the column names,
    and ``coordinates: [x, y]`` (or null), which is how DemoStep.to_dict()
    hands steps out and so what the Training page's steps editor sends back.
    The column names win when either is present.
    """
    if "coordinates_x" in step_data or "coordinates_y" in step_data:
        return step_data.get("coordinates_x"), step_data.get("coordinates_y")
    coords = step_data.get("coordinates")
    if coords is None:
        return None, None
    if not isinstance(coords, (list, tuple)) or len(coords) != 2:
        return None
    try:
        return int(coords[0]), int(coords[1])
    except (TypeError, ValueError):
        return None


@agent_control_bp.route("/learn/demonstrations/<int:demo_id>/steps", methods=["PUT"])
def learn_replace_steps(demo_id):
    """Replace all steps for a demonstration with the provided JSON array.

    Steps may carry their click position either as coordinates_x/_y or as
    coordinates: [x, y] (see _step_coordinates).
    """
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
        positions = []
        for i, s in enumerate(steps):
            if s.get("action_type") not in valid_actions:
                return jsonify({"success": False, "error": f"Step {i}: invalid action_type '{s.get('action_type')}'"}), 400
            position = _step_coordinates(s)
            if position is None:
                return jsonify({"success": False, "error": f"Step {i}: coordinates must be [x, y] or null"}), 400
            positions.append(position)
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
                coordinates_x=positions[i][0],
                coordinates_y=positions[i][1],
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

        if action == "type" and not data.get("text", ""):
            return jsonify({"success": False, "error": "Missing 'text' for type action"}), 400
        if action == "hotkey" and not data.get("keys", ""):
            return jsonify({"success": False, "error": "Missing 'keys' for hotkey action"}), 400
        if action not in ("click", "type", "hotkey", "scroll"):
            return jsonify({"success": False, "error": f"Unknown action: {action}"}), 400

        recorder = None
        try:
            from backend.services.agent_control_service import get_agent_control_service
            service = get_agent_control_service()
            if service.is_learning and service._demo_recorder:
                recorder = service._demo_recorder
        except Exception as e:
            logger.warning(f"DemoRecorder lookup failed (non-fatal): {e}")

        if action == "scroll":
            x, y = int(data.get("x", 640)), int(data.get("y", 360))
        else:
            x, y = int(data.get("x", 0)), int(data.get("y", 0))
        amount = int(data.get("amount", -3))

        from backend.services.local_screen_backend import LocalScreenBackend
        screen = LocalScreenBackend()

        # This request records the action itself below, so the recorder's
        # display listener must not record the injected copy as well.
        with (recorder.injected_input() if recorder else contextlib.nullcontext()):
            if action == "click":
                button = data.get("button", "left")
                result = screen.click(x, y, button=button)
            elif action == "type":
                result = screen.type_text(data.get("text", ""))
            elif action == "hotkey":
                result = screen.hotkey(*data.get("keys", "").split("+"))
            else:
                result = screen.scroll(x, y, amount=amount)

        # Feed the event to DemoRecorder if recording is active
        if result.get("success") and recorder:
            try:
                recorder.record_event(
                    action=action,
                    x=x,
                    y=y,
                    text=data.get("text", ""),
                    keys=data.get("keys", ""),
                    amount=amount if action == "scroll" else None,
                )
            except Exception as e:
                logger.warning(f"DemoRecorder event capture failed (non-fatal): {e}")

        return jsonify({"success": result.get("success", False), "result": result})
    except Exception as e:
        logger.error(f"Error forwarding input: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Task feedback — thumbs up/down from the user after any agent task
# ---------------------------------------------------------------------------

import re as _re_module

# Phrases that signal the user wasn't just acknowledging — they were impressed.
# Captured in the feedback comment, or in the previous user message in the same
# session. A 👍 alone is bookkeeping; a 👍 paired with one of these phrases means
# "remember this one, it worked really well." We boost the resulting candidate
# recipe's importance so it ranks higher in next-session recall.
_STRONG_POSITIVE_PHRASES = _re_module.compile(
    r"\b(excellent|perfect(?:ly)?|exactly|nailed\s+it|spot\s+on|"
    r"well\s+done|amazing|fantastic|brilliant|love\s+it|that'?s\s+it|"
    r"very\s+good|great\s+(?:job|work)|nice\s+(?:one|work))\b",
    _re_module.IGNORECASE,
)


def _detect_strong_positive(why_text: str, session_id: str = None) -> bool:
    """Did the user express enthusiastic approval, not just a routine 👍?

    Looks in the feedback why_text first (cheap); falls back to the most recent
    user message in the session (one DB hit, capped). Both are scanned for
    strong-positive phrases. Either match returns True.

    Errors are non-fatal — strong-positive is an enhancement, not a correctness
    requirement. If we can't tell, we treat the feedback as routine.
    """
    if why_text and _STRONG_POSITIVE_PHRASES.search(why_text):
        return True
    if not session_id:
        return False
    try:
        from backend.models import LLMMessage
        last_user = (
            LLMMessage.query
            .filter(LLMMessage.session_id == session_id, LLMMessage.role == "user")
            .order_by(LLMMessage.timestamp.desc())
            .limit(1)
            .first()
        )
        if last_user and _STRONG_POSITIVE_PHRASES.search(last_user.content or ""):
            return True
    except Exception:
        pass
    return False


def _induce_candidate_recipe(app, session_id: str, feedback_task: str, strong_positive: bool = False):
    """Background thread: a thumbs-up on a verified screen task induces a
    recipe from the run, so the same task pattern executes deterministically
    next time.

    Policy (operator decision 2026-09-22): the induced recipe is written to
    recipes.json immediately and marked PROVISIONAL in recipe_stats.json. A
    thumbs-down on any later run that executed it disables it (the matcher
    skips disabled recipes; an un-thumb re-enables). Two clean thumbs-up on
    later runs graduate it. An AgentMemory row (source="candidate_recipe")
    keeps the audit trail; GET /candidate-recipes lists those rows and
    POST /candidate-recipes/<id>/promote re-installs one that was removed.

    strong_positive=True (a strong phrase in the why-text or the last user
    message) bumps the audit row's importance from 0.7 to 0.9 and lets the
    run through even when the loop's own verifier did not confirm it.

    Safety gates:
    - Only induces if the agent's _last_result.task == feedback.task
      (avoids inducing from stale state when the user thumbs-up's an old run).
    - Only induces when the run's final action was VERIFIED (servo region-DPC
      or semantic vision verify saw the expected effect), unless
      strong_positive. success=True alone is not enough — phantom successes
      (the agent declared "done" but nothing actually changed on screen)
      would teach the wrong path. AgentResult.verified is populated by the
      finish() wrapper in execute_task from the last step's verifier result.
    - Skips if action_history is empty.
    - Hard rules in the prompt: vision-actionable target_descriptions,
      short labels (<=6 words, what the validator enforces), no pixel
      coordinates. Per data/agent/LEARNING_PRINCIPLES.md.
    """
    if not app or not session_id or not feedback_task:
        return
    with app.app_context():
        try:
            from backend.models import db, AgentMemory
            from backend.config import OLLAMA_BASE_URL
            from backend.services.agent_control_service import get_agent_control_service
            import requests
            import uuid as _uuid

            service = get_agent_control_service()
            last = service._last_result
            if not last or not last.success:
                logger.info("[INDUCE] no successful last_result — skipping induction")
                return
            # Verified gate (response_2026-05-19 §C). A successful loop
            # termination that produced no verified visible effect is a
            # phantom success — teaching from it bakes "clicks that do
            # nothing" into the recipe library. Strong-positive feedback
            # is treated as user-confirmed verification when the in-loop
            # verifier missed it (e.g., long renders past the 12s budget).
            if not getattr(last, "verified", False) and not strong_positive:
                logger.info(
                    f"[INDUCE] last_result.verified=False (verifier="
                    f"{getattr(last, 'verified_reason', '?')!r}) and no "
                    f"strong-positive override — skipping induction to avoid "
                    f"learning a phantom-success path"
                )
                return
            if (last.task or "").strip().lower() != (feedback_task or "").strip().lower():
                logger.info(
                    f"[INDUCE] feedback task does not match last run — skipping. "
                    f"feedback={feedback_task[:60]!r} last={last.task[:60] if last.task else ''!r}"
                )
                return
            steps = last.steps or []
            if len(steps) < 1:
                logger.info("[INDUCE] no action steps to learn from — skipping")
                return

            # Render action history for the LLM. Keep it compact; the model
            # needs the shape, not narrative prose.
            history_lines = []
            for i, s in enumerate(steps, 1):
                a = s.action
                act = a.action_type or "?"
                bits = [f"{i}. {act}"]
                if a.target_description:
                    bits.append(f'target="{a.target_description}"')
                if a.text:
                    bits.append(f'text={a.text!r}')
                if a.keys:
                    bits.append(f"keys={a.keys}")
                bits.append("[OK]" if not s.failed else "[FAIL]")
                history_lines.append(" ".join(bits))

            try:
                from backend.utils.llm_service import get_saved_active_model_name
                active_model = get_saved_active_model_name() or "gemma4:e4b"
            except Exception:
                active_model = "gemma4:e4b"

            prompt = (
                "You are inducing a reusable recipe for the Guaardvark agent. "
                "The user just confirmed (👍) that a task succeeded. Extract "
                "the GENERIC pattern so it can auto-execute next time.\n\n"
                "Return STRICT JSON only — no prose, no code fences, no commentary:\n"
                '{"description": "<one short sentence, generic, no specific names>", '
                '"triggers": ["<regex pattern matching the task phrase>"], '
                '"steps": [<step objects>]}\n\n'
                "Step shapes:\n"
                '  Click:  {"action": "click", "target_description": "<short label>"}\n'
                '  Type:   {"action": "type", "text": "<literal text or {placeholder}>"}\n'
                '  Hotkey: {"action": "hotkey", "keys": ["ctrl","l"]}\n'
                '  Wait:   {"action": "wait", "seconds": <float>}\n\n'
                "Hard rules:\n"
                "- target_description MUST be a SHORT, conventional UI label "
                "  (≤6 words, one distinctive adjective): 'primary submit button', "
                "  'chat input field', 'main navigation icon'. Long descriptions break the "
                "  vision detector. Long-form context belongs in self_knowledge_compact.md, "
                "  not in target_description.\n"
                "- NEVER include pixel coordinates (x, y) — vision finds targets per-frame. "
                "  Coordinates rot when layouts shift.\n"
                "- Replace specific values (URLs, search terms, channel names) with "
                "  {snake_case_placeholder} tokens. UI element names stay literal.\n"
                "- Trigger regex MUST anchor on ^ and end with \\s*$, use "
                "  non-capturing groups (?:...) for synonyms, and capture variable "
                "  parts as positional groups that map to step placeholders {1}, {2}, etc.\n"
                "- Mirror the action history's ORDER and shape — do not invent steps "
                "  that didn't happen, do not omit waits between actions.\n\n"
                f"=== Successful task ===\n{feedback_task[:300]}\n\n"
                "=== Action history (what the agent did, in order) ===\n"
                + "\n".join(history_lines)
                + "\n\nJSON:"
            )

            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": active_model,
                        "prompt": prompt,
                        "format": "json",
                        "stream": False,
                        "options": {"num_predict": 800, "temperature": 0.3},
                    },
                    timeout=90,
                )
                resp.raise_for_status()
                raw = (resp.json().get("response") or "").strip()
            except Exception as llm_err:
                logger.warning(f"[INDUCE] LLM call failed: {llm_err}")
                return

            import json as _json
            import re as _re
            
            try:
                parsed = _json.loads(raw)
            except Exception:
                logger.warning(f"[INDUCE] could not parse JSON candidate from LLM: {raw[:200]!r}")
                return

            # Validate shape — bail on anything missing the essentials.
            description = str(parsed.get("description") or "").strip()[:200]
            triggers = parsed.get("triggers") or []
            steps_in = parsed.get("steps") or []
            if not description or not triggers or not steps_in:
                logger.info("[INDUCE] parsed JSON missing description/triggers/steps — skipping")
                return

            # Reject candidates that smuggled coordinates back in. The prompt
            # forbade them but LLMs hallucinate; defense in depth.
            offending = []
            for s in steps_in:
                if isinstance(s, dict) and (("x" in s and isinstance(s["x"], (int, float))) or
                                              ("y" in s and isinstance(s["y"], (int, float)))):
                    offending.append(s)
            if offending:
                logger.warning(
                    f"[INDUCE] candidate contained coordinate steps — refusing to save. "
                    f"first offender: {offending[0]}"
                )
                return

            normalized = {
                "description": description,
                "triggers": [str(t) for t in triggers if isinstance(t, str)][:5],
                "steps": [s for s in steps_in if isinstance(s, dict) and s.get("action")][:30],
            }
            if not normalized["triggers"] or not normalized["steps"]:
                logger.info("[INDUCE] post-validation empties — skipping")
                return
            from backend.services.agent_knowledge_validator import validate_recipe
            validation = validate_recipe("auto_induced", normalized, strict=True)
            if not validation.ok:
                logger.warning(
                    "[INDUCE] candidate failed recipe validation: %s",
                    "; ".join(validation.error_messages()[:5]),
                )
                return

            # Auto-Promote to recipes.json
            from pathlib import Path
            from backend.config import GUAARDVARK_ROOT
            
            slug = _re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")[:40] or "induced"
            recipe_name = f"auto_{slug}"
            recipes_path = Path(GUAARDVARK_ROOT) / "data" / "agent" / "recipes.json"
            
            try:
                recipes_path.parent.mkdir(parents=True, exist_ok=True)
                if recipes_path.exists():
                    with recipes_path.open("r") as f:
                        recipes = _json.load(f)
                else:
                    recipes = {}
                    
                proposed_recipe = {
                    "description": normalized["description"],
                    "triggers": normalized["triggers"],
                    "steps": normalized["steps"],
                    "_origin": "auto_induced",
                    "_session_id": session_id,
                }
                recipes[recipe_name] = proposed_recipe
                
                from backend.services.agent_knowledge_validator import validate_recipe_library
                library_validation = validate_recipe_library(recipes, strict=False)
                if library_validation.ok:
                    tmp_path = recipes_path.with_suffix(".json.tmp")
                    with tmp_path.open("w") as f:
                        _json.dump(recipes, f, indent=2, ensure_ascii=False)
                    tmp_path.replace(recipes_path)
                    logger.info(f"[INDUCE] Auto-promoted recipe '{recipe_name}' to recipes.json (provisional)")
                    try:
                        from backend.services import recipe_stats
                        recipe_stats.set_provisional(recipe_name, True)
                    except Exception as st_err:
                        logger.debug(f"[INDUCE] recipe_stats provisional mark skipped: {st_err}")
                    
                    # Force cache reload
                    try:
                        from backend.services.agent_control_service import AgentControlService
                        AgentControlService._recipe_cache = None
                        AgentControlService._recipe_mtime = 0.0
                    except Exception:
                        pass
                else:
                    logger.warning(f"[INDUCE] Auto-promotion failed library validation: {'; '.join(library_validation.error_messages())}")
            except Exception as e:
                logger.error(f"[INDUCE] Auto-promotion to recipes.json failed: {e}")

            # Keep audit log in AgentMemory
            content_json = _json.dumps(normalized, ensure_ascii=False)
            row_tags = ["candidate_recipe", "auto_induced", "promoted"]
            row_importance = 0.7
            if strong_positive:
                row_tags.append("strong_positive")
                row_importance = 0.9
            from backend.api.memory_api import add_memory
            row = add_memory(
                content=content_json,
                source="candidate_recipe",
                session_id=session_id,
                memory_type="snippet",
                importance=row_importance,
                tags=row_tags,
                metadata={"candidate_recipe": normalized, "promoted": True},
            )
            if row:
                logger.info(
                    f"[INDUCE] saved recipe audit log {row.id[:8]} — "
                    f"\"{description[:60]}\" with {len(normalized['steps'])} steps "
                    f"(importance={row_importance}{', strong_positive' if strong_positive else ''})"
                )
        except Exception as e:
            logger.warning(f"[INDUCE] failed for session {session_id[:12]}: {e}", exc_info=True)


def _distill_pearl_memory(app, session_id: str):
    """DEPRECATED 2026-05-05 — no longer invoked. Kept here as a reference
    for any future revival with a vision-actionable prompt.

    Why it was killed: the prompt below explicitly asks for a first-person
    "note to your future self," which produced introspective reflections
    ("When a direct action fails, I must wait for the user to prompt me to
    re-engage my vision tools and coordinate system...") that don't
    translate to action and clutter AgentMemory with unused rows. Per
    data/agent/LEARNING_PRINCIPLES.md, stored knowledge must be
    vision-actionable ("find this and do that"), not self-reflective.

    The End-Lesson distiller in backend/api/lessons_api.py is the
    surviving distillation path — user-bracketed, structured-JSON output,
    parameterized steps. Build there if more distillation is needed.

    Original docstring:
    Background thread: roll this session's positive pearls into a short
    note to the agent's future self, UPSERT one AgentMemory per session.

    Runs async so the feedback endpoint returns fast. Failures are logged
    and swallowed — a distillation hiccup should never break feedback UX.
    """
    if not app or not session_id:
        return
    with app.app_context():
        try:
            from backend.models import db, LLMMessage, ToolFeedback, AgentMemory
            from backend.config import OLLAMA_BASE_URL
            from datetime import datetime
            import requests

            # Recent conversation — the context the lesson is being drawn from
            messages = (
                LLMMessage.query
                .filter_by(session_id=session_id)
                .order_by(LLMMessage.timestamp.desc())
                .limit(20)
                .all()
            )
            messages.reverse()
            convo_lines = []
            for m in messages[-12:]:
                role = (m.role or "?")[:1].upper()
                content = (m.content or "")[:220]
                convo_lines.append(f"{role}: {content}")

            # Earlier positive pearls for this session — the string so far
            pearls = (
                ToolFeedback.query
                .filter_by(session_id=session_id, positive=True)
                .order_by(ToolFeedback.created_at.asc())
                .all()
            )
            pearl_lines = [f"- {(p.task or '')[:150]}" for p in pearls if p.task]

            try:
                from backend.utils.llm_service import get_saved_active_model_name
                active_model = get_saved_active_model_name() or "gemma4:e4b"
            except Exception:
                active_model = "gemma4:e4b"

            from backend.config import GUAARDVARK_PROJECT_NAME

            prompt = (
                f"You are {GUAARDVARK_PROJECT_NAME}, a local AI assistant. The user just approved "
                "(👍) a response in this chat session. Read the positive pearls "
                "from this session and the recent exchange, then write a short note "
                "to your future self (2–3 sentences, plain English, first person) "
                "about what you learned that worked. Focus on what's reusable next "
                "time — not a play-by-play. Output only the note, no preamble.\n\n"
                "=== Positive pearls so far this session ===\n"
                + ("\n".join(pearl_lines) if pearl_lines else "(first pearl of the session)")
                + "\n\n=== Recent conversation ===\n"
                + ("\n".join(convo_lines) if convo_lines else "(no prior turns loaded)")
                + "\n\nNote to self:"
            )

            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": active_model,
                        "prompt": prompt,
                        "stream": False,
                        # Gemma4 and similar thinking models can burn 200-400
                        # tokens on internal reasoning before the first visible
                        # output token — 800 gives us headroom for ~50 words
                        # of actual note after the think.
                        "options": {"num_predict": 800, "temperature": 0.6},
                    },
                    timeout=90,
                )
                resp.raise_for_status()
                note = (resp.json().get("response") or "").strip()
            except Exception as llm_err:
                logger.warning(f"[DISTILL] LLM call failed for session {session_id[:12]}: {llm_err}")
                return

            if not note:
                logger.debug(f"[DISTILL] Empty note for session {session_id[:12]} — skipping")
                return

            # UPSERT — one rolling memory per session, source tag keeps these
            # separate from user-typed memories for later curation.
            existing = (
                AgentMemory.query
                .filter_by(session_id=session_id, source="learned_from_feedback")
                .first()
            )
            if existing:
                existing.content = note
                existing.updated_at = datetime.now()
                logger.info(f"[DISTILL] Updated pearl memory for session {session_id[:12]}: {note[:80]}")
            else:
                from backend.api.memory_api import add_memory
                mem = add_memory(
                    content=note,
                    source="learned_from_feedback",
                    session_id=session_id,
                    memory_type="note",
                    importance=0.7,
                )
                logger.info(f"[DISTILL] Saved pearl memory for session {session_id[:12]}: {note[:80]}")
                if mem is None:
                    return
            db.session.commit()
        except Exception as e:
            logger.error(f"[DISTILL] session {session_id[:12]} failed: {e}", exc_info=True)
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass


@agent_control_bp.route("/feedback", methods=["POST"])
def submit_feedback():
    """Record a thumb on one reply and make it count.

    Body: {verdict: "up"|"down"|"none" (or legacy positive: bool),
           kind: "response" | "tool:<name>@<step>.<call>",
           message_id, request_id, session_id, lesson_id,
           task (fallback for rows without ids), tool_name, steps,
           time_seconds, model, why_text, why_tags}

    The reply is resolved by id, then by request id, then by content prefix.
    One feedback row per (message, kind): a re-thumb updates it and an
    un-thumb ("none") retracts what the earlier verdict applied. Effects
    (memory credit and blame, recipe stats, a correction memory from the
    why-text, recipe induction, a lesson pearl) come from feedback_teacher
    and are returned as `taught` so the client can show what changed.
    Every event is also appended to data/training/knowledge/feedback.jsonl.
    """
    data = request.get_json(silent=True) or {}
    from backend.services import feedback_teacher as ft

    verdict = ft.normalise_verdict(data)
    if verdict is None:
        return jsonify({"success": False,
                        "error": "'verdict' must be up, down or none (or legacy 'positive': true/false)"}), 400

    session_id = data.get("session_id") or None
    lesson_id = data.get("lesson_id") or None
    if not lesson_id and session_id:
        try:
            from backend.api.lessons_api import get_active_lesson_id
            lesson_id = get_active_lesson_id(session_id)
        except Exception:
            lesson_id = None
    if lesson_id:
        data["lesson_id"] = lesson_id

    try:
        row, resolved_by = ft.resolve_message(data)
        if row is not None and not session_id:
            session_id = row.session_id
            data["session_id"] = session_id
        if row is None and session_id is None:
            logger.warning("[FEEDBACK] no message and no session on a %s thumb; recorded only", verdict)

        fb, previous = ft.upsert_feedback(row, data, verdict)
        taught = []
        if previous not in (None, "none") and previous != verdict:
            taught.extend(ft.retract(fb))
        if verdict == "none":
            if previous in (None, "none"):
                taught.append({"kind": "retracted", "ref": str(fb.id), "label": "feedback withdrawn"})
            ft.stamp_message(row, "none", None)
            event = "retract"
        else:
            ft.stamp_message(row, verdict, fb.id)
            from flask import current_app
            _app = current_app._get_current_object()
            taught.extend(ft.apply(fb, row, ft.preceding_user_message(row), app=_app))
            event = "set"
            logger.info(f"[FEEDBACK] {verdict} kind={fb.kind} message={fb.message_id} "
                        f"resolved_by={resolved_by} task=\"{(fb.task or '')[:60]}\"")

            if verdict == "up" and session_id:
                if lesson_id:
                    # Inside a lesson the thumb is a pearl; End Lesson distils.
                    try:
                        from backend.socketio_events import emit_lesson_event
                        emit_lesson_event("pearl_added", {
                            "lesson_id": lesson_id,
                            "session_id": session_id,
                            "pearl_id": fb.id,
                            "task": fb.task or "",
                            "created_at": fb.created_at.isoformat() if fb.created_at else None,
                        })
                        taught.append({"kind": "pearl", "ref": lesson_id, "label": "pearl added to the open lesson"})
                    except Exception as emit_err:
                        logger.warning(f"[LESSON] emit pearl_added failed (non-fatal): {emit_err}")
                else:
                    # A verified screen task that pleased the user becomes a
                    # recipe (provisional; a thumbs-down on a later run that
                    # uses it disables it). Gated inside on the run being
                    # verified and matching this task.
                    try:
                        prov = fb.provenance or {}
                        task_for_induction = (prov.get("agent_tasks") or [fb.task or ""])[-1]
                        is_strong = _detect_strong_positive(fb.why_text or "", session_id=session_id)
                        if is_strong:
                            logger.info(f"[INDUCE] strong-positive signal for session={session_id[:8]}")
                        threading.Thread(
                            target=_induce_candidate_recipe,
                            args=(_app, session_id, task_for_induction),
                            kwargs={"strong_positive": is_strong},
                            daemon=True,
                            name=f"induce-{session_id[:8]}",
                        ).start()
                        taught.append({"kind": "induction", "ref": session_id, "label": "checking whether this run becomes a recipe"})
                    except Exception as spawn_err:
                        logger.warning(f"[INDUCE] Failed to spawn induction thread: {spawn_err}")

        ft.append_jsonl(ft.log_entry(fb, event, data, taught, resolved_by))
        return jsonify({"success": True, "feedback": fb.to_dict(),
                        "resolved_by": resolved_by, "taught": taught}), 201
    except Exception as e:
        logger.error(f"Failed to record feedback: {e}", exc_info=True)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/feedback", methods=["GET"])
def list_feedback():
    """List feedback entries. ?limit=50&positive=true"""
    import json
    from pathlib import Path
    from backend.config import GUAARDVARK_ROOT

    feedback_file = Path(GUAARDVARK_ROOT) / "data" / "training" / "knowledge" / "feedback.jsonl"
    if not feedback_file.exists():
        return jsonify({"success": True, "feedback": [], "total": 0})

    entries = []
    for line in open(feedback_file, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Filter
    pos_filter = request.args.get("positive")
    if pos_filter is not None:
        want = pos_filter.lower() == "true"
        entries = [e for e in entries if e.get("positive") == want]

    # Sort newest first
    entries.sort(key=lambda e: e.get("epoch", 0), reverse=True)

    limit = request.args.get("limit", 50, type=int)
    total = len(entries)
    entries = entries[:limit]

    return jsonify({"success": True, "feedback": entries, "total": total})


# ---------------------------------------------------------------------------
# Learning analysis — cross-reference servo data with human feedback
# ---------------------------------------------------------------------------

@agent_control_bp.route("/learning/summary", methods=["GET"])
def learning_summary():
    """Get learning summary — servo stats + feedback cross-reference.

    ?model=gemma4:e4b to filter by model.
    """
    model = request.args.get("model", "")
    try:
        from backend.services.servo_knowledge_store import get_servo_archive
        archive = get_servo_archive()
        summary = archive.get_learning_summary(model=model)
        return jsonify({"success": True, **summary})
    except Exception as e:
        logger.error(f"Learning summary failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


import time as _time

# Circuit breaker: cache capture errors for 5s to prevent log spam from frontend polling
_capture_error_cache = {"error": None, "expires": 0}


@agent_control_bp.route("/capture/raw", methods=["POST"])
def capture_raw():
    """Return a raw JPEG screenshot of the virtual display — no vision analysis."""
    now = _time.time()
    if _capture_error_cache["error"] and now < _capture_error_cache["expires"]:
        return jsonify({"success": False, "error": _capture_error_cache["error"]}), 503

    try:
        from backend.services.local_screen_backend import LocalScreenBackend
        from backend.utils.agent_display_utils import start_agent_display_if_needed
        from io import BytesIO

        data = request.get_json() or {}
        if data.get("auto_start") and not _probe_display_socket(99):
            if not start_agent_display_if_needed():
                _capture_error_cache["error"] = "Agent display not running"
                _capture_error_cache["expires"] = now + 5
                return jsonify({"success": False, "error": "Agent display not running"}), 503

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

        # Clear error cache on success
        _capture_error_cache["error"] = None

        # Only the Agent Screen viewer asks for these frames, so each one says
        # the viewer is open; a watched outreach post reads that.
        from backend.utils.agent_screen_presence import note_frame
        note_frame()

        from flask import send_file
        return send_file(buf, mimetype="image/jpeg")

    except IndexError:
        _capture_error_cache["error"] = "Agent display not running"
        _capture_error_cache["expires"] = now + 5
        logger.error("No monitors available on agent display — is Xvfb running?")
        return jsonify({"success": False, "error": "Agent display not running"}), 503
    except Exception as e:
        _capture_error_cache["error"] = str(e)
        _capture_error_cache["expires"] = now + 5
        logger.error(f"Error in raw capture: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# Candidate recipes: the AgentMemory audit rows written when a thumbs-up
# induced a recipe. Induced recipes are installed at once as provisional (see
# _induce_candidate_recipe); these endpoints list the rows and re-install one
# that was removed. Rejection reuses the existing DELETE /api/memory/<id>.
#   GET  /api/agent-control/candidate-recipes        — list pending
#   POST /api/agent-control/candidate-recipes/<id>/promote
#                                                    — merge into recipes.json
# Rejection reuses the existing DELETE /api/memory/<id>; nothing new needed.
# ---------------------------------------------------------------------------

@agent_control_bp.route("/candidate-recipes", methods=["GET"])
def list_candidate_recipes():
    """List pending candidate recipes induced from successful runs.

    Each row's content is JSON; we parse it inline so the caller sees the
    structured shape without re-parsing.
    """
    try:
        from backend.models import AgentMemory
        rows = (
            AgentMemory.query
            .filter_by(source="candidate_recipe")
            .order_by(AgentMemory.created_at.desc())
            .limit(50)
            .all()
        )
        items = []
        for r in rows:
            parsed = None
            try:
                import json as _json
                parsed = _json.loads(r.content or "")
            except Exception:
                parsed = None
            items.append({
                "id": r.id,
                "session_id": r.session_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "importance": r.importance,
                "candidate": parsed,  # null if parse failed; raw content kept below
                "raw": r.content if parsed is None else None,
            })
        return jsonify({"success": True, "candidates": items, "total": len(items)})
    except Exception as e:
        logger.error(f"list_candidate_recipes failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@agent_control_bp.route("/candidate-recipes/<memory_id>/promote", methods=["POST"])
def promote_candidate_recipe(memory_id: str):
    """Merge a candidate into data/agent/recipes.json under a derived name.

    Body (optional): {"name": "<custom_recipe_name>"} — caller-provided
    recipe key. If omitted, derived from description.

    On success, the AgentMemory row is tagged 'promoted' (not deleted) so
    the user can see what's been merged. Idempotent under repeat calls
    against the same candidate (re-promotion overwrites the recipe entry).
    """
    try:
        import json as _json
        import re as _re
        from pathlib import Path
        from backend.models import db, AgentMemory
        from backend.config import GUAARDVARK_ROOT

        row = AgentMemory.query.filter_by(id=memory_id, source="candidate_recipe").first()
        if not row:
            return jsonify({"success": False, "error": "candidate not found"}), 404

        try:
            candidate = _json.loads(row.content or "")
        except Exception as e:
            return jsonify({"success": False, "error": f"candidate JSON malformed: {e}"}), 400

        if not isinstance(candidate, dict) or "steps" not in candidate or "triggers" not in candidate:
            return jsonify({"success": False, "error": "candidate missing required fields"}), 400

        body = request.get_json(silent=True) or {}
        custom_name = (body.get("name") or "").strip()

        # Defense: reject candidates with coordinate-only click steps. The
        # inducer already filters these out, but a hand-edited row could
        # smuggle them back.
        for s in candidate.get("steps", []):
            if isinstance(s, dict) and s.get("action") == "click":
                if "x" in s or "y" in s:
                    return jsonify({
                        "success": False,
                        "error": "candidate contains coordinate click step (LEARNING_PRINCIPLES violation)"
                    }), 400
        from backend.services.agent_knowledge_validator import validate_recipe
        validation = validate_recipe("candidate", candidate, strict=True)
        if not validation.ok:
            return jsonify({
                "success": False,
                "error": "candidate failed recipe validation",
                "issues": validation.error_messages(),
            }), 400

        # Derive a recipe name from the description if not provided. snake_case
        # short slug, prefixed 'auto_' so it's identifiable as inducer-origin.
        if custom_name:
            name = custom_name
        else:
            desc = candidate.get("description", "") or "induced"
            slug = _re.sub(r"[^a-z0-9]+", "_", desc.lower()).strip("_")[:40] or "induced"
            name = f"auto_{slug}"

        recipes_path = Path(GUAARDVARK_ROOT) / "data" / "agent" / "recipes.json"
        try:
            with recipes_path.open("r") as f:
                recipes = _json.load(f)
        except Exception as e:
            return jsonify({"success": False, "error": f"recipes.json read failed: {e}"}), 500

        # Atomic write: load → modify → write to temp → rename. Avoids a
        # half-written recipes.json if the process gets killed mid-flight.
        proposed_recipe = {
            "description": candidate.get("description", ""),
            "triggers": candidate.get("triggers", []),
            "steps": candidate.get("steps", []),
            "_origin": "candidate_recipe_promotion",
            "_promoted_from": memory_id,
        }
        recipes[name] = proposed_recipe
        from backend.services.agent_knowledge_validator import validate_recipe_library
        library_validation = validate_recipe_library(recipes, strict=False)
        if not library_validation.ok:
            return jsonify({
                "success": False,
                "error": "merged recipe library failed validation",
                "issues": library_validation.error_messages(),
            }), 400
        tmp_path = recipes_path.with_suffix(".json.tmp")
        with tmp_path.open("w") as f:
            _json.dump(recipes, f, indent=2, ensure_ascii=False)
        tmp_path.replace(recipes_path)

        # Tag the AgentMemory row as promoted (kept for audit trail). Re-tag
        # idempotently — a second promote just bumps updated_at.
        existing_tags = []
        if row.tags:
            try:
                existing_tags = _json.loads(row.tags) or []
            except Exception:
                existing_tags = []
        if "promoted" not in existing_tags:
            existing_tags.append("promoted")
        row.tags = _json.dumps(existing_tags)
        db.session.commit()

        # Force the cached recipe library to reload on next agent task.
        try:
            from backend.services.agent_control_service import AgentControlService
            AgentControlService._recipe_cache = None
            AgentControlService._recipe_mtime = 0.0
        except Exception:
            pass

        logger.info(f"[INDUCE] promoted candidate {memory_id[:8]} → recipes.json key '{name}'")
        return jsonify({
            "success": True,
            "name": name,
            "description": candidate.get("description", ""),
            "step_count": len(candidate.get("steps", [])),
        })
    except Exception as e:
        logger.error(f"promote_candidate_recipe failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Agent Display dependency detector + installer
#
# Mirrors voice_api's install-whisper pattern for the virtual display stack:
# Xvfb, x11vnc, the XFCE session, dbus, xdotool, scrot, lsof, a browser, the python `mss`
# module, the start_agent_display.sh script, and the live :99 X socket.
#
# GET  /api/agent-control/display-status  → per-component {installed, version}
# POST /api/agent-control/install-display → apt-get + pip install everything
#                                           that came back missing
# ---------------------------------------------------------------------------

# What the script actually invokes. (apt_package, command_to_probe).
_DISPLAY_SYSTEM_DEPS = [
    ("xvfb", "Xvfb"),
    ("x11vnc", "x11vnc"),
    ("xfce4-session", "startxfce4"),
    ("dbus-daemon", "dbus-run-session"),
    ("xdotool", "xdotool"),
    ("scrot", "scrot"),
    ("lsof", "lsof"),
]
# Browsers — the script auto-picks whichever is present, so any one is fine.
_DISPLAY_BROWSER_CHOICES = [
    ("firefox", "firefox"),
    ("chromium-browser", "chromium-browser"),
    ("chromium", "chromium"),
    ("google-chrome", "google-chrome"),
]


# Not run just to read a version: startxfce4 ignores --version and starts an X server,
# lsof has no --version and answers with an error message.
_NO_VERSION_PROBE = frozenset({"startxfce4", "lsof"})


def _probe_command_version(cmd: str) -> str | None:
    """Best-effort version string. Quick-and-quiet — falls back to None."""
    import shutil
    import subprocess
    if not shutil.which(cmd):
        return None
    if cmd in _NO_VERSION_PROBE:
        return "installed"
    for flag in ("--version", "-version", "-v"):
        try:
            r = subprocess.run(
                [cmd, flag], capture_output=True, text=True, timeout=3
            )
            out = (r.stdout or r.stderr or "").strip().splitlines()
            first = out[0][:120] if out else ""
            # Skip banners that are clearly an error message rather than a version.
            if first and not first.lower().startswith(("unrecognized", "unknown option", "error", "usage:")):
                return first
        except Exception:
            continue
    return "installed"  # Found via shutil.which but no clean version banner


def _probe_display_socket(display_num: int = 99) -> bool:
    """True if /tmp/.X11-unix/X<n> exists — the cheap way to know Xvfb is up."""
    from backend.utils.agent_display_utils import probe_display_socket
    return probe_display_socket(display_num)


def _passwordless_sudo_available() -> bool:
    """True when `sudo -n` runs without prompting for a password."""
    return passwordless_sudo_available()


def _manual_apt_command(packages: list) -> str:
    """The exact line a user should paste to install `packages` themselves."""
    return manual_apt_command(packages)


def _desktop_session_available() -> bool:
    """True when pkexec can raise a graphical password prompt."""
    return desktop_session_available()


# Everything this endpoint is ever allowed to install. Package names come from the
# module constants above, never from the request, but this is interpolated into a
# shell line for pkexec — so gate it explicitly rather than relying on that.
_ALLOWED_APT_PACKAGES = frozenset(
    [pkg for pkg, _ in _DISPLAY_SYSTEM_DEPS]
    + [pkg for pkg, _ in _DISPLAY_BROWSER_CHOICES]
)


def _apt_install_script(packages: list) -> str:
    """One shell line that refreshes the index then installs `packages`."""
    return apt_install_script(packages, allowed=_ALLOWED_APT_PACKAGES)


def _run_privileged_apt(packages: list, timeout: int = 600) -> dict:
    """Install apt packages as root, without a terminal.

    Looks up the sudo/desktop probes by name at call time so tests can patch
    ``_passwordless_sudo_available`` / ``_desktop_session_available`` on this
    module the same way they did before the helper lived in privileged_apt.
    """
    return run_privileged_apt(
        packages,
        allowed=_ALLOWED_APT_PACKAGES,
        timeout=timeout,
        log_label="Agent display install",
        sudo_probe=_passwordless_sudo_available,
        desktop_probe=_desktop_session_available,
    )


@agent_control_bp.route("/display-status", methods=["GET"])
def display_status():
    """Probe everything the agent virtual display needs and report per-component.

    Frontend uses this to decide whether to show the install prompt and to
    list which pieces are missing. Returns 200 even when stuff is broken —
    the JSON tells you what's wrong.
    """
    import os
    import shutil
    from importlib.util import find_spec
    from backend.config import GUAARDVARK_ROOT

    components = {}
    missing_apt = []

    for apt_pkg, cmd in _DISPLAY_SYSTEM_DEPS:
        installed = shutil.which(cmd) is not None
        components[cmd] = {
            "installed": installed,
            "version": _probe_command_version(cmd) if installed else None,
            "apt_package": apt_pkg,
        }
        if not installed:
            missing_apt.append(apt_pkg)

    # Browser — at least one of the choices must be present.
    browser_found = None
    for apt_pkg, cmd in _DISPLAY_BROWSER_CHOICES:
        if shutil.which(cmd):
            browser_found = {
                "command": cmd,
                "apt_package": apt_pkg,
                "version": _probe_command_version(cmd),
            }
            break
    components["browser"] = {
        "installed": browser_found is not None,
        "version": browser_found["version"] if browser_found else None,
        "command": browser_found["command"] if browser_found else None,
        "apt_package": browser_found["apt_package"] if browser_found else "firefox",
    }
    if not browser_found:
        missing_apt.append("firefox")  # Default install target

    # Python mss — what the screen backend uses to capture pixels.
    try:
        mss_spec = find_spec("mss")
        mss_installed = mss_spec is not None
    except Exception:
        mss_installed = False
    mss_version = None
    if mss_installed:
        try:
            import mss as _mss
            mss_version = getattr(_mss, "__version__", "installed")
        except Exception:
            mss_version = "installed"
    components["mss"] = {
        "installed": mss_installed,
        "version": mss_version,
        "pip_package": "mss",
    }

    # Script presence — without this, none of the rest helps.
    script_path = os.path.join(GUAARDVARK_ROOT, "scripts", "start_agent_display.sh")
    components["start_script"] = {
        "installed": os.path.exists(script_path),
        "path": script_path,
    }

    # Live :99 socket — already-running display means the user is good to go.
    components["display_running"] = {
        "installed": _probe_display_socket(99),
        "display": ":99",
    }

    all_ready = all(c.get("installed") for c in components.values() if c is not components["display_running"])

    # Only apt packages need root. Probe just when something is actually missing, so
    # the common all-installed path does no extra work. The UI uses install_method to
    # warn that a password prompt will appear on the desktop, and falls back to a
    # copyable command when neither escalation route exists.
    can_auto_install = True
    install_method = None
    manual_command = None
    if missing_apt:
        if _passwordless_sudo_available():
            install_method = "sudo"
        elif _desktop_session_available():
            install_method = "pkexec"
        else:
            install_method = "none"
            can_auto_install = False
            manual_command = _manual_apt_command(missing_apt)

    return jsonify({
        "success": True,
        "ready": all_ready,
        "display_running": components["display_running"]["installed"],
        "components": components,
        "missing_apt_packages": missing_apt,
        "missing_pip_packages": [] if mss_installed else ["mss"],
        "can_auto_install": can_auto_install,
        "install_method": install_method,
        "manual_command": manual_command,
    })


@agent_control_bp.route("/install-display", methods=["POST"])
def install_display():
    """Install the apt + pip dependencies the agent display needs.

    Mirrors voice_api install-whisper: figure out what's missing, shell out to
    `sudo apt-get install -y ...` and `pip install ...`, return what we did.
    Sudo is required for apt; if the host doesn't have passwordless sudo for
    apt-get, this will fail with a useful error message.
    """
    import os
    import shutil
    import subprocess
    import sys
    from importlib.util import find_spec

    try:
        # Recompute what's missing so we don't trust stale frontend state.
        missing_apt = []
        for apt_pkg, cmd in _DISPLAY_SYSTEM_DEPS:
            if not shutil.which(cmd):
                missing_apt.append(apt_pkg)

        # Browser — pick one if none present. Default to firefox.
        if not any(shutil.which(cmd) for _, cmd in _DISPLAY_BROWSER_CHOICES):
            missing_apt.append("firefox")

        try:
            mss_missing = find_spec("mss") is None
        except Exception:
            mss_missing = True

        steps = []

        # Only apt needs root; a pip-only gap installs unattended either way.
        if missing_apt:
            apt = _run_privileged_apt(missing_apt)
            steps.append({
                "step": "apt_install",
                "packages": missing_apt,
                "method": apt["method"],
                "returncode": apt["returncode"],
                "stderr_tail": apt["stderr"][-500:],
            })

            if apt["method"] == "none":
                # No passwordless sudo and no desktop session to prompt on — e.g.
                # reached over SSH or from another machine's browser. Nothing to do
                # but hand back the command.
                return jsonify({
                    "success": False,
                    "needs_manual_install": True,
                    "manual_command": _manual_apt_command(missing_apt),
                    "missing_apt_packages": missing_apt,
                    "error": (
                        "Installing system packages needs root, and this session has no "
                        "desktop to show a password prompt on. Run the command below on "
                        "the machine itself, then click Recheck."
                    ),
                    "steps": steps,
                }), 409

            if not apt["ok"]:
                stderr = apt["stderr"].strip()
                rc = apt["returncode"]
                # pkexec: 126 = dismissed or wrong password, 127 = agent missing.
                if apt["method"] == "pkexec" and rc == 126:
                    return jsonify({
                        "success": False,
                        "error": (
                            "Authorisation was dismissed or the password was wrong. "
                            "Click Install again and approve the prompt on your desktop."
                        ),
                        "steps": steps,
                    }), 403
                if rc == "timeout":
                    return jsonify({
                        "success": False,
                        "error": (
                            "Timed out waiting for the install. If a password prompt is "
                            "open on your desktop, approve it and try again."
                        ),
                        "steps": steps,
                    }), 504
                return jsonify({
                    "success": False,
                    "needs_manual_install": True,
                    "manual_command": _manual_apt_command(missing_apt),
                    "missing_apt_packages": missing_apt,
                    "error": (
                        f"apt-get failed (exit {rc}) via {apt['method']}. "
                        "Run the command below in a terminal, then click Recheck."
                    ),
                    "stderr_tail": stderr[-500:],
                    "steps": steps,
                }), 500

        if mss_missing:
            logger.info("Agent display install: pip installing mss")
            pip_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "mss"],
                capture_output=True, text=True, timeout=180,
            )
            steps.append({
                "step": "pip_install",
                "packages": ["mss"],
                "returncode": pip_result.returncode,
                "stderr_tail": (pip_result.stderr or "")[-500:],
            })
            if pip_result.returncode != 0:
                return jsonify({
                    "success": False,
                    "error": "pip install mss failed",
                    "stderr_tail": (pip_result.stderr or "")[-500:],
                    "steps": steps,
                }), 500

        # Verify the script is present too — it ships with the repo, so its
        # absence means a corrupt checkout, not something we can apt-install.
        from backend.config import GUAARDVARK_ROOT
        script_path = os.path.join(GUAARDVARK_ROOT, "scripts", "start_agent_display.sh")
        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": f"start_agent_display.sh missing at {script_path} — re-pull the repo",
                "steps": steps,
            }), 500

        # Re-probe after install so the response reflects reality.
        still_missing = [pkg for pkg, cmd in _DISPLAY_SYSTEM_DEPS if not shutil.which(cmd)]
        if not any(shutil.which(cmd) for _, cmd in _DISPLAY_BROWSER_CHOICES):
            still_missing.append("(browser)")
        try:
            mss_ok = find_spec("mss") is not None
        except Exception:
            mss_ok = False

        if still_missing or not mss_ok:
            payload = {
                "success": False,
                "error": f"Install completed but components still missing: {still_missing} mss_ok={mss_ok}",
                "steps": steps,
            }
            # "(browser)" is a placeholder for "any one of several", not a real
            # package, so keep it out of a command we tell the user to paste.
            real_pkgs = [p for p in still_missing if not p.startswith("(")]
            if real_pkgs:
                payload["needs_manual_install"] = True
                payload["manual_command"] = _manual_apt_command(real_pkgs)
                payload["missing_apt_packages"] = real_pkgs
            return jsonify(payload), 500

        nothing_to_do = not missing_apt and not mss_missing
        return jsonify({
            "success": True,
            "already_installed": nothing_to_do,
            "message": (
                "Agent Display dependencies already installed."
                if nothing_to_do else
                "Agent Display dependencies installed. Run "
                "`bash scripts/start_agent_display.sh start` or restart Guaardvark."
            ),
            "steps": steps,
        })

    except Exception as e:
        logger.error(f"install_display failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


def _run_display_script(action: str, timeout: int = 60) -> dict:
    """Shell out to scripts/start_agent_display.sh <action> and return a
    structured result. Used by both start-display and stop-display so the
    error shape stays consistent.
    """
    from backend.utils.agent_display_utils import run_display_script
    return run_display_script(action, timeout=timeout)


@agent_control_bp.route("/start-display", methods=["POST"])
def start_display():
    """Bring the agent display up. Idempotent — the start script returns
    fast if Xvfb / the XFCE session / x11vnc are already running.

    Returns 200 on success with a `display_running` flag, 500 on script
    failure with stderr_tail for debugging.
    """
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    running = start_agent_display_if_needed()
    if running:
        return jsonify({
            "success": True,
            "display_running": True,
            "message": "Agent display is up on :99.",
        })
    result = _run_display_script("start", timeout=60)
    return jsonify({
        "success": False,
        "error": f"start_agent_display.sh start failed (exit {result.get('returncode')})",
        "stderr_tail": result.get("stderr_tail", ""),
        "stdout_tail": result.get("stdout_tail", ""),
    }), 500


@agent_control_bp.route("/stop-display", methods=["POST"])
def stop_display():
    """Tear the agent display down. Idempotent — stop is a no-op if
    nothing's running.
    """
    from backend.utils.agent_display_utils import stop_agent_display

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    result = stop_agent_display(force=force)

    if result.get("blocked"):
        return jsonify({
            "success": False,
            "blocked": True,
            "error": result.get("error"),
            "display_running": result.get("display_running", True),
        }), 409

    if not result.get("display_running", True):
        return jsonify({
            "success": True,
            "display_running": False,
            "message": "Agent display stopped.",
            "stdout_tail": result.get("stdout_tail", ""),
        })

    return jsonify({
        "success": False,
        "error": "Stop ran but :99 socket is still alive — something is keeping it up",
        "stderr_tail": result.get("stderr_tail", ""),
        "stdout_tail": result.get("stdout_tail", ""),
    }), 500


@agent_control_bp.route("/display-idle-arm", methods=["POST"])
def display_idle_arm():
    """Arm idle shutdown after the viewer/training UI closes (default 5 min)."""
    from backend.utils.agent_display_utils import (
        DEFAULT_IDLE_SHUTDOWN_SECONDS,
        arm_display_idle_shutdown,
        get_idle_shutdown_deadline,
    )

    data = request.get_json(silent=True) or {}
    seconds = int(data.get("seconds", DEFAULT_IDLE_SHUTDOWN_SECONDS))
    seconds = max(60, min(3600, seconds))
    deadline = arm_display_idle_shutdown(seconds=seconds)
    return jsonify({
        "success": True,
        "armed": True,
        "seconds": seconds,
        "deadline": deadline,
        "idle_deadline": get_idle_shutdown_deadline(),
    })


@agent_control_bp.route("/display-idle-disarm", methods=["POST"])
def display_idle_disarm():
    """Cancel a pending idle shutdown (viewer/training reopened)."""
    from backend.utils.agent_display_utils import cancel_display_idle_shutdown

    cancel_display_idle_shutdown()
    return jsonify({"success": True, "armed": False})
