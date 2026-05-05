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
import os
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
    max_iterations: int = 15
    # Bumped from 3 → 5 so transient screen states (mid-load page, brief
    # black between window switches, vision model spitting bad JSON once)
    # don't kill the loop. The failure counter still resets on every successful
    # action, so this only matters for genuinely-stuck sequences.
    max_consecutive_failures: int = 5
    task_timeout_seconds: int = 60  # 1 minute — good tasks finish in <10s
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
    action_type: str = ""  # click, right_click, type, hotkey, scroll, move, done
    target_cell: str = ""  # Grid cell (e.g., "D4") — for clicks
    target_description: str = ""  # What the agent thinks it's clicking
    coordinates: Optional[Tuple[int, int]] = None  # Refined pixel coords
    text: str = ""  # For type actions
    keys: List[str] = field(default_factory=list)  # For hotkey actions
    scroll_amount: int = 0  # For scroll actions
    reasoning: str = ""  # Why the agent chose this action
    confidence: float = 1.0  # Confidence score (0.0 to 1.0)


@dataclass
class AgentDecision:
    """The LLM's decision for the current iteration."""
    action: AgentAction = field(default_factory=AgentAction)
    task_complete: bool = False
    stuck: bool = False
    confidence: float = 1.0  # Overall decision confidence
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

    def execute_task(self, task: str, screen, mouse_only: bool = False, training_mode: bool = False) -> AgentResult:
        """
        Execute a task using the see-think-act loop.

        Args:
            task: Natural language description of the task
            screen: ScreenInterface implementation
            mouse_only: If True, disable keyboard shortcuts — pure mouse clicks only
            training_mode: If True, keep clicking forever — no early done, no loop breaker,
                          extended iterations and timeout. For vision trainer practice.

        Returns:
            AgentResult with success status and action history
        """
        self._mouse_only = mouse_only
        self._training_mode = training_mode
        from backend.services.servo_controller import ServoController
        from backend.services.training_data_collector import TrainingDataCollector
        from backend.utils.vision_analyzer import VisionAnalyzer

        with self._lock:
            if self._active:
                # New task supersedes old one — kill the stale task so its
                # see-think-act loop exits on the next _killed check.
                old_task = self._current_task
                logger.warning(f"[AGENT] Killing stale task \"{old_task}\" to start new task \"{task}\"")
                self._killed = True

        # Give the old task's loop time to see the kill flag and exit.
        # Each iteration checks _killed before doing work, so one iteration
        # cycle (~2-3s for vision call) is the worst case.
        if self._killed:
            for _ in range(10):
                time.sleep(0.3)
                if not self._active:
                    break
            # If it's STILL active after 3s, force-clear — the old thread's
            # finally block will harmlessly set _active=False again later.
            if self._active:
                logger.warning("[AGENT] Force-clearing stale active flag after kill timeout")

        with self._lock:
            self._active = True
            self._killed = False
            self._current_task = task
            self._current_iteration = 0
            self._action_history = []

        # Pick the vision model for servo coordinate estimation.
        # If vision_model is None, the model does its own coords — no middleman.
        from backend.services.servo_knowledge_store import get_vision_config
        vision_config = get_vision_config()
        servo_vision_model = vision_config.get("vision_model")  # None = model does its own coords

        if servo_vision_model:
            logger.info(f"[AGENT] Servo eyes: {servo_vision_model} (external)")
        else:
            # Auto-detect — use the same model that's doing the unified see+decide
            servo_vision_model = self._get_unified_model() or self.config.vision_model
            logger.info(f"[AGENT] Servo eyes: {servo_vision_model} (same model sees, decides, AND clicks)")

        logger.info(f"[AGENT] Vision config: servo_eyes={servo_vision_model} scale=({vision_config['scale_x']}, {vision_config['scale_y']})")
        analyzer = VisionAnalyzer(default_model=servo_vision_model)
        collector = TrainingDataCollector()
        servo = ServoController(screen, analyzer, collector=collector, vision_config=vision_config)
        consecutive_failures = 0
        start_time = time.time()

        # Check for recipe match — skip see-think-act loop for known patterns
        recipe_result = self._try_recipe(task, screen)
        if recipe_result is not None:
            self._active = False
            return self._store_and_return(recipe_result)

        # Training mode: crank up limits so the agent keeps practicing
        max_iters = 1000 if training_mode else self.config.max_iterations
        task_timeout = 3600 if training_mode else self.config.task_timeout_seconds  # 1 hour for training

        try:
            for iteration in range(max_iters):
                if self._killed:
                    return self._store_and_return(AgentResult(
                        success=False, reason="killed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if time.time() - start_time > task_timeout:
                    return self._store_and_return(AgentResult(
                        success=False, reason="timeout",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                self._current_iteration = iteration

                # Training mode: 5s pause between iterations so the agent doesn't rush
                if training_mode and iteration > 0:
                    time.sleep(5.0)

                # 1. SEE — Capture screenshot
                screenshot, cursor_pos = self._capture_with_retry(screen)
                logger.warning(f"[AGENT][STEP {iteration+1}][SEE] Capturing screen, cursor at {cursor_pos}")

                scene_desc = ""  # Will be populated by either unified or split path

                # Check for unified vision+decision model (qwen3-vl:4b+)
                unified_model = self._get_unified_model()

                if unified_model:
                    # UNIFIED MODE: Compact prompt — vision model sees screenshot + short context
                    unified_prompt = self._build_unified_prompt(task, self._action_history, training_mode=training_mode)
                    # Persistent cross-session knowledge rides the system slot,
                    # not the user prompt — keeps the per-step prompt small
                    # enough to hold the model in instructed mode while still
                    # carrying URL routes, Firefox button location, recipe
                    # index, etc. into every decision.
                    persistent_system = self._build_persistent_knowledge_system()
                    if persistent_system:
                        logger.warning(
                            f"[AGENT][PROMPT-UNIFIED] system_msg={len(persistent_system)}ch "
                            f"user_prompt={len(unified_prompt)}ch"
                        )
                    result = analyzer.analyze(
                        screenshot, prompt=unified_prompt,
                        model=unified_model, num_predict=256, temperature=0.1,
                        system=persistent_system or None,
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

                if decision.task_complete and training_mode:
                    # Training mode: ignore "done" — force the model to keep clicking
                    logger.info(f"[AGENT][STEP {iteration+1}][TRAINING] Ignoring 'done' — keep practicing")
                    decision.task_complete = False
                    decision.action.action_type = "click"
                    decision.action.target_description = "colored circle"

                if decision.task_complete:
                    # Guard: "done" on step 1 with no actions taken is suspicious.
                    # If the screen is black or no actions were executed, this is
                    # likely the model giving up, not genuine completion.
                    if iteration == 0 and not self._action_history:
                        # Check if screen is actually showing something
                        check_shot, _ = screen.capture()
                        if self._is_black_frame(check_shot):
                            logger.warning(f"[AGENT][DONE] Rejected — model said 'done' on black screen with 0 actions")
                            return self._store_and_return(AgentResult(
                                success=False,
                                reason="Screen is black — virtual display may need restart. No actions were taken.",
                                steps=self._action_history,
                                total_time_seconds=time.time() - start_time
                            ))

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
                if getattr(self, '_mouse_only', False) and decision.action.action_type not in ("click", "right_click", "done"):
                    logger.info(f"[AGENT][STEP {iteration+1}][ACT] Mouse-only: rejecting {decision.action.action_type}")
                    decision.action.action_type = "click"
                    if not decision.action.target_description:
                        consecutive_failures += 1
                        continue

                if decision.action.action_type in ("click", "right_click"):
                    button = "right" if decision.action.action_type == "right_click" else "left"
                    target = decision.action.target_description

                    # For generic area targets (desktop, empty space), click center-screen
                    # instead of asking the vision model to locate "the desktop"
                    import re as _re
                    if _re.search(r'(?:desktop|empty|blank|background|open area|center|middle)\s*(?:area|space|screen)?', target, _re.IGNORECASE):
                        sw, sh = screen.screen_size()
                        cx, cy = sw // 2, sh // 2
                        screen.click(cx, cy, button=button)
                        decision.action.coordinates = (cx, cy)
                        result = {"success": True}
                        failed = False
                    else:
                        servo_result = servo.click_target(target, button=button, single_attempt=training_mode)
                        decision.action.coordinates = (servo_result.get("x", 0), servo_result.get("y", 0))
                        result = {"success": servo_result.get("success", False)}
                        failed = not servo_result.get("success", False)

                    status_icon = "OK" if not failed else "FAIL"
                    logger.warning(f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} \"{target}\" "
                                  f"at ({decision.action.coordinates[0]},{decision.action.coordinates[1]}) [{status_icon}]")
                else:
                    result = self._execute_action(decision.action, screen)
                    failed = not result.get("success", False)

                    # Post-action observation: wait for the UI to update, then
                    # take a verification screenshot so the NEXT iteration's
                    # SEE step reflects the actual outcome of this action.
                    if not failed and decision.action.action_type in ("type", "hotkey", "scroll"):
                        time.sleep(0.5)
                        post_shot, _ = self._capture_with_retry(screen)
                        # Compare before/after to detect if the action had any visible effect
                        if screenshot is not None and post_shot is not None:
                            import numpy as np
                            before_mean = np.array(screenshot).mean()
                            after_mean = np.array(post_shot).mean()
                            pixel_diff = abs(after_mean - before_mean)
                            if pixel_diff < 0.05 and decision.action.action_type == "type":
                                # Screen didn't change at all after typing — likely typed into nothing
                                logger.warning(f"[AGENT][STEP {iteration+1}][VERIFY] Screen unchanged after type — "
                                              f"text may not have landed in a field")
                                result["verified"] = False
                            else:
                                result["verified"] = True
                                logger.warning(f"[AGENT][STEP {iteration+1}][VERIFY] Screen changed after "
                                              f"{decision.action.action_type} (delta={pixel_diff:.2f})")

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

                # 5b. EARLY DONE — after a successful action, check if the
                # task goal is obviously met based on desktop state. Saves
                # an entire LLM round-trip (~3-5s) when the answer is clear.
                # Disabled in training mode — keep clicking targets forever.
                if not failed and len(self._action_history) >= 1 and not getattr(self, '_training_mode', False):
                    early_done = self._check_early_done(task)
                    if early_done:
                        logger.warning(
                            f"[AGENT][EARLY_DONE] Task goal met after step {iteration+1}: {early_done}"
                        )
                        return self._store_and_return(AgentResult(
                            success=True, reason=f"completed ({early_done})",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time
                        ))

                # 5c. LOOP BREAKER — if last 3 actions are identical, the LLM
                # is stuck in a loop. Force-complete instead of burning iterations.
                # Disabled in training mode — repeating clicks is the whole point.
                # Also disabled for `wait` — patiently waiting through a slow load
                # is exactly what we WANT, not a loop to break out of.
                if len(self._action_history) >= 3 and not getattr(self, '_training_mode', False):
                    last3 = [
                        (h.action.action_type, h.action.target_description, h.action.text)
                        for h in self._action_history[-3:]
                    ]
                    if len(set(last3)) == 1 and last3[0][0] != "wait":
                        logger.warning(
                            f"[AGENT][LOOP] Same action repeated 3x: "
                            f"{last3[0][0]} \"{last3[0][1] or last3[0][2]}\". "
                            f"Forcing task complete."
                        )
                        return self._store_and_return(AgentResult(
                            success=True,
                            reason="completed (loop detected — action repeated 3 times)",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time
                        ))

                if failed:
                    consecutive_failures += 1
                    # Training mode: never kill on consecutive failures — missing targets is learning
                    max_failures = 999 if training_mode else self.config.max_consecutive_failures
                    if consecutive_failures >= max_failures:
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
                from backend.services.servo_knowledge_store import get_vision_config as _gvc
                servo = ServoController(screen=screen, analyzer=analyzer, collector=collector, vision_config=_gvc())

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

    @staticmethod
    def _get_desktop_state() -> str:
        """Query the actual state of the virtual desktop (DISPLAY=:99).

        Returns a compact text summary of what's running — ground truth
        from the window manager, not a vision model guess.  Takes <10ms.
        """
        import subprocess
        # Always query the agent's virtual display, not the user's desktop
        display = ":99"
        env = {**os.environ, "DISPLAY": display}

        try:
            import re as _re

            # Search for known application windows by name
            app_searches = ["Firefox", "Chromium", "Chrome", "Terminal",
                            "Files", "Text Editor", "LibreOffice"]
            all_wids = set()
            for app in app_searches:
                result = subprocess.run(
                    ["xdotool", "search", "--name", app],
                    capture_output=True, text=True, timeout=2, env=env,
                )
                for wid in result.stdout.strip().split("\n"):
                    if wid.strip():
                        all_wids.add(wid.strip())

            # Also get the active window
            active = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2, env=env,
            )
            if active.stdout.strip():
                all_wids.add(active.stdout.strip())

            windows = []
            seen_names = set()
            for wid in all_wids:
                try:
                    name_result = subprocess.run(
                        ["xdotool", "getwindowname", wid],
                        capture_output=True, text=True, timeout=2, env=env,
                    )
                    name = name_result.stdout.strip()
                    if not name or name in ("Desktop", "xfdesktop-desktop",
                                            "Panel", "xfce4-panel"):
                        continue

                    geo_result = subprocess.run(
                        ["xdotool", "getwindowgeometry", wid],
                        capture_output=True, text=True, timeout=2, env=env,
                    )
                    geo_text = geo_result.stdout.strip()
                    geo_match = _re.search(r"Geometry:\s*(\d+)x(\d+)", geo_text)
                    if not geo_match:
                        continue
                    w, h = int(geo_match.group(1)), int(geo_match.group(2))
                    if w < 50 or h < 50:
                        continue

                    # Deduplicate by name (Firefox has multiple internal windows)
                    short_name = name.split(" — ")[-1] if " — " in name else name
                    short_name = name.split(" - ")[-1] if " - " in name else short_name
                    if short_name in seen_names:
                        continue
                    seen_names.add(short_name)

                    pos_match = _re.search(r"Position:\s*(-?\d+),(-?\d+)", geo_text)
                    pos = f" at ({pos_match.group(1)},{pos_match.group(2)})" if pos_match else ""
                    windows.append(f"  - {name} ({w}x{h}{pos})")
                except Exception:
                    continue

            if not windows:
                return "Desktop state: No application windows open. Desktop is empty."

            return "Desktop state — currently open:\n" + "\n".join(windows)

        except Exception as e:
            logger.debug(f"Desktop state query failed: {e}")
            return "Desktop state: unknown (query failed)"

    @staticmethod
    def _check_early_done(task: str) -> str:
        """Check if the task goal is obviously met based on desktop state.

        Returns a reason string if done, empty string if not.
        Fast check (<20ms) — no vision model, just xdotool queries.
        """
        import re as _re
        task_lower = task.lower()
        desktop = AgentControlService._get_desktop_state()

        # "Close X" tasks: if no windows are open, we're done
        if _re.search(r'\b(?:close|quit|exit|kill|shut\s*down|stop)\b', task_lower):
            if "No application windows open" in desktop:
                return "no windows open — target closed"

            # If closing a specific app, check if that app is gone
            for app in ("firefox", "chrome", "chromium", "browser", "terminal"):
                if app in task_lower and app.capitalize() not in desktop.lower():
                    return f"{app} no longer visible"

        # "Open X" tasks: if the target app is now visible
        if _re.search(r'\b(?:open|start|launch)\b', task_lower):
            for app in ("firefox", "chrome", "chromium", "terminal"):
                if app in task_lower and app.lower() in desktop.lower():
                    return f"{app} is now open"

        return ""

    def _store_and_return(self, result: AgentResult) -> AgentResult:
        """Store result for status reporting and return it."""
        self._last_result = result
        # Enforce window boundaries so windows don't escape the virtual display
        self._enforce_window_boundaries()

        # Distill learning from tasks that succeeded after retries —
        # turns one-session learning into persistent memory
        if (result.success
                and len(result.steps) > 1
                and "recipe:" not in result.reason):
            any_failures = any(s.failed for s in result.steps)
            if any_failures:
                try:
                    from backend.celery_app import celery_app
                    step_dicts = [
                        {
                            "iteration": s.iteration,
                            "action_type": s.action.action_type,
                            "target": s.action.target_description,
                            "text": s.action.text,
                            "keys": s.action.keys,
                            "failed": s.failed,
                            "result_success": s.result.get("success", False),
                        }
                        for s in result.steps
                    ]
                    celery_app.send_task(
                        "self_improvement.distill_task_learning",
                        kwargs={
                            "task": getattr(self, '_current_task', '') or '',
                            "steps": step_dicts,
                            "model_name": getattr(self, '_model_name', ''),
                        },
                    )
                    logger.info("[DISTILL] Dispatched learning distillation for successful retry task")
                except Exception as e:
                    logger.debug(f"Distillation dispatch skipped: {e}")

        return result

    def _enforce_window_boundaries(self, screen=None):
        """Clamp all windows to fit within the virtual display.

        Runs after every task to prevent windows from drifting off-screen
        where the agent can't see or interact with them.
        """
        import subprocess
        display = os.environ.get("DISPLAY", ":99")

        # Get actual screen size from the backend or direct xdotool call
        if screen:
            screen_w, screen_h = screen.screen_size()
        else:
            try:
                r = subprocess.run(
                    ["xdotool", "getdisplaygeometry"],
                    capture_output=True, text=True, timeout=2,
                    env={**os.environ, "DISPLAY": display},
                )
                if r.returncode == 0:
                    parts = r.stdout.strip().split()
                    screen_w, screen_h = int(parts[0]), int(parts[1])
                else:
                    screen_w, screen_h = 1024, 1024
            except Exception:
                screen_w, screen_h = 1024, 1024
        try:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", ""],
                capture_output=True, text=True, timeout=3,
                env={**os.environ, "DISPLAY": display},
            )
            for wid in result.stdout.strip().split("\n"):
                wid = wid.strip()
                if not wid:
                    continue
                try:
                    geo = subprocess.run(
                        ["xdotool", "getwindowgeometry", wid],
                        capture_output=True, text=True, timeout=2,
                        env={**os.environ, "DISPLAY": display},
                    )
                    # Parse "Position: X,Y" and "Geometry: WxH"
                    lines = geo.stdout.strip().split("\n")
                    pos_line = [l for l in lines if "Position:" in l]
                    geo_line = [l for l in lines if "Geometry:" in l]
                    if not pos_line or not geo_line:
                        continue
                    import re
                    pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", pos_line[0])
                    geo_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geo_line[0])
                    if not pos_match or not geo_match:
                        continue
                    x, y = int(pos_match.group(1)), int(pos_match.group(2))
                    w, h = int(geo_match.group(1)), int(geo_match.group(2))
                    # Skip tiny windows (tooltips, hidden frames)
                    if w < 50 or h < 50:
                        continue
                    # Check if out of bounds
                    needs_fix = False
                    new_x, new_y = x, y
                    if x < 0:
                        new_x = 0
                        needs_fix = True
                    if y < 0:
                        new_y = 0
                        needs_fix = True
                    if x + w > screen_w:
                        new_x = max(0, screen_w - w)
                        needs_fix = True
                    if y + h > screen_h:
                        new_y = max(0, screen_h - h)
                        needs_fix = True
                    if needs_fix:
                        subprocess.run(
                            ["xdotool", "windowmove", wid, str(new_x), str(new_y)],
                            capture_output=True, timeout=2,
                            env={**os.environ, "DISPLAY": display},
                        )
                        logger.info(f"Clamped window {wid} from ({x},{y}) to ({new_x},{new_y})")
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Window boundary enforcement skipped: {e}")

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
            if action.action_type in ("click", "right_click"):
                if action.coordinates:
                    button = "right" if action.action_type == "right_click" else "left"
                    return screen.click(action.coordinates[0], action.coordinates[1], button=button)
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

            elif action.action_type == "wait":
                # Patience action — for transient screens (mid-load, focus loss,
                # animation in flight). Defaults to 1.5s; the model can ask
                # for longer by stuffing seconds into scroll_amount.
                seconds = max(0.3, min(float(action.scroll_amount or 1.5), 8.0))
                time.sleep(seconds)
                return {"success": True, "waited": seconds}

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
            # (1000, 100) is a safe bet for a 1024px screen
            screen.click(1000, 100)
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
                from backend.services.servo_knowledge_store import get_vision_config as _gvc2
                servo = ServoController(screen, analyzer, collector=TrainingDataCollector(), vision_config=_gvc2())
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
    def _build_unified_prompt(task: str, history, training_mode: bool = False) -> str:
        """Build a compact prompt for unified vision+decision models.

        Shorter prompts = better detection accuracy. Only include what the model
        needs to pick the next action.
        """
        # Last 3 actions only — enough for context, not enough to overwhelm
        done_lines = ""
        loop_warning = ""
        if history:
            recent = history[-3:]
            steps = []
            for h in recent:
                status = "FAIL" if h.failed else "OK"
                desc = h.action.text or h.action.target_description or str(h.action.keys or "")
                steps.append(f"  {h.action.action_type}: {desc} [{status}]")
            done_lines = "Done:\n" + "\n".join(steps) + "\n"

            if len(history) >= 3:
                last3 = [(h.action.action_type, h.action.text) for h in history[-3:]]
                if len(set(last3)) == 1:
                    loop_warning = "\nYou repeated the same action 3 times. Do something DIFFERENT.\n"

        desktop_state = AgentControlService._get_desktop_state()

        # DOM metadata — interactive elements with screen coordinates
        dom_block = ""
        try:
            unified_model = AgentControlService._get_unified_model()
            if unified_model and "gemma4" in unified_model.lower():
                from backend.services.dom_metadata_extractor import DOMMetadataExtractor
                snapshot = DOMMetadataExtractor.get_instance().extract()
                if snapshot.success and snapshot.elements:
                    dom_block = DOMMetadataExtractor.format_for_prompt(snapshot) + "\n\n"
        except Exception:
            pass

        training_override = ""
        if training_mode:
            training_override = "\nTRAINING MODE: NEVER say done. Click the next target.\n"

        # One-line confidence rules. Kept terse on purpose — verbose
        # prompt text leaks into typed actions when the model parrots context.
        confidence = (
            "Web task + no browser visible: open Firefox first. "
            "Screen mid-load or transient: wait, do not quit. "
            "Step 1 done is forbidden."
        )

        return f"""Task: {task}

{desktop_state}
{dom_block}{done_lines}{loop_warning}{training_override}Step {len(history) + 1}. ONE next action. {confidence}

Reply ONLY with JSON:
{{"action": "click|right_click|type|hotkey|scroll|wait|done", "target_description": "...", "text": "literal value only", "keys": ["ctrl","t"], "reasoning": "why"}}"""

    @staticmethod
    def _get_unified_model() -> str:
        """Find a vision model capable of both seeing and deciding (4b+ VLM)."""
        try:
            import requests as _requests
            response = _requests.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                # Prefer larger VLMs that can reason + see
                for preferred in ["gemma4:e4b", "qwen3-vl:8b-instruct", "qwen3-vl:4b-instruct"]:
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
    _recipe_mtime = 0.0

    @classmethod
    def _load_recipes(cls):
        """Load recipe library from JSON file, auto-reloading on file change."""
        import os, json
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "recipes.json")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        if cls._recipe_cache is not None and mtime <= cls._recipe_mtime:
            return cls._recipe_cache
        try:
            with open(path, "r") as f:
                data = json.load(f)
            cls._recipe_cache = {k: v for k, v in data.items() if not k.startswith("_")}
            cls._recipe_mtime = mtime
            logger.info(f"Loaded {len(cls._recipe_cache)} recipes from {path}")
            return cls._recipe_cache
        except Exception as e:
            logger.warning(f"Failed to load recipes: {e}")
            cls._recipe_cache = {}
            return {}

    @classmethod
    def _load_recipe_index(cls) -> str:
        """Render the recipe library as a one-line-per-recipe index for the
        agent's decision prompt. Recipes that match a task are auto-executed
        before the LLM ever sees the prompt — but listing them by description
        tells the LLM what shortcuts exist, so it can phrase its own steps the
        same way (e.g. 'click the orange Firefox button' instead of pixel
        hunting).
        """
        recipes = cls._load_recipes()
        if not recipes:
            return ""
        lines = []
        for name, recipe in recipes.items():
            desc = (recipe.get("description") or "").strip()
            if desc:
                lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

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
                    # If the recipe wants to LAUNCH Firefox but it's already running,
                    # just focus the existing window instead of the desktop-menu dance.
                    if recipe_name in ("open_firefox",) and self._is_firefox_running(screen):
                        logger.warning(f"[AGENT][RECIPE] Skipping '{recipe_name}' — Firefox already running, focusing it")
                        return self._focus_firefox(screen)
                    logger.warning(f"[AGENT][RECIPE] Matched '{recipe_name}': {recipe['description']}")
                    return self._execute_recipe(recipe_name, recipe, match, screen)

        return None

    def _is_firefox_running(self, screen) -> bool:
        """Check if Firefox has a window on the virtual display."""
        import subprocess
        display = getattr(screen, 'display', os.environ.get('DISPLAY', ':99'))
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", "Mozilla Firefox"],
                capture_output=True, text=True, timeout=3,
                env={**os.environ, "DISPLAY": display},
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _focus_firefox(self, screen) -> 'AgentResult':
        """Focus the existing Firefox window instead of launching a new one."""
        import subprocess, time as _time
        display = getattr(screen, 'display', os.environ.get('DISPLAY', ':99'))
        env = {**os.environ, "DISPLAY": display}
        start = _time.time()
        try:
            # Get Firefox window ID and activate it
            result = subprocess.run(
                ["xdotool", "search", "--name", "Mozilla Firefox"],
                capture_output=True, text=True, timeout=3, env=env,
            )
            wids = result.stdout.strip().split()
            if wids:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wids[0]],
                    capture_output=True, timeout=3, env=env,
                )
                _time.sleep(0.5)
                logger.warning("[AGENT][RECIPE] Focused existing Firefox window")
        except Exception as e:
            logger.warning(f"[AGENT][RECIPE] Firefox focus failed: {e}")

        elapsed = _time.time() - start
        return AgentResult(
            success=True, reason="recipe:focus_firefox",
            steps=[], total_time_seconds=elapsed,
        )

    def _execute_recipe(self, name: str, recipe: dict, match, screen) -> 'AgentResult':
        """Execute a matched recipe — deterministic sequence of actions.

        Click steps may specify either a `target_description` (vision-driven —
        the servo finds the target on the current frame, surviving layout
        shifts) or explicit `x`/`y` coordinates (legacy, brittle when the
        environment changes). Recipes should prefer target_description; the
        coordinate path stays for back-compat but is on the way out.
        """
        import time as _time
        start = _time.time()
        action_steps = []
        step_num = 0

        # Lazy-build the servo only if a step actually needs vision targeting.
        # Keyboard-only recipes (~80% of the library) pay zero vision cost.
        servo_box = {"servo": None}

        def get_servo():
            if servo_box["servo"] is None:
                from backend.services.servo_controller import ServoController
                from backend.services.training_data_collector import TrainingDataCollector
                from backend.services.servo_knowledge_store import get_vision_config
                from backend.utils.vision_analyzer import VisionAnalyzer
                servo_box["servo"] = ServoController(
                    screen, VisionAnalyzer(),
                    collector=TrainingDataCollector(),
                    vision_config=get_vision_config(),
                )
            return servo_box["servo"]

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
                target = substitute(step.get("target_description", ""))
                x = step.get("x")
                y = step.get("y")
                button = step.get("button", "left")
                if target:
                    # Vision-driven: the recipe says WHAT to click, the servo
                    # finds WHERE on this frame. Resilient to layout changes,
                    # at the cost of one vision call per click.
                    result = get_servo().click_target(target, button=button)
                elif isinstance(x, int) and isinstance(y, int):
                    # Legacy coordinate path. Brittle — retained only so older
                    # recipes don't break before they've been migrated.
                    result = screen.click(x, y, button=button)
                else:
                    result = {"success": False, "error": "click step needs target_description or x/y"}
                action_steps.append(ActionStep(
                    iteration=step_num, scene_description=f"recipe:{name}",
                    action=AgentAction(
                        action_type="click" if button == "left" else "right_click",
                        target_description=target or "",
                        coordinates=(x or 0, y or 0),
                    ),
                    result=result, failed=not result.get("success", False)
                ))
            step_num += 1

        elapsed = _time.time() - start
        failed_steps = [s for s in action_steps if s.failed]
        all_succeeded = len(failed_steps) == 0

        if failed_steps:
            logger.warning(f"[AGENT][RECIPE] {name} had {len(failed_steps)} failed step(s)")
        logger.info(f"[AGENT][RECIPE] {name} complete in {elapsed:.1f}s ({len(action_steps)} actions, success={all_succeeded})")

        self._action_history = action_steps
        return AgentResult(
            success=all_succeeded,
            reason=f"recipe:{name}" if all_succeeded else f"recipe:{name} — {len(failed_steps)} step(s) failed",
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
    def _load_self_knowledge_compact() -> str:
        """Load the compact self-knowledge file for unified VLM prompts.
        Smaller and prose-only — sized to ride below the threshold that
        flips a vision-LLM into its CSS-selector training prior. Used in
        the system-message slot, paired with the recipe index.
        """
        import os
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "self_knowledge_compact.md")
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to load compact self-knowledge: {e}")
        return ""

    @classmethod
    def _build_persistent_knowledge_system(cls) -> str:
        """Build the system-message content carrying the agent's persistent
        knowledge — compact facts plus recipe index. Routed via Ollama's
        system role so it doesn't compete with the per-step user prompt
        for action-format conditioning. This is the cross-session memory
        slot: anything in here survives reboots and primes every decision.
        """
        parts = []
        sk = cls._load_self_knowledge_compact()
        if sk:
            parts.append(sk)
        recipes = cls._load_recipe_index()
        if recipes:
            parts.append(
                "## Available Recipes (the system auto-executes these on matching task strings; "
                "knowing they exist tells you what shortcuts the environment offers)\n"
                + recipes
            )
        return "\n\n".join(parts)

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
            "open_firefox_from_desktop": ["firefox", "browser", "launch browser", "start browser"],
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
        desktop_state = self._get_desktop_state()
        prompt = (
            f"Task: {task}\n\n"
            f"{desktop_state}\n\n"
            "Describe the screen: what app/URL is showing, what interactive elements are visible, "
            "and whether the task looks complete."
        )
        if history:
            last = history[-1]
            status = "FAIL" if last.failed else "OK"
            desc = last.action.target_description or last.action.text or ""
            prompt += f"\nLast: {last.action.action_type} {desc} [{status}]"
        return prompt

    def _build_decision_prompt(self, task, scene, history):
        """Build the prompt for the LLM to decide the next action."""
        history_text = ""
        if history:
            lines = []
            recent = history[-5:]
            for i, step in enumerate(recent):
                status = "FAIL" if step.failed else "OK"
                desc = step.action.target_description or step.action.text or str(step.action.keys)
                lines.append(f"  {step.action.action_type}: {desc} [{status}]")
            history_text = "Done:\n" + "\n".join(lines)
            history_text += f"\n\nStep {len(history) + 1}."

        loop_warning = ""
        if len(history) >= 3:
            last_actions = [(s.action.action_type, s.action.text, s.action.target_description) for s in history[-3:]]
            if len(set(last_actions)) == 1:
                loop_warning = "\nYou repeated the same action 3 times. Do something DIFFERENT.\n"

        mouse_only = getattr(self, '_mouse_only', False)

        if mouse_only:
            rules = """MOUSE ONLY. Actions: click, right_click, done.

Reply ONLY with JSON:
{{"action": "click|right_click|done", "target_description": "...", "reasoning": "why"}}"""
        else:
            rules = """One action per step. After typing a URL, press Return.

Reply ONLY with JSON:
{{"action": "click|right_click|type|hotkey|scroll|done", "target_description": "...", "text": "literal value only", "keys": ["ctrl","l"], "reasoning": "why"}}"""

        desktop_state = self._get_desktop_state()

        # Persistent knowledge — loaded once per call, stable across sessions.
        # This is the cross-session memory: what the agent has learned about
        # its own environment, the shortcuts it can rely on, and patterns
        # that have worked before. Without these the LLM rediscovers the
        # screen layout every step.
        self_knowledge = self._load_self_knowledge()
        recipe_index = self._load_recipe_index()
        example_traces = self._load_example_traces(task)

        knowledge_block = ""
        if self_knowledge:
            knowledge_block += f"## Known Facts (always true)\n{self_knowledge.strip()}\n\n"
        if recipe_index:
            knowledge_block += (
                "## Available Recipes (the system auto-executes these on matching task strings; "
                "knowing they exist tells you what shortcuts the environment offers)\n"
                f"{recipe_index}\n\n"
            )
        if example_traces:
            knowledge_block += f"{example_traces.strip()}\n\n"

        # Phase-1 verification log: confirm the loaders fire and how much
        # knowledge gets injected. Remove once we're sure it's wired right.
        logger.warning(
            f"[AGENT][PROMPT] knowledge_block={len(knowledge_block)}ch "
            f"self_knowledge={len(self_knowledge)}ch "
            f"recipe_index={len(recipe_index)}ch "
            f"example_traces={len(example_traces)}ch"
        )

        return f"""{knowledge_block}---

Task: {task}

{desktop_state}

Screen: {scene}

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

            # Sanitize text field — models sometimes parrot instruction text
            # instead of just the value. Strip common instruction prefixes.
            raw_text = data.get("text", "") or ""
            if action_type == "type" and raw_text:
                import re as _re
                # If text contains quoted content, extract just the quoted part
                # e.g. "type 'guaardvark' in search" → "guaardvark"
                quoted = _re.search(r"['\"]([^'\"]+)['\"]", raw_text)
                if quoted and len(raw_text) > len(quoted.group(1)) + 10:
                    raw_text = quoted.group(1)
                # Strip instruction-like prefixes
                raw_text = _re.sub(
                    r'^(?:type|enter|search|input|write|put)\s+', '', raw_text, flags=_re.IGNORECASE
                ).strip().strip("'\"")

            action = AgentAction(
                action_type=action_type,
                target_cell=data.get("target_cell", ""),
                target_description=data.get("target_description", ""),
                text=raw_text,
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
