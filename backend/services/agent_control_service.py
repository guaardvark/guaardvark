#!/usr/bin/env python3
"""
Agent Control Service — The brain of the Agent Vision Control system.

Orchestrates the see-think-act loop:
1. Capture screenshot (via ScreenInterface)
2. Analyze with vision model (direct Ollama call)
3. LLM decides next action
4. Execute action — clicks via ServoController (closed-loop motor control),
   type/hotkey/scroll via direct ScreenInterface calls
5. Record step and repeat until task complete or limits hit
"""

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

# Singleton instance
_service_instance = None
_service_lock = threading.Lock()


@dataclass
class AgentControlConfig:
    """Configuration for the agent control loop."""
    max_iterations: int = 50
    max_consecutive_failures: int = 5
    task_timeout_seconds: int = 300  # 5 minutes
    action_timeout_seconds: int = 60
    verify_actions: bool = True
    grid_cols: int = 8
    grid_rows: int = 8
    bullseye_size: int = 48
    vision_model: str = "qwen3-vl:2b-instruct"
    escalation_model: str = "qwen3-vl:8b"
    escalation_threshold: int = 3  # failures before escalating


@dataclass
class AgentAction:
    """A single action the agent wants to perform."""
    action_type: str = ""  # click, type, hotkey, scroll, move, done
    target_cell: str = ""  # Grid cell (e.g., "D4") — for clicks
    target_description: str = ""  # What the agent thinks it's clicking
    coordinates: Optional[Tuple[int, int]] = None  # Refined pixel coords
    text: str = ""  # For type actions
    keys: List[str] = field(default_factory=list)  # For hotkey actions
    scroll_amount: int = 0  # For scroll actions
    reasoning: str = ""  # Why the agent chose this action


@dataclass
class AgentDecision:
    """The LLM's decision for the current iteration."""
    action: AgentAction = field(default_factory=AgentAction)
    task_complete: bool = False
    stuck: bool = False
    raw_output: str = ""


@dataclass
class ActionStep:
    """Record of a single action taken."""
    iteration: int = 0
    scene_description: str = ""
    action: AgentAction = field(default_factory=AgentAction)
    result: Dict[str, Any] = field(default_factory=dict)
    verification: Optional[str] = None
    failed: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """Final result of a task execution."""
    success: bool = False
    reason: str = ""
    steps: List[ActionStep] = field(default_factory=list)
    total_time_seconds: float = 0.0


