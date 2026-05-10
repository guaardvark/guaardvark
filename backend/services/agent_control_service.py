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
import re
import threading
import time
from dataclasses import asdict, dataclass, field
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
    # When action_type == "done": short, vision-verifiable description of the
    # screen state that proves the task is complete (e.g. "cursor blinking
    # inside the comment text area", "comment now appears in thread"). Empty
    # or trivial values are rejected — see done-handling guard.
    success_proof: str = ""


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
    task: str = ""  # the user-facing task string this result is for


@dataclass
class WorldState:
    """Compact per-iteration environment snapshot for decision grounding."""
    timestamp_iso: str = ""
    desktop_state: str = ""
    dom_url: str = ""
    dom_title: str = ""
    dom_element_count: int = 0
    cursor_pos: Tuple[int, int] = (0, 0)
    last_action: str = ""
    last_action_status: str = ""
    scene_hint: str = ""
    progress_label: str = ""
    progress_confidence: float = 0.0
    progress_evidence: str = ""
    progress_next_hint: str = ""
    learned_recovery_hint: str = ""
    blocked_actions: str = ""
    current_subgoal: str = ""
    next_subgoal: str = ""
    subgoal_completion_signal: str = ""


@dataclass
class ProgressSignal:
    """Structured outcome signal from the previous action."""
    label: str = "unknown"
    confidence: float = 0.0
    evidence: str = ""
    next_hint: str = ""


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
        # Cached per-iteration DOM snapshot (Firefox via Bidi). Refreshed once per
        # see-think-act tick; used by both the prompt builder and the click-time
        # DOM-match guard so the two share a consistent view.
        self._dom_snapshot: Optional[Any] = None
        self._world_state: Optional[WorldState] = None
        self._last_progress_signal: Optional[ProgressSignal] = None
        self._strategy_cooldowns: Dict[str, int] = {}
        self._last_failed_strategy: str = ""
        self._same_strategy_failures: int = 0
        self._pending_failure_label: str = ""
        self._recovery_memory: Dict[str, Dict[str, int]] = {}
        self._recipe_fallback_note: str = ""
        self._lock = threading.Lock()
        self.config = AgentControlConfig()
        self._debug_run_id = ""

    def _debug_emit(self, hypothesis_id: str, location: str, message: str, data: Dict[str, Any]) -> None:
        # region agent log
        try:
            payload = {
                "sessionId": "aa957d",
                "runId": self._debug_run_id or f"run-{int(time.time() * 1000)}",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }
            with open("/home/llamax1/LLAMAX8/.cursor/debug-aa957d.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")
        except Exception:
            pass
        # endregion

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
            self._recipe_fallback_note = ""

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
        self._debug_run_id = f"run-{int(start_time * 1000)}"
        self._debug_emit(
            "H2",
            "agent_control_service.py:execute_task:start",
            "Task start",
            {
                "task": task[:200],
                "unified_model": self._get_unified_model(),
                "screen_display": getattr(screen, "display", "unknown"),
            },
        )

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
                self._tick_strategy_cooldowns()
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
                self._debug_emit(
                    "H2",
                    "agent_control_service.py:execute_task:see",
                    "SEE capture",
                    {
                        "iteration": iteration + 1,
                        "cursor_pos": cursor_pos,
                        "image_size": getattr(screenshot, "size", None),
                    },
                )

                scene_desc = ""  # Will be populated by either unified or split path

                # Check for unified vision+decision model (qwen3-vl:4b+)
                unified_model = self._get_unified_model()

                if unified_model:
                    # Refresh DOM once per iteration so the prompt builder and
                    # the click-time DOM-match guard see the same elements.
                    self._refresh_dom_snapshot()
                    self._world_state = self._build_world_state(cursor_pos=cursor_pos, scene_hint="")
                    self._debug_emit(
                        "H1",
                        "agent_control_service.py:execute_task:dom",
                        "DOM snapshot refreshed",
                        {
                            "iteration": iteration + 1,
                            "dom_count": len(getattr(self._dom_snapshot, "elements", []) or []),
                            "dom_url": getattr(self._dom_snapshot, "url", ""),
                        },
                    )
                    # UNIFIED MODE: Compact prompt — vision model sees screenshot + short context
                    unified_prompt = self._build_unified_prompt(
                        task,
                        self._action_history,
                        world_state=self._world_state,
                        training_mode=training_mode,
                    )
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
                    self._world_state = self._build_world_state(
                        cursor_pos=cursor_pos,
                        scene_hint=scene_desc,
                    )

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
                        task, scene.description, self._action_history, world_state=self._world_state
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
                self._debug_emit(
                    "H10",
                    "agent_control_service.py:execute_task:decision",
                    "Decision chosen",
                    {
                        "iteration": iteration + 1,
                        "action": decision.action.action_type,
                        "target": decision.action.target_description or "",
                        "text": (decision.action.text or "")[:120],
                        "keys": decision.action.keys or [],
                    },
                )

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

                    # Guard: require a non-trivial success_proof — the model must
                    # echo the visible state that proves completion. Empty or
                    # generic "task done" / "n/a" answers are rejected as phantom
                    # success. Only enforced for vision-capable models that get
                    # the success_proof requirement in their prompt.
                    proof = (decision.action.success_proof or "").strip()
                    proof_lc = proof.lower()
                    trivial_proofs = {"", "n/a", "na", "task complete", "done", "task done", "ok", "complete"}
                    enforce_proof = bool(self._get_unified_model())
                    if enforce_proof and proof_lc in trivial_proofs:
                        logger.warning(
                            f"[AGENT][DONE] Rejected — success_proof empty or trivial "
                            f"(got: {proof!r}). Treating as failed step."
                        )
                        decision.task_complete = False
                        # Record the rejected done as a failed step so the loop
                        # breaker / max_failures can still trip on persistent retries.
                        self._action_history.append(ActionStep(
                            iteration=iteration,
                            scene_description=scene_desc or "no scene",
                            action=decision.action,
                            result={"success": False, "reason": "missing_success_proof"},
                            verification="done proof missing/trivial",
                            failed=True,
                        ))
                        self._last_progress_signal = self._semantic_progress_signal(
                            decision.action,
                            {"success": False, "reason": "missing_success_proof"},
                            failed=True,
                            pixel_diff=None,
                        )
                        self._record_failure_label(self._last_progress_signal.label)
                        self._debug_emit(
                            "H4",
                            "agent_control_service.py:execute_task:done_reject",
                            "Done rejected due trivial proof",
                            {"iteration": iteration + 1, "proof": proof},
                        )
                        consecutive_failures += 1
                        continue

                    logger.warning(f"[AGENT][DONE] Task complete after {iteration+1} steps, "
                                  f"{time.time() - start_time:.1f}s "
                                  f"(proof: {proof[:80]!r})")
                    return self._store_and_return(AgentResult(
                        success=True, reason="completed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if decision.stuck:
                    logger.warning(f"[AGENT][STEP {iteration+1}][THINK] Agent reports stuck")
                    consecutive_failures += 1
                    continue

                # Strategy-level anti-looping: if an action class failed repeatedly,
                # put it on short cooldown and force a pivot step.
                blocked_steps = self._strategy_cooldowns.get(decision.action.action_type, 0)
                if blocked_steps > 0 and decision.action.action_type not in ("done", "wait"):
                    blocked = decision.action.action_type
                    logger.warning(
                        f"[AGENT][STEP {iteration+1}][PIVOT] Blocking repeated strategy "
                        f"'{blocked}' for {blocked_steps} more step(s); forcing wait"
                    )
                    decision.action.action_type = "wait"
                    decision.action.scroll_amount = 1
                    decision.action.reasoning = (
                        f"strategy cooldown active for {blocked}; wait and re-observe before new tactic"
                    )
                    decision.action.target_description = ""
                    decision.action.text = ""
                    decision.action.keys = []

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
                    pixel_diff_value: Optional[float] = None

                    # For generic area targets (desktop, empty space), click center-screen
                    # instead of asking the vision model to locate "the desktop"
                    if re.search(r'(?:desktop|empty|blank|background|open area|center|middle)\s*(?:area|space|screen)?', target, re.IGNORECASE):
                        sw, sh = screen.screen_size()
                        cx, cy = sw // 2, sh // 2
                        screen.click(cx, cy, button=button)
                        decision.action.coordinates = (cx, cy)
                        result = {"success": True}
                        failed = False
                    elif not self._dom_match(target):
                        # DOM is fresh, has elements, none match the target — clicking
                        # blindly would have the servo fabricate coords or land at (0,0).
                        # Fail this step so the next iteration sees [FAIL] and can pivot
                        # (typically by scrolling to bring the target into the DOM).
                        logger.warning(
                            f"[AGENT][STEP {iteration+1}][DOM-GUARD] No DOM element matches "
                            f"target=\"{target}\" — refusing click. Try scroll first."
                        )
                        decision.action.coordinates = (0, 0)
                        result = {"success": False, "reason": "no_dom_match"}
                        failed = True
                        self._debug_emit(
                            "H1",
                            "agent_control_service.py:execute_task:dom_guard",
                            "DOM guard rejected click",
                            {
                                "iteration": iteration + 1,
                                "target": target,
                                "dom_count": len(getattr(self._dom_snapshot, "elements", []) or []),
                            },
                        )
                    else:
                        servo_result = servo.click_target(target, button=button, single_attempt=training_mode)
                        decision.action.coordinates = (servo_result.get("x", 0), servo_result.get("y", 0))
                        result = {"success": servo_result.get("success", False)}
                        failed = not servo_result.get("success", False)

                    # Post-Act verification for clicks too: fresh capture + pixel diff
                    # to confirm visible change before marking [OK]. Record failure
                    # screenshot on ineffective click.
                    if not failed:
                        time.sleep(0.5)
                        post_shot, _ = self._capture_with_retry(screen)
                        if screenshot is not None and post_shot is not None:
                            import numpy as np
                            before_mean = np.array(screenshot).mean()
                            after_mean = np.array(post_shot).mean()
                            pixel_diff = abs(after_mean - before_mean)
                            pixel_diff_value = float(pixel_diff)
                            if pixel_diff < 0.005:
                                failed = True
                                result["success"] = False
                                result["verified"] = False
                                # Record failure screenshot for ineffective click
                                try:
                                    from backend.services.servo_controller import capture_servo_failure
                                    capture_servo_failure(
                                        screenshot=screenshot,
                                        target_description=target,
                                        reason="click_no_visible_change",
                                    )
                                except Exception:
                                    pass
                            else:
                                result["verified"] = True

                    status_icon = "OK" if not failed else "FAIL"
                    logger.warning(f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} \"{target}\" "
                                  f"at ({decision.action.coordinates[0]},{decision.action.coordinates[1]}) [{status_icon}]")
                else:
                    result = self._execute_action(decision.action, screen)
                    failed = not result.get("success", False)
                    pixel_diff_value: Optional[float] = None

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
                            pixel_diff_value = float(pixel_diff)
                            # A scroll that produced no pixel delta means the page
                            # didn't move — either we're at the bottom, the cursor
                            # is over a non-scrollable region (sidebar, overlay), or
                            # Firefox doesn't have keyboard focus. Treat it as a
                            # failed action so the loop-breaker (and the LLM's next
                            # prompt context) sees the scroll didn't help, instead
                            # of cheerfully logging "[OK]" three times in a row
                            # before aborting as loop_detected_no_progress.
                            ineffective = (
                                (decision.action.action_type == "type" and pixel_diff < 0.05)
                                or (decision.action.action_type == "scroll" and pixel_diff < 0.01)
                            )
                            self._debug_emit(
                                "H12",
                                "agent_control_service.py:execute_task:nonclick_verify",
                                "Non-click verification",
                                {
                                    "iteration": iteration + 1,
                                    "action": decision.action.action_type,
                                    "pixel_diff": round(float(pixel_diff), 5),
                                    "ineffective": bool(ineffective),
                                    "text": (decision.action.text or "")[:120],
                                    "keys": decision.action.keys or [],
                                },
                            )
                            if ineffective:
                                logger.warning(
                                    f"[AGENT][STEP {iteration+1}][VERIFY] "
                                    f"{decision.action.action_type} produced no visible change "
                                    f"(delta={pixel_diff:.3f}) — flagging as failed so the LLM "
                                    f"sees the previous attempt was ineffective"
                                )
                                result["verified"] = False
                                result["success"] = False
                                failed = True
                            else:
                                result["verified"] = True
                                logger.warning(f"[AGENT][STEP {iteration+1}][VERIFY] Screen changed after "
                                              f"{decision.action.action_type} (delta={pixel_diff:.2f})")

                    status_icon = "OK" if not failed else "FAIL"
                    detail = decision.action.text or str(decision.action.keys) or ""
                    logger.warning(f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} "
                                  f"\"{detail}\" [{status_icon}]")

                signal = self._semantic_progress_signal(
                    decision.action,
                    result,
                    failed=failed,
                    pixel_diff=pixel_diff_value,
                )
                self._last_progress_signal = signal
                result["semantic_progress"] = asdict(signal)
                self._record_strategy_outcome(decision.action, failed)
                self._record_recovery_memory(signal, decision.action, failed)

                # 5. RECORD step
                step = ActionStep(
                    iteration=iteration,
                    scene_description=scene_desc or "no scene",
                    action=decision.action,
                    result=result,
                    verification=signal.evidence,
                    failed=failed,
                )
                self._action_history.append(step)
                if len(self._action_history) >= 2:
                    last2 = self._action_history[-2:]
                    if (
                        last2[0].action.action_type == "type"
                        and last2[1].action.action_type == "type"
                        and (last2[0].action.text or "") == (last2[1].action.text or "")
                    ):
                        self._debug_emit(
                            "H13",
                            "agent_control_service.py:execute_task:type_repeat",
                            "Repeated identical type action",
                            {
                                "iteration": iteration + 1,
                                "text": (last2[1].action.text or "")[:120],
                                "prev_failed": bool(last2[0].failed),
                                "curr_failed": bool(last2[1].failed),
                            },
                        )

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
                        # Was returning success=True, which fed phantom-posts into the
                        # outreach pipeline (caller's "if not result.success" never tripped).
                        # Repeating an action 3x is evidence of stuck, not done.
                        logger.warning(
                            f"[AGENT][LOOP] Same action repeated 3x: "
                            f"{last3[0][0]} \"{last3[0][1] or last3[0][2]}\". "
                            f"Aborting as failure."
                        )
                        # Capture fresh failure screenshot + reason for prompt/history
                        try:
                            fail_shot, _ = self._capture_with_retry(screen)
                            # inject via history step already present; reason carries it
                        except Exception:
                            pass
                        return self._store_and_return(AgentResult(
                            success=False,
                            reason="loop_detected_no_progress",
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
                        self._debug_emit(
                            "H5",
                            "agent_control_service.py:execute_task:max_failures",
                            "Max failures triggered",
                            {"iteration": iteration + 1, "consecutive_failures": consecutive_failures},
                        )
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
        if self._recipe_fallback_note:
            result.reason = f"{result.reason} ({self._recipe_fallback_note})"
            self._recipe_fallback_note = ""
        if not result.task:
            # Stamp the task so consumers (e.g. Phase 3 inducer) can match the
            # result against later feedback without depending on _current_task,
            # which gets cleared in the loop's finally block.
            result.task = self._current_task or ""
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

    def _refresh_dom_snapshot(self) -> None:
        """Pull a fresh DOM snapshot from Firefox once per iteration.

        Stored on the instance so the prompt builder and the click-time DOM-match
        guard see the same elements. Silently no-ops when the unified model isn't
        gemma4 or when Bidi/Firefox isn't reachable — both fall through to
        coordinate-only vision flow.
        """
        self._dom_snapshot = None
        try:
            unified_model = self._get_unified_model()
            if unified_model and "gemma4" in unified_model.lower():
                from backend.services.dom_metadata_extractor import DOMMetadataExtractor
                snapshot = DOMMetadataExtractor.get_instance().extract()
                if snapshot.success and snapshot.elements:
                    self._dom_snapshot = snapshot
        except Exception:
            pass

    def _dom_match(self, target_description: str) -> bool:
        """True if target_description plausibly maps to a visible DOM element.

        Only meaningful when self._dom_snapshot is fresh and has elements; when
        the snapshot is missing (no Firefox / non-Bidi page) returns True so we
        don't block clicks on screens we can't introspect.
        """
        snap = self._dom_snapshot
        if not snap or not getattr(snap, "elements", None):
            return True
        target = (target_description or "").strip().lower()
        if not target:
            return True
        # Fuzzy substring match across element text + tag + element_type. The
        # vision model gets short labels like "Comment button" or "text input
        # field" — usually one of the words shows up in element.text or .tag.
        target_words = [w for w in re.split(r"[^a-z0-9]+", target) if len(w) >= 3]
        if not target_words:
            return True
        for el in snap.elements:
            haystack = " ".join(filter(None, [
                getattr(el, "text", "") or "",
                getattr(el, "tag", "") or "",
                getattr(el, "element_type", "") or "",
                getattr(el, "name", "") or "",
            ])).lower()
            if any(w in haystack for w in target_words):
                return True
        return False

    def _build_unified_prompt(
        self,
        task: str,
        history,
        world_state: Optional[WorldState] = None,
        training_mode: bool = False,
    ) -> str:
        """Build a compact prompt for unified vision+decision models.

        Shorter prompts = better detection accuracy. Only include what the model
        needs to pick the next action.
        """
        # Last 3 actions only — enough for context, not enough to overwhelm
        done_lines = ""
        pivot_block = ""
        if history:
            recent = history[-3:]
            steps = []
            for h in recent:
                status = "FAIL" if h.failed else "OK"
                desc = h.action.text or h.action.target_description or str(h.action.keys or "")
                steps.append(f"  {h.action.action_type}: {desc} [{status}]")
            done_lines = "Done:\n" + "\n".join(steps) + "\n"

            # Fire the pivot at 2 identical actions, not 3. With it at 3, the
            # loop_breaker (which also triggers at 3) had already aborted by
            # the time the LLM would have seen this warning — so the warning
            # never reached the model. At 2 identical, the LLM gets the
            # warning on the iteration BEFORE the loop_breaker fires, giving
            # it one chance to actually pivot before we abort the task.
            if len(history) >= 2:
                last_full = [
                    (h.action.action_type, h.action.target_description, h.action.text)
                    for h in history[-2:]
                ]
                if len(set(last_full)) == 1:
                    a_type, a_target, a_text = last_full[0]
                    a_desc = a_target or a_text or ""
                    # Gemma4:e4b ignored a soft "you must pick something
                    # different" — it acknowledged the warning and scrolled
                    # again anyway. Replace the abstract instruction with
                    # explicit options the model can copy. If it can't
                    # deviate even from concrete choices, that's a model
                    # ceiling, not a prompt problem.
                    pivot_block = (
                        f"STOP. \"{a_type}: {a_desc}\" already failed TWICE in a row. "
                        f"Doing it a third time will hard-abort the task — you will not "
                        f"reach the goal by repeating this action.\n"
                        f"YOUR NEXT ACTION MUST BE EXACTLY ONE OF THESE:\n"
                        f"  • {{\"action\": \"hotkey\", \"keys\": [\"Escape\"], \"reasoning\": \"release focus from search/address bar so scroll reaches page\"}}\n"
                        f"  • {{\"action\": \"hotkey\", \"keys\": [\"Home\"], \"reasoning\": \"jump to top of page\"}}\n"
                        f"  • {{\"action\": \"hotkey\", \"keys\": [\"End\"], \"reasoning\": \"jump to bottom\"}}\n"
                        f"  • {{\"action\": \"hotkey\", \"keys\": [\"Page_Up\"], \"reasoning\": \"page up\"}}\n"
                        f"  • {{\"action\": \"hotkey\", \"keys\": [\"Page_Down\"], \"reasoning\": \"page down\"}}\n"
                        f"  • {{\"action\": \"click\", \"target_description\": \"comment input field\", \"reasoning\": \"focus the textarea directly\"}}\n"
                        f"  • {{\"action\": \"click\", \"target_description\": \"reply button\", \"reasoning\": \"open reply UI\"}}\n"
                        f"Pick one. Do not pick {a_type} again.\n\n"
                    )


        desktop_state = AgentControlService._get_desktop_state()

        # DOM metadata — interactive elements with screen coordinates.
        # Snapshot is refreshed by execute_task() before this call so the
        # click-time guard sees the same elements the LLM saw.
        dom_block = ""
        if self._dom_snapshot is not None:
            try:
                from backend.services.dom_metadata_extractor import DOMMetadataExtractor
                dom_block = DOMMetadataExtractor.format_for_prompt(self._dom_snapshot) + "\n\n"
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
        world_block = self._format_world_state_for_prompt(world_state or self._world_state)

        return f"""{pivot_block}Task: {task}

{desktop_state}
{world_block}
{dom_block}{done_lines}{training_override}Step {len(history) + 1}. ONE next action. After Act the system ALWAYS re-captures the screen (re-See) before your next Think. {confidence}

target_description rules: SHORT label, ≤4 words, one distinctive adjective. Examples: "orange Firefox button", "chat input field", "Send button", "desktop background". NOT "the orange Firefox flame in the top-left Shortcuts panel" — multi-clause descriptions break the vision detector and land at (0,0).

done rule: when action="done", success_proof MUST describe the visible state that proves the task is complete (e.g. "cursor inside text area", "comment now visible in thread"). Empty or generic ("n/a", "task done") is rejected. This rule applies to all models and paths.

Reply ONLY with JSON:
{{"action": "click|right_click|type|hotkey|scroll|wait|done", "target_description": "...", "text": "literal value only", "keys": ["ctrl","t"], "reasoning": "why", "success_proof": "visible state proving done (only when action=done)"}}"""

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

    def _wait_until_visible(
        self,
        target_description: str,
        screen,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.6,
    ) -> Dict[str, Any]:
        """Poll the vision model until the target appears, or timeout.

        Gemma4 + Gemini's "Verified Sequence" gate at major transitions:
        rather than ``time.sleep(4)`` after launching Firefox and praying,
        ask the small VLM "is the URL bar visible?" until it says yes.
        Cheaper than the full brain; correct under network/render lag.
        """
        import time as _time
        from backend.utils.vision_analyzer import VisionAnalyzer

        analyzer = VisionAnalyzer()
        deadline = _time.monotonic() + timeout_s
        polls = 0
        last_err = None

        while _time.monotonic() < deadline:
            polls += 1
            try:
                img, _ = screen.capture()
                # Tight prompt keeps latency low — yes/no with one-line justification.
                result = analyzer.analyze(
                    img,
                    prompt=(
                        f"Is the following visible on this screen RIGHT NOW: \"{target_description}\"?\n"
                        "Answer with EXACTLY one word on the first line: yes or no.\n"
                        "Do not guess — only say yes if you can actually see it in the image."
                    ),
                    num_predict=8,
                    temperature=0.0,
                )
                if result.success:
                    answer = (result.description or "").strip().lower()
                    first_word = answer.split()[0] if answer.split() else ""
                    if first_word.startswith("yes"):
                        elapsed = timeout_s - max(0.0, deadline - _time.monotonic())
                        logger.info(
                            f"[AGENT][GATE] visible: \"{target_description}\" "
                            f"after {polls} polls ({elapsed:.1f}s)"
                        )
                        return {
                            "success": True,
                            "action": "wait_until_visible",
                            "target": target_description,
                            "polls": polls,
                        }
                else:
                    last_err = result.error
            except Exception as e:
                last_err = str(e)
                logger.warning(f"[AGENT][GATE] vision poll failed: {e}")
            _time.sleep(poll_interval_s)

        logger.warning(
            f"[AGENT][GATE] target NOT visible within {timeout_s}s: \"{target_description}\" "
            f"({polls} polls; last_err={last_err})"
        )
        return {
            "success": False,
            "action": "wait_until_visible",
            "target": target_description,
            "error": f"target not visible within {timeout_s}s",
            "polls": polls,
        }

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
                # Legacy timer wait — kept for back-compat but flagged. Recipes
                # should migrate to wait_until_settled / wait_until_visible so
                # they don't blindly fire on slow networks.
                seconds = step.get("seconds", 0.5)
                logger.warning(
                    f"[AGENT][RECIPE] {name}: legacy timer wait({seconds}s) — "
                    "migrate to wait_until_settled/wait_until_visible"
                )
                _time.sleep(seconds)
                continue

            if action_type == "wait_until_settled":
                # Cheap pixel-delta gate — wait until the screen actually
                # finishes painting before firing the next action.
                timeout_s = float(step.get("timeout_s", 5.0))
                stable_for_ms = int(step.get("stable_for_ms", 200))
                result = screen.wait_until_settled(
                    timeout_s=timeout_s, stable_for_ms=stable_for_ms,
                )
                action_steps.append(ActionStep(
                    iteration=step_num, scene_description=f"recipe:{name}",
                    action=AgentAction(action_type="wait_until_settled"),
                    result=result, failed=not result.get("success", False),
                ))
                step_num += 1
                continue

            if action_type == "wait_until_visible":
                # Vision-driven gate — block until target shows up on screen.
                # Use this AFTER major transitions (page load, app launch).
                target = step.get("target_description", "")
                timeout_s = float(step.get("timeout_s", 8.0))
                result = self._wait_until_visible(
                    target, screen, timeout_s=timeout_s,
                )
                action_steps.append(ActionStep(
                    iteration=step_num, scene_description=f"recipe:{name}",
                    action=AgentAction(
                        action_type="wait_until_visible",
                        target_description=target,
                    ),
                    result=result, failed=not result.get("success", False),
                ))
                # If we couldn't see the prerequisite, don't blindly fire the rest.
                if not result.get("success", False):
                    logger.warning(
                        f"[AGENT][RECIPE] {name}: aborting — "
                        f"prerequisite not visible: \"{target}\""
                    )
                    break
                step_num += 1
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

        failed_steps = [s for s in action_steps if s.failed]
        all_steps_ok = len(failed_steps) == 0

        # Final-state verify — Gemma4 + Gemini's mandatory cure for the
        # "celebrate with hallucinations" loop. If the recipe declares a
        # success_proof, the run cannot be reported successful until the
        # vision model confirms that proof is on screen. No more reporting
        # intent as reality.
        proof = recipe.get("success_proof")
        proof_failed = False
        if all_steps_ok and proof:
            verify_timeout = float(recipe.get("success_proof_timeout_s", 8.0))
            verify = self._wait_until_visible(
                proof, screen, timeout_s=verify_timeout,
            )
            action_steps.append(ActionStep(
                iteration=step_num, scene_description=f"recipe:{name}:verify",
                action=AgentAction(
                    action_type="wait_until_visible",
                    target_description=proof,
                ),
                result=verify, failed=not verify.get("success", False),
            ))
            if not verify.get("success", False):
                proof_failed = True
                logger.warning(
                    f"[AGENT][RECIPE] {name}: steps reported OK but final verify "
                    f"FAILED — '{proof}' not visible"
                )

        all_succeeded = all_steps_ok and not proof_failed
        elapsed = _time.time() - start

        if failed_steps:
            logger.warning(f"[AGENT][RECIPE] {name} had {len(failed_steps)} failed step(s)")
        logger.info(
            f"[AGENT][RECIPE] {name} complete in {elapsed:.1f}s "
            f"({len(action_steps)} actions, success={all_succeeded})"
        )

        if not all_succeeded:
            # Any recipe miss should degrade into adaptive see-think-act, not a
            # hard failure. Recipes are shortcuts, never the only path.
            logger.warning(
                f"[AGENT][RECIPE] {name}: falling back to adaptive loop "
                f"(proof_failed={proof_failed}, failed_steps={len(failed_steps)})"
            )
            self._recipe_fallback_note = (
                f"recipe_fallback:{name},proof_failed={proof_failed},failed_steps={len(failed_steps)}"
            )
            return None

        reason = f"recipe:{name}"

        self._action_history = action_steps
        return AgentResult(
            success=all_succeeded,
            reason=reason,
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
        knowledge — compact facts plus recipe index plus distilled lessons.
        Routed via Ollama's system role so it doesn't compete with the
        per-step user prompt for action-format conditioning. This is the
        cross-session memory slot: anything in here survives reboots and
        primes every decision.
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
        lessons = cls._load_lesson_memories()
        if lessons:
            parts.append(lessons)
        return "\n\n".join(parts)

    @staticmethod
    def _load_lesson_memories(max_rows: int = 6, max_chars: int = 2500) -> str:
        """Pull distilled lessons + user-curated memories out of AgentMemory
        and format them as readable text for the system prompt.

        Filters out source='learned_from_feedback' — the deprecated junk
        distiller's first-person reflections (deprecated 2026-05-05, see
        agent_control_api._distill_pearl_memory). Only lesson_summary
        (End-Lesson structured distillations) and manual (user-typed
        memories) are kept.

        lesson_summary content is stored as JSON ({title, steps,
        parameters}); parsed and rendered as markdown bullets so the
        system message stays JSON-shape-clean (the model sees competing
        JSON shapes and starts hallucinating selectors).
        """
        try:
            from backend.models import AgentMemory
        except Exception as e:
            logger.debug(f"AgentMemory import failed in lesson loader: {e}")
            return ""

        try:
            rows = (
                AgentMemory.query
                .filter(AgentMemory.source.in_(["lesson_summary", "manual"]))
                .order_by(AgentMemory.importance.desc(), AgentMemory.id.desc())
                .limit(max_rows)
                .all()
            )
        except Exception as e:
            logger.debug(f"AgentMemory query failed in lesson loader: {e}")
            return ""

        if not rows:
            return ""

        sections = []
        total = 0
        for row in rows:
            content = (row.content or "").strip()
            if not content:
                continue
            block = ""
            if row.source == "lesson_summary":
                # Try parse-as-JSON first; fall back to raw text on failure.
                import json as _json
                try:
                    payload = _json.loads(content)
                    title = (payload.get("title") or "Lesson").strip()
                    steps = payload.get("steps") or []
                    step_lines = []
                    for s in steps:
                        if isinstance(s, dict):
                            text = (s.get("text") or s.get("step") or "").strip()
                        else:
                            text = str(s).strip()
                        if text:
                            step_lines.append(f"  {len(step_lines)+1}. {text[:200]}")
                    if step_lines:
                        block = f"### {title}\n" + "\n".join(step_lines)
                except Exception:
                    block = f"### Lesson\n{content[:600]}"
            else:  # manual
                block = f"- {content[:400]}"
            if not block:
                continue
            if total + len(block) > max_chars:
                break
            sections.append(block)
            total += len(block) + 2

        if not sections:
            return ""
        return "## Lessons & Notes (cross-session memory — apply when relevant)\n" + "\n\n".join(sections)

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

    def _build_world_state(self, cursor_pos: Tuple[int, int], scene_hint: str = "") -> WorldState:
        """Construct the grounded state packet for the current loop iteration."""
        last_action = ""
        last_action_status = ""
        if self._action_history:
            step = self._action_history[-1]
            detail = step.action.target_description or step.action.text or ""
            last_action = f"{step.action.action_type} {detail}".strip()
            last_action_status = "FAIL" if step.failed else "OK"

        dom_url = ""
        dom_title = ""
        dom_count = 0
        if self._dom_snapshot is not None:
            dom_url = getattr(self._dom_snapshot, "url", "") or ""
            dom_title = getattr(self._dom_snapshot, "title", "") or ""
            dom_count = len(getattr(self._dom_snapshot, "elements", []) or [])
        signal = self._last_progress_signal or ProgressSignal()
        blocked = self._format_strategy_cooldowns()
        learned_hint = self._best_recovery_hint(signal.label)
        current_subgoal, next_subgoal = self._infer_subgoals(
            task=self._current_task or "",
            signal=signal,
            history=self._action_history,
        )
        next_hint = signal.next_hint
        if learned_hint:
            next_hint = (
                f"{next_hint}; learned recovery: {learned_hint}"
                if next_hint
                else f"learned recovery: {learned_hint}"
            )

        return WorldState(
            timestamp_iso=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            desktop_state=self._get_desktop_state(),
            dom_url=dom_url,
            dom_title=dom_title,
            dom_element_count=dom_count,
            cursor_pos=cursor_pos,
            last_action=last_action,
            last_action_status=last_action_status,
            scene_hint=(scene_hint or "")[:220],
            progress_label=signal.label,
            progress_confidence=signal.confidence,
            progress_evidence=signal.evidence,
            progress_next_hint=next_hint,
            learned_recovery_hint=learned_hint,
            blocked_actions=blocked,
            current_subgoal=current_subgoal,
            next_subgoal=next_subgoal,
            subgoal_completion_signal=signal.evidence,
        )

    @staticmethod
    def _is_failure_label(label: str) -> bool:
        return label in {
            "target_not_visible",
            "completion_unproven",
            "input_not_applied",
            "click_no_effect",
            "scroll_no_effect",
            "action_failed",
        }

    def _record_failure_label(self, label: str) -> None:
        if self._is_failure_label(label):
            self._pending_failure_label = label

    def _action_recovery_key(self, action: AgentAction) -> str:
        action_type = (action.action_type or "").strip().lower()
        if action_type == "hotkey" and action.keys:
            return f"hotkey:{'+'.join(action.keys[:2])}"
        if action_type in ("click", "right_click"):
            target = (action.target_description or "").strip().lower()
            return f"{action_type}:{target[:24]}" if target else action_type
        return action_type or "unknown"

    def _record_recovery_memory(self, signal: ProgressSignal, action: AgentAction, failed: bool) -> None:
        """Learn what action tended to recover from specific failure labels."""
        if failed:
            self._record_failure_label(signal.label)
            return

        action_type = (action.action_type or "").strip().lower()
        if action_type in ("", "done", "wait"):
            return
        if not self._pending_failure_label:
            return
        bucket = self._recovery_memory.setdefault(self._pending_failure_label, {})
        key = self._action_recovery_key(action)
        bucket[key] = bucket.get(key, 0) + 1
        self._pending_failure_label = ""

    def _best_recovery_hint(self, current_label: str) -> str:
        """Return highest-signal learned recovery for the current failure label."""
        label = current_label if self._is_failure_label(current_label) else self._pending_failure_label
        if not label:
            return ""
        bucket = self._recovery_memory.get(label) or {}
        if not bucket:
            return ""
        best_action, count = max(bucket.items(), key=lambda kv: kv[1])
        if count < 2:
            # Require at least 2 wins before we tell the model to trust it.
            return ""
        return f"after {label}, {best_action} worked ({count}x)"

    def _task_milestones(self, task: str) -> List[str]:
        """Generate lightweight milestone templates from task intent."""
        task_lc = (task or "").lower()
        if any(w in task_lc for w in ("open ", "navigate", "go to", "url", "http", "www", "browser")):
            return [
                "establish browser/app focus",
                "reach destination view",
                "perform requested interaction",
                "verify visible completion state",
            ]
        if any(w in task_lc for w in ("type", "write", "comment", "reply", "email", "message", "post")):
            return [
                "focus intended input area",
                "enter requested content",
                "submit or apply the content",
                "verify the content is visibly present",
            ]
        return [
            "locate relevant UI region",
            "perform next required interaction",
            "observe visible progress",
            "verify completion evidence",
        ]

    def _infer_subgoals(
        self,
        task: str,
        signal: ProgressSignal,
        history: List[ActionStep],
    ) -> Tuple[str, str]:
        """Infer current/next subgoal from task intent + latest progress."""
        milestones = self._task_milestones(task)
        current_idx = 0 if not history else 1

        if signal.label in ("progress_confirmed", "partial_progress"):
            current_idx = min(current_idx + 1, len(milestones) - 1)
        elif signal.label in ("target_not_visible", "scroll_no_effect"):
            return (
                "recover visibility/focus for target controls",
                milestones[min(1, len(milestones) - 1)],
            )
        elif signal.label == "completion_unproven":
            return (
                "produce concrete completion evidence",
                "re-check completion with explicit visible proof",
            )

        current = milestones[current_idx]
        next_goal = milestones[min(current_idx + 1, len(milestones) - 1)]
        return current, next_goal

    def _tick_strategy_cooldowns(self) -> None:
        """Age out temporary action-class blocks."""
        if not self._strategy_cooldowns:
            return
        updated: Dict[str, int] = {}
        for action_type, remaining in self._strategy_cooldowns.items():
            next_remaining = int(remaining) - 1
            if next_remaining > 0:
                updated[action_type] = next_remaining
        self._strategy_cooldowns = updated

    def _record_strategy_outcome(self, action: AgentAction, failed: bool) -> None:
        """Track repeated failed strategies and apply short cooldowns."""
        action_type = (action.action_type or "").strip().lower()
        if action_type in ("", "done", "wait"):
            return
        if failed:
            if self._last_failed_strategy == action_type:
                self._same_strategy_failures += 1
            else:
                self._last_failed_strategy = action_type
                self._same_strategy_failures = 1
            if self._same_strategy_failures >= 2:
                # Short cooldown so the model must try a different tactic.
                self._strategy_cooldowns[action_type] = max(
                    self._strategy_cooldowns.get(action_type, 0),
                    2,
                )
        else:
            if self._last_failed_strategy == action_type:
                self._last_failed_strategy = ""
                self._same_strategy_failures = 0

    def _format_strategy_cooldowns(self) -> str:
        """Human-readable strategy blocks for prompt context."""
        if not self._strategy_cooldowns:
            return "none"
        items = [
            f"{action_type}:{remaining}"
            for action_type, remaining in sorted(self._strategy_cooldowns.items())
        ]
        return ", ".join(items)

    def _semantic_progress_signal(
        self,
        action: AgentAction,
        result: Dict[str, Any],
        failed: bool,
        pixel_diff: Optional[float],
    ) -> ProgressSignal:
        """Convert low-level verification into an LLM-friendly progress signal."""
        action_type = (action.action_type or "").strip().lower()
        reason = (result.get("reason") or "").strip().lower()
        verified = bool(result.get("verified"))

        if not failed and verified:
            evidence = f"{action_type} verified with visible change"
            if pixel_diff is not None:
                evidence += f" (delta={pixel_diff:.3f})"
            return ProgressSignal(
                label="progress_confirmed",
                confidence=0.95,
                evidence=evidence,
                next_hint="continue toward next visible sub-goal",
            )

        if failed:
            if reason == "no_dom_match":
                return ProgressSignal(
                    label="target_not_visible",
                    confidence=0.95,
                    evidence="target label not found in current DOM snapshot",
                    next_hint="change viewport or choose a currently visible target",
                )
            if reason == "missing_success_proof":
                return ProgressSignal(
                    label="completion_unproven",
                    confidence=0.9,
                    evidence="done was rejected because visible proof was missing",
                    next_hint="perform one more action that creates an obvious visible completion state",
                )
            if action_type == "type":
                return ProgressSignal(
                    label="input_not_applied",
                    confidence=0.85,
                    evidence="typed text did not produce a meaningful visual update",
                    next_hint="focus the intended input first, then type once",
                )
            if action_type in ("click", "right_click"):
                return ProgressSignal(
                    label="click_no_effect",
                    confidence=0.85,
                    evidence="click did not produce visible UI state change",
                    next_hint="pick a different target or reveal a hidden control first",
                )
            if action_type == "scroll":
                return ProgressSignal(
                    label="scroll_no_effect",
                    confidence=0.85,
                    evidence="scroll did not move visible content",
                    next_hint="focus the main pane or use a different navigation action",
                )
            return ProgressSignal(
                label="action_failed",
                confidence=0.8,
                evidence=reason or "action returned unsuccessful result",
                next_hint="choose a different action strategy",
            )

        # Success without explicit verification is still useful, just less certain.
        evidence = "action reported success"
        if pixel_diff is not None:
            evidence += f" (delta={pixel_diff:.3f})"
        return ProgressSignal(
            label="partial_progress",
            confidence=0.65,
            evidence=evidence,
            next_hint="verify with a follow-up action aligned to the goal",
        )

    def _format_world_state_for_prompt(self, world_state: Optional[WorldState]) -> str:
        """Render world-state as short, stable prompt context."""
        if not world_state:
            return ""

        lines = [
            "WorldState:",
            f"- timestamp: {world_state.timestamp_iso}",
            f"- cursor: {world_state.cursor_pos}",
            f"- dom_url: {world_state.dom_url or 'n/a'}",
            f"- dom_title: {world_state.dom_title or 'n/a'}",
            f"- dom_elements: {world_state.dom_element_count}",
            f"- last_action: {world_state.last_action or 'none'} [{world_state.last_action_status or 'n/a'}]",
            f"- progress: {world_state.progress_label or 'unknown'} (conf={world_state.progress_confidence:.2f})",
            f"- progress_evidence: {world_state.progress_evidence or 'n/a'}",
            f"- next_hint: {world_state.progress_next_hint or 'n/a'}",
            f"- learned_recovery: {world_state.learned_recovery_hint or 'none'}",
            f"- blocked_actions: {world_state.blocked_actions or 'none'}",
            f"- current_subgoal: {world_state.current_subgoal or 'n/a'}",
            f"- next_subgoal: {world_state.next_subgoal or 'n/a'}",
            f"- subgoal_signal: {world_state.subgoal_completion_signal or 'n/a'}",
        ]
        if world_state.scene_hint:
            lines.append(f"- scene_hint: {world_state.scene_hint}")
        lines.append("")
        return "\n".join(lines)

    def _build_decision_prompt(self, task, scene, history, world_state: Optional[WorldState] = None):
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
        world_block = self._format_world_state_for_prompt(world_state or self._world_state)

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
{world_block}

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
                decision.action.action_type = "done"
                decision.action.reasoning = data.get("reasoning", "")
                decision.action.success_proof = (data.get("success_proof") or "").strip()
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
