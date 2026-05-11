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

        mouse_only = data.get("mouse_only", False)
        training_mode = data.get("training_mode", False)

        # Run in background thread so the API doesn't block
        def run_task():
            result = service.execute_task(task, screen, mouse_only=mouse_only, training_mode=training_mode)
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


# ---------------------------------------------------------------------------
# Task feedback — thumbs up/down from the user after any agent task
# ---------------------------------------------------------------------------

def _induce_candidate_recipe(app, session_id: str, feedback_task: str):
    """Background thread: when the user thumbs-up's a successful task that
    wasn't part of a bracketed lesson, induce a recipes.json-shaped entry
    capturing what made it work, so the same task pattern auto-executes
    deterministically next time.

    This is the AWM (Agent Workflow Memory, ICML 2025) pattern, adapted to
    Guaardvark: positive feedback + matching last successful run = candidate
    recipe. Output goes to AgentMemory with source='candidate_recipe' for
    user review; never auto-promoted to recipes.json. Promotion is a
    deliberate action via /api/candidate-recipes/<id>/promote.

    Safety gates:
    - Only induces if the agent's _last_result.task == feedback.task
      (avoids inducing from stale state when the user thumbs-up's an old run).
    - Only induces on success=True (failed runs would teach the wrong path).
    - Skips if action_history is empty or has only one trivial step.
    - Hard rules in the prompt: vision-actionable target_descriptions,
      short labels (≤4 words), no pixel coordinates. Per
      data/agent/LEARNING_PRINCIPLES.md.
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
                "  (≤4 words, one distinctive adjective): 'orange Firefox button', "
                "  'chat input field', 'Send button'. Long descriptions break the "
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

            # Parse: try direct JSON, then regex-extract the first {...} block.
            import json as _json
            import re as _re
            parsed = None
            if raw:
                candidates = [raw]
                m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                if m:
                    candidates.append(m.group(0))
                for cand in candidates:
                    try:
                        parsed = _json.loads(cand)
                        if isinstance(parsed, dict) and "steps" in parsed and "triggers" in parsed:
                            break
                        parsed = None
                    except Exception:
                        parsed = None
            if not parsed:
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

            content_json = _json.dumps(normalized, ensure_ascii=False)
            row = AgentMemory(
                id=str(_uuid.uuid4()),
                content=content_json,
                source="candidate_recipe",
                session_id=session_id,
                type="snippet",
                importance=0.7,
                tags=_json.dumps(["candidate_recipe", "auto_induced"]),
            )
            db.session.add(row)
            db.session.commit()
            logger.info(
                f"[INDUCE] saved candidate recipe {row.id[:8]} — "
                f"\"{description[:60]}\" with {len(normalized['steps'])} steps"
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

            prompt = (
                "You are Guaardvark, a local AI assistant. The user just approved "
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
                import uuid
                mem = AgentMemory(
                    id=uuid.uuid4().hex[:12],
                    content=note,
                    source="learned_from_feedback",
                    session_id=session_id,
                    type="note",
                    importance=0.7,
                )
                db.session.add(mem)
                logger.info(f"[DISTILL] Saved pearl memory for session {session_id[:12]}: {note[:80]}")
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
    """Record thumbs up/down feedback for an agent task.

    Body: {
        positive: bool,         # true = thumbs up, false = thumbs down
        task: str,              # the task description
        session_id: str?,       # chat session that triggered the task
        steps: int?,            # number of steps the task took
        time_seconds: float?,   # total execution time
        comment: str?,          # optional user comment
    }

    Writes to data/training/knowledge/feedback.jsonl — same dir as servo_archive.
    Each entry carries the human verdict so the learning loop has ground truth.
    """
    data = request.get_json(silent=True)
    if not data or "positive" not in data:
        return jsonify({"success": False, "error": "'positive' field required (true/false)"}), 400

    import json
    import time
    from datetime import datetime
    from pathlib import Path
    from backend.config import GUAARDVARK_ROOT

    # Read lesson_id from body; if absent, auto-attach from the active-lesson
    # registry so the frontend doesn't have to carry it. Belt-and-suspenders:
    # even if MessageItem forgets to send lesson_id, pearls captured inside an
    # open Begin/End bracket get grouped correctly.
    lesson_id = (data.get("lesson_id") or None)
    session_id = data.get("session_id")
    if not lesson_id and session_id:
        try:
            from backend.api.lessons_api import get_active_lesson_id
            lesson_id = get_active_lesson_id(session_id)
        except Exception:
            lesson_id = None

    entry = {
        "timestamp": datetime.now().isoformat(),
        "epoch": time.time(),
        "positive": bool(data["positive"]),
        "task": data.get("task", ""),
        "type": data.get("type", "tool_action"),  # "tool_action" or "response"
        "session_id": session_id,
        "lesson_id": lesson_id,
        "steps": data.get("steps"),
        "time_seconds": data.get("time_seconds"),
        "comment": data.get("comment", ""),
        "model": data.get("model", ""),
    }

    # Session-less pearls can't be grouped into a thread later, so surface
    # that here — the frontend should send session_id on every feedback ping.
    if entry["session_id"] is None:
        logger.warning(
            "[FEEDBACK] session_id missing on %s — pearl won't be groupable by session",
            entry["type"],
        )

    feedback_file = Path(GUAARDVARK_ROOT) / "data" / "training" / "knowledge" / "feedback.jsonl"
    try:
        feedback_file.parent.mkdir(parents=True, exist_ok=True)
        with open(feedback_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"[FEEDBACK] {'👍' if entry['positive'] else '👎'} task=\"{entry['task'][:60]}\"")
        
        # PERSIST TO DATABASE (Structured storage)
        db_entry_id = None
        try:
            from backend.models import db, ToolFeedback
            db_entry = ToolFeedback(
                session_id=entry["session_id"],
                lesson_id=entry["lesson_id"],
                tool_name=data.get("tool_name", entry["task"][:100]), # preferred tool_name
                task=entry["task"],
                positive=entry["positive"],
                steps=entry["steps"],
                time_seconds=entry["time_seconds"],
                model=entry["model"]
            )
            db.session.add(db_entry)
            db.session.commit()
            db_entry_id = db_entry.id
            logger.debug(f"[FEEDBACK] Persisted to database: ID={db_entry.id}")
        except Exception as db_err:
            logger.warning(f"[FEEDBACK] Failed to persist to database (non-fatal): {db_err}")

        # Stamp the feedback state onto the matching chat message so the
        # thumb-icon survives a page refresh. Match by session_id + content
        # prefix — the "task" field is already content[:200] from the frontend.
        if entry["session_id"] and entry["task"]:
            try:
                from backend.models import db, LLMMessage
                msg = (
                    LLMMessage.query
                    .filter(
                        LLMMessage.session_id == entry["session_id"],
                        LLMMessage.role == ("user" if entry["type"] == "tool_action" else "assistant"),
                        LLMMessage.content.like(entry["task"][:100].replace("%", r"\%").replace("_", r"\_") + "%"),
                    )
                    .order_by(LLMMessage.timestamp.desc())
                    .first()
                )
                if msg is not None:
                    current_extra = dict(msg.extra_data or {})
                    current_extra["feedback"] = "up" if entry["positive"] else "down"
                    msg.extra_data = current_extra
                    # JSON mutation assignment — SQLAlchemy needs flag_modified
                    # for nested dicts, but whole-dict reassignment is tracked.
                    db.session.commit()
            except Exception as stamp_err:
                logger.warning(f"[FEEDBACK] Could not stamp message extra_data: {stamp_err}")
                try:
                    from backend.models import db
                    db.session.rollback()
                except Exception:
                    pass

        # Positive pearl handling:
        #   - Active lesson  → emit a live pearl event so the lesson floater
        #     shows progress. Real distillation runs on POST /api/lessons/<id>/end,
        #     which produces vision-actionable, parameterized lesson steps.
        #   - No active lesson → spawn AWM-style recipe induction. Replaces the
        #     deprecated _distill_pearl_memory junk distiller. Inducer is gated
        #     to only fire on a successful last_result that matches the feedback
        #     task, and it produces a candidate_recipe row in AgentMemory for
        #     user review (never auto-promoted to recipes.json).
        if entry["positive"] and entry["session_id"]:
            if entry["lesson_id"]:
                try:
                    from backend.socketio_events import emit_lesson_event
                    emit_lesson_event("pearl_added", {
                        "lesson_id": entry["lesson_id"],
                        "session_id": entry["session_id"],
                        "pearl_id": db_entry_id,
                        "task": entry["task"],
                        "created_at": entry["timestamp"],
                    })
                except Exception as emit_err:
                    logger.warning(f"[LESSON] emit pearl_added failed (non-fatal): {emit_err}")
            else:
                try:
                    from flask import current_app
                    _app = current_app._get_current_object()
                    threading.Thread(
                        target=_induce_candidate_recipe,
                        args=(_app, entry["session_id"], entry["task"] or ""),
                        daemon=True,
                        name=f"induce-{entry['session_id'][:8]}",
                    ).start()
                except Exception as spawn_err:
                    logger.warning(f"[INDUCE] Failed to spawn induction thread: {spawn_err}")

        return jsonify({"success": True, "feedback": entry}), 201
    except Exception as e:
        logger.error(f"Failed to write feedback: {e}")
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

        # Clear error cache on success
        _capture_error_cache["error"] = None

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


# ---------------------------------------------------------------------------
# Candidate recipes — Phase 3 (Agent Workflow Memory)
#
# After a successful task that wasn't part of a bracketed lesson, the
# inducer (above) drops a recipes.json-shaped JSON into AgentMemory with
# source="candidate_recipe". These two endpoints surface that queue:
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
                    if not s.get("target_description"):
                        return jsonify({
                            "success": False,
                            "error": "candidate contains coordinate-only click step (LEARNING_PRINCIPLES violation)"
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
        recipes[name] = {
            "description": candidate.get("description", ""),
            "triggers": candidate.get("triggers", []),
            "steps": candidate.get("steps", []),
            "_origin": "candidate_recipe_promotion",
            "_promoted_from": memory_id,
        }
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
# Xvfb, x11vnc, openbox, tint2, xdotool, scrot, a browser, the python `mss`
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
    ("openbox", "openbox"),
    ("tint2", "tint2"),
    ("xdotool", "xdotool"),
    ("scrot", "scrot"),
]
# Browsers — the script auto-picks whichever is present, so any one is fine.
_DISPLAY_BROWSER_CHOICES = [
    ("firefox", "firefox"),
    ("chromium-browser", "chromium-browser"),
    ("chromium", "chromium"),
    ("google-chrome", "google-chrome"),
]


def _probe_command_version(cmd: str) -> str | None:
    """Best-effort version string. Quick-and-quiet — falls back to None."""
    import shutil
    import subprocess
    if not shutil.which(cmd):
        return None
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
    import os
    return os.path.exists(f"/tmp/.X11-unix/X{display_num}")


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
    return jsonify({
        "success": True,
        "ready": all_ready,
        "display_running": components["display_running"]["installed"],
        "components": components,
        "missing_apt_packages": missing_apt,
        "missing_pip_packages": [] if mss_installed else ["mss"],
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

        if missing_apt:
            logger.info(f"Agent display install: apt-get installing {missing_apt}")
            try:
                apt_result = subprocess.run(
                    ["sudo", "-n", "apt-get", "install", "-y", *missing_apt],
                    capture_output=True, text=True, timeout=600,
                )
            except subprocess.TimeoutExpired:
                return jsonify({
                    "success": False,
                    "error": "apt-get timed out after 10 minutes — check network and try again",
                }), 500
            steps.append({
                "step": "apt_install",
                "packages": missing_apt,
                "returncode": apt_result.returncode,
                "stderr_tail": (apt_result.stderr or "")[-500:],
            })
            if apt_result.returncode != 0:
                stderr = (apt_result.stderr or "").strip()
                hint = ""
                if "sudo" in stderr.lower() or "password" in stderr.lower():
                    hint = (
                        " Passwordless sudo not configured for apt-get. "
                        "Run manually: sudo apt-get install -y "
                        + " ".join(missing_apt)
                    )
                return jsonify({
                    "success": False,
                    "error": f"apt-get install failed (exit {apt_result.returncode}).{hint}",
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
            return jsonify({
                "success": False,
                "error": f"Install completed but components still missing: {still_missing} mss_ok={mss_ok}",
                "steps": steps,
            }), 500

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
    import os
    import subprocess
    from backend.config import GUAARDVARK_ROOT

    script = os.path.join(GUAARDVARK_ROOT, "scripts", "start_agent_display.sh")
    if not os.path.exists(script):
        return {
            "success": False,
            "error": f"start_agent_display.sh missing at {script} — re-pull the repo",
            "returncode": -1,
        }

    try:
        result = subprocess.run(
            ["bash", script, action],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"{action} timed out after {timeout}s",
            "returncode": -1,
        }

    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-500:],
        "stderr_tail": (result.stderr or "")[-500:],
    }


@agent_control_bp.route("/start-display", methods=["POST"])
def start_display():
    """Bring the agent display up. Idempotent — the start script returns
    fast if Xvfb / openbox / x11vnc are already running.

    Returns 200 on success with a `display_running` flag, 500 on script
    failure with stderr_tail for debugging.
    """
    result = _run_display_script("start", timeout=60)
    # Probe the live socket regardless of script returncode — sometimes
    # the script reports stale errors but :99 is up. Truth lives in /tmp.
    result["display_running"] = _probe_display_socket(99)

    if result["success"] or result["display_running"]:
        return jsonify({
            "success": True,
            "display_running": result["display_running"],
            "message": "Agent display is up on :99.",
            "stdout_tail": result.get("stdout_tail", ""),
        })
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
    result = _run_display_script("stop", timeout=30)
    result["display_running"] = _probe_display_socket(99)

    # We trust the post-stop socket probe: if the socket is gone, it's
    # stopped, regardless of what the script's returncode said.
    if not result["display_running"]:
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