class AgentControlService:
    """
    Master-side orchestration service for Agent Vision Control.

    Manages agent mode state and executes the see-think-act loop.
    """

    def __init__(self):
        self._active = False
        self._ready = False
        self._killed = False
        self._learning = False
        self._demo_recorder = None
        self._current_demonstration_id = None
        self._learning_answer_queue = queue.Queue()
        self._step_confirm_event = threading.Event()
        self._step_confirm_data = None
        self._current_task: Optional[str] = None
        self._current_iteration: int = 0
        self._action_history: List[ActionStep] = []
        self._last_result: Optional[AgentResult] = None
        self._lock = threading.Lock()
        self.config = AgentControlConfig()

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_learning(self) -> bool:
        return self._learning

    def start(self):
        """Activate agent mode — ready to accept tasks."""
        self._killed = False
        self._ready = True
        logger.info("Agent mode activated")

    def stop(self):
        """Gracefully stop agent mode."""
        self._ready = False
        self._active = False
        logger.info("Agent mode deactivated")

    def kill(self):
        """Emergency stop — immediately halt all agent operations."""
        self._killed = True
        self._active = False
        self._ready = False
        logger.warning("KILL SWITCH ACTIVATED — all agent operations halted")

    def get_status(self) -> Dict[str, Any]:
        """Get current agent control status."""
        last = None
        if self._last_result:
            last = {"success": self._last_result.success, "reason": self._last_result.reason,
                     "steps": len(self._last_result.steps), "time": self._last_result.total_time_seconds}
        return {
            "active": self._active,
            "ready": self._ready,
            "killed": self._killed,
            "learning": self._learning,
            "current_demonstration_id": self._current_demonstration_id,
            "current_task": self._current_task,
            "iteration": self._current_iteration,
            "history_length": len(self._action_history),
            "last_result": last,
        }

    def execute_task(self, task: str, screen, mouse_only: bool = False) -> AgentResult:
        """
        Execute a task using the see-think-act loop.

        Args:
            task: Natural language description of the task
            screen: ScreenInterface implementation
            mouse_only: If True, disable keyboard shortcuts — pure mouse clicks only

        Returns:
            AgentResult with success status and action history
        """
        self._mouse_only = mouse_only
        from backend.services.servo_controller import ServoController
        from backend.services.training_data_collector import TrainingDataCollector
        from backend.utils.vision_analyzer import VisionAnalyzer

        with self._lock:
            if self._active:
                return self._store_and_return(AgentResult(success=False, reason="Agent already active"))
            self._active = True
            self._killed = False
            self._current_task = task
            self._current_iteration = 0
            self._action_history = []

        analyzer = VisionAnalyzer(default_model=self.config.vision_model)
        collector = TrainingDataCollector()
        servo = ServoController(screen, analyzer, collector=collector)
        consecutive_failures = 0
        start_time = time.time()

        # Check for recipe match — skip see-think-act loop for known patterns
        recipe_result = self._try_recipe(task, screen)
        if recipe_result is not None:
            self._active = False
            return self._store_and_return(recipe_result)

        try:
            for iteration in range(self.config.max_iterations):
                if self._killed:
                    return self._store_and_return(AgentResult(
                        success=False, reason="killed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if time.time() - start_time > self.config.task_timeout_seconds:
                    return self._store_and_return(AgentResult(
                        success=False, reason="timeout",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                self._current_iteration = iteration

                # 1. SEE — Capture screenshot
                screenshot, cursor_pos = self._capture_with_retry(screen)
                logger.warning(f"[AGENT][STEP {iteration+1}][SEE] Capturing screen, cursor at {cursor_pos}")

                scene_desc = ""  # Will be populated by either unified or split path

                # Check for unified vision+decision model (qwen3-vl:4b+)
                unified_model = self._get_unified_model()

                if unified_model:
                    # UNIFIED MODE: Compact prompt — vision model sees screenshot + short context
                    unified_prompt = self._build_unified_prompt(task, self._action_history)
                    result = analyzer.analyze(
                        screenshot, prompt=unified_prompt,
                        model=unified_model, num_predict=256, temperature=0.1
                    )
                    if not result.success:
                        logger.error(f"[AGENT][STEP {iteration+1}][UNIFIED] Vision+decision failed: {result.error}")
                        consecutive_failures += 1
                        continue
                    scene_desc = result.description[:200]
                    logger.warning(f"[AGENT][STEP {iteration+1}][UNIFIED] {scene_desc}")
                    decision = self._parse_decision(result.description)
                else:
                    # SPLIT MODE: Separate SEE → ASSESS → THINK pipeline
                    # 2. ANALYZE — Vision model describes the screen
                    vision_prompt = self._build_vision_prompt(task, self._action_history)
                    scene = analyzer.analyze(screenshot, prompt=vision_prompt)
                    if not scene.success:
                        logger.error(f"[AGENT][STEP {iteration+1}][SEE] Vision failed: {scene.error}")
                        consecutive_failures += 1
                        continue
                    scene_desc = scene.description[:200].replace('\n', ' ')
                    logger.warning(f"[AGENT][STEP {iteration+1}][SEE] {scene_desc}")

                    # 2b. ASSESS — Check for obstacles before proceeding
                    obstacle = self._assess_obstacles(scene.description, analyzer, screen, iteration)
                    if obstacle == "handled":
                        logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Obstacle handled, re-scanning")
                        continue
                    elif obstacle == "escalated":
                        logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Escalated to thinking model")
                        continue

                    # 3. THINK — Text LLM decides next action
                    decision_prompt = self._build_decision_prompt(
                        task, scene.description, self._action_history
                    )
                    decision_result = analyzer.text_query(decision_prompt)
                    if not decision_result.success:
                        logger.error(f"[AGENT][STEP {iteration+1}][THINK] Decision failed")
                        consecutive_failures += 1
                        continue
                    decision = self._parse_decision(decision_result.description)
                logger.warning(f"[AGENT][STEP {iteration+1}][THINK] action={decision.action.action_type} "
                              f"target=\"{decision.action.target_description or ''}\" "
                              f"text=\"{decision.action.text or ''}\" "
                              f"keys={decision.action.keys or ''} "
                              f"reasoning=\"{decision.action.reasoning or ''}\"")

                if decision.task_complete:
                    logger.warning(f"[AGENT][DONE] Task complete after {iteration+1} steps, "
                                  f"{time.time() - start_time:.1f}s")
                    return self._store_and_return(AgentResult(
                        success=True, reason="completed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if decision.stuck:
                    logger.warning(f"[AGENT][STEP {iteration+1}][THINK] Agent reports stuck")
                    consecutive_failures += 1
                    continue

                # 4. ACT — Execute via servo (for clicks) or direct (for type/hotkey/scroll)
                # In mouse_only mode, reject any non-click action
                if getattr(self, '_mouse_only', False) and decision.action.action_type not in ("click", "done"):
                    logger.info(f"[AGENT][STEP {iteration+1}][ACT] Mouse-only: rejecting {decision.action.action_type}")
                    decision.action.action_type = "click"
                    if not decision.action.target_description:
                        consecutive_failures += 1
                        continue

                if decision.action.action_type == "click":
                    servo_result = servo.click_target(decision.action.target_description)
                    decision.action.coordinates = (servo_result.get("x", 0), servo_result.get("y", 0))
                    result = {"success": servo_result.get("success", False)}
                    failed = not servo_result.get("success", False)
                    status_icon = "OK" if not failed else "FAIL"
                    logger.warning(f"[AGENT][STEP {iteration+1}][ACT] click \"{decision.action.target_description}\" "
                                  f"at ({servo_result.get('x', '?')},{servo_result.get('y', '?')}) [{status_icon}]")
                else:
                    result = self._execute_action(decision.action, screen)
                    failed = not result.get("success", False)
                    status_icon = "OK" if not failed else "FAIL"
                    detail = decision.action.text or str(decision.action.keys) or ""
                    logger.warning(f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} "
                                  f"\"{detail}\" [{status_icon}]")

                # 5. RECORD step
                step = ActionStep(
                    iteration=iteration,
                    scene_description=scene_desc or "no scene",
                    action=decision.action,
                    result=result,
                    failed=failed,
                )
                self._action_history.append(step)

                if failed:
                    consecutive_failures += 1
                    if consecutive_failures >= self.config.max_consecutive_failures:
                        logger.warning(f"Kill switch: {consecutive_failures} consecutive failures")
                        self.kill()
                        return self._store_and_return(AgentResult(
                            success=False, reason="max_failures",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time
                        ))
                else:
                    consecutive_failures = 0

            return self._store_and_return(AgentResult(
                success=False, reason="max_iterations",
                steps=self._action_history,
                total_time_seconds=time.time() - start_time
            ))

        except Exception as e:
            logger.error(f"Task execution error: {e}", exc_info=True)
            return self._store_and_return(AgentResult(
                success=False, reason=f"error: {e}",
                steps=self._action_history,
                total_time_seconds=time.time() - start_time
            ))

        finally:
            self._active = False
            self._current_task = None

    def start_learning(self, name=None, description="", tags=None):
        """Enter LEARNING state and start recording human demonstration."""
        if self._active:
            return {"success": False, "error": "Agent is currently executing a task. Kill it first."}
        if self._learning:
            return {"success": False, "error": "Already in learning mode."}

        from backend.models import db, Demonstration
        from flask import current_app

        with current_app.app_context():
            demo = Demonstration(
                name=name,
                description=description or "Untitled demonstration",
                tags=tags or [],
                is_complete=False,
            )
            db.session.add(demo)
            db.session.commit()
            demo_id = demo.id

        self._current_demonstration_id = demo_id

        from backend.services.local_screen_backend import LocalScreenBackend
        from backend.utils.vision_analyzer import VisionAnalyzer
        screen = LocalScreenBackend()
        analyzer = VisionAnalyzer()

        from backend.services.demo_recorder import DemoRecorder
        self._demo_recorder = DemoRecorder(
            screen=screen,
            analyzer=analyzer,
        )
        self._demo_recorder.start(demo_id=str(demo_id))
        self._learning = True

        from backend.socketio_events import emit_learning_mode_started
        emit_learning_mode_started(demo_id, name)

        logger.info(f"Learning mode started: demo_id={demo_id}, name={name}")
        return {"success": True, "demonstration_id": demo_id}

    def stop_learning(self):
        """Stop recording and save the demonstration."""
        if not self._learning or not self._demo_recorder:
            return {"success": False, "error": "Not in learning mode."}

        self._demo_recorder.stop()
        steps = self._demo_recorder.get_steps()

        from backend.models import db, Demonstration, DemoStep
        from flask import current_app

        demo_id = self._current_demonstration_id

        with current_app.app_context():
            demo = db.session.get(Demonstration, demo_id)
            if demo:
                for step_data in steps:
                    step = DemoStep(
                        demonstration_id=demo_id,
                        step_index=step_data["step_index"],
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
                        is_mistake=step_data.get("is_potential_mistake", False),
                        screenshot_before=step_data.get("screenshot_before"),
                        screenshot_after=step_data.get("screenshot_after"),
                    )
                    db.session.add(step)
                demo.is_complete = True
                db.session.commit()

        self._learning = False
        self._demo_recorder = None

        from backend.socketio_events import emit_learning_mode_stopped
        emit_learning_mode_stopped(demo_id, len(steps))

        logger.info(f"Learning mode stopped: demo_id={demo_id}, {len(steps)} steps recorded")
        return {"success": True, "demonstration_id": demo_id, "steps_recorded": len(steps)}

    def attempt_demonstration(self, demonstration_id):
        """Start an attempt to execute a saved demonstration."""
        if self._active or self._learning:
            return {"success": False, "error": "Agent is busy."}

        from backend.models import db, Demonstration
        from flask import current_app

        with current_app.app_context():
            demo = db.session.get(Demonstration, demonstration_id)
            if not demo:
                return {"success": False, "error": f"Demonstration {demonstration_id} not found."}
            steps = [s.to_dict() for s in demo.steps]
            level = demo.autonomy_level

        if not steps:
            return {"success": False, "error": "Demonstration has no steps."}

        # Capture app reference for use in background thread
        app = current_app._get_current_object()

        def _run():
            self._active = True
            try:
                from backend.services.local_screen_backend import LocalScreenBackend
                from backend.utils.vision_analyzer import VisionAnalyzer
                from backend.services.servo_controller import ServoController
                from backend.services.training_data_collector import TrainingDataCollector
                from backend.socketio_events import (
                    emit_learning_question, emit_step_preview,
                    emit_step_executed, emit_attempt_complete,
                )
                from backend.services.apprentice_engine import ApprenticeEngine

                screen = LocalScreenBackend()
                analyzer = VisionAnalyzer()
                collector = TrainingDataCollector()
                servo = ServoController(screen=screen, analyzer=analyzer, collector=collector)

                engine = ApprenticeEngine(
                    screen=screen,
                    analyzer=analyzer,
                    servo=servo,
                    collector=collector,
                )
                engine._step_confirm_event = self._step_confirm_event
                engine._answer_queue = self._learning_answer_queue

                result = engine.execute(
                    steps=steps,
                    autonomy_level=level,
                    demonstration_id=demonstration_id,
                    emit_fn={
                        "learning_question": emit_learning_question,
                        "step_preview": emit_step_preview,
                        "step_executed": emit_step_executed,
                    },
                )

                emit_attempt_complete(
                    demonstration_id=demonstration_id,
                    success=result.success,
                    steps_completed=result.steps_completed,
                    total_steps=result.total_steps,
                )

                # Update graduation
                with app.app_context():
                    demo = db.session.get(Demonstration, demonstration_id)
                    if demo:
                        demo.attempt_count += 1
                        if result.success:
                            demo.success_count += 1
                            if ApprenticeEngine._should_promote(demo.autonomy_level, demo.success_count):
                                demo.autonomy_level = ApprenticeEngine._promote(demo.autonomy_level)
                                demo.success_count = 0
                                logger.info(f"Demo {demonstration_id} promoted to {demo.autonomy_level}")
                        else:
                            demo.success_count = 0
                            old_level = demo.autonomy_level
                            demo.autonomy_level = ApprenticeEngine._demote(demo.autonomy_level)
                            if old_level != demo.autonomy_level:
                                logger.info(f"Demo {demonstration_id} demoted to {demo.autonomy_level}")
                        db.session.commit()

            except Exception as e:
                logger.error(f"Attempt failed: {e}", exc_info=True)
            finally:
                self._active = False

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return {"success": True, "message": f"Attempt started at level '{level}'", "autonomy_level": level}

    def _store_and_return(self, result: AgentResult) -> AgentResult:
        """Store result for status reporting and return it."""
        self._last_result = result
        return result

    def _is_black_frame(self, image: Image.Image) -> bool:
        """Check if frame is mostly black (display went dark)."""
        import numpy as np
        arr = np.array(image)
        return arr.mean() < 10  # Average pixel value below 10 = effectively black

    def _capture_with_retry(self, screen, max_retries: int = 3) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture screenshot with black frame detection and retry."""
        for attempt in range(max_retries):
            screenshot, cursor_pos = screen.capture()
            if not self._is_black_frame(screenshot):
                return screenshot, cursor_pos
            logger.warning(f"Black frame detected (attempt {attempt + 1}/{max_retries}), retrying...")
            time.sleep(1.5)
        # Still black after retries — return it anyway, let the vision model deal with it
        logger.error("Display appears black after retries — virtual screen may need restart")
        return screenshot, cursor_pos

    def _execute_action(self, action: AgentAction, screen) -> Dict[str, Any]:
        """Execute a single agent action via the screen interface."""
        try:
            if action.action_type == "click":
                if action.coordinates:
                    return screen.click(action.coordinates[0], action.coordinates[1])
                else:
                    return {"success": False, "error": "No coordinates for click"}

            elif action.action_type == "type":
                return screen.type_text(action.text)

            elif action.action_type == "hotkey":
                return screen.hotkey(*action.keys)

            elif action.action_type == "scroll":
                pos = action.coordinates or screen.cursor_position()
                return screen.scroll(pos[0], pos[1], amount=action.scroll_amount)

            elif action.action_type == "move":
                if action.coordinates:
                    return screen.move(action.coordinates[0], action.coordinates[1])
                return {"success": False, "error": "No coordinates for move"}

            else:
                return {"success": False, "error": f"Unknown action: {action.action_type}"}

        except Exception as e:
            logger.error(f"Action execution error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _assess_obstacles(self, scene_description: str, analyzer, screen, iteration: int) -> str:
        """
        ASSESS phase: detect and handle obstacles before the main THINK step.

        Returns:
            "clear"     — no obstacles, proceed with normal THINK
            "handled"   — obstacle dismissed by fast model, re-scan needed
            "escalated" — obstacle required thinking model, re-scan needed
        """
        import time as _time
        scene_lower = scene_description.lower()

        # Fast detection: known obstacle patterns (no LLM needed)
        obstacle_patterns = {
            "permission": ["allow microphone", "allow camera", "block this", "permission request",
                          "wants to use your microphone", "wants to use your camera",
                          "notification permission", "allow notifications"],
            "popup": ["popup is blocking", "popup appeared", "popup is visible",
                      "dialog is blocking", "dialog is covering", "modal is open",
                      "are you sure you want to"],
            "restore": ["restore session", "restore previous", "open previous tabs", "previous session"],
            "cookie": ["cookie", "accept cookies", "cookie consent", "gdpr"],
            "error": ["page not found", "404", "server error", "500", "connection refused"],
        }

        detected_type = None
        for obs_type, keywords in obstacle_patterns.items():
            if any(kw in scene_lower for kw in keywords):
                detected_type = obs_type
                break

        if not detected_type:
            return "clear"

        logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Obstacle detected: {detected_type}")

        # Stage 1: Fast model handles known obstacles with simple actions
        if detected_type == "permission":
            # Permission dialogs: try Escape, then look for Block/Deny button
            screen.hotkey("Escape")
            _time.sleep(0.5)
            logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Tried Escape for permission dialog")
            return "handled"

        elif detected_type == "restore":
            # Restore session bars: click the X dismiss button (far right)
            screen.hotkey("Escape")
            _time.sleep(0.3)
            # The X is typically at the far right of the notification bar
            screen.click(1290, 127)
            _time.sleep(0.5)
            logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Dismissed restore session bar")
            return "handled"

        elif detected_type == "cookie":
            screen.hotkey("Escape")
            _time.sleep(0.5)
            logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Tried Escape for cookie banner")
            return "handled"

        elif detected_type == "error":
            # 404 or connection error — this is informational, not blocking
            # Let the THINK step handle it (might need to navigate elsewhere)
            logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Error page detected, letting THINK handle")
            return "clear"

        # Stage 2: Unknown popup — escalate to thinking model
        logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS] Unknown obstacle, escalating to thinking model")

        thinking_model = self._get_thinking_model()
        if not thinking_model:
            # No thinking model available, try Escape as fallback
            screen.hotkey("Escape")
            _time.sleep(0.5)
            return "handled"

        # Ask the thinking model to reason about the obstacle
        escalation_prompt = (
            f"An unexpected popup or dialog appeared while performing a screen automation task.\n\n"
            f"Scene description: {scene_description}\n\n"
            f"What is this popup about? What is the safest action to dismiss it?\n"
            f"Respond with ONLY a JSON object:\n"
            f'{{"analysis": "what this popup is", "action": "click" | "hotkey", '
            f'"target_description": "what to click", "keys": ["Escape"], '
            f'"reasoning": "why this is safe"}}'
        )

        thinking_result = analyzer.text_query(escalation_prompt, model=thinking_model)
        if thinking_result.success:
            logger.warning(f"[AGENT][STEP {iteration+1}][ASSESS][THINKING] {thinking_result.description[:200]}")
            decision = self._parse_decision(thinking_result.description)
            if decision.action.action_type == "click" and decision.action.target_description:
                from backend.services.servo_controller import ServoController
                from backend.services.training_data_collector import TrainingDataCollector
                servo = ServoController(screen, analyzer, collector=TrainingDataCollector())
                servo.click_target(decision.action.target_description)
            elif decision.action.action_type == "hotkey" and decision.action.keys:
                screen.hotkey(*decision.action.keys)
            else:
                screen.hotkey("Escape")
            _time.sleep(0.5)
            return "escalated"

        # Thinking model also failed — last resort Escape
        screen.hotkey("Escape")
        _time.sleep(0.5)
        return "handled"

    @staticmethod
    def _build_unified_prompt(task: str, history) -> str:
        """Build a compact prompt for unified vision+decision models.

        Keep it SHORT — the model is also processing an image.
        """
        # Compact history: last 5 actions only
        done_lines = ""
        loop_warning = ""
        if history:
            recent = history[-5:]
            steps = []
            for h in recent:
                status = "FAIL" if h.failed else "OK"
                desc = h.action.text or h.action.target_description or str(h.action.keys or "")
                steps.append(f"  {h.action.action_type}: {desc} [{status}]")
            done_lines = "Last actions:\n" + "\n".join(steps) + "\n"

            # Detect loops — if last 3 actions are the same, warn strongly
            if len(history) >= 3:
                last3 = [(h.action.action_type, h.action.text) for h in history[-3:]]
                if len(set(last3)) == 1:
                    loop_warning = (
                        "\nSTOP REPEATING! You did the same action 3 times. "
                        "The screen has not changed. Do something DIFFERENT.\n"
                        "If you typed text, now press Return. If you clicked, try a different target.\n"
                    )

        return f"""Task: {task}

{done_lines}{loop_warning}Step {len(history) + 1}. Look at the screenshot. What is the ONE next action?

IMPORTANT RULES:
- After typing a URL, you MUST press Return (hotkey ["Return"]) to navigate
- Do NOT type the same text twice
- If the task is complete, use "done"

Reply with ONLY JSON:
{{"action": "click|type|hotkey|scroll|done", "target_description": "what to click", "text": "text to type", "keys": ["ctrl", "t"], "reasoning": "why"}}"""

    @staticmethod
    def _get_unified_model() -> str:
        """Find a vision model capable of both seeing and deciding (4b+ VLM)."""
        try:
            import requests as _requests
            response = _requests.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                # Prefer larger instruct VLMs that can reason + see
                for preferred in ["qwen3-vl:4b-instruct", "qwen3-vl:8b-instruct"]:
                    if preferred in models:
                        return preferred
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_thinking_model() -> str:
        """Find the best available thinking model for obstacle escalation."""
        try:
            import requests as _requests
            response = _requests.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                for preferred in ["lfm2.5-thinking:1.2b-bf16", "qwen3.5:9b", "deepseek-r1:8b"]:
                    if preferred in models:
                        return preferred
        except Exception:
            pass
        return ""

    _recipe_cache = None

    @classmethod
    def _load_recipes(cls):
        """Load recipe library from JSON file."""
        if cls._recipe_cache is not None:
            return cls._recipe_cache
        import os, json
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "recipes.json")
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Remove metadata key
            cls._recipe_cache = {k: v for k, v in data.items() if not k.startswith("_")}
            logger.info(f"Loaded {len(cls._recipe_cache)} recipes from {path}")
            return cls._recipe_cache
        except Exception as e:
            logger.warning(f"Failed to load recipes: {e}")
            cls._recipe_cache = {}
            return {}

    def _try_recipe(self, task: str, screen) -> 'AgentResult | None':
        """Match task against recipe library and execute deterministically."""
        import re
        task_lower = task.lower().strip()

        # Skip recipes for multi-step or compound tasks
        if re.search(r'step\s*\d|^\d+\.\s|\n\d+\.|then\s+(?:type|press|click|open|navigate)', task_lower):
            return None
        if re.search(r'(?:go to|navigate to)\s+\S+.*\b(?:and|then)\b.*\b(?:click|check|find|look|tell|scroll|type|search|read|describe|suggest)', task_lower):
            return None
        if len(task_lower) > 200:
            return None

        # Also handle "go to X page" → localhost:5175/X
        page_match = re.search(
            r'(?:go\s+to|open|navigate\s+to)\s+(?:the\s+)?(\w+)\s+page', task_lower
        )
        if page_match:
            page_routes = {
                'chat': '/chat', 'dashboard': '/', 'settings': '/settings',
                'images': '/images', 'media': '/images', 'video': '/video',
                'documents': '/documents', 'notes': '/notes', 'projects': '/projects',
                'clients': '/clients', 'rules': '/rules', 'agents': '/agents',
                'tools': '/tools', 'plugins': '/plugins', 'code': '/code-editor',
            }
            page = page_match.group(1)
            if page in page_routes:
                task_lower = f"navigate to localhost:5175{page_routes[page]}"

        recipes = self._load_recipes()
        for recipe_name, recipe in recipes.items():
            for pattern in recipe.get("triggers", []):
                match = re.search(pattern, task_lower, re.IGNORECASE)
                if match:
                    logger.warning(f"[AGENT][RECIPE] Matched '{recipe_name}': {recipe['description']}")
                    return self._execute_recipe(recipe_name, recipe, match, screen)

        return None

    def _execute_recipe(self, name: str, recipe: dict, match, screen) -> 'AgentResult':
        """Execute a matched recipe — deterministic sequence of actions."""
        import time as _time
        start = _time.time()
        action_steps = []
        step_num = 0

        for step in recipe.get("steps", []):
            action_type = step.get("action")

            if action_type == "wait":
                _time.sleep(step.get("seconds", 0.5))
                continue

            # Substitute capture groups: {1}, {2}, etc.
            def substitute(text):
                if not text:
                    return text
                for i in range(1, 10):
                    placeholder = f"{{{i}}}"
                    if placeholder in text:
                        try:
                            text = text.replace(placeholder, match.group(i) or "")
                        except IndexError:
                            pass
                return text

            if action_type == "hotkey":
                keys = [substitute(k) for k in step.get("keys", [])]
                result = screen.hotkey(*keys)
                action_steps.append(ActionStep(
                    iteration=step_num, scene_description=f"recipe:{name}",
                    action=AgentAction(action_type="hotkey", keys=keys),
                    result=result, failed=not result.get("success", False)
                ))
            elif action_type == "type":
                text = substitute(step.get("text", ""))
                result = screen.type_text(text)
                action_steps.append(ActionStep(
                    iteration=step_num, scene_description=f"recipe:{name}",
                    action=AgentAction(action_type="type", text=text),
                    result=result, failed=not result.get("success", False)
                ))
            elif action_type == "click":
                x, y = step.get("x", 0), step.get("y", 0)
                result = screen.click(x, y)
                action_steps.append(ActionStep(
                    iteration=step_num, scene_description=f"recipe:{name}",
                    action=AgentAction(action_type="click", coordinates=(x, y)),
                    result=result, failed=not result.get("success", False)
                ))
            step_num += 1

        elapsed = _time.time() - start
        logger.warning(f"[AGENT][RECIPE] {name} complete in {elapsed:.1f}s ({len(action_steps)} actions)")

        self._action_history = action_steps
        return AgentResult(
            success=True, reason=f"recipe:{name}",
            steps=action_steps, total_time_seconds=elapsed
        )

    @staticmethod
    def _load_self_knowledge() -> str:
        """Load the Guaardvark self-knowledge map for agent context."""
        import os
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "self_knowledge.md")
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip() + "\n\n"
        except Exception as e:
            logger.warning(f"Failed to load self-knowledge: {e}")
        return ""

    @staticmethod
    def _load_example_traces(task: str) -> str:
        """Load relevant example traces for the task to use as few-shot examples."""
        import os, json, re
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "example_traces.json")
        try:
            if not os.path.exists(path):
                return ""
            with open(path, "r") as f:
                traces = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load example traces: {e}")
            return ""

        task_lower = task.lower()
        matched = []

        # Match traces to task by keyword detection
        match_rules = {
            "navigate_to_page": ["navigate", "go to", "open", "localhost", "page"],
            "send_chat_message": ["chat", "type", "send", "message", "hello", "test"],
            "open_past_chats": ["past chat", "history", "previous chat", "old chat"],
            "check_narration": ["narrat", "voice", "speak", "audio", "tts"],
            "navigate_to_settings": ["setting"],
            "close_popup": ["popup", "modal", "close", "dismiss"],
            "youtube_search_and_watch": ["youtube", "video", "watch", "search youtube"],
            "youtube_add_comment": ["comment", "add a comment", "leave a comment", "post a comment"],
        }

        for trace_name, keywords in match_rules.items():
            if any(kw in task_lower for kw in keywords):
                trace = traces.get(trace_name)
                if trace:
                    matched.append((trace_name, trace))

        if not matched:
            return ""

        lines = ["EXAMPLE INTERACTIONS (follow these patterns):"]
        for name, trace in matched[:2]:  # Max 2 examples
            lines.append(f"\n--- {trace['description']} ---")
            if trace.get("prerequisite"):
                lines.append(f"Prerequisite: {trace['prerequisite']}")
            for i, step in enumerate(trace["steps"], 1):
                action = step["action"]
                detail = ""
                if step.get("target_description"):
                    detail = f' target="{step["target_description"]}"'
                if step.get("text"):
                    detail += f' text="{step["text"]}"'
                if step.get("keys"):
                    detail += f' keys={step["keys"]}'
                lines.append(f"  Step {i}: {action}{detail} — {step['reasoning']}")

        return "\n".join(lines) + "\n\n"

    def _build_vision_prompt(self, task: str, history: List[ActionStep]) -> str:
        """Build the prompt for scene analysis."""
        prompt = (
            f"Task: {task}\n\n"
            "Describe what is on screen right now. Be specific and concise:\n"
            "1. What website or application is showing? Read the URL if visible.\n"
            "2. How many browser tabs are open? What are their titles?\n"
            "3. What is the main content on the page? (text, images, forms, videos)\n"
            "4. What interactive elements are visible? (buttons, text fields, links, search boxes)\n"
            "5. Is anything blocking the main page content? (only mention if something IS blocking)\n"
            "6. Does the screen show that the current task step is complete?\n"
        )
        if history:
            last = history[-1]
            prompt += f"\nLast action: {last.action.action_type}"
            if last.action.target_description:
                prompt += f" on '{last.action.target_description}'"
            if last.action.text:
                prompt += f" text='{last.action.text}'"
            if last.failed:
                prompt += " (FAILED)"
        return prompt

    def _build_decision_prompt(self, task, scene, history):
        """Build the prompt for the LLM to decide the next action."""
        history_text = ""
        if history:
            lines = []
            # Show ALL completed actions as a numbered progress log
            for i, step in enumerate(history):
                status = "FAILED" if step.failed else "OK"
                desc = step.action.target_description or step.action.text or str(step.action.keys)
                lines.append(f"  {i+1}. {step.action.action_type}: {desc} [{status}]")
            # Show full history up to 10, then truncate older entries
            if len(lines) > 10:
                lines = lines[:3] + [f"  ... ({len(lines) - 6} more steps) ..."] + lines[-3:]
            history_text = f"Completed actions ({len(history)} total):\n" + "\n".join(lines)
            history_text += f"\n\nYou are now on step {len(history) + 1}. What is the NEXT action?"

        # Detect repeated actions
        loop_warning = ""
        if len(history) >= 2:
            last_actions = [(s.action.action_type, s.action.text, s.action.target_description) for s in history[-3:]]
            if len(set(last_actions)) == 1:
                loop_warning = "\nWARNING: You are repeating the same action. This is NOT working. Try a DIFFERENT approach.\n"

        mouse_only = getattr(self, '_mouse_only', False)

        if mouse_only:
            rules = """RULES (MOUSE ONLY MODE):
- You can ONLY use "click" and "done" actions. No keyboard, no typing, no hotkeys.
- Identify the target visually and click it directly with the mouse.
- Describe WHAT you want to click clearly (e.g., "the Save button", "the green circle with number 5").
- If the task is complete, use "done".
- Do NOT use keyboard shortcuts. Do NOT type text. MOUSE CLICKS ONLY.

Respond with ONLY a JSON object:
{{
    "action": "click" | "done",
    "target_description": "exactly what to click (servo will find it)",
    "reasoning": "why"
}}"""
        else:
            rules = """BROWSER SHORTCUTS (use hotkey action for these):
- Navigate to URL: hotkey ["ctrl", "l"] then type URL then hotkey ["Return"]
- New tab: hotkey ["ctrl", "t"]
- Close tab: hotkey ["ctrl", "w"]
- Back: hotkey ["alt", "Left"]
- Search on page: hotkey ["ctrl", "f"]
- Submit/confirm: hotkey ["Return"]
- Cancel/escape: hotkey ["Escape"]

RULES:
- To navigate to a website: FIRST use hotkey ctrl+l, THEN type the URL, THEN press Return
- To type text: FIRST click the text field, THEN use type action
- To search: click search box, type query, press Return
- Each action is ONE step. Do hotkey, type, and click as separate actions.
- If the screen shows the task is done (e.g., the target website loaded), use "done"

Respond with ONLY a JSON object:
{{
    "action": "click" | "type" | "hotkey" | "scroll" | "done",
    "target_description": "what you are clicking (servo will find it precisely)",
    "text": "text to type",
    "keys": ["ctrl", "l"],
    "scroll_amount": -3,
    "reasoning": "why"
}}"""

        # Inject self-knowledge and example traces when working on the Guaardvark UI
        self_knowledge = ""
        example_traces = ""
        task_lower = task.lower()
        if any(kw in task_lower for kw in ['guaardvark', 'localhost:5175', 'localhost:5002', 'our app', 'our ui', 'this app', 'self-test']):
            self_knowledge = self._load_self_knowledge()
        # Always load example traces — they help with any UI interaction task
        example_traces = self._load_example_traces(task)

        return f"""{self_knowledge}{example_traces}Task: {task}

Current screen:
{scene}

{history_text}
{loop_warning}
{rules}"""

    def _parse_decision(self, llm_output: str) -> AgentDecision:
        """Parse the LLM's JSON decision into an AgentDecision."""
        decision = AgentDecision(raw_output=llm_output)

        try:
            # Try to extract JSON from the output
            text = llm_output.strip()
            # Extract outermost JSON object (handles markdown fences, leading prose, etc.)
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

            data = json.loads(text)
            action_type = data.get("action", "").lower().strip()

            if action_type == "done":
                decision.task_complete = True
                decision.action.reasoning = data.get("reasoning", "")
                return decision

            action = AgentAction(
                action_type=action_type,
                target_cell=data.get("target_cell", ""),
                target_description=data.get("target_description", ""),
                text=data.get("text", ""),
                keys=data.get("keys", []),
                scroll_amount=data.get("scroll_amount", 0),
                reasoning=data.get("reasoning", ""),
            )
            decision.action = action

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse LLM decision: {e}")
            decision.stuck = True

        return decision


def get_agent_control_service() -> AgentControlService:
    """Get the singleton AgentControlService instance."""
    global _service_instance
    with _service_lock:
        if _service_instance is None:
            _service_instance = AgentControlService()
    return _service_instance
