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
import math
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.services.demonstration_lessons import (
    clean_target_description,
    describe_step,
    scroll_amount,
)

logger = logging.getLogger(__name__)

# --- Constants ---

PROMOTION_THRESHOLD = 3          # consecutive successes needed to promote
CONFIDENCE_FALLBACK_THRESHOLD = 0.6  # below this, SUPERVISED falls back to GUIDED
QUESTION_TIMEOUT = 60            # seconds to wait for human answer
SIGNOFF_TIMEOUT = 300            # seconds a finished step waits for a person's verdict
SIGNOFF_VERDICTS = ("ok", "retry", "stop")


@dataclass
class AttemptResult:
    """Result of replaying a full demonstration."""
    success: bool
    steps_completed: int
    total_steps: int
    step_results: List[Dict]
    failure_reason: str = ""


_MATCHES_FIELD_RE = re.compile(
    r"""["']?\bmatches["']?\s*[:=]\s*["']?(true|false)\b""", re.IGNORECASE
)
_DOES_NOT_MATCH_RE = re.compile(r"\b(?:does|do)\s*(?:not|n['’]t)\s+match", re.IGNORECASE)
_LEADING_YES_NO_RE = re.compile(r"\W*(yes|no)\b", re.IGNORECASE)


def _parse_match_verdict(text: str) -> Optional[bool]:
    """
    Read the match verdict from the precondition model's reply.

    Tries, in order: a JSON object's "matches" field (bool, or the strings
    "true"/"false"), a `matches: true/false` fragment in malformed JSON,
    "does not match" prose, and a leading yes/no. A JSON object without a
    readable "matches" field gives no verdict rather than falling through
    to the prose checks, which could pick up words from its description.

    Returns:
        True or False, or None when the reply holds no readable verdict.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    # Spanning first "{" to last "}" also unwraps ```json fences and any
    # prose the model put around the object.
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("matches")
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in ("true", "false"):
                return value.strip().lower() == "true"
            return None

    field = _MATCHES_FIELD_RE.search(text)
    if field:
        return field.group(1).lower() == "true"

    if _DOES_NOT_MATCH_RE.search(text):
        return False

    leading = _LEADING_YES_NO_RE.match(text)
    if leading:
        return leading.group(1).lower() == "yes"

    return None


_VISIBLE_FIELD_RE = re.compile(
    r"""["']?\bvisible["']?\s*[:=]\s*["']?(true|false)\b""", re.IGNORECASE
)
_CONFIDENCE_FIELD_RE = re.compile(
    r"""["']?\bconfidence["']?\s*[:=]\s*["']?(\d+(?:\.\d+)?|\.\d+)""", re.IGNORECASE
)


def _parse_visible_confidence(text: str) -> float:
    """
    Read the vision model's {"visible": ..., "confidence": ...} reply as a score.

    Read like _parse_match_verdict: a JSON object anywhere in the reply (fences
    and surrounding prose included), with booleans or the strings "true"/"false",
    then `visible: ...` / `confidence: ...` fragments of malformed JSON. Prose
    is not read: "not visible" contains the word too.

    Only visible true returns the model's confidence, clamped to 0.0-1.0.
    Visible false, a missing or unreadable visible field, and a missing or
    unreadable confidence all give 0.0, so a supervised replay asks first.
    """
    if not isinstance(text, str) or not text.strip():
        return 0.0

    visible: Optional[bool] = None
    confidence: Any = None
    parsed = None
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        value = parsed.get("visible")
        if isinstance(value, bool):
            visible = value
        elif isinstance(value, str) and value.strip().lower() in ("true", "false"):
            visible = value.strip().lower() == "true"
        confidence = parsed.get("confidence")
    else:
        field = _VISIBLE_FIELD_RE.search(text)
        if field:
            visible = field.group(1).lower() == "true"
        conf = _CONFIDENCE_FIELD_RE.search(text)
        if conf:
            confidence = conf.group(1)

    if visible is not True or isinstance(confidence, bool):
        return 0.0
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return min(max(score, 0.0), 1.0)


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
        review: bool = False,
    ) -> AttemptResult:
        """
        Execute a demonstration's steps at the given autonomy level.

        Args:
            steps: List of step dicts from a Demonstration
            autonomy_level: 'guided', 'supervised', or 'autonomous'
            demonstration_id: Optional ID for tracking
            emit_fn: Optional callback for emitting SocketIO events
                     signature: emit_fn(event_name: str, data: dict)
            review: A person is there to judge finished steps. A guided run
                    then waits after every step for their verdict (worked,
                    retry, stop), and a supervised run after a step that
                    failed. Their verdict is the step's result: a step they
                    say worked counts even when the click's own check saw
                    nothing change. Signing a step off also approves running
                    the next one, which the review shows, unless the screen
                    then fails that step's precondition.

        Returns:
            AttemptResult with success status and per-step results. With
            review, each step result carries 'signed_off'.
        """
        self._killed = False
        level = autonomy_level.lower()
        step_results: List[Dict] = []
        steps_completed = 0
        total = len(steps)

        def _ended(reason: str) -> AttemptResult:
            return AttemptResult(
                success=False,
                steps_completed=steps_completed,
                total_steps=total,
                step_results=step_results,
                failure_reason=reason,
            )

        logger.info(
            f"[APPRENTICE] Executing {total} steps at level={level} "
            f"demo_id={demonstration_id} review={review}"
        )

        index = 0
        preapproved = False  # the last sign-off covered running this step
        retrying = False     # the person asked for this step again
        retries = 0

        while index < total:
            step = steps[index]
            if self._killed:
                return _ended("Execution killed by user")

            step_index = step.get("step_index", index)
            action_type = step.get("action_type", "unknown")
            precondition = step.get("precondition", "")
            description = describe_step(step)

            logger.info(f"[APPRENTICE] Step {step_index}: {description}")

            # --- 1. Precondition check ---
            precondition_ok = True
            if precondition and not retrying:
                pre_result = self._check_precondition(precondition)
                precondition_ok = pre_result.get("matches", False)
                if not precondition_ok:
                    logger.warning(
                        f"[APPRENTICE] Precondition failed for step {step_index}: "
                        f"{pre_result.get('description', '')}"
                    )
                    if level == "autonomous":
                        return _ended(f"Precondition failed at step {step_index}: {precondition}")
                    # Approved against a screen that is not the one in front
                    # of the step: ask again.
                    preapproved = False

            # --- 2. Handle variable input ---
            variable_input = None
            if step.get("variability"):
                # In GUIDED/SUPERVISED mode, could ask human for input
                # For now, use the recorded text as default
                variable_input = step.get("text")

            # --- 3. Ask before running, where the level calls for it ---
            ask = False
            preview = {
                "step_index": step_index,
                "action_type": action_type,
                "target_description": description,
                "precondition": precondition,
                "precondition_ok": precondition_ok,
                "demonstration_id": demonstration_id,
            }
            if retrying:
                pass  # asking for the step again is the go-ahead
            elif level == "guided":
                ask = not preapproved
            elif level == "supervised":
                # Auto-execute but fall back to GUIDED for low-confidence steps
                confidence = self._estimate_confidence(step)
                if confidence < CONFIDENCE_FALLBACK_THRESHOLD:
                    logger.info(
                        f"[APPRENTICE] Low confidence ({confidence:.2f}) at step {step_index}, "
                        f"falling back to guided mode"
                    )
                    preview["confidence"] = confidence
                    ask = True
            # level == "autonomous": just execute, no preview or fallback

            if ask:
                if emit_fn:
                    emit_fn("step_preview", preview)
                confirmation = self._wait_for_confirmation(QUESTION_TIMEOUT)
                if self._killed:
                    return _ended("Execution killed by user")
                # The step was put to a person; with no answer it does not run.
                if confirmation is None:
                    return _ended(f"Timeout waiting for confirmation at step {step_index}")
                if confirmation.get("verdict") == "stop":
                    return _ended(f"Stopped by the user before step {step_index}")
                # Check if human provided override input
                if confirmation.get("variable_input"):
                    variable_input = confirmation["variable_input"]
            preapproved = False

            # --- 4. Execute the step ---
            exec_result = self._execute_step(step, variable_input=variable_input)
            machine_success = bool(exec_result.get("success", False))
            success = machine_success
            signed_off = False

            # --- 5. A person's verdict on the finished step ---
            if review and (level == "guided" or (level == "supervised" and not machine_success)):
                following = steps[index + 1] if index + 1 < total else None
                if emit_fn:
                    emit_fn("step_review", {
                        "step_index": step_index,
                        "action_type": action_type,
                        "target_description": description,
                        "machine_success": machine_success,
                        "error": "" if machine_success else str(exec_result.get("error") or ""),
                        "next_description": describe_step(following) if following else "",
                        "demonstration_id": demonstration_id,
                    })
                verdict = self._wait_for_signoff(step_index, SIGNOFF_TIMEOUT)
                if self._killed:
                    return _ended("Execution killed by user")
                choice = verdict.get("verdict") if verdict else None
                if choice == "retry":
                    retrying = True
                    retries += 1
                    continue
                if choice != "ok":
                    step_results.append({
                        "step_index": step_index,
                        "action_type": action_type,
                        "success": False,
                        "machine_success": machine_success,
                        "signed_off": False,
                        "retries": retries,
                        "precondition_ok": precondition_ok,
                        "result": exec_result,
                    })
                    if choice == "stop":
                        return _ended(f"Stopped by the user at step {step_index}")
                    return _ended(f"No sign-off at step {step_index}")
                success = True
                signed_off = True
                preapproved = level == "guided"

            step_result = {
                "step_index": step_index,
                "action_type": action_type,
                "success": success,
                "machine_success": machine_success,
                "signed_off": signed_off,
                "retries": retries,
                "precondition_ok": precondition_ok,
                "result": exec_result,
            }
            step_results.append(step_result)

            if not success:
                logger.warning(f"[APPRENTICE] Step {step_index} failed: {exec_result}")
                if level == "autonomous":
                    return _ended(f"Step {step_index} ({action_type}) failed")
                # In guided/supervised with nobody reviewing, continue but mark failure
            else:
                steps_completed += 1

            # --- 6. Emit step completion ---
            if emit_fn:
                emit_fn("step_complete", {
                    "step_index": step_index,
                    "action_type": action_type,
                    "success": success,
                    "demonstration_id": demonstration_id,
                })
                if signed_off:
                    emit_fn("step_signed", {
                        "step_index": step_index,
                        "step": step,
                        "description": description,
                        "demonstration_id": demonstration_id,
                    })

            # Click steps are recorded for training by servo.click_target's own
            # collector (the same TrainingDataCollector instance, see the
            # construction in agent_control_service). A second record here would
            # double-count clicks, and non-click steps carry no coordinate label.

            index += 1
            retrying = False
            retries = 0

        all_success = steps_completed == total
        return AttemptResult(
            success=all_success,
            steps_completed=steps_completed,
            total_steps=total,
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
                # Found by what it looks like, never by where it was recorded.
                target = clean_target_description(step.get("target_description", ""))
                context = clean_target_description(step.get("element_context", ""))
                if context and context != target:
                    target = f"{target} {context}"
                result = self.servo.click_target(target)
                # A sent click is not a done step: when nothing changed where it
                # landed, the target was missed (a replay once clicked 80 px above
                # the Pictures icon and still counted the step as completed).
                if result.get("success") and result.get("post_action_effect") == "no_visible_change":
                    return {**result, "success": False,
                            "error": "The click changed nothing on screen where it landed."}
                return result

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
                # Saved steps carry the position as coordinates: [x, y].
                coords = step.get("coordinates") or [step.get("x"), step.get("y")]
                x = coords[0] if coords[0] is not None else 640
                y = coords[1] if coords[1] is not None else 360
                return self.screen.scroll(x, y, scroll_amount(step))

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

        Fails closed: when the screen cannot be described, the text model
        fails, the reply holds no readable verdict, or anything raises,
        'matches' is False and 'checked' is False. An autonomous replay then
        stops; guided and supervised runs only log it.

        Returns:
            dict with 'matches' (bool), 'checked' (bool, whether a verdict
            was obtained) and 'description' (str)
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
                return {
                    "matches": False,
                    "checked": False,
                    "description": "Vision unavailable, precondition not checked",
                }

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
                return {
                    "matches": False,
                    "checked": False,
                    "description": "Text model unavailable, precondition not checked",
                }

            reply = text_result.description or ""
            verdict = _parse_match_verdict(reply)
            if verdict is None:
                logger.warning(f"[APPRENTICE] No match verdict in precondition reply: {reply[:200]!r}")
                return {
                    "matches": False,
                    "checked": False,
                    "description": f"No match verdict in model reply: {reply}",
                }
            return {"matches": verdict, "checked": True, "description": reply}

        except Exception as e:
            logger.error(f"[APPRENTICE] Precondition check error: {e}", exc_info=True)
            return {
                "matches": False,
                "checked": False,
                "description": f"Error checking precondition: {e}",
            }

    # ------------------------------------------------------------------
    # Confidence estimation
    # ------------------------------------------------------------------

    def _estimate_confidence(self, step: Dict) -> float:
        """
        Estimate confidence that we can successfully execute this step.

        Asks the vision model whether the target element is visible on screen.
        Fails closed: anything short of a readable visible true scores 0.0
        (see _parse_visible_confidence), as does a failed or raising vision
        call, so a supervised replay asks before running the step.

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
                logger.warning(f"[APPRENTICE] Vision analysis failed: {result.error}")
                return 0.0

            return _parse_visible_confidence(result.description or "")

        except Exception as e:
            logger.warning(f"[APPRENTICE] Confidence estimation error: {e}")
            return 0.0

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
        """Called by frontend/API to answer the step the replay is waiting on.

        Before a step: {} or {"confirmed": True} runs it, {"verdict": "stop"}
        ends the replay. After a step under review: {"verdict": "ok" |
        "retry" | "stop", "step_index": n}.
        """
        self._confirmation_data = data or {"confirmed": True}
        self._confirmation_event.set()

    def _wait_for_signoff(self, step_index: int, timeout: float = SIGNOFF_TIMEOUT) -> Optional[Dict]:
        """
        Wait for a person's verdict on the step that just ran.

        Only a reply that names a verdict counts, and one that names another
        step is ignored, so a late click on the earlier Go button is not
        taken as a sign-off.

        Returns:
            The reply dict, or None on timeout or kill
        """
        deadline = time.monotonic() + timeout
        self._confirmation_event.clear()
        self._confirmation_data = None
        while not self._killed:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._confirmation_event.wait(timeout=remaining):
                return None
            data = self._confirmation_data or {}
            self._confirmation_event.clear()
            self._confirmation_data = None
            if self._killed:
                return None
            if data.get("verdict") not in SIGNOFF_VERDICTS:
                continue
            named = data.get("step_index")
            if named is not None and named != step_index:
                continue
            return data
        return None

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
