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
        self._current_task: Optional[str] = None
        self._current_iteration: int = 0
        self._action_history: List[ActionStep] = []
        self._last_result: Optional[AgentResult] = None
        self._lock = threading.Lock()
        self.config = AgentControlConfig()

    @property
    def is_active(self) -> bool:
        return self._active

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
            "current_task": self._current_task,
            "iteration": self._current_iteration,
            "history_length": len(self._action_history),
            "last_result": last,
        }

    def execute_task(self, task: str, screen) -> AgentResult:
        """
        Execute a task using the see-think-act loop.

        Args:
            task: Natural language description of the task
            screen: ScreenInterface implementation

        Returns:
            AgentResult with success status and action history
        """
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

                # 2. ANALYZE — Vision model describes the screen
                vision_prompt = self._build_vision_prompt(task, self._action_history)
                scene = analyzer.analyze(screenshot, prompt=vision_prompt)
                if not scene.success:
                    logger.error(f"Vision analysis failed: {scene.error}")
                    consecutive_failures += 1
                    continue

                # 3. THINK — Text LLM decides next action
                decision_prompt = self._build_decision_prompt(
                    task, scene.description, self._action_history
                )
                decision_result = analyzer.text_query(decision_prompt)
                if not decision_result.success:
                    consecutive_failures += 1
                    continue

                decision = self._parse_decision(decision_result.description)

                if decision.task_complete:
                    return self._store_and_return(AgentResult(
                        success=True, reason="completed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if decision.stuck:
                    consecutive_failures += 1
                    continue

                # 4. ACT — Execute via servo (for clicks) or direct (for type/hotkey/scroll)
                if decision.action.action_type == "click":
                    servo_result = servo.click_target(decision.action.target_description)
                    decision.action.coordinates = (servo_result.get("x", 0), servo_result.get("y", 0))
                    result = {"success": servo_result.get("success", False)}
                    failed = not servo_result.get("verified", False)
                else:
                    result = self._execute_action(decision.action, screen)
                    failed = not result.get("success", False)

                # 5. RECORD step
                step = ActionStep(
                    iteration=iteration,
                    scene_description=scene.description,
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

    def _build_vision_prompt(self, task: str, history: List[ActionStep]) -> str:
        """Build the prompt for scene analysis."""
        prompt = (
            f"Task: {task}\n\n"
            "Describe what is currently on screen. Include:\n"
            "1. What website/app is showing\n"
            "2. Key interactive elements (buttons, text fields, links) and which grid cell they are in\n"
            "3. Whether the task appears COMPLETE (e.g., the target website has loaded)\n"
            "Format elements as: [description] -> [cell]\n"
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
            recent = history[-5:]  # Last 5 actions
            lines = []
            for step in recent:
                status = "FAILED" if step.failed else "OK"
                desc = step.action.target_description or step.action.text or str(step.action.keys)
                lines.append(f"  - {step.action.action_type}: {desc} [{status}]")
            history_text = "Recent actions:\n" + "\n".join(lines)

        # Detect repeated actions
        loop_warning = ""
        if len(history) >= 2:
            last_actions = [(s.action.action_type, s.action.text, s.action.target_description) for s in history[-3:]]
            if len(set(last_actions)) == 1:
                loop_warning = "\nWARNING: You are repeating the same action. This is NOT working. Try a DIFFERENT approach.\n"

        return f"""Task: {task}

Current screen:
{scene}

{history_text}
{loop_warning}
BROWSER SHORTCUTS (use hotkey action for these):
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
