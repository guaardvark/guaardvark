#!/usr/bin/env python3
"""
ApprenticeEngine — Replays demonstrations with graduated autonomy.

Autonomy levels:
- GUIDED: emit step_preview, wait for human confirmation before each step
- SUPERVISED: auto-execute, but fall back to GUIDED for low-confidence steps
- AUTONOMOUS: execute everything, fail fast on errors

Uses ServoController for vision-grounded clicks and the ScreenInterface
backend directly for typing, hotkeys, and scrolling.
"""

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Constants ---

PROMOTION_THRESHOLD = 3          # consecutive successes needed to promote
CONFIDENCE_FALLBACK_THRESHOLD = 0.6  # below this, SUPERVISED falls back to GUIDED
QUESTION_TIMEOUT = 60            # seconds to wait for human answer


@dataclass
class AttemptResult:
    """Result of replaying a full demonstration."""
    success: bool
    steps_completed: int
    total_steps: int
    step_results: List[Dict]
    failure_reason: str = ""


class ApprenticeEngine:
    """
    Replays recorded demonstrations with graduated autonomy.

    Clicks go through ServoController (vision-grounded, coordinate-free).
    Type / hotkey / scroll go through the ScreenInterface directly.
    """

    def __init__(self, screen, analyzer, servo, collector=None):
        """
        Args:
            screen: ScreenInterface backend (LocalScreenBackend, etc.)
            analyzer: VisionAnalyzer for precondition checks and confidence estimation
            servo: ServoController for vision-grounded clicking
            collector: Optional TrainingDataCollector for recording attempt data
        """
        self.screen = screen
        self.analyzer = analyzer
        self.servo = servo
        self.collector = collector

        # Emergency stop flag
        self._killed = False

        # Human-in-the-loop synchronization
        self._confirmation_event = threading.Event()
        self._confirmation_data: Optional[Dict] = None
        self._answer_queue: queue.Queue = queue.Queue()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def kill(self):
        """Emergency stop — abort current execution immediately."""
        self._killed = True
        self._confirmation_event.set()  # unblock any waiting confirmation
        logger.warning("[APPRENTICE] Kill signal received")

    def execute(
        self,
        steps: List[Dict],
        autonomy_level: str = "guided",
        demonstration_id: Optional[str] = None,
        emit_fn: Optional[Callable] = None,
    ) -> AttemptResult:
        """
        Execute a demonstration's steps at the given autonomy level.

        Args:
            steps: List of step dicts from a Demonstration
            autonomy_level: 'guided', 'supervised', or 'autonomous'
            demonstration_id: Optional ID for tracking
            emit_fn: Optional callback for emitting SocketIO events
                     signature: emit_fn(event_name: str, data: dict)

        Returns:
            AttemptResult with success status and per-step results
        """
        self._killed = False
        level = autonomy_level.lower()
        step_results: List[Dict] = []
        steps_completed = 0

        logger.info(
            f"[APPRENTICE] Executing {len(steps)} steps at level={level} "
            f"demo_id={demonstration_id}"
        )

        for step in steps:
            if self._killed:
                return AttemptResult(
                    success=False,
                    steps_completed=steps_completed,
                    total_steps=len(steps),
                    step_results=step_results,
                    failure_reason="Execution killed by user",
                )

            step_index = step.get("step_index", steps_completed)
            action_type = step.get("action_type", "unknown")
            precondition = step.get("precondition", "")

            logger.info(
                f"[APPRENTICE] Step {step_index}: {action_type} "
                f"target={step.get('target_description', '')}"
            )

            # --- 1. Precondition check ---
            precondition_ok = True
            if precondition:
                pre_result = self._check_precondition(precondition)
                precondition_ok = pre_result.get("matches", True)
                if not precondition_ok:
                    logger.warning(
                        f"[APPRENTICE] Precondition failed for step {step_index}: "
                        f"{pre_result.get('description', '')}"
                    )
                    if level == "autonomous":
                        return AttemptResult(
                            success=False,
                            steps_completed=steps_completed,
                            total_steps=len(steps),
                            step_results=step_results,
                            failure_reason=f"Precondition failed at step {step_index}: {precondition}",
                        )

            # --- 2. Handle variable input ---
            variable_input = None
            if step.get("variability"):
                # In GUIDED/SUPERVISED mode, could ask human for input
                # For now, use the recorded text as default
                variable_input = step.get("text")

            # --- 3. Mode-specific behavior ---
            if level == "guided":
                # Emit preview and wait for human confirmation
                if emit_fn:
                    emit_fn("step_preview", {
                        "step_index": step_index,
                        "action_type": action_type,
                        "target_description": step.get("target_description", ""),
                        "precondition": precondition,
                        "precondition_ok": precondition_ok,
                        "demonstration_id": demonstration_id,
                    })
                confirmation = self._wait_for_confirmation(QUESTION_TIMEOUT)
                if self._killed:
                    return AttemptResult(
                        success=False,
                        steps_completed=steps_completed,
                        total_steps=len(steps),
                        step_results=step_results,
                        failure_reason="Execution killed by user",
                    )
                if confirmation is None:
                    return AttemptResult(
                        success=False,
                        steps_completed=steps_completed,
                        total_steps=len(steps),
                        step_results=step_results,
                        failure_reason=f"Timeout waiting for confirmation at step {step_index}",
                    )
                # Check if human provided override input
                if confirmation.get("variable_input"):
                    variable_input = confirmation["variable_input"]

            elif level == "supervised":
                # Auto-execute but fall back to GUIDED for low-confidence steps
                confidence = self._estimate_confidence(step)
                if confidence < CONFIDENCE_FALLBACK_THRESHOLD:
                    logger.info(
                        f"[APPRENTICE] Low confidence ({confidence:.2f}) at step {step_index}, "
                        f"falling back to guided mode"
                    )
                    if emit_fn:
                        emit_fn("step_preview", {
                            "step_index": step_index,
                            "action_type": action_type,
                            "target_description": step.get("target_description", ""),
                            "precondition": precondition,
                            "precondition_ok": precondition_ok,
                            "confidence": confidence,
                            "demonstration_id": demonstration_id,
                        })
                    confirmation = self._wait_for_confirmation(QUESTION_TIMEOUT)
                    if self._killed:
                        return AttemptResult(
                            success=False,
                            steps_completed=steps_completed,
                            total_steps=len(steps),
                            step_results=step_results,
                            failure_reason="Execution killed by user",
                        )
                    if confirmation and confirmation.get("variable_input"):
                        variable_input = confirmation["variable_input"]

            # level == "autonomous": just execute, no preview or fallback

            # --- 4. Execute the step ---
            exec_result = self._execute_step(step, variable_input=variable_input)

            step_result = {
                "step_index": step_index,
                "action_type": action_type,
                "success": exec_result.get("success", False),
                "precondition_ok": precondition_ok,
                "result": exec_result,
            }
            step_results.append(step_result)

            if not exec_result.get("success", False):
                logger.warning(f"[APPRENTICE] Step {step_index} failed: {exec_result}")
                if level == "autonomous":
                    return AttemptResult(
                        success=False,
                        steps_completed=steps_completed,
                        total_steps=len(steps),
                        step_results=step_results,
                        failure_reason=f"Step {step_index} ({action_type}) failed",
                    )
                # In guided/supervised, continue but mark failure
            else:
                steps_completed += 1

            # --- 5. Emit step completion ---
            if emit_fn:
                emit_fn("step_complete", {
                    "step_index": step_index,
                    "success": exec_result.get("success", False),
                    "demonstration_id": demonstration_id,
                })

            # --- 6. Record for training ---
            if self.collector:
                try:
                    self.collector.record(
                        source="apprentice",
                        data={
                            "demonstration_id": demonstration_id,
                            "step_index": step_index,
                            "action_type": action_type,
                            "success": exec_result.get("success", False),
                            "result": exec_result,
                        },
                    )
                except Exception as e:
                    logger.debug(f"[APPRENTICE] Collector record failed: {e}")

        all_success = steps_completed == len(steps)
        return AttemptResult(
            success=all_success,
            steps_completed=steps_completed,
            total_steps=len(steps),
            step_results=step_results,
            failure_reason="" if all_success else "Some steps failed",
        )

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _execute_step(self, step: Dict, variable_input: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute a single demonstration step.

        Clicks use servo.click_target(description) for vision-grounded targeting.
        Type/hotkey/scroll use the screen backend directly.
        """
        action_type = step.get("action_type", "")

        try:
            if action_type == "click":
                target = step.get("target_description", "")
                context = step.get("element_context", "")
                if context:
                    target = f"{target} {context}"
                return self.servo.click_target(target)

            elif action_type == "type":
                text = variable_input if variable_input is not None else step.get("text", "")
                if not text:
                    return {"success": False, "error": "No text to type"}
                return self.screen.type_text(text)

            elif action_type == "hotkey":
                keys_str = step.get("keys", "")
                if not keys_str:
                    return {"success": False, "error": "No keys specified"}
                keys = keys_str.split("+")
                return self.screen.hotkey(*keys)

            elif action_type == "scroll":
                x = step.get("x", 640)
                y = step.get("y", 360)
                amount = step.get("scroll_amount", -3)
                return self.screen.scroll(x, y, amount)

            else:
                return {"success": False, "error": f"Unknown action type: {action_type}"}

        except Exception as e:
            logger.error(f"[APPRENTICE] Step execution error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Precondition checking
    # ------------------------------------------------------------------

    def _check_precondition(self, precondition: str) -> Dict[str, Any]:
        """
        Check whether the current screen matches the expected precondition.

        Uses VisionAnalyzer: capture screen -> describe it -> ask text model
        if the description matches the precondition.

        Returns:
            dict with 'matches' (bool) and 'description' (str)
        """
        try:
            # Step 1: Capture current screen
            screenshot, _ = self.screen.capture()

            # Step 2: Describe what's on screen
            vision_result = self.analyzer.analyze(
                screenshot,
                "Describe what you see on this screen. Focus on UI elements, forms, buttons, and labels.",
            )

            if not vision_result.success:
                logger.warning(f"[APPRENTICE] Vision analysis failed: {vision_result.error}")
                return {"matches": True, "description": "Vision unavailable, assuming match"}

            screen_description = vision_result.description

            # Step 3: Ask text model if screen matches precondition
            prompt = (
                f"Screen description: {screen_description}\n\n"
                f"Expected precondition: {precondition}\n\n"
                f"Does the current screen match the expected precondition? "
                f"Respond with JSON: {{\"matches\": true/false, \"description\": \"brief explanation\"}}"
            )
            text_result = self.analyzer.text_query(prompt)

            if not text_result.success:
                logger.warning(f"[APPRENTICE] Text query failed: {text_result.error}")
                return {"matches": True, "description": "Text model unavailable, assuming match"}

            # Parse JSON response
            try:
                parsed = json.loads(text_result.description)
                return {
                    "matches": bool(parsed.get("matches", True)),
                    "description": parsed.get("description", ""),
                }
            except (json.JSONDecodeError, TypeError):
                # If model didn't return JSON, try to infer from text
                desc = text_result.description.lower()
                matches = "yes" in desc or "matches" in desc or "true" in desc
                return {"matches": matches, "description": text_result.description}

        except Exception as e:
            logger.error(f"[APPRENTICE] Precondition check error: {e}", exc_info=True)
            return {"matches": True, "description": f"Error checking precondition: {e}"}

    # ------------------------------------------------------------------
    # Confidence estimation
    # ------------------------------------------------------------------

    def _estimate_confidence(self, step: Dict) -> float:
        """
        Estimate confidence that we can successfully execute this step.

        Asks the vision model whether the target element is visible on screen.

        Returns:
            float between 0.0 and 1.0
        """
        try:
            target = step.get("target_description", "")
            if not target:
                return 0.5

            screenshot, _ = self.screen.capture()
            result = self.analyzer.analyze(
                screenshot,
                f"Is '{target}' visible on this screen? "
                f"Respond with JSON: {{\"visible\": true/false, \"confidence\": 0.0-1.0}}",
            )

            if not result.success:
                return 0.5

            try:
                parsed = json.loads(result.description)
                return float(parsed.get("confidence", 0.5))
            except (json.JSONDecodeError, TypeError, ValueError):
                desc = result.description.lower()
                if "yes" in desc or "visible" in desc:
                    return 0.8
                return 0.4

        except Exception as e:
            logger.debug(f"[APPRENTICE] Confidence estimation error: {e}")
            return 0.5

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    def _wait_for_confirmation(self, timeout: float = QUESTION_TIMEOUT) -> Optional[Dict]:
        """
        Wait for human confirmation before executing a step.

        The frontend calls confirm_step() which sets the event.

        Returns:
            Confirmation data dict, or None on timeout
        """
        self._confirmation_event.clear()
        self._confirmation_data = None
        got_it = self._confirmation_event.wait(timeout=timeout)
        if got_it and not self._killed:
            return self._confirmation_data or {"confirmed": True}
        return None

    def confirm_step(self, data: Optional[Dict] = None):
        """Called by frontend/API to confirm the current step."""
        self._confirmation_data = data or {"confirmed": True}
        self._confirmation_event.set()

    def _wait_for_answer(self, timeout: float = QUESTION_TIMEOUT) -> Optional[Dict]:
        """
        Wait for a human answer to a clarification question.

        Returns:
            Answer data dict, or None on timeout
        """
        try:
            return self._answer_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def submit_answer(self, answer: Dict):
        """Called by frontend/API to submit an answer to a question."""
        self._answer_queue.put(answer)

    # ------------------------------------------------------------------
    # Question generation
    # ------------------------------------------------------------------

    def generate_clarification_questions(self, steps: List[Dict]) -> List[Dict]:
        """
        Generate Type A questions for ambiguous steps before execution.

        Identifies steps with variability=True or vague target descriptions
        and generates questions to disambiguate them.
        """
        questions = []
        for step in steps:
            if step.get("variability"):
                questions.append({
                    "type": "clarification",
                    "step_index": step.get("step_index", 0),
                    "question": f"What value should be entered for '{step.get('target_description', 'this field')}'?",
                    "default": step.get("text", ""),
                    "action_type": step.get("action_type", ""),
                })
        return questions

    def generate_generalization_questions(self, steps: List[Dict]) -> List[Dict]:
        """
        Generate Type B questions after a successful run.

        Asks the human to confirm whether the demonstration should
        generalize to similar contexts.
        """
        questions = []
        variable_steps = [s for s in steps if s.get("variability")]
        if variable_steps:
            questions.append({
                "type": "generalization",
                "question": (
                    f"This demonstration has {len(variable_steps)} variable step(s). "
                    f"Should the same procedure apply with different values?"
                ),
                "variable_fields": [
                    {
                        "step_index": s.get("step_index", 0),
                        "target": s.get("target_description", ""),
                        "recorded_value": s.get("text", ""),
                    }
                    for s in variable_steps
                ],
            })
        return questions

    # ------------------------------------------------------------------
    # Graduation logic (static methods for testability)
    # ------------------------------------------------------------------

    @staticmethod
    def _should_promote(level: str, consecutive_successes: int) -> bool:
        """Check if autonomy level should be promoted based on success count."""
        if level == "autonomous":
            return False
        return consecutive_successes >= PROMOTION_THRESHOLD

    @staticmethod
    def _promote(level: str) -> str:
        """Promote to next autonomy level."""
        promotion_map = {
            "guided": "supervised",
            "supervised": "autonomous",
            "autonomous": "autonomous",
        }
        return promotion_map.get(level, level)

    @staticmethod
    def _demote(level: str) -> str:
        """Demote to previous autonomy level on failure."""
        demotion_map = {
            "autonomous": "supervised",
            "supervised": "guided",
            "guided": "guided",
        }
        return demotion_map.get(level, level)
