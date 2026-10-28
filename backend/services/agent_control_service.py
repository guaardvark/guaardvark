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
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.step_budget import StepBudget
from backend.utils.clock import utcnow


# ────────────────────────────────────────────────────────────────────────────
# Chat emit-fn handoff: the unified chat engine and agent_task_execute tool
# don't share a call stack — the LLM picks the tool, the registry invokes
# it, and only then do we need to know which session's chat:thinking events
# to emit. Each chat session runs on its own thread (and agent tools are
# SERIAL within a session), so a threading.local is the cleanest bridge.
# ────────────────────────────────────────────────────────────────────────────
_chat_emit_local = threading.local()


def set_chat_emit_fn(fn: Optional[Callable]) -> None:
    """Stash the current chat session's emit_fn for tools that want to stream
    back. Always called paired with a clear in finally — see chat engine."""
    _chat_emit_local.emit_fn = fn
    logger.debug(
        f"[EMIT-HANDOFF][ACS_THREADLOCAL] set_chat_emit_fn fn_id={id(fn) if fn else None} "
        f"thread={threading.get_ident()}"
    )


def set_chat_stop_check(fn: Optional[Callable[[], bool]]) -> None:
    """Stash a callable that says whether this chat turn was stopped, so a tool
    that waits (for the GPU, say) can give up when the person presses Stop."""
    _chat_emit_local.stop_check = fn


def get_chat_stop_check() -> Optional[Callable[[], bool]]:
    return getattr(_chat_emit_local, "stop_check", None)


def get_chat_emit_fn() -> Optional[Callable]:
    fn = getattr(_chat_emit_local, "emit_fn", None)
    logger.debug(
        f"[EMIT-HANDOFF][ACS_THREADLOCAL] get_chat_emit_fn -> fn_id={id(fn) if fn else None} "
        f"thread={threading.get_ident()} present={fn is not None}"
    )
    return fn

from PIL import Image

logger = logging.getLogger(__name__)

# Singleton instance
_service_instance = None


# Patterns in expected_effect that indicate a slow visible change (>3s):
# app launches, navigations, full-page loads, new windows. These get the
# semantic vision-asked verifier with a 12s budget instead of the in-page
# region-diff DPC. Pattern is checked case-insensitively against the
# expected_effect string the LLM produced. Order doesn't matter; any
# match flips to the slow path.
_SLOW_EFFECT_TOKENS = (
    "page loads", "page load", "page opens", "page open",
    "window opens", "window open", "window appears",
    "browser opens", "browser starts", "browser launches",
    "firefox opens", "firefox launches", "firefox starts",
    "app opens", "app launches", "app starts",
    "navigates to", "navigates", "loads to", "loaded",
    "redirects to", "redirected",
    "tab opens", "new tab",
    "dialog appears", "modal appears",
)


def _looks_like_slow_effect(expected_effect: str) -> bool:
    """True when expected_effect mentions an effect that typically takes
    >3s to settle (app launch, page navigation, new window). False for
    in-page UI changes (button states, comment posted, form filled)."""
    if not expected_effect:
        return False
    needle = expected_effect.strip().lower()
    return any(token in needle for token in _SLOW_EFFECT_TOKENS)
_service_lock = threading.Lock()


@dataclass
class AgentControlConfig:
    """Configuration for the agent control loop.

    The loop stops when progress stops (``max_stall_steps``); the step and
    time caps are the safety floor under that, not the normal exit. Until
    2026-09-23 they were the only exit: every task got 15 steps and 120 s
    whatever it needed, so a task with more than a dozen targets could never
    finish, and the loop had no way to notice it was going nowhere before
    the clock ran out.
    """
    # Hard ceiling on steps. Measured 2026-09-23 on the dots task at ~9.3 s a
    # step with the servo's precision probes armed and ~7 s without; the
    # timeout below is sized to it. It is also the cap on the Done list the
    # model is shown (one line per step).
    max_iterations: int = 40
    # Bumped from 3 → 5 so transient screen states (mid-load page, brief
    # black between window switches, vision model spitting bad JSON once)
    # don't kill the loop. The failure counter still resets on every successful
    # action, so this only matters for genuinely-stuck sequences.
    max_consecutive_failures: int = 5
    # Outer safety floor: 40 steps × ~9 s plus the done guard's 10 s waits.
    task_timeout_seconds: int = 480
    # Consecutive successful steps that made no progress (no target the task
    # had not clicked yet, and no verified screen change) before the loop
    # stops as stalled_no_progress. Failed steps have their own guard
    # (max_consecutive_failures) and do not count here. 4: the 2026-09-23
    # dots runs cycled A-B-C-D and A-B-C-D-E in whole rounds; 3 would fire
    # inside one legitimate "open menu, scroll, scroll" sequence.
    max_stall_steps: int = 4
    action_timeout_seconds: int = 60
    verify_actions: bool = True
    grid_cols: int = 8
    grid_rows: int = 8
    bullseye_size: int = 48
    vision_model: str = "gemma4:e4b"
    escalation_model: str = "gemma4:e4b"
    escalation_threshold: int = 3  # failures before escalating
    # Put one line about the last run of the same task text in the decision
    # prompt. Off: in four live dots runs on 2026-09-23 the line was present
    # in both runs that failed and its effect could not be separated from the
    # done guard's rejections, so the episode record ships and the prompt
    # line waits for evidence. The record itself is always written.
    prior_run_note_enabled: bool = False


@dataclass
class AgentAction:
    """A single action the agent wants to perform."""
    # action_type vocabulary:
    #   click, right_click, double_click, triple_click, drag, hover,
    #   type, hotkey, scroll, move, wait, navigate, done
    action_type: str = ""
    target_cell: str = ""  # Grid cell (e.g., "D4") — for clicks
    target_description: str = ""  # What the agent thinks it's clicking
    # For drag actions: description of the DESTINATION. target_description
    # holds the source. Both go through the same servo-locate path.
    drag_to_description: str = ""
    coordinates: Optional[Tuple[int, int]] = None  # Refined pixel coords
    text: str = ""  # For type actions
    keys: List[str] = field(default_factory=list)  # For hotkey actions
    # For draw: the stroke's screen points in order. click_at uses
    # `coordinates` for its one point.
    points: List[Tuple[int, int]] = field(default_factory=list)
    scroll_amount: int = 0  # For scroll actions
    url: str = ""  # For navigate actions
    reasoning: str = ""  # Why the agent chose this action
    confidence: float = 1.0  # Confidence score (0.0 to 1.0)
    expected_effect: str = ""  # Optional visible outcome expected after action
    # When action_type == "done": short, vision-verifiable description of the
    # screen state that proves the task is complete (e.g. "cursor blinking
    # inside the comment text area", "comment now appears in thread"). Empty
    # or trivial values are rejected — see done-handling guard.
    success_proof: str = ""
    # Support for action="tool": allow natural language or direct calls to the full agent toolbox (any registered tool) from within screen/ACS loop.
    # This brings general tools (web, code, media, etc.) together with screen skills/recipes for true natural language control in /agent mode.
    tool_name: str = ""
    tool_params: dict = field(default_factory=dict)


@dataclass
class AgentDecision:
    """The LLM's decision for the current iteration."""
    action: AgentAction = field(default_factory=AgentAction)
    task_complete: bool = False
    stuck: bool = False
    status: str = "IN_PROGRESS"  # INITIAL, IN_PROGRESS, COMPLETE
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


# Reason prefix of an AgentResult from a recipe that refused to run. The task
# ends there instead of falling back to the see-think-act loop.
RECIPE_REFUSED = "recipe_refused"


@dataclass
class AgentResult:
    """Final result of a task execution."""
    success: bool = False
    reason: str = ""
    steps: List[ActionStep] = field(default_factory=list)
    total_time_seconds: float = 0.0
    task: str = ""  # the user-facing task string this result is for
    # True when the agent's final action produced a verified visible effect
    # (servo region-diff DPC OR semantic vision verification, depending on
    # action class — see verifier-selection block in execute_task). Distinct
    # from `success`: `success` reflects loop termination (done/timeout/error);
    # `verified` reflects whether the world actually changed. The induction
    # gate in agent_control_api._induce_candidate_recipe uses `verified` to
    # filter "clicked-but-nothing-happened" runs out of recipe learning.
    verified: bool = False
    # Carries the verification reason forward for logging / debugging.
    verified_reason: str = ""
    # The agent_task_runs row this result was written to ("" when the write
    # was skipped or failed). The episode record; see _persist_task_run.
    run_id: str = ""
    # Notes the user sent while the task ran that arrived after its last
    # step, so the model never read them. The chat reply says so.
    late_notes: List[str] = field(default_factory=list)


@dataclass
class AgentTaskContext:
    """Ownership token for one execute_task run.

    The service is a singleton, but task execution is threaded. This keeps an
    older task from clearing or overwriting a newer one after a slow vision call.
    """
    task_id: str
    task: str
    started_at: float
    killed: bool = False


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


@dataclass
class FailureReport:
    """Structured evidence for a failed step, fed back into the THINK prompt.

    Replaces the thin "previous attempt failed" signal with concrete data
    the model can reason about: what it tried, what the servo aimed at,
    how the screen reacted, whether the target was even visible. The
    cause_hypothesis is the loop's best guess based on the other fields.
    """
    iteration: int = 0
    action_type: str = ""              # click | type | hotkey | scroll | wait | done
    expected_target: str = ""          # what the model named
    attempted_at_coords: Tuple[int, int] = (0, 0)  # (0,0) for non-click actions
    screen_delta: Optional[float] = None  # post-action pixel diff; None when never measured (e.g. click branch)
    visibility_check: str = ""         # "" until Phase-3 re-grounding fills it
    dom_match: bool = False            # did DOM extraction find a matching element?
    cause_hypothesis: str = ""         # one-liner derived from the other fields


@dataclass
class Expectation:
    """One belief about what should be visible on screen this session.

    Phase 4 of see-think-act-remember. The agent's knowledge files claim
    "the desktop has these icons; the launcher menu lives in the top-left."
    Each such claim is an Expectation — a hypothesis we test against the
    fresh WORLD_OBSERVED block from Phase-3 re-grounding. When the
    observation contradicts the expectation (claimed visible, not seen),
    a row gets appended to _expectation_log; at task end the log distils
    into one-line lessons that persist as belief_update memories.

    source + source_line carry provenance so Phase 5 can later propose
    a permanent edit to the knowledge file. source="model_belief" means
    the model invented the element on its own (it's not in any doc); we
    still record it as evidence for the next session prompt, but Phase 5
    has nothing to edit so it skips those rows.
    """
    element: str = ""                  # short name of the claimed element
    expected_visible: bool = True
    observed_visible: bool = False
    source: str = ""                   # "self_knowledge_compact.md" | "model_belief" | ...
    source_line: Optional[int] = None  # line in source file, None for model_belief
    confidence: float = 0.5            # 0..1; how strongly the source asserted it


@dataclass(frozen=True)
class BrainEye:
    """Who decides and who looks, for one task.

    brain   the model that reads the scene and chooses the next action
    eye     the model that looks at the screen and points
    unified brain is eye and can drive the screen on its own
    """
    brain: str
    eye: str
    unified: bool
    eye_mechanism: str   # native | sibling_vlm | none
    reason: str


def _read_saved_active_model() -> str:
    """The user's chosen chat model, without needing Flask or the database."""
    try:
        from backend.config import _read_saved_model_name
        name = _read_saved_model_name()
        if name:
            return name
    except Exception:
        pass
    try:
        from backend.models import get_active_model_name
        name = get_active_model_name()
        if name and name != "default_model_name":
            return name
    except Exception:
        pass
    return ""


def _eye_accuracy_px(eye_model: str, screen_w: int, screen_h: int):
    """Median pointing error for this eye: measured on this machine, else the
    shipped measurement for the installed build, else None. The correction
    loop sizes its search from it; read from the local store alone, a fresh
    clone searched ±115px around an eye that misses by 255."""
    try:
        from backend.services.model_capability_resolver import accuracy_px
        return accuracy_px(eye_model, (screen_w, screen_h))
    except Exception:
        return None


def build_servo(screen, eye_model: str, collector=None, config_overlay: Optional[dict] = None):
    """The one way to build a servo: eye-keyed config plus measured accuracy.

    Both the agent loop and the chat side's fallback click construct their
    servo here, so they cannot drift apart again (they had: the chat path was
    building a different eye with a different config than the loop).
    """
    from backend.services.servo_knowledge_store import get_vision_config
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services.servo_controller import ServoController
    cfg = dict(get_vision_config(eye_model))
    cfg.update(config_overlay or {})
    analyzer = VisionAnalyzer(default_model=eye_model)
    w, h = screen.screen_size()
    acc = cfg.pop("eye_accuracy_px", None)
    if acc is None:
        acc = _eye_accuracy_px(eye_model, w, h)
    try:
        from backend.services.model_capability_resolver import judge_rate
        judge = judge_rate(eye_model, (w, h))
    except Exception:
        judge = None
    servo = ServoController(screen, analyzer, collector=collector, vision_config=cfg,
                            eye_accuracy_px=acc, eye_judge_rate=judge)
    return analyzer, servo


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
        self._active_task_id: str = ""
        self._task_context: Optional[AgentTaskContext] = None
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
        # Per task: how often the eye looked for a click target and found
        # nothing (keyed by _target_key), and the targets held back from
        # clicking until the screen next changes (key -> name as written).
        self._not_found_counts: Dict[str, int] = {}
        self._banned_targets: Dict[str, str] = {}
        # Per task: obstacles of each type handled or escalated by ASSESS.
        self._obstacle_counts: Dict[str, int] = {}
        # Where the field being typed into was clicked; the typed text has to
        # show up near it.
        self._field_point: Optional[Tuple[int, int]] = None
        self._pending_failure_label: str = ""
        self._recovery_memory: Dict[str, Dict[str, int]] = {}
        # Rolling window of structured failure reports for prompt context.
        # Replaces the model's "previous attempt failed" guess with concrete
        # evidence: what it tried, where the servo aimed, screen reaction,
        # whether the target was even visible. Capped to last N to keep the
        # prompt bounded — old failures rot in usefulness anyway.
        self._failure_reports: List[FailureReport] = []
        self._failure_reports_cap: int = 10
        # Counter of consecutive same-target failures, used by Phase-3
        # re-grounding to fire once per "stuck cluster" instead of every step.
        self._stuck_target: str = ""
        self._stuck_target_count: int = 0
        # Latest WORLD_OBSERVED block from a re-grounding pass, injected into
        # the next THINK prompt. Cleared after the model has seen it once.
        self._pending_world_observed: str = ""
        # Phase 4: session belief log. Each contradiction between an expected
        # element (from self_knowledge / recipes) and a fresh WORLD_OBSERVED
        # appends a row. Distilled at task end into belief_update memories
        # so the *next* session prompt has the lesson. Cap on writes lives
        # in _distill_lessons (5 per session); this in-memory list is
        # naturally session-scoped — discarded with the service instance.
        self._expectation_log: List[Expectation] = []
        # Cache derived expectations once per session — the knowledge files
        # don't change mid-run, and the parser walks every line. Lazily
        # populated by _derive_session_expectations.
        self._session_expectations: Optional[List[Expectation]] = None
        self._recipe_fallback_note: str = ""
        # One line about the last run of the same task text, or "". Set per
        # task from the agent_task_runs table; rendered above the Done list.
        self._prior_run_note: str = ""
        self._task_session_id: Optional[str] = None
        self._task_started_at: float = 0.0
        self._stall_steps: int = 0
        # Total clicks the current task allows when it states a limit.
        self._click_budget: Optional[int] = None
        # Circuit breaker: track coordinates of issued clicks to detect
        # infinite loops on non-responsive elements.
        self._click_history: List[Tuple[int, int]] = []
        self._lock = threading.Lock()
        # Notes the user sends while a task runs ("chime-ins"): each is
        # {"text", "received_at", "before_step", "seen_at_step"}. Read by the
        # loop at its next step and kept in the prompt for the rest of the
        # task. Guarded by _lock; reset when a task starts.
        self._steer_notes: List[Dict[str, Any]] = []
        # The display's size for the current task, for the point rule and
        # the off-screen check on click_at / draw.
        self._screen_size: Optional[Tuple[int, int]] = None
        # Whether this task is offered click_at and draw (_points_wanted).
        self._points_on = False
        # Closed as a task starts to finish, so a note can't be accepted
        # after the last step that could have read it.
        self._notes_open = False
        self.config = AgentControlConfig()
        self._debug_run_id = ""
        self._emit_fn: Optional[Callable] = None  # set per-task by execute_task
        # Mirror of every chat:thinking event emitted this turn, in {iteration,
        # label, reasoning} shape. unified_chat_engine drains this when saving
        # the assistant message so the trail survives a page refresh.
        self._thinking_steps_buffer: List[Dict[str, Any]] = []
        # What execute_task ran this turn: recipe hits, fallbacks and plain
        # tasks. Drained into the reply's provenance at save time, the same
        # way the thinking trail is, so feedback can reach the recipe.
        self._recipe_usage_buffer: List[Dict[str, Any]] = []

    def _emit_thinking(self, iteration: int, label: str, reasoning: str, kind: str = "") -> None:
        """Stream a per-step reasoning blob to the chat. No-ops when emit_fn unset
        (CLI/tests/legacy callers). Errors are swallowed — the loop must not be
        derailed by a flaky socket."""
        # Always buffer for persistence, even when emit_fn isn't wired — the
        # caller drains this after the loop regardless of socket state.
        entry = {
            "iteration": int(iteration),
            "label": label or "",
            "reasoning": reasoning or "",
        }
        if kind:
            # "note": the user's own message, shown as theirs in the trail.
            entry["kind"] = kind
        self._thinking_steps_buffer.append(entry)
        logger.debug(
            f"[THINKING-PERSIST] emit iter={iteration} label={label!r} "
            f"buffer_len={len(self._thinking_steps_buffer)} id(self)={id(self)}"
        )
        emit = self._emit_fn
        logger.debug(
            f"[EMIT-HANDOFF][ACS_EMIT] _emit_thinking iter={iteration} label={label!r} "
            f"emit_fn_id={id(emit) if emit else None} will_emit_live={emit is not None} "
            f"self_id={id(self)} source=agent_loop"
        )
        if not emit:
            logger.info(f"[SOCKET-CHAT] _emit_thinking SKIPPED (no emit_fn) iter={iteration} label={label!r} -- live chat:thinking will be lost until join or refresh (buffered for drain)")
            return
        try:
            logger.info(f"[SOCKET-CHAT] EMIT via ACS chat:thinking iter={iteration} status={label!r} source=agent_loop")
            payload = {
                "iteration": int(iteration),
                "status": label,
                "reasoning": reasoning or "",
                "source": "agent_loop",
            }
            if kind:
                payload["kind"] = kind
            emit("chat:thinking", payload)
            logger.debug(f"[EMIT-HANDOFF][ACS_EMIT] chat:thinking(source=agent_loop) emitted for iter={iteration}")
        except Exception as e:
            logger.debug(f"_emit_thinking failed (non-fatal): {e}")

    def note_recipe_usage(self, task: str, name: Optional[str] = None, fallback: bool = False) -> None:
        try:
            self._recipe_usage_buffer.append({"task": task, "name": name, "fallback": bool(fallback)})
        except Exception:
            pass

    def drain_recipe_usage(self) -> Dict[str, Any]:
        """Return {"recipe": last recipe used or None, "agent_tasks": [...]} and clear."""
        entries = list(self._recipe_usage_buffer)
        self._recipe_usage_buffer.clear()
        out: Dict[str, Any] = {}
        tasks = [e["task"] for e in entries if e.get("task")]
        if tasks:
            out["agent_tasks"] = tasks
        recipes = [e for e in entries if e.get("name")]
        out["recipe"] = ({"name": recipes[-1]["name"], "fallback": recipes[-1]["fallback"]}
                         if recipes else None)
        return out

    def drain_thinking_steps(self) -> List[Dict[str, Any]]:
        """Return the accumulated thinking steps and clear the buffer.

        Called by unified_chat_engine when persisting the assistant message
        so the trail survives a page refresh.
        """
        steps = list(self._thinking_steps_buffer)
        self._thinking_steps_buffer.clear()
        logger.debug(
            f"[THINKING-PERSIST][EMIT-HANDOFF] drain returning {len(steps)} steps id(self)={id(self)} "
            f"thread={threading.get_ident()}"
        )
        return steps

    @staticmethod
    def _build_action_label(action) -> str:
        """One-line human label for the chat's thinking spinner. Keeps the
        live status bar readable; full reasoning is shipped in the `reasoning`
        field for the trail."""
        kind = getattr(action, "action_type", "") or ""
        target = (getattr(action, "target_description", "") or "").strip()
        text = (getattr(action, "text", "") or "").strip()
        if kind == "click" and target:
            return f"click — {target[:60]}"
        if kind == "click_at" and getattr(action, "coordinates", None):
            x, y = action.coordinates
            return f"click at ({x}, {y})"
        if kind == "draw":
            pts = getattr(action, "points", None) or []
            return f"draw — {len(pts)} points"
        if kind == "type" and text:
            preview = text[:40] + ("…" if len(text) > 40 else "")
            return f"type — {preview!r}"
        if kind == "type":
            return f"type — into {target[:40] or 'focused field'}"
        if kind == "hotkey":
            keys = getattr(action, "keys", None) or []
            return f"hotkey — {'+'.join(keys) or '(none)'}"
        if kind == "scroll":
            amount = int(getattr(action, "scroll_amount", 0) or 0)
            if amount:
                return f"scroll — {'down' if amount < 0 else 'up'} {abs(amount)}"
            return "scroll"
        if kind == "wait":
            return "wait"
        if kind == "done":
            return "done"
        return kind or "thinking"

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
        if self._task_context is not None:
            self._task_context.killed = True
        self._killed = True
        self._active = False
        self._ready = False
        logger.warning("KILL SWITCH ACTIVATED — all agent operations halted")

    # A note that only says stop ends the run, as the Stop button does.
    _STOP_NOTE = re.compile(
        r"^\s*(?:please\s+)?(?:stop|cancel|abort|halt|quit)(?:\s+(?:it|that|this|now|please))*"
        r"\s*[.!]*\s*$",
        re.IGNORECASE,
    )
    _MAX_NOTES = 10
    _MAX_NOTE_CHARS = 500

    def add_steer_note(self, text: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Hand a running task a note from the user without stopping it.

        The loop reads the note before its next step and keeps it in the
        prompt for the rest of the task; the task itself is unchanged. A
        message sent the ordinary way while a task runs starts a new task
        and kills this one, which is what this exists to avoid.

        Returns {"queued": bool, ...}. Not queued when no task is running,
        or the running task belongs to another chat session.
        """
        text = (text or "").strip()
        if not text:
            return {"queued": False, "reason": "empty"}
        stop = False
        with self._lock:
            if not self._active or self._killed or not self._notes_open:
                return {"queued": False, "reason": "no_active_task"}
            if session_id and self._task_session_id and session_id != self._task_session_id:
                return {"queued": False, "reason": "other_session"}
            if self._STOP_NOTE.match(text):
                stop = True
            elif len(self._steer_notes) >= self._MAX_NOTES:
                return {"queued": False, "reason": "too_many_notes"}
            else:
                before_step = self._current_iteration + 2
                self._steer_notes.append({
                    "text": text[:self._MAX_NOTE_CHARS],
                    "received_at": time.time(),
                    "before_step": before_step,
                    "seen_at_step": None,
                })
                count = len(self._steer_notes)
        if stop:
            logger.warning("[AGENT][NOTE] stop note received; stopping the task")
            self.kill()
            return {"queued": True, "stopping": True}
        logger.info(f"[AGENT][NOTE] note {count} queued for step {before_step}: {text[:80]!r}")
        return {"queued": True, "notes": count, "before_step": before_step}

    def _take_new_notes(self, step: int) -> List[Dict[str, Any]]:
        """Notes not yet shown to the model, marked as read at ``step``."""
        with self._lock:
            new = [n for n in self._steer_notes if n["seen_at_step"] is None]
            for n in new:
                n["seen_at_step"] = step
            return [dict(n) for n in new]

    def _has_unseen_notes(self) -> bool:
        with self._lock:
            return any(n["seen_at_step"] is None for n in self._steer_notes)

    def _late_notes(self) -> List[str]:
        with self._lock:
            return [n["text"] for n in self._steer_notes if n["seen_at_step"] is None]

    @staticmethod
    def _is_local_url(url: str) -> bool:
        from urllib.parse import urlparse
        raw = (url or "").strip()
        u = urlparse(raw)
        if u.scheme in ("file", "about", "moz-extension", "chrome"):
            return True
        if "//" not in raw:
            # "localhost:5173/x" or "example.com": a host with no scheme
            u = urlparse("http://" + raw)
        return (u.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")

    def _outside_allowed(self, url: str) -> bool:
        """Web access (Settings) for a navigate. The agent browser refuses
        outside sites itself when it is off (backend/utils/agent_web_gate.py);
        this says so in words before the browser shows an error page."""
        if self._is_local_url(url):
            return True
        try:
            from backend.utils.settings_utils import web_access_block_reason
            with self._app_context():
                return web_access_block_reason("open outside sites") is None
        except Exception:
            return True

    @staticmethod
    def _point_identity(action) -> tuple:
        """Where a click_at or draw went, for telling repeats apart: ten dots at
        ten places are not one action repeated ten times."""
        if action.action_type == "click_at":
            return tuple(action.coordinates or ())
        if action.action_type == "draw":
            return tuple(tuple(p) for p in (action.points or []))
        return ()

    @staticmethod
    def _parse_point(data) -> Optional[Tuple[int, int]]:
        """(x, y) from a reply's "x"/"y", or a [x, y] under "point"/"coordinates"."""
        try:
            if data.get("x") is not None and data.get("y") is not None:
                return int(round(float(data["x"]))), int(round(float(data["y"])))
            for key in ("point", "coordinates"):
                v = data.get(key)
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    return int(round(float(v[0]))), int(round(float(v[1])))
        except (TypeError, ValueError):
            return None
        return None

    @classmethod
    def _parse_points(cls, raw) -> List[Tuple[int, int]]:
        """The points of a draw, as int pairs; malformed entries dropped."""
        out: List[Tuple[int, int]] = []
        for p in (raw if isinstance(raw, list) else [])[:cls._MAX_DRAW_POINTS]:
            try:
                if isinstance(p, dict):
                    out.append((int(round(float(p["x"]))), int(round(float(p["y"])))))
                elif isinstance(p, (list, tuple)) and len(p) == 2:
                    out.append((int(round(float(p[0]))), int(round(float(p[1])))))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    # A task that gives coordinates ("(420, 320)", "x=420") or is about
    # drawing. Only those are offered click_at and draw: offered to every
    # task, Ornith-1.5 35B deciding blind from the eye's description used
    # click_at with guessed pixels on the five-dots page and hit 3 of 5
    # (2026-10-02), where naming the dots for the eye hits 5 of 5.
    _POINTS_WANTED = re.compile(
        r"\(\s*\d{1,4}\s*,\s*\d{1,4}\s*\)|\b[xy]\s*[=:]\s*\d{1,4}|\bclick_at\b|"
        r"\b(?:draw(?:s|ing|n)?|sketch\w*|doodle\w*|scribble\w*|canvas\w*|paint\w*|pencil\w*|"
        r"brush\w*|strokes?)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _points_wanted(cls, text: str) -> bool:
        return bool(cls._POINTS_WANTED.search(text or ""))

    @property
    def _points_allowed(self) -> bool:
        return bool(getattr(self, "_points_on", False))

    def _schema_full(self) -> str:
        return self._SCHEMA_FULL_POINTS if self._points_allowed else self._SCHEMA_FULL

    def _point_block(self) -> str:
        return self._point_rule() + "\n\n" if self._points_allowed else ""

    def _point_rule(self) -> str:
        size = getattr(self, "_screen_size", None)
        return self._POINT_RULE.format(
            size=f"; the screen is {size[0]}x{size[1]}" if size else "")

    def _steer_block(self) -> str:
        """Every note the user sent during this task, for the prompt."""
        with self._lock:
            notes = [dict(n) for n in self._steer_notes if n["seen_at_step"] is not None]
        if not notes:
            return ""
        newest = max(n["seen_at_step"] for n in notes)
        lines = []
        for n in notes:
            mark = "NEW " if n["seen_at_step"] == newest else ""
            lines.append(f"  - {mark}(read at step {n['seen_at_step']}) \"{n['text']}\"")
        return (
            "NOTES FROM THE USER, sent while you were working, oldest first. They come from "
            "the person who gave you the task and they update it: where a note and the task "
            "disagree, follow the note. Keep going; do not start the task over unless a note "
            "says to.\n" + "\n".join(lines) + "\n\n"
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current agent control status."""
        last = None
        if self._last_result:
            last = {"success": self._last_result.success, "reason": self._last_result.reason,
                     "steps": len(self._last_result.steps), "time": self._last_result.total_time_seconds,
                     "run_id": self._last_result.run_id}
        return {
            "active": self._active,
            "ready": self._ready,
            "killed": self._killed,
            "learning": self._learning,
            "current_demonstration_id": self._current_demonstration_id,
            "current_task": self._current_task,
            "active_task_id": self._active_task_id,
            "iteration": self._current_iteration,
            "history_length": len(self._action_history),
            "last_result": last,
        }

    def execute_task(self, task: str, screen, mouse_only: bool = False, training_mode: bool = False,
                     emit_fn: Optional[Callable] = None, chat_context: str = "", max_steps: Optional[int] = None,
                     budget: Optional[StepBudget] = None, correction_mode: Optional[str] = None,
                     session_id: Optional[str] = None) -> AgentResult:
        """
        Execute a task using the see-think-act loop.

        session_id: the chat session that asked, when there is one; stored on the
                task's episode row (agent_task_runs) so a run can be traced back to
                the conversation.

        correction_mode: off | shadow | on for this task's servo, overriding the
                environment and the reflex; None leaves that precedence alone. The
                off/shadow/on measurement runs set it per request, no restart.

        budget: preferred explicit StepBudget from AgentBrain (carries cross-tier history and
                remaining count). If provided, takes precedence for capping.
        max_steps: legacy int form of the cross-tier termination budget (still supported for
                   callers that don't pass the full object).

        The budget is the "solidification" mechanism: the agentic loop now has a consistent
        inherited cap across Reflex/Instinct/Deliberation/GemmaDirect paths.

        Args:
            task: Natural language description of the task
            screen: ScreenInterface implementation
            mouse_only: If True, disable keyboard shortcuts — pure mouse clicks only
            training_mode: If True, keep clicking forever — no early done, no loop breaker,
                          extended iterations and timeout. For vision trainer practice.
            emit_fn: Optional callback (event_name, payload_dict) for streaming the
                    loop's per-step reasoning back to the chat. When set, the loop
                    fires `chat:thinking` after each [THINK] decision so the user
                    sees the agent's reasoning live instead of digging through logs.

        Returns:
            AgentResult with success status and action history
        """
        self._mouse_only = mouse_only
        self._emit_fn = emit_fn
        self._training_mode = training_mode
        logger.debug(
            f"[EMIT-HANDOFF][ACS_EXECUTE] execute_task received emit_fn_id={id(emit_fn) if emit_fn else None} "
            f"for task={task[:60]!r} thread={threading.get_ident()} self_id={id(self)}"
        )
        from backend.services.servo_controller import ServoController
        from backend.services.training_data_collector import TrainingDataCollector
        from backend.utils.vision_analyzer import VisionAnalyzer

        task_id = f"task-{int(time.time() * 1000)}-{threading.get_ident()}"
        task_ctx = AgentTaskContext(task_id=task_id, task=task, started_at=time.time())

        def finish(result: AgentResult) -> AgentResult:
            with self._lock:
                owns_notes = self._active_task_id == task_id
                if owns_notes:
                    self._notes_open = False
            result.late_notes = self._late_notes() if owns_notes else []
            # Derive `verified` from the last step's per-action verifier result.
            # The induction gate in agent_control_api uses this to filter
            # "clicked but nothing happened" runs out of candidate-recipe
            # learning (response_2026-05-19 §C). For non-success runs we
            # leave verified=False (default).
            if result.success and result.steps:
                last_step = result.steps[-1]
                last_action_kind = getattr(last_step.action, "action_type", "")
                if last_action_kind == "done":
                    # "done" itself isn't a screen-changing action; the
                    # verification belongs to the PRECEDING step. Find the
                    # last non-done step and use its verifier outcome.
                    for st in reversed(result.steps[:-1]):
                        if getattr(st.action, "action_type", "") != "done":
                            result.verified = bool(st.result.get("verified", False))
                            result.verified_reason = st.result.get("verifier", "")
                            break
                else:
                    result.verified = bool(last_step.result.get("verified", False))
                    result.verified_reason = last_step.result.get("verifier", "")
            return self._store_and_return(result, task_id=task_id, task=task)

        with self._lock:
            if self._active:
                # New task supersedes old one — kill the stale task so its
                # see-think-act loop exits on the next _killed check.
                old_task = self._current_task
                logger.warning(f"[AGENT] Killing stale task \"{old_task}\" to start new task \"{task}\"")
                if self._task_context is not None:
                    self._task_context.killed = True
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
            self._active_task_id = task_id
            self._task_context = task_ctx
            self._current_task = task
            self._current_iteration = 0
            self._action_history = []
            self._click_budget = self._click_budget_from_task(task)
            self._recipe_fallback_note = ""
            self._prior_run_note = ""
            self._task_session_id = session_id
            self._task_started_at = time.time()
            self._steer_notes = []
            self._notes_open = True
            # Phase 4: session state is task-scoped. Re-parse the knowledge
            # files in case they were edited between runs; reset the log so
            # last task's contradictions don't bleed into this one's lessons.
            self._expectation_log = []
            self._session_expectations = None
            self._click_history = []
            # Blocks and miss counts belong to the task that earned them.
            self._strategy_cooldowns = {}
            self._last_failed_strategy = ""
            self._same_strategy_failures = 0
            self._not_found_counts = {}
            self._banned_targets = {}
            self._obstacle_counts = {}
            self._field_point = None

        # Attempts per click target this task: the second try at the same
        # target asks the servo for precision (arms its correction loop).
        self._click_attempts: Dict[str, int] = {}
        # The stuck target is per task. Left over, a Google task's "first
        # organic search result" was re-ground into every prompt of the dots
        # task that followed it.
        self._stuck_target = ""
        self._stuck_target_count = 0
        self._failure_reports = []
        # Who thinks, who looks. Resolved once per task; the loop reads it.
        try:
            self._screen_size = tuple(screen.screen_size())
        except Exception:
            self._screen_size = None
        self._points_on = self._points_wanted(task)
        self._brain_eye = self.resolve_brain_eye(screen_size=self._screen_size)
        if not self._brain_eye.eye:
            logger.error(f"[AGENT] no drivable eye: {self._brain_eye.reason}")
            return finish(AgentResult(success=False, reason=f"no_drivable_eye: {self._brain_eye.reason}",
                                      task=task))
        logger.info(
            "[AGENT] Brain: %s (%s) | Eye: %s (%s) — %s",
            self._brain_eye.brain, "sighted" if self._brain_eye.unified else "blind",
            self._brain_eye.eye, self._brain_eye.eye_mechanism, self._brain_eye.reason,
        )
        collector = TrainingDataCollector()
        analyzer, servo = build_servo(
            screen, self._brain_eye.eye, collector=collector,
            config_overlay=({"correction_mode": correction_mode} if correction_mode else None),
        )
        if training_mode:
            # Training sessions get TRUE hit/miss labels from the trainer
            # page's own scoreboard (probe is inert when the page isn't the
            # trainer or BiDi isn't up). This is what un-lies the learning
            # data — DPC counts a miss-marker as "screen changed".
            try:
                from backend.services.trainer_truth_probe import TrainerTruthProbe
                servo.truth_probe = TrainerTruthProbe()
                logger.info("[AGENT][TRUTH] trainer truth probe attached for training session")
            except Exception as e:
                logger.debug(f"[AGENT][TRUTH] probe unavailable: {e}")
        consecutive_failures = 0
        start_time = time.time()
        self._debug_run_id = f"run-{int(start_time * 1000)}"

        # Meta/memory tasks ("save your learnings to memory") are NOT screen
        # tasks — there is nothing to click and no visible proof, so forcing
        # them through the see-think-act loop guarantees a done-proof reject
        # spiral (observed live: 3× "proof not visible" → 60s timeout). Handle
        # them here: flush session beliefs via the existing lesson machinery
        # and return honestly. Lessons ALSO auto-persist at every task exit
        # (the finish chokepoint), so this is a flush + acknowledgement, not a
        # new storage path — LEARNING_PRINCIPLES-compliant.
        meta_result = self._try_meta_memory_task(task)
        if meta_result is not None:
            with self._lock:
                if self._active_task_id == task_id:
                    self._active = False
            meta_result.total_time_seconds = time.time() - start_time
            return finish(meta_result)

        # Check for recipe match — skip see-think-act loop for known patterns
        recipe_result = self._try_recipe(task, screen)
        if recipe_result is not None and (recipe_result.reason or "").startswith(RECIPE_REFUSED):
            # A recipe that publishes matched on a page it is not for. The
            # see-think-act loop would post the text on that page instead, so
            # the task ends with the refusal.
            with self._lock:
                if self._active_task_id == task_id:
                    self._active = False
            recipe_result.total_time_seconds = time.time() - start_time
            return finish(recipe_result)
        if recipe_result is not None:
            _rname = (recipe_result.reason or "").replace("recipe:", "", 1).strip() or None
            if _rname:
                try:
                    from backend.services import recipe_stats
                    recipe_stats.touch_run(_rname, fallback=not recipe_result.success)
                except Exception:
                    pass
            if recipe_result.success:
                self.note_recipe_usage(task, _rname, fallback=False)
                with self._lock:
                    if self._active_task_id == task_id:
                        self._active = False
                return finish(recipe_result)
            else:
                self.note_recipe_usage(task, _rname, fallback=True)
                self._action_history.extend(recipe_result.steps)
                self._recipe_fallback_note = f"Tried recipe {recipe_result.reason} but it failed. Continuing manually."
        elif not self._recipe_usage_buffer or self._recipe_usage_buffer[-1].get("task") != task:
            self.note_recipe_usage(task)

        # The agent looks back: one line about the last run of this exact task,
        # from the episode table. Empty when there is none, or when the line is
        # switched off (the default; see AgentControlConfig.prior_run_note_enabled).
        self._prior_run_note = (
            self._prior_run_note_for(task) if self.config.prior_run_note_enabled else ""
        )

        # Training mode: crank up limits so the agent keeps practicing
        max_iters = 1000 if training_mode else self.config.max_iterations
        if max_steps is not None:
            # An explicit cap from the caller still wins (legacy int form).
            max_iters = min(max_iters, max(1, int(max_steps)))
        effective_budget = budget
        if effective_budget is not None:
            # The cross-tier budget caps escalations between chat tiers; a
            # screen task is one entry against it, not one charge per step.
            # Charging per step (and capping the loop at what was left) made
            # the chat path stop a task at 12 steps whatever it needed.
            if effective_budget.remaining <= 0:
                logger.warning("[AGENT] Cross-tier budget exhausted — not starting the loop")
                return finish(AgentResult(
                    success=False, reason="budget_exhausted",
                    steps=self._action_history,
                ))
            effective_budget.charge(1, 3, "entered ACS execute_task")
        self._current_budget = effective_budget
        task_timeout = 3600 if training_mode else self.config.task_timeout_seconds  # 1 hour for training
        self._stall_steps = 0
        # User notes read this iteration, recorded on the step it produces.
        notes_for_step: List[str] = []

        try:
            for iteration in range(max_iters):
                self._tick_strategy_cooldowns()
                if task_ctx.killed or self._active_task_id != task_id:
                    return finish(AgentResult(
                        success=False, reason="superseded",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if self._killed:
                    return finish(AgentResult(
                        success=False, reason="killed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if time.time() - start_time > task_timeout:
                    return finish(AgentResult(
                        success=False, reason="timeout",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                self._current_iteration = iteration

                # Interruptible sleep so a Stop click bites within ~100ms
                # instead of waiting out the full breathe.
                def _breathe(total_s: float):
                    deadline = time.time() + total_s
                    while time.time() < deadline:
                        if self._killed or task_ctx.killed:
                            return True
                        time.sleep(min(0.1, deadline - time.time()))
                    return False

                # Training mode: 5s pause between iterations so the agent doesn't rush
                if training_mode and iteration > 0:
                    if _breathe(5.0):
                        return finish(AgentResult(
                            success=False, reason="killed",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time,
                        ))

                # BREATHE — inter-iteration cool-down so the screen settles and
                # the agent doesn't chase its own success/X-mark animations.
                # 2.1s, deliberately odd to stand out on grep. Skipped on iter 0
                # (no prior action) and when training_mode already paused.
                if iteration > 0 and not training_mode:
                    logger.debug(f"[AGENT][STEP {iteration+1}][BREATHE] pausing before next See")
                    if _breathe(2.1):
                        return finish(AgentResult(
                            success=False, reason="killed",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time,
                        ))

                # Notes the user sent since the last step: read now and kept
                # in the prompt from here on. A note is new information, so the
                # stall and failure counts and the not-on-screen holds start
                # over and the model has room to act on it.
                new_notes = self._take_new_notes(iteration + 1)
                if new_notes:
                    for n in new_notes:
                        self._emit_thinking(
                            iteration=iteration + 1,
                            label=f"your note — {n['text'][:60]}",
                            reasoning=f"Read before step {iteration + 1}: \"{n['text']}\"",
                            kind="note",
                        )
                    self._stall_steps = 0
                    consecutive_failures = 0
                    self._banned_targets.clear()
                    notes_for_step.extend(n["text"] for n in new_notes)
                    if any(self._points_wanted(n["text"]) for n in new_notes):
                        self._points_on = True

                # 1. SEE — Capture screenshot
                screenshot, cursor_pos = self._capture_with_retry(screen)
                logger.debug(f"[AGENT][STEP {iteration+1}][SEE] Capturing screen, cursor at {cursor_pos}")

                scene_desc = ""  # Will be populated by either unified or split path

                # Unified when the brain can see and point for itself; otherwise
                # the eye describes and points and the brain decides.
                unified_model = self._brain_eye.brain if self._brain_eye.unified else ""

                # Refresh DOM once per iteration, for BOTH modes, so the prompt
                # builder and the click-time DOM-match guard see the same
                # elements. Split mode never had a snapshot before this.
                self._refresh_dom_snapshot()

                if unified_model:
                    self._world_state = self._build_world_state(cursor_pos=cursor_pos, scene_hint="")
                    # UNIFIED MODE: Compact prompt — vision model sees screenshot + short context
                    unified_prompt = self._build_unified_prompt(
                        task,
                        self._action_history,
                        world_state=self._world_state,
                        training_mode=training_mode,
                        chat_context=chat_context,
                    )
                    # Persistent cross-session knowledge rides the system slot,
                    # not the user prompt — keeps the per-step prompt small
                    # enough to hold the model in instructed mode while still
                    # carrying the compact knowledge (URL routes, browser
                    # tactics) and distilled lessons into every decision.
                    persistent_system = self._build_persistent_knowledge_system()
                    if persistent_system:
                        logger.debug(
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
                    # The whole reply goes on the step: cut to 200 characters,
                    # the episode lost fields such as scroll_amount.
                    scene_desc = result.description
                    logger.debug(f"[AGENT][STEP {iteration+1}][UNIFIED] {scene_desc[:200]}")
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
                    logger.debug(f"[AGENT][STEP {iteration+1}][SEE] {scene_desc}")
                    self._world_state = self._build_world_state(
                        cursor_pos=cursor_pos,
                        scene_hint=scene_desc,
                    )

                    # 2b. ASSESS — Check for obstacles before proceeding
                    obstacle = self._assess_obstacles(scene.description, analyzer, screen, iteration)
                    if obstacle == "handled":
                        logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Obstacle handled, re-scanning")
                        continue
                    elif obstacle == "escalated":
                        logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Escalated to thinking model")
                        continue

                    # 3. THINK — Text LLM decides next action
                    decision_prompt = self._build_decision_prompt(
                        task, scene.description, self._action_history, world_state=self._world_state,
                        training_mode=training_mode, chat_context=chat_context,
                    )
                    persistent_system = self._build_persistent_knowledge_system(task=task, full=True)
                    decision_result = analyzer.text_query(
                        decision_prompt, model=self._brain_eye.brain,
                        system=persistent_system or None, num_predict=256, temperature=0.1,
                    )
                    if not decision_result.success:
                        logger.error(f"[AGENT][STEP {iteration+1}][THINK] Decision failed")
                        consecutive_failures += 1
                        continue
                    decision = self._parse_decision(decision_result.description)
                logger.debug(
                    f"[AGENT][STEP {iteration+1}][THINK] "
                    f"action={decision.action.action_type} "
                    f"target=\"{decision.action.target_description or ''}\" "
                    f"text_len={len(decision.action.text or '')} "
                    f"keys={decision.action.keys or ''} "
                    f"reasoning_len={len(decision.action.reasoning or '')}"
                )
                # Decided before the thinking line goes out, so a step that is
                # not sent is one entry in the trail, not two with the same number.
                refusal = ""
                if not decision.task_complete and not decision.stuck:
                    refusal = self._refusal_for(decision.action, training_mode=training_mode)
                label = self._build_action_label(decision.action)
                reasoning = decision.action.reasoning or ""
                if refusal:
                    label = f"{label} — not sent"
                    reasoning = (reasoning + "\n\n" if reasoning else "") + f"Not sent: {refusal}"
                self._emit_thinking(iteration=iteration + 1, label=label, reasoning=reasoning)

                if decision.task_complete and training_mode:
                    # Training mode: ignore "done" — force the model to keep clicking
                    logger.info(f"[AGENT][STEP {iteration+1}][TRAINING] Ignoring 'done' — keep practicing")
                    decision.task_complete = False
                    decision.action.action_type = "click"
                    decision.action.target_description = "colored circle"

                if decision.task_complete and not training_mode and self._has_unseen_notes():
                    # A note arrived while this step was being decided. The
                    # model hasn't read it, so "done" may no longer be true:
                    # read the note first.
                    logger.info(f"[AGENT][STEP {iteration+1}][NOTE] done held: a note arrived")
                    continue

                if decision.task_complete:
                    # Guard: "done" on step 1 with no actions taken is suspicious.
                    # If the screen is black or no actions were executed, this is
                    # likely the model giving up, not genuine completion.
                    if iteration == 0 and not self._action_history:
                        # Check if screen is actually showing something
                        check_shot, _ = screen.capture()
                        if self._is_black_frame(check_shot):
                            logger.warning(f"[AGENT][DONE] Rejected — model said 'done' on black screen with 0 actions")
                            return finish(AgentResult(
                                success=False,
                                reason="Screen is black — virtual display may need restart. No actions were taken.",
                                steps=self._action_history,
                                total_time_seconds=time.time() - start_time
                            ))

                    # Detect prior servo-verified success (DPC or equivalent) on a non-trivial action.
                    # When present, the done semantic verify (on a model-provided proof) is treated
                    # as advisory — the physical change was already observed by the fast path.
                    # This re-uses the exact "advisory" contract documented for slow expected_effect
                    # (1085) and recipe final proof (2846-2853). Also used for proof grounding below.
                    has_recent_verified = self._task_has_verified_click(self._action_history)

                    # Guard: require a non-trivial success_proof — the model must
                    # echo the visible state that proves completion. Empty or
                    # generic "task done" / "n/a" answers are rejected as phantom
                    # success. Only enforced for vision-capable models that get
                    # the success_proof requirement in their prompt.
                    proof = (decision.action.success_proof or "").strip()
                    proof_lc = proof.lower()
                    trivial_proofs = {"", "n/a", "na", "task complete", "done", "task done", "ok", "complete"}
                    # Keyed on whether the prompt we actually sent asked for a
                    # proof — not on which model is loaded. It used to be
                    # bool(self._get_unified_model()), so choosing a model that
                    # happened not to be gemma4:e4b silently disabled this
                    # check. A safety rule that can be turned off by picking a
                    # different model is not a safety rule.
                    enforce_proof = bool(getattr(self, "_proof_contract", True))

                    # Ground success_proof from prior successful (servo-verified) step when the
                    # model provided none or a trivial one. Re-uses the last target_description
                    # (e.g. "first video thumbnail") + verified effect already recorded
                    # in ActionStep.result. This gives the subsequent _wait_until_visible (or
                    # advisory path) a concrete, history-backed string instead of forcing the
                    # model to perfectly re-phrase the visible state. Mirrors how expected_effect
                    # is pulled from action (1067) and recipe success_proof is top-level declared.
                    # A grounded proof is ours, not the model's: it must be seen on
                    # screen, and never takes the advisory branch below.
                    proof_grounded = False
                    if enforce_proof and (not proof or proof_lc in trivial_proofs) and has_recent_verified:
                        for st in reversed(self._action_history):
                            if getattr(getattr(st, "action", None), "action_type", "") == "done":
                                continue
                            tgt = getattr(getattr(st, "action", None), "target_description", "") or ""
                            if tgt:
                                proof = f"{tgt} now visible/achieved (per prior verified servo change)"
                                proof_lc = proof.lower()
                                proof_grounded = True
                                # attach back so emit/history and finish() see the grounded value
                                decision.action.success_proof = proof
                                logger.debug(f"[AGENT][DONE] Grounded success_proof from prior verified step: {proof!r}")
                                break

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
                        consecutive_failures += 1
                        continue

                    # Guard: screen-verify the success_proof before trusting "done".
                    # A non-trivial proof STRING is not enough — the model can echo
                    # a plausible-but-false state (e.g. declaring the page loaded
                    # right after navigate returned [OK], when navigate [OK] only
                    # means the URL keystrokes were sent, not that the page
                    # rendered). Mirror the recipe-path final verify
                    # (_wait_until_visible) so adaptive completion cannot report
                    # intent as reality.
                    #
                    # ADVISORY EXCEPTION (verified fix for servo-success + user-visible
                    # goal not leading to termination): if the latest click (see
                    # _task_has_verified_click) had servo DPC (or equivalent) `verified`
                    # or post_action_effect indicating real visible change (e.g. the exact
                    # thumbnail click that achieved the goal per user + step-1 [OK]),
                    # then the semantic verify on the *model's* proof is
                    # treated as advisory only. A proof grounded from history never is.
                    # This re-uses the documented contract for slow expected_effect
                    # verifies (1085: "keeping click as OK; next SEE will observe actual
                    # state") and recipe final proof (2846-2853: "DO NOT flip the whole
                    # recipe to failed — the actions clearly happened. The next
                    # see-think-act SEE pass observes actual screen state, which is
                    # the real ground truth."). Prevents the reported symptom while
                    # preserving hard guards for black/zero-action/trivial cases.
                    # Only enforced for vision-capable models (same gate as above).
                    has_recent_verified = self._task_has_verified_click(self._action_history)

                    if enforce_proof:
                        # Keep DONE strict on vision — a false-positive DOM match
                        # here would wrongly declare the whole task complete. The
                        # per-action verifies (below) get the DOM fast-path since a
                        # false positive there just proceeds to the next SEE, which
                        # observes ground truth.
                        done_verify = self._wait_until_visible(
                            proof, screen, timeout_s=10.0, allow_dom_fastpath=False,
                        )
                        if not bool(done_verify.get("success", False)):
                            if has_recent_verified and not proof_grounded:
                                # Advisory path: prior servo evidence (DPC change) + user-visible
                                # goal already confirm the achievement. Log + emit advisory note
                                # (visible in AgentThinkingTrail), append non-failed advisory step
                                # for trail/history, but do NOT treat as failure, do NOT increment
                                # consecutive_failures, and proceed to success return.
                                logger.info(
                                    f"[AGENT][DONE] success_proof not visible to verifier within 10s "
                                    f"(proof: {proof!r}) but prior step had servo verified change — "
                                    f"treating done as advisory success (re-uses slow-verify + recipe "
                                    f"advisory patterns at 1085/2846)."
                                )
                                try:
                                    self._emit_thinking(
                                        iteration=iteration + 1,
                                        label="done (advisory — prior servo verified change)",
                                        reasoning=(
                                            f"Model claimed complete ('{proof}') but the vision "
                                            f"verifier could not confirm it on screen. However, a "
                                            f"prior step reported servo DPC verified visible change "
                                            f"for the target action and the goal is visibly achieved. "
                                            f"Proceeding per advisory contract (next SEE is ground truth)."
                                        ),
                                    )
                                except Exception:
                                    pass
                                # Record advisory (non-failed) for trail/persistence but do not fail the run
                                self._action_history.append(ActionStep(
                                    iteration=iteration,
                                    scene_description=scene_desc or "no scene",
                                    action=AgentAction(
                                        action_type="wait_until_visible",
                                        target_description=proof,
                                    ),
                                    result=done_verify,
                                    verification="done proof advisory (prior servo verified)",
                                    failed=False,
                                ))
                                # fall through to success return below (no continue, no failure counters)
                            else:
                                logger.warning(
                                    f"[AGENT][DONE] Rejected — success_proof not visible on "
                                    f"screen within 10s (proof: {proof!r}). Treating as failed "
                                    f"step (no reporting intent as reality)."
                                )
                                decision.task_complete = False
                                self._action_history.append(ActionStep(
                                    iteration=iteration,
                                    scene_description=scene_desc or "no scene",
                                    action=AgentAction(
                                        action_type="wait_until_visible",
                                        target_description=proof,
                                    ),
                                    result=done_verify,
                                    verification="done proof not visible on screen",
                                    failed=True,
                                ))
                                self._last_progress_signal = self._semantic_progress_signal(
                                    decision.action,
                                    {"success": False, "reason": "done_proof_unverified"},
                                    failed=True,
                                    pixel_diff=None,
                                )
                                self._record_failure_label(self._last_progress_signal.label)
                                consecutive_failures += 1
                                try:
                                    self._emit_thinking(
                                        iteration=iteration + 1,
                                        label="done rejected — proof not visible",
                                        reasoning=(
                                            f"Model claimed complete ('{proof}') but the vision "
                                            f"verifier could not confirm it on screen. Continuing "
                                            f"to observe actual state rather than report success."
                                        ),
                                    )
                                except Exception:
                                    pass
                                continue

                    logger.info(f"[AGENT][DONE] Task complete after {iteration+1} steps, "
                                f"{time.time() - start_time:.1f}s "
                                f"(proof_len={len(proof)})")
                    return finish(AgentResult(
                        success=True, reason="completed",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if decision.stuck:
                    logger.warning(f"[AGENT][STEP {iteration+1}][THINK] Agent reports stuck")
                    consecutive_failures += 1
                    continue

                # 4. ACT — Execute via servo (for clicks) or direct (for type/hotkey/scroll)
                # In mouse_only mode, reject any keyboard-driven action. Mouse-driven
                # gestures (click family + drag + hover) are all allowed.
                _MOUSE_ONLY_ALLOWED = (
                    "click", "right_click", "double_click", "triple_click",
                    "drag", "hover", "move", "scroll", "done", "click_at", "draw"
                )
                if (not refusal and getattr(self, '_mouse_only', False)
                        and decision.action.action_type not in _MOUSE_ONLY_ALLOWED):
                    logger.info(f"[AGENT][STEP {iteration+1}][ACT] Mouse-only: rejecting {decision.action.action_type}")
                    decision.action.action_type = "click"
                    if not decision.action.target_description:
                        consecutive_failures += 1
                        continue

                # A click past the task's own stated limit is never sent. The
                # run ends instead: a task that says "5 click attempts total"
                # was failed by a 6th click, whatever the screen shows after it.
                if (not refusal and self._click_budget is not None and not training_mode
                        and decision.action.action_type in self._CLICK_FAMILY + ("click_at",)):
                    used = sum(1 for s in self._action_history
                               if s.action.action_type in self._CLICK_FAMILY + ("click_at",))
                    if used >= self._click_budget:
                        logger.warning(
                            f"[AGENT][STEP {iteration+1}][BUDGET] {used} of {self._click_budget} "
                            f"clicks used; not sending a click on "
                            f"{decision.action.target_description!r}, stopping"
                        )
                        self._emit_thinking(
                            iteration=iteration + 1,
                            label=f"click budget spent ({used}/{self._click_budget}) — stopping",
                            reasoning=(
                                f"The task allows {self._click_budget} clicks and all of them "
                                f"have been used. The next click "
                                f"('{decision.action.target_description}') was not sent."
                            ),
                        )
                        return finish(AgentResult(
                            success=False,
                            reason=f"click_budget_spent: {used} of {self._click_budget} clicks used",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time
                        ))

                if refusal:
                    # Not sent. The model reads why on its next step, as a
                    # [NOT SENT] line and in the failure evidence.
                    logger.warning(
                        f"[AGENT][STEP {iteration+1}][REFUSE] {decision.action.action_type} "
                        f"{(decision.action.target_description or decision.action.text or '')[:60]!r} "
                        f"not sent: {refusal}"
                    )
                    result = {
                        "success": False, "refused": True, "refusal": refusal,
                        "reason": "action_refused", "click_issued": False,
                        "post_action_effect": "not_checked",
                    }
                    failed = True
                    pixel_diff_value = None
                    if (decision.action.action_type in self._CLICK_FAMILY
                            and self._target_key(decision.action.target_description) in self._banned_targets):
                        self._note_not_found(decision.action.target_description)
                elif decision.action.action_type in ("click", "right_click"):
                    button = "right" if decision.action.action_type == "right_click" else "left"
                    target = decision.action.target_description
                    pixel_diff_value: Optional[float] = None

                    # DOM match is a heuristic optimization, not a hard guard.
                    # If it fails, we still let the vision model try — it might
                    # be a desktop icon or OS element Firefox's DOM can't see.
                    if target and not self._dom_match(target):
                        logger.warning(
                            f"[AGENT][STEP {iteration+1}][DOM] target not in DOM snapshot: {target!r} (proceeding via vision)"
                        )

                    # For generic area targets (desktop, empty space), click center-screen.
                    # Anchored to the WHOLE target string: a loose substring match here
                    # used to hijack concrete element targets like "large purple circle
                    # center" — blind-clicking screen midpoint with success:True and
                    # never invoking the servo (2026-07-07 vision-trainer incident).
                    if re.fullmatch(
                        r'(?:the\s+)?(?:desktop|empty|blank|background|open\s+area|center|middle)'
                        r'(?:\s+(?:area|space|screen|of\s+(?:the\s+)?screen))?',
                        target.strip(), re.IGNORECASE,
                    ):
                        sw, sh = screen.screen_size()
                        cx, cy = sw // 2, sh // 2
                        screen.click(cx, cy, button=button)
                        decision.action.coordinates = (cx, cy)
                        result = {
                            "success": True,
                            "target_found": True,
                            "click_issued": True,
                            "post_action_effect": "pending_observation",
                        }
                        failed = False
                    else:
                        attempt_key = target.strip().lower()
                        attempt = self._click_attempts.get(attempt_key, 0) + 1
                        self._click_attempts[attempt_key] = attempt
                        servo_result = servo.click_target(
                            target, button=button, single_attempt=training_mode,
                            precision=(True if attempt >= 2 else None), attempt=attempt,
                        )
                        if servo_result.get("verified"):
                            self._click_attempts.pop(attempt_key, None)
                        decision.action.coordinates = (servo_result.get("x", 0), servo_result.get("y", 0))
                        # Track issued clicks for the circuit breaker
                        self._click_history.append(decision.action.coordinates)
                        result = dict(servo_result)
                        failed = not servo_result.get("success", False)
                        if (not servo_result.get("target_found", True)
                                and servo_result.get("reason") == "target_not_visible"
                                and not training_mode):
                            self._note_not_found(target)

                    # Special handling for desktop / launcher icon clicks (e.g. "Firefox icon").
                    # The servo DPC at the click site may see a small "icon pressed" animation
                    # and report success, but the actual app launch + window mapping on XFCE
                    # can easily take 5-12s. Force the slow semantic verifier so the loop
                    # actually waits for the window instead of immediately re-observing
                    # the desktop and re-deciding "must click the icon again".
                    # Hybrid: also check xdotool for "Mozilla Firefox" window (faster than vision sometimes).
                    # If vision gate fails, attempt direct launch fallback using the agent wrapper/profile.
                    if target and re.search(r'(firefox|browser|chrome|icon|launcher|desktop)\s*icon|icon\s*(firefox|browser|launcher|app)|desktop.*(icon|launcher)', target, re.IGNORECASE):
                        if not failed and not getattr(self, "_training_mode", False):
                            launch_target = "Firefox browser window with URL bar visible or any browser/application window opened from the desktop"
                            # Hybrid fast check via xdotool (reliable for process/window existence)
                            try:
                                if self._is_firefox_running(screen):
                                    launch_verify = {"success": True, "action": "xdotool_check", "polls": 0}
                                else:
                                    launch_verify = self._wait_until_visible(launch_target, screen, timeout_s=12.0)
                            except Exception:
                                launch_verify = self._wait_until_visible(launch_target, screen, timeout_s=12.0)

                            result["expected_effect"] = "application window launched and visible from desktop icon click"
                            result["post_action_effect"] = "verified" if launch_verify.get("success") else "not_observed"
                            result["verified"] = bool(launch_verify.get("success"))
                            result["verifier"] = "forced_desktop_launcher_semantic_12s_hybrid"
                            result["launch_verify"] = launch_verify
                            if not launch_verify.get("success"):
                                logger.info(f"[AGENT][STEP {iteration+1}][VERIFY] launcher click on {target!r} did not produce visible window in 12s (xdotool/vision) — trying direct launch fallback + marking for re-grounding")
                                # Direct launch fallback (lean on existing launch utils to ensure window)
                                try:
                                    import subprocess, time as _time
                                    from pathlib import Path
                                    from backend.utils.agent_display_utils import get_firefox_profile_path, get_agent_display_env
                                    profile = get_firefox_profile_path()
                                    env = get_agent_display_env()
                                    launcher_script = os.environ.get("AGENT_FIREFOX_LAUNCHER") or str(Path(__file__).resolve().parents[2] / "scripts" / "agent_firefox_launch.sh")
                                    if os.path.exists(launcher_script):
                                        cmd = [launcher_script]
                                    else:
                                        cmd = ["firefox", "--no-remote", "--profile", profile]
                                    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                                    _time.sleep(4)  # allow map + paint
                                    # re-verify
                                    launch_verify = self._wait_until_visible(launch_target, screen, timeout_s=8.0)
                                    result["launch_fallback"] = "direct"
                                    result["verified"] = bool(launch_verify.get("success"))
                                    if launch_verify.get("success"):
                                        logger.info(f"[AGENT][STEP {iteration+1}][VERIFY] direct launch fallback succeeded for {target!r}")
                                        failed = False
                                except Exception as fb_err:
                                    logger.warning(f"direct firefox launch fallback failed: {fb_err}")
                                failed = not launch_verify.get("success", False)
                            # Lean on memory/lessons for recovery (per approved plan + STA awareness): query prior similar fails/lessons.
                            try:
                                from backend.api.memory_api import search_memories
                                prior = search_memories(
                                    query=f"launcher icon click failed for {target}",
                                    limit=3, min_importance=0.3,
                                    match_text=target, min_match=0.2,
                                )
                                if prior:
                                    lesson_hints = [(m.get('content') or '')[:120] for m in prior]
                                    if lesson_hints:
                                        result["memory_lesson_hints"] = lesson_hints
                                        logger.debug(f"[AGENT][STA] launcher memory hints for {target}: {lesson_hints}")
                            except Exception:
                                pass  # non-fatal; STA continues with budget/verify

                    # Verifier selection by action class (response_2026-05-19 §B).
                    # Two verifiers, picked by latency profile:
                    #   • Fast verifier  = servo's region-diff DPC (already polled
                    #     for 3s inside servo.click_target). Right for in-page UI
                    #     where the visible change happens in milliseconds.
                    #   • Slow verifier  = _wait_until_visible (asks the vision
                    #     model). Right for navigations / app launches / new
                    #     windows where the world takes 5-15s to settle.
                    # The original POST-VERIFY-DISABLED comment (2026-05-12) was
                    # the right instinct for the wrong reason — Firefox cold
                    # starts outlast a 6s poll, but in-page clicks don't need
                    # a poll at all. Split the cases instead of disabling both.
                    expected_effect = (decision.action.expected_effect or "").strip()
                    if not failed and not getattr(self, "_training_mode", False):
                        if _looks_like_slow_effect(expected_effect):
                            # Slow path: 12s vision-asked verify for navigation /
                            # app-launch / window-open / page-load patterns.
                            verify = self._wait_until_visible(expected_effect, screen, timeout_s=12.0)
                            result["expected_effect"] = expected_effect
                            result["post_action_effect"] = (
                                "verified" if verify.get("success") else "not_observed"
                            )
                            result["verified"] = bool(verify.get("success"))
                            result["verifier"] = "semantic_12s"
                            result["semantic_verify"] = verify
                            # Slow verify stays ADVISORY — long renders (Firefox
                            # cold start behind XFCE Untrusted-launcher) sometimes
                            # outlast even 12s. Next SEE pass is the real ground
                            # truth; we keep the click as OK and let the loop
                            # observe.
                            if not verify.get("success"):
                                logger.info(
                                    f"[AGENT][STEP {iteration+1}][VERIFY] slow expected_effect "
                                    f"{expected_effect!r} not yet visible after 12s — "
                                    f"keeping click as OK; next SEE will observe actual state"
                                )
                        else:
                            # Fast path: trust servo's region-diff DPC. servo
                            # already polled for 3s and recorded `verified` in
                            # the result dict. No second poll — it's redundant
                            # and slows the loop.
                            servo_verified = bool(result.get("verified", False))
                            result["verifier"] = "servo_region_dpc"
                            if expected_effect:
                                result["expected_effect"] = expected_effect
                            # Region DPC IS authoritative here — if servo polled
                            # for 3s and the click site didn't change, the click
                            # really didn't land. Surface that as a soft signal
                            # (not a hard fail; the next SEE still arbitrates)
                            # so the LLM sees the previous attempt was inert
                            # rather than thinking it worked.
                            if not servo_verified:
                                logger.info(
                                    f"[AGENT][STEP {iteration+1}][VERIFY] fast verify "
                                    f"(region DPC) saw no change at click site — "
                                    f"surfacing as 'not_observed' for the LLM"
                                )
                                result["post_action_effect"] = "not_observed"
                            else:
                                result["post_action_effect"] = "verified"

                    status_icon = "OK" if not failed else "FAIL"
                    log_action = logger.warning if failed else logger.debug
                    log_action(
                        f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} "
                        f"\"{target}\" at ({decision.action.coordinates[0]},"
                        f"{decision.action.coordinates[1]}) [{status_icon}]"
                    )
                elif decision.action.action_type in ("click_at", "draw"):
                    # Exact points from the model: no eye, no servo. Checked
                    # against the screen, then by what changed near them.
                    kind = decision.action.action_type
                    pts = ([tuple(decision.action.coordinates)] if kind == "click_at" and decision.action.coordinates
                           else list(decision.action.points))
                    pixel_diff_value = None
                    sw, sh = self._screen_size or screen.screen_size()
                    off = [p for p in pts if not (0 <= p[0] < sw and 0 <= p[1] < sh)]
                    if not pts or off:
                        why = (f"{'x and y are' if kind == 'click_at' else 'points are'} missing" if not pts
                               else f"point {off[0]} is off the {sw}x{sh} screen")
                        result = {"success": False, "reason": "bad_points", "error": why,
                                  "click_issued": False, "post_action_effect": "not_checked"}
                        failed = True
                    else:
                        try:
                            pre_shot, _ = self._capture_with_retry(screen)
                        except Exception:
                            pre_shot = None
                        if kind == "click_at":
                            act = screen.click(pts[0][0], pts[0][1])
                        elif hasattr(screen, "stroke"):
                            act = screen.stroke(pts)
                        else:
                            act = {"success": False, "error": "this screen cannot draw strokes"}
                        failed = not act.get("success", False)
                        decision.action.coordinates = pts[0]
                        result = {
                            "success": not failed, "click_issued": not failed,
                            "points": [list(p) for p in pts],
                            "post_action_effect": "pending_observation",
                            "reason": "" if not failed else act.get("error", f"{kind}_failed"),
                        }
                        if not failed:
                            time.sleep(0.4)
                            post_shot, _ = self._capture_with_retry(screen)
                            if kind == "click_at":
                                change = self._screen_change(screenshot, pre_shot, post_shot,
                                                             near=pts[0], box=(24, 24))
                                moved = bool(change) and change.get("near_blocks", 0) >= 1
                            else:
                                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                                centre = ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
                                half = ((max(xs) - min(xs)) // 2 + 24, (max(ys) - min(ys)) // 2 + 24)
                                change = self._screen_change(screenshot, pre_shot, post_shot,
                                                             near=centre, box=half)
                                moved = bool(change) and change.get("near_blocks", 0) >= 2
                            if change:
                                result["screen_change"] = change
                                pixel_diff_value = round(change["share"] * 100, 3)
                            # Advisory, like a hotkey: a click on a blank spot is
                            # allowed to change nothing.
                            result["verified"] = moved
                            result["post_action_effect"] = "verified" if moved else "no_visible_change"
                    log_action = logger.warning if failed else logger.debug
                    log_action(f"[AGENT][STEP {iteration+1}][ACT] {kind} {len(pts)} point(s) "
                               f"[{'OK' if not failed else 'FAIL'}] {result.get('reason') or ''}")
                elif decision.action.action_type in ("double_click", "triple_click", "hover"):
                    # Locate the target without clicking, then dispatch to the
                    # native gesture method. Splitting locate+act is what makes
                    # double_click possible at all — servo.click_target would
                    # have already single-clicked before we got control.
                    target = decision.action.target_description
                    if not target:
                        result = {
                            "success": False, "target_found": False, "click_issued": False,
                            "x": 0, "y": 0, "reason": "missing_target",
                            "post_action_effect": "not_checked",
                        }
                        failed = True
                    else:
                        loc = servo.locate_target(target)
                        if not loc.get("found"):
                            result = {
                                "success": False, "target_found": False, "click_issued": False,
                                "x": 0, "y": 0,
                                "reason": loc.get("reason", "target_not_visible"),
                                "post_action_effect": "not_checked",
                                "detection_source": loc.get("detection_source", ""),
                            }
                            failed = True
                        else:
                            x, y = loc["x"], loc["y"]
                            decision.action.coordinates = (x, y)
                            if decision.action.action_type == "double_click":
                                act_result = screen.double_click(x, y)
                            elif decision.action.action_type == "triple_click":
                                act_result = screen.triple_click(x, y)
                            else:  # hover
                                act_result = screen.hover(x, y)
                            failed = not act_result.get("success", False)
                            result = {
                                "success": act_result.get("success", False),
                                "target_found": True,
                                "click_issued": decision.action.action_type != "hover",
                                "x": x, "y": y,
                                "post_action_effect": "pending_observation",
                                "detection_source": loc.get("detection_source", ""),
                                "parse_path": loc.get("parse_path", ""),
                                "reason": "" if not failed else act_result.get("error", "gesture_failed"),
                            }
                    pixel_diff_value: Optional[float] = None
                    coords_str = f"({decision.action.coordinates[0]},{decision.action.coordinates[1]})" if decision.action.coordinates else "(?,?)"
                    log_action = logger.warning if failed else logger.debug
                    log_action(
                        f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} "
                        f"\"{target}\" at {coords_str} [{'OK' if not failed else 'FAIL'}]"
                    )
                elif decision.action.action_type == "drag":
                    # Drag needs TWO target locations (source + destination).
                    # Both go through the same vision pass; failure on either
                    # is fail-fast — no half-drag, no orphaned mousedown.
                    src_desc = decision.action.target_description
                    dst_desc = decision.action.drag_to_description
                    if not src_desc or not dst_desc:
                        result = {
                            "success": False, "target_found": False, "click_issued": False,
                            "x": 0, "y": 0,
                            "reason": "missing_drag_endpoints",
                            "post_action_effect": "not_checked",
                        }
                        failed = True
                    else:
                        loc_src = servo.locate_target(src_desc)
                        loc_dst = servo.locate_target(dst_desc) if loc_src.get("found") else {"found": False, "reason": "skipped_after_source_miss"}
                        if not loc_src.get("found") or not loc_dst.get("found"):
                            result = {
                                "success": False, "target_found": False, "click_issued": False,
                                "x": 0, "y": 0,
                                "reason": "drag_endpoint_not_found: " + (loc_src if not loc_src.get("found") else loc_dst).get("reason", ""),
                                "post_action_effect": "not_checked",
                            }
                            failed = True
                        else:
                            sx, sy = loc_src["x"], loc_src["y"]
                            tx, ty = loc_dst["x"], loc_dst["y"]
                            decision.action.coordinates = (sx, sy)
                            act_result = screen.drag(sx, sy, tx, ty)
                            failed = not act_result.get("success", False)
                            result = {
                                "success": act_result.get("success", False),
                                "target_found": True,
                                "click_issued": True,
                                "x": sx, "y": sy,
                                "drag_to": (tx, ty),
                                "post_action_effect": "pending_observation",
                                "reason": "" if not failed else act_result.get("error", "drag_failed"),
                            }
                    pixel_diff_value: Optional[float] = None
                    log_action = logger.warning if failed else logger.debug
                    log_action(
                        f"[AGENT][STEP {iteration+1}][ACT] drag \"{src_desc}\" → \"{dst_desc}\" "
                        f"[{'OK' if not failed else 'FAIL'}]"
                    )
                else:
                    kind = decision.action.action_type
                    checks_change = kind in ("type", "hotkey", "scroll")
                    # The frame just before acting. Compared with the frame the
                    # model decided on, it shows what moves by itself (a playing
                    # video, a spinner), which the after-check leaves out.
                    pre_shot = None
                    if checks_change:
                        try:
                            pre_shot, _ = self._capture_with_retry(screen)
                        except Exception as e:
                            logger.debug(f"[AGENT][STEP {iteration+1}][VERIFY] pre-action capture failed: {e}")
                    result = self._execute_action(decision.action, screen)
                    failed = not result.get("success", False)
                    pixel_diff_value: Optional[float] = None

                    # Post-action observation: wait for the UI to update, then
                    # take a verification screenshot so the NEXT iteration's
                    # SEE step reflects the actual outcome of this action.
                    if not failed and checks_change:
                        time.sleep(0.5)
                        post_shot, _ = self._capture_with_retry(screen)
                        near = self._field_point if kind == "type" else None
                        change = self._screen_change(screenshot, pre_shot, post_shot, near=near)
                        if change is None:
                            result["verified"] = False
                        else:
                            result["screen_change"] = change
                            # Percent of the screen that changed, live areas left out.
                            pixel_diff = round(change["share"] * 100, 3)
                            pixel_diff_value = pixel_diff
                            if kind == "scroll":
                                moved = (change["share"] >= self._SCROLL_MIN_SHARE
                                         and change["width_share"] >= self._SCROLL_MIN_WIDTH)
                            elif kind == "type":
                                # One or two characters can't change four blocks.
                                need = min(self._CHANGE_MIN_BLOCKS,
                                           max(1, len((decision.action.text or "").strip())))
                                moved = change.get("near_blocks", change["changed_blocks"]) >= need
                            else:
                                moved = change["changed_blocks"] >= self._CHANGE_MIN_BLOCKS
                            # A scroll that moved nothing: at the end of the page,
                            # or the cursor is over a pane that doesn't scroll.
                            # Text that changed nothing near the field it was
                            # meant for went somewhere else; on a page with
                            # single-key shortcuts that is the page acting on it.
                            # Both are failed steps so the next THINK knows.
                            ineffective = kind in ("type", "scroll") and not moved
                            if ineffective:
                                if kind == "type":
                                    result["reason"] = (
                                        "typed_text_not_in_field"
                                        if change["changed_blocks"] >= self._CHANGE_MIN_BLOCKS
                                        else "typed_text_not_visible"
                                    )
                                logger.warning(
                                    f"[AGENT][STEP {iteration+1}][VERIFY] "
                                    f"{kind} produced no visible change where expected "
                                    f"({change}) — flagging as failed so the LLM "
                                    f"sees the previous attempt was ineffective"
                                )
                                result["verified"] = False
                                result["success"] = False
                                failed = True
                                # Ineffective scroll = the page didn't move (wrong focus,
                                # non-scrollable region, or already at the end). Repeating
                                # the scroll just burns steps (the STEP 9/10 dead-scroll
                                # loop that timed out the YouTube comment task). Steer the
                                # next THINK to the grounded DOM: click a real listed
                                # element (e.g. the composer) or jump with keys instead of
                                # re-scrolling.
                                # NOTE (2026-08-01, needs revisit): this is a prompt-level
                                # nudge, not a hard reroute. A fuller fix resolves the scroll
                                # target from the DOM (or focuses the scrollable pane first)
                                # rather than relying on the model to heed the hint.
                                if kind == "scroll":
                                    self._pending_world_observed = (
                                        "OBSERVED: your last scroll moved NOTHING — the page did "
                                        "not scroll. Do NOT scroll again. Instead, either click a "
                                        "specific element you can see (e.g. the comment/composer "
                                        "box), or press Page_Down / End to jump. If that does "
                                        "nothing, click the page body once to give it keyboard "
                                        "focus, then retry."
                                    )
                            else:
                                # type and scroll reach here only past their change
                                # thresholds. A hotkey reaches here whatever it did,
                                # so it is verified only if the screen moved: a Home
                                # on a page already at the top is not progress, and
                                # counting it as one reset the stall guard.
                                result["verified"] = moved
                                logger.debug(f"[AGENT][STEP {iteration+1}][VERIFY] "
                                             f"{kind} change={change} verified={moved}")

                    status_icon = "OK" if not failed else "FAIL"
                    detail_len = len(decision.action.text or "")
                    keys = decision.action.keys or []
                    log_action = logger.warning if failed else logger.debug
                    log_action(
                        f"[AGENT][STEP {iteration+1}][ACT] {decision.action.action_type} "
                        f"detail_len={detail_len} keys={keys} [{status_icon}]"
                    )

                signal = self._semantic_progress_signal(
                    decision.action,
                    result,
                    failed=failed,
                    pixel_diff=pixel_diff_value,
                )
                self._last_progress_signal = signal
                result["semantic_progress"] = asdict(signal)
                if not result.get("refused"):
                    # A step that was not sent says nothing about whether the
                    # action works; only executed failures earn a block.
                    self._record_strategy_outcome(decision.action, failed)
                if not failed and result.get("verified"):
                    # The screen changed, so a target the eye could not find
                    # may be there now.
                    self._banned_targets.clear()
                self._record_recovery_memory(signal, decision.action, failed)
                self._record_failure_report(
                    iteration=iteration + 1,
                    action=decision.action,
                    result=result,
                    pixel_diff=pixel_diff_value,
                    failed=failed,
                )
                # Phase-3 re-grounding: when the model has failed twice on
                # the same target_description, fire a no-context vision pass
                # so the next THINK prompt carries the model's own un-primed
                # list of what's on screen. One re-ground per stuck cluster;
                # the counter resets when the target changes.
                if (
                    self._stuck_target_count == 2
                    and self._stuck_target
                    and not self._pending_world_observed
                ):
                    observation = self._observe_only_pass(screen)
                    if observation:
                        self._pending_world_observed = observation
                        # Phase 4: feed the fresh observation through the
                        # session belief tracker. Each claimed-visible element
                        # that's missing from the observation lands in
                        # _expectation_log; at task end the log distils into
                        # belief_update memories that surface in the next
                        # session's system prompt.
                        try:
                            self._record_expectation_contradictions(
                                self._derive_session_expectations(),
                                observation,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[AGENT][BELIEF] contradiction-record failed: {e}"
                            )

                # 5. RECORD step
                step = ActionStep(
                    iteration=iteration,
                    scene_description=scene_desc or "no scene",
                    action=decision.action,
                    result=result,
                    verification=signal.evidence,
                    failed=failed,
                )
                if notes_for_step:
                    result["user_notes"] = list(notes_for_step)
                    notes_for_step.clear()
                self._action_history.append(step)

                # A target the eye has looked for this many times without
                # finding it is not going to appear by asking again.
                given_up = self._target_given_up()
                if given_up:
                    name, looks = given_up
                    logger.warning(f"[AGENT][NOT_FOUND] {name!r} not on screen in {looks} looks; stopping")
                    self._emit_thinking(
                        iteration=iteration + 1,
                        label=f"stopping — '{name[:40]}' never found",
                        reasoning=(f"The eye looked for '{name}' {looks} times and did not find it "
                                   f"on screen, so the run stops here instead of asking again."),
                    )
                    return finish(AgentResult(
                        success=False,
                        reason=f"target_not_found: '{name[:60]}' was not on screen in {looks} looks",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time,
                    ))

                # 5b. EARLY DONE — after a successful action, check if the
                # task goal is obviously met based on desktop state. Saves
                # an entire LLM round-trip (~3-5s) when the answer is clear.
                # Disabled in training mode — keep clicking targets forever.
                if not failed and len(self._action_history) >= 1 and not getattr(self, '_training_mode', False):
                    early_done = self._check_early_done(
                        task,
                        display=getattr(screen, "display", None),
                    )
                    if early_done:
                        logger.warning(
                            f"[AGENT][EARLY_DONE] Task goal met after step {iteration+1}: {early_done}"
                        )
                        return finish(AgentResult(
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
                        (h.action.action_type, h.action.target_description, h.action.text,
                         self._point_identity(h.action))
                        for h in self._action_history[-3:]
                    ]
                    
                    # Circuit Breaker Part 1: Semantic Identicality
                    semantic_loop = len(set(last3)) == 1 and last3[0][0] != "wait"
                    
                    # Circuit Breaker Part 2: Spatial Identicality (Idempotency)
                    # Detect if we clicked the exact same coordinates 3 times without effect.
                    spatial_loop = False
                    if len(self._click_history) >= 3:
                        last3_clicks = self._click_history[-3:]
                        # If all three are non-zero (valid clicks) and identical
                        if len(set(last3_clicks)) == 1 and last3_clicks[0] != (0, 0):
                            spatial_loop = True

                    if semantic_loop or spatial_loop:
                        loop_type = "Spatial" if spatial_loop else "Semantic"
                        repeat_ok, repeat_reason = self._repetition_verdict(self._action_history[-3:])
                        logger.warning(
                            f"[AGENT][LOOP] {loop_type} action repeated 3x: "
                            f"{last3[0][0]} \"{last3[0][1] or last3[0][2]}\" "
                            f"at {self._click_history[-1] if self._click_history else 'n/a'}. "
                            f"Aborting ({repeat_reason})."
                        )
                        # Capture fresh screenshot for prompt/history context
                        try:
                            fail_shot, _ = self._capture_with_retry(screen)
                        except Exception:
                            pass
                        return finish(AgentResult(
                            success=repeat_ok,
                            reason=repeat_reason,
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time
                        ))

                # 5d. STALL — successful steps that change nothing and touch no
                # new target. The loop breaker above only sees identical
                # repeats; the A-B-C-D-A cycle of 2026-09-23 was never caught.
                if self._note_progress(step, training_mode=training_mode):
                    logger.warning(
                        f"[AGENT][STALL] {self._stall_steps} successful steps with no new target "
                        f"and no verified change; stopping as stalled_no_progress"
                    )
                    return finish(AgentResult(
                        success=False, reason="stalled_no_progress",
                        steps=self._action_history,
                        total_time_seconds=time.time() - start_time
                    ))

                if failed:
                    consecutive_failures += 1
                    # Training mode: never kill on consecutive failures — missing targets is learning
                    max_failures = 999 if training_mode else self.config.max_consecutive_failures
                    if consecutive_failures >= max_failures:
                        recent = [
                            f"{s.action.action_type}:{(s.action.target_description or s.action.text or '?')[:30]}"
                            for s in self._action_history[-3:]
                        ]
                        logger.warning(
                            f"Kill switch: {consecutive_failures} consecutive failures. "
                            f"Recent actions: {recent}. Last reason(s) in history."
                        )
                        self.kill()
                        return finish(AgentResult(
                            success=False, reason="max_failures",
                            steps=self._action_history,
                            total_time_seconds=time.time() - start_time
                        ))
                else:
                    consecutive_failures = 0

            return finish(AgentResult(
                success=False, reason="max_iterations",
                steps=self._action_history,
                total_time_seconds=time.time() - start_time
            ))

        except Exception as e:
            logger.error(f"Task execution error: {e}", exc_info=True)
            return finish(AgentResult(
                success=False, reason=f"error: {e}",
                steps=self._action_history,
                total_time_seconds=time.time() - start_time
            ))

        finally:
            # Release the truth probe's long-lived BiDi session (Firefox caps
            # concurrent sessions; leaking one per training run exhausts it).
            try:
                if getattr(servo, "truth_probe", None):
                    servo.truth_probe.close()
            except Exception:
                pass
            with self._lock:
                if self._active_task_id == task_id:
                    self._active = False
                    self._current_task = None
                    self._active_task_id = ""
                    self._task_context = None

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

                def _apprentice_emit(event: str, payload: dict) -> None:
                    """Adapt the engine's emit_fn(event, payload) contract to
                    the kwarg-style socketio emitters."""
                    try:
                        if event == "step_preview":
                            emit_step_preview(
                                demonstration_id=payload.get("demonstration_id"),
                                step_index=payload.get("step_index", 0),
                                target_description=payload.get(
                                    "target_description", ""),
                                action_type=payload.get("action_type", ""),
                                confidence=payload.get("confidence", 1.0),
                            )
                        elif event in ("step_complete", "step_executed"):
                            emit_step_executed(
                                demonstration_id=payload.get("demonstration_id"),
                                step_index=payload.get("step_index", 0),
                                success=payload.get("success", False),
                                action_type=payload.get("action_type", ""),
                            )
                        elif event == "learning_question":
                            emit_learning_question(
                                question_id=payload.get("question_id", ""),
                                question_type=payload.get("question_type", ""),
                                text=payload.get("text", ""),
                                demonstration_id=payload.get(
                                    "demonstration_id"),
                                step_index=payload.get("step_index"),
                                options=payload.get("options"),
                            )
                    except Exception as emit_err:  # noqa: BLE001
                        logger.warning(
                            f"[APPRENTICE] emit {event} failed: {emit_err}")

                result = engine.execute(
                    steps=steps,
                    autonomy_level=level,
                    demonstration_id=demonstration_id,
                    emit_fn=_apprentice_emit,
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
    def _get_desktop_state(display: Optional[str] = None) -> str:
        """Query the actual state of the virtual desktop.

        Returns a compact text summary of what's running — ground truth
        from the window manager, not a vision model guess.  Takes <10ms.
        """
        import subprocess
        # Always query the agent's configured virtual display, not the user's desktop.
        display = display or os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")
        env = {**os.environ, "DISPLAY": display}

        try:
            import re as _re

            # A display that cannot be opened makes every search below come back
            # empty, which would read as "no windows open".
            probe = subprocess.run(
                ["xdotool", "getdisplaygeometry"],
                capture_output=True, text=True, timeout=2, env=env,
            )
            if probe.returncode != 0:
                logger.debug(f"Desktop state: display {display} not reachable: {probe.stderr.strip()[:120]}")
                return "Desktop state: unknown (display not reachable)"

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

    # A task that is nothing but opening or closing one app, anchored like the
    # recipe triggers. Anything after the app ("open firefox and go to
    # reddit.com", "open the settings in firefox") is not met by a window
    # appearing or going away.
    _BARE_OPEN_TASK_RE = re.compile(
        r"^\s*(?:please\s+)?(?:open|start|launch)\s+(?:the\s+)?"
        r"(?:firefox|chrome|chromium|browser|terminal)\s*[.!]?\s*$")
    _BARE_CLOSE_TASK_RE = re.compile(
        r"^\s*(?:please\s+)?(?:(?:close|quit|exit)\s+(?:the\s+)?"
        r"(?:firefox|chrome|chromium|browser|terminal)|close\s+all\s+(?:the\s+)?windows)"
        r"\s*[.!]?\s*$")

    @staticmethod
    def _check_early_done(task: str, display: Optional[str] = None) -> str:
        """Check if the task goal is obviously met based on desktop state.

        Only a bare "open <app>" or "close <app>" task (or "close all
        windows") is checked; any other task returns "" without a query.
        Returns a reason string if done, empty string if not or if the
        desktop state could not be read.
        Fast check (<20ms) — no vision model, just xdotool queries.
        """
        task_lower = task.lower()
        is_close = bool(AgentControlService._BARE_CLOSE_TASK_RE.match(task_lower))
        is_open = bool(AgentControlService._BARE_OPEN_TASK_RE.match(task_lower))
        if not (is_close or is_open):
            return ""
        desktop = AgentControlService._get_desktop_state(display=display)
        # An unread desktop lists no windows, which would look like every app had closed.
        if desktop.startswith("Desktop state: unknown"):
            return ""
        titles = "\n".join(
            line for line in desktop.splitlines() if line.lstrip().startswith("- ")
        ).lower()

        # "Close X" tasks: if no windows are open, we're done
        if is_close:
            if "No application windows open" in desktop:
                return "no windows open — target closed"

            # Terminal titles read "user@host: ~", so a terminal can't be told apart by title.
            for app, window_names in (
                ("firefox", ("firefox",)),
                ("chromium", ("chromium", "chrome")),
                ("chrome", ("chrome", "chromium")),
                ("browser", ("firefox", "chromium", "chrome")),
            ):
                if app in task_lower and not any(name in titles for name in window_names):
                    return f"{app} no longer visible"

        # "Open X" tasks: if the target app is now visible
        if is_open:
            for app in ("firefox", "chrome", "chromium", "terminal"):
                if app in task_lower and app in titles:
                    return f"{app} is now open"

        return ""

    def _store_and_return(
        self,
        result: AgentResult,
        task_id: Optional[str] = None,
        task: Optional[str] = None,
    ) -> AgentResult:
        """Store result for status reporting and return it."""
        owns_active_task = not task_id or self._active_task_id == task_id
        if self._recipe_fallback_note:
            result.reason = f"{result.reason} ({self._recipe_fallback_note})"
            if owns_active_task:
                self._recipe_fallback_note = ""
        # Phase 4: persist session beliefs as belief_update memories. Lives
        # in this chokepoint so every task-exit path captures them, including
        # early-done, recipe-shortcut, and error returns. Errors swallowed —
        # a memory hiccup must never block task completion.
        try:
            if owns_active_task:
                self._write_session_lessons()
        except Exception as e:
            logger.debug(f"[AGENT][BELIEF] lesson-write skipped: {e}")
        # The episode record. Same chokepoint, same contract: every exit path
        # writes it, and a failed write never blocks task completion.
        if owns_active_task:
            self._persist_task_run(result, task_id=task_id, task=task)
        if not result.task:
            # Stamp the task so consumers (e.g. Phase 3 inducer) can match the
            # result against later feedback without depending on _current_task,
            # which gets cleared in the loop's finally block.
            result.task = task or self._current_task or ""
        if owns_active_task:
            self._last_result = result
        else:
            logger.warning(
                "[AGENT] Ignoring stale task result for %s; active task is %s",
                task_id,
                self._active_task_id,
            )
            return result
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
                    from backend.celery_app import celery as celery_app
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

    # ------------------------------------------------------------------
    # Episodic memory: agent_task_runs / agent_task_steps
    # ------------------------------------------------------------------
    #
    # Beliefs and lessons persisted long before the task did: the steps of a
    # task lived in _action_history and were overwritten by the next task, so
    # the agent could not look back at what it had done, in this task or the
    # last one. Every exit path now writes one run row and one row per step.

    _STOP_RULES = (
        ("completed_with_repetition", "loop"),
        ("completed (", "early_done"),
        ("completed", "done"),
        ("recipe", "recipe"),
        ("stalled_no_progress", "stalled"),
        ("max_iterations", "ceiling"),
        ("timeout", "timeout"),
        ("killed", "killed"),
        ("superseded", "superseded"),
        ("max_failures", "max_failures"),
        ("loop_detected", "loop"),
        ("budget_exhausted", "budget"),
        ("click_budget_spent", "budget"),
        ("target_not_found", "not_found"),
        ("error", "error"),
    )

    @classmethod
    def _stop_rule_from_reason(cls, reason: str) -> str:
        r = (reason or "").strip()
        for prefix, rule in cls._STOP_RULES:
            if r.startswith(prefix):
                return rule
        return "other"

    @staticmethod
    def _task_key(task: str) -> str:
        import hashlib
        normalised = " ".join((task or "").lower().split())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _json_safe(value):
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return {"unserialisable": str(type(value))}

    def _app_context(self):
        """The caller's app context, or the real app's when there is none.

        Never the real app's under GUAARDVARK_MODE=test: a mocked loop test
        with no app context wrote three episodes into the live database on
        2026-09-23 through this fallback. Tests that want the tables push
        their own sqlite context; everything else skips the write.
        """
        from flask import has_app_context
        from contextlib import nullcontext
        if has_app_context():
            return nullcontext()
        if os.environ.get("GUAARDVARK_MODE") == "test":
            raise RuntimeError("no app context in test mode; episode not written")
        from backend.app import app as _flask_app
        return _flask_app.app_context()

    def _persist_task_run(self, result: AgentResult, task_id: Optional[str], task: Optional[str]) -> str:
        """Write the run and its steps. Returns the run id, or "" when skipped."""
        try:
            from datetime import datetime as _dt
            from backend.models import db, AgentTaskRun, AgentTaskStep
            task_text = task or result.task or self._current_task or ""
            steps = list(result.steps or [])
            started = self._task_started_at or (time.time() - (result.total_time_seconds or 0.0))
            ended = time.time()
            be = getattr(self, "_brain_eye", None)
            if getattr(self, "_training_mode", False):
                mode = "training"
            elif self._task_session_id:
                mode = "chat"
            else:
                mode = "api"
            run = AgentTaskRun(
                task=task_text,
                task_key=self._task_key(task_text),
                session_id=self._task_session_id,
                brain=getattr(be, "brain", None),
                eye=getattr(be, "eye", None),
                unified=bool(getattr(be, "unified", True)),
                mode=mode,
                started_at=_dt.fromtimestamp(started),
                ended_at=_dt.fromtimestamp(ended),
                total_seconds=float(result.total_time_seconds or (ended - started)),
                success=bool(result.success),
                reason=(result.reason or "")[:128],
                stop_rule=self._stop_rule_from_reason(result.reason),
                verified=bool(result.verified),
                steps_count=len(steps),
                click_attempts=sum(1 for st in steps
                                   if getattr(st.action, "action_type", "") in self._CLICK_FAMILY),
            )
            with self._app_context():
                db.session.add(run)
                db.session.flush()
                for st in steps:
                    a = st.action
                    r = st.result or {}
                    attempt = r.get("attempt")
                    db.session.add(AgentTaskStep(
                        run_id=run.id,
                        iteration=int(st.iteration or 0),
                        action_type=a.action_type,
                        target=a.target_description or None,
                        text=a.text or None,
                        keys=list(a.keys) if a.keys else None,
                        coordinates=list(a.coordinates) if a.coordinates else None,
                        failed=bool(st.failed),
                        verified=bool(r.get("verified", False)),
                        post_action_effect=(str(r.get("post_action_effect") or "")[:64] or None),
                        reason=(str(r.get("reason") or "")[:128] or None),
                        reasoning=a.reasoning or None,
                        expected_effect=a.expected_effect or None,
                        success_proof=a.success_proof or None,
                        scene_description=st.scene_description or None,
                        servo_attempt=int(attempt) if isinstance(attempt, (int, float)) else None,
                        result=self._json_safe(r),
                        created_at=_dt.fromtimestamp(st.timestamp) if st.timestamp else None,
                    ))
                db.session.commit()
                result.run_id = run.id
                logger.info(f"[AGENT][EPISODE] run {run.id} written: {len(steps)} steps, {run.stop_rule}")
                return run.id
        except Exception as e:
            logger.warning(f"[AGENT][EPISODE] run not written: {e}")
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
            return ""

    def _prior_run_note_for(self, task: str, max_age_days: int = 30) -> str:
        """One line about the most recent run of this exact task, or ""."""
        try:
            from datetime import datetime as _dt, timedelta
            from backend.models import db, AgentTaskRun, AgentTaskStep
            key = self._task_key(task)
            with self._app_context():
                run = (db.session.query(AgentTaskRun)
                       .filter(AgentTaskRun.task_key == key,
                               AgentTaskRun.ended_at >= _dt.now() - timedelta(days=max_age_days))
                       .order_by(AgentTaskRun.ended_at.desc())
                       .first())
                if run is None:
                    return ""
                steps = (db.session.query(AgentTaskStep)
                         .filter(AgentTaskStep.run_id == run.id)
                         .order_by(AgentTaskStep.iteration.asc(), AgentTaskStep.created_at.asc())
                         .all())
                when = run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "earlier"
                # Counts only, never the steps. Shown the previous run's steps
                # as a trace ("click red dot A, click blue dot B, ..., done"),
                # gemma4:12b read it as a script and, having finished A-E,
                # started again from A until the timeout (2026-09-23 20:43,
                # run 242cd07c). The done guard's own wait_until_visible rows
                # are not actions the model took, so they are not counted.
                acted = [st for st in steps if st.action_type not in ("wait_until_visible", "done")]
                if run.success:
                    return (f"This exact task succeeded before ({when}, {len(acted)} actions). "
                            f"The Done list below is THIS attempt only; say done once it shows the work.")
                return (f"The last attempt at this exact task ({when}) ended \"{run.reason}\" after "
                        f"{len(acted)} actions without finishing. The Done list below is THIS attempt "
                        f"only; do not redo work it already shows.")
        except Exception as e:
            logger.debug(f"[AGENT][EPISODE] prior-run lookup skipped: {e}")
            return ""

    def _prior_run_block(self) -> str:
        note = (getattr(self, "_prior_run_note", "") or "").strip()
        return f"{note}\n" if note else ""

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

    # The before/after comparison works on 8 px blocks: a block changed when
    # any pixel in it moved by more than 24 grey levels.
    _CHANGE_BLOCK = 8
    _CHANGE_PIXEL_DELTA = 24
    # A type or hotkey is visible at 4 changed blocks (two or three
    # characters of 14 px text); a scroll at 1% of the screen, spread over a
    # tenth of its width (a 100 px pane on the 1000 px agent display).
    # Measured there: a wheel event at the bottom of a page changed 0.92%,
    # all of it the overlay scrollbar strip (~1 block column); real scrolls
    # changed 8-50%.
    _CHANGE_MIN_BLOCKS = 4
    _SCROLL_MIN_SHARE = 0.01
    _SCROLL_MIN_WIDTH = 0.10
    # Half-width and half-height of the box around a clicked field that typed
    # text has to change. Wide because text starts at the field's left edge,
    # wherever in the field the click landed.
    _FIELD_BOX = (450, 200)

    @classmethod
    def _changed_blocks(cls, a, b):
        """Block grid of where frame ``b`` differs from frame ``a``, or None."""
        import numpy as np
        if a is None or b is None or a.size != b.size:
            return None
        ga = np.asarray(a.convert("L"), dtype=np.int16)
        gb = np.asarray(b.convert("L"), dtype=np.int16)
        s = cls._CHANGE_BLOCK
        h, w = (ga.shape[0] // s) * s, (ga.shape[1] // s) * s
        if not h or not w:
            return None
        diff = np.abs(ga[:h, :w] - gb[:h, :w]) > cls._CHANGE_PIXEL_DELTA
        return diff.reshape(h // s, s, w // s, s).any(axis=(1, 3))

    @classmethod
    def _screen_change(cls, seen, before, after, near: Optional[Tuple[int, int]] = None,
                       box: Optional[Tuple[int, int]] = None):
        """What an action changed on screen, leaving out what moves by itself.

        ``seen`` is the frame the model decided on, ``before`` the one taken
        just before acting. Blocks that differ between those two (a playing
        video, a spinner, a blinking caret) changed without the action and
        are not counted. This replaced a comparison of the whole screen's
        mean brightness, which a playing video passed every time: a scroll
        that moved nothing and text that reached no field both read as done.

        Returns {"changed_blocks", "share", "live_blocks"} plus "near_blocks"
        (changes inside ``box``, default _FIELD_BOX, around ``near``) when
        ``near`` is given; None when the frames can't be compared.
        """
        moved = cls._changed_blocks(before, after)
        if moved is None:
            return None
        live = cls._changed_blocks(seen, before)
        live_count = 0
        if live is not None:
            live_count = int(live.sum())
            grown = live.copy()
            if live_count >= 20:
                # One block of margin, so the edge of a playing video counts
                # as live. Not for a few blocks (a blinking caret): grown, it
                # would hide the first characters typed next to it.
                grown[1:, :] |= live[:-1, :]
                grown[:-1, :] |= live[1:, :]
                grown[:, 1:] |= live[:, :-1]
                grown[:, :-1] |= live[:, 1:]
            moved = moved & ~grown
        changed = int(moved.sum())
        columns = moved.shape[1]
        out = {
            "changed_blocks": changed,
            "share": changed / moved.size if moved.size else 0.0,
            # Share of block columns with any change: a page that scrolled
            # changes across its width, an overlay scrollbar flashing on the
            # wheel event changes one or two columns at the edge.
            "width_share": int(moved.any(axis=0).sum()) / columns if columns else 0.0,
            "live_blocks": live_count,
        }
        if near is not None:
            s = cls._CHANGE_BLOCK
            bx, by = box or cls._FIELD_BOX
            x0, x1 = max(0, (near[0] - bx) // s), max(0, (near[0] + bx) // s + 1)
            y0, y1 = max(0, (near[1] - by) // s), max(0, (near[1] + by) // s + 1)
            out["near_blocks"] = int(moved[y0:y1, x0:x1].sum())
        return out

    def _capture_with_retry(self, screen, max_retries: int = 3) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture a healthy frame or fail before poisoning the vision loop."""
        last_error = ""
        last_frame: Optional[Tuple[Image.Image, Tuple[int, int]]] = None
        for attempt in range(1, max_retries + 1):
            try:
                screenshot, cursor_pos = screen.capture()
                last_frame = (screenshot, cursor_pos)
                if screenshot.width < 10 or screenshot.height < 10:
                    last_error = f"tiny frame {screenshot.width}x{screenshot.height}"
                elif self._is_black_frame(screenshot):
                    last_error = "black frame"
                else:
                    return screenshot, cursor_pos
            except Exception as e:
                last_error = str(e)
            logger.warning(
                "[AGENT][DISPLAY] unhealthy capture attempt %s/%s: %s",
                attempt,
                max_retries,
                last_error or "unknown",
            )
            time.sleep(0.4 * attempt)

        if last_frame is not None:
            screenshot, cursor_pos = last_frame
            if not self._is_black_frame(screenshot):
                return screenshot, cursor_pos
        raise RuntimeError(
            f"agent display capture unhealthy after {max_retries} attempts: {last_error or 'unknown'}"
        )

    def check_display_health(self, screen=None) -> Dict[str, Any]:
        """Lightweight status check for the virtual display before agent work."""
        try:
            if screen is None:
                from backend.services.local_screen_backend import LocalScreenBackend
                screen = LocalScreenBackend()
            screenshot, cursor_pos = self._capture_with_retry(screen, max_retries=2)
            return {
                "success": True,
                "display": getattr(screen, "display", os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")),
                "screen_size": [screenshot.width, screenshot.height],
                "cursor_pos": list(cursor_pos),
                "black_frame": self._is_black_frame(screenshot),
            }
        except Exception as e:
            return {
                "success": False,
                "display": getattr(screen, "display", os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")),
                "error": str(e),
            }

    def _execute_action(self, action: AgentAction, screen) -> Dict[str, Any]:
        """Execute a single agent action via the screen interface.

        For click-family + gesture actions (click, right_click, double_click,
        triple_click, drag, hover) the main execute_task loop normally handles
        target acquisition via servo before calling here. This dispatcher is
        the fallback when coordinates are already pre-computed.
        """
        try:
            if action.action_type in ("click", "right_click"):
                if action.coordinates:
                    button = "right" if action.action_type == "right_click" else "left"
                    return screen.click(action.coordinates[0], action.coordinates[1], button=button)
                else:
                    return {"success": False, "error": "No coordinates for click"}

            elif action.action_type in ("double_click", "triple_click", "hover"):
                if not action.coordinates:
                    return {"success": False, "error": f"No coordinates for {action.action_type}"}
                x, y = action.coordinates
                if action.action_type == "double_click":
                    return screen.double_click(x, y)
                if action.action_type == "triple_click":
                    return screen.triple_click(x, y)
                return screen.hover(x, y)

            elif action.action_type == "drag":
                # Requires pre-computed source AND destination coords. The
                # main loop's drag branch produces both via servo; this
                # fallback path doesn't try to be clever about resolving
                # drag_to_description on its own.
                if not action.coordinates:
                    return {"success": False, "error": "No source coordinates for drag"}
                # Destination has to come from somewhere — use scroll_amount
                # as a sentinel value isn't right. Refuse without explicit
                # endpoints; the main loop handles the resolved-coords case.
                return {"success": False, "error": "drag in _execute_action requires main-loop routing"}

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

            elif action.action_type == "navigate":
                if action.url:
                    screen.hotkey("ctrl", "l")
                    time.sleep(0.3)
                    screen.hotkey("ctrl", "a")
                    time.sleep(0.1)
                    screen.type_text(action.url)
                    time.sleep(0.2)
                    screen.hotkey("Return")
                    return {"success": True, "navigated": action.url}
                return {"success": False, "error": "No url provided for navigate"}

            elif action.action_type == "wait":
                # Patience action — for transient screens (mid-load, focus loss,
                # animation in flight). Floor 2.1s (deliberately odd so it
                # stands out on grep); model can request longer via scroll_amount.
                seconds = max(2.1, min(float(action.scroll_amount or 1.5), 8.0))
                time.sleep(seconds)
                return {"success": True, "waited": seconds}

            elif action.action_type == "tool":
                # Support natural language or direct invocation of any tool in the full agent toolbox from the screen loop.
                # This unifies general tools (web, code, media, memory, etc.) with screen skills/recipes for true NL control in /agent mode.
                # Execution goes through the central registry (same as deliberation path).
                if not action.tool_name:
                    return {"success": False, "error": "tool action requires tool_name"}
                try:
                    from backend.tools.tool_registry_init import initialize_all_tools
                    registry = initialize_all_tools()
                    if action.tool_name not in registry.list_tools():
                        return {"success": False, "error": f"unknown tool {action.tool_name}"}
                    res = registry.execute_tool(action.tool_name, **(action.tool_params or {}))
                    return {"success": res.success, "tool_result": res.output if res.success else res.error, "tool": action.tool_name}
                except Exception as e:
                    return {"success": False, "error": f"tool execution failed: {e}"}

            else:
                return {"success": False, "error": f"Unknown action: {action.action_type}"}

        except Exception as e:
            logger.error(f"Action execution error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # Obstacles of one type ASSESS handles per task before leaving the screen
    # to THINK: a banner Escape did not close the first two times stays open.
    _OBSTACLE_TRIES_PER_TYPE = 2

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
            "cookie": ["accept cookies", "cookie consent", "gdpr"],
            "error": ["page not found", "404", "server error", "500", "connection refused"],
        }

        detected_type = None
        for obs_type, keywords in obstacle_patterns.items():
            if any(kw in scene_lower for kw in keywords):
                detected_type = obs_type
                break

        if not detected_type:
            return "clear"

        tries = self._obstacle_counts.get(detected_type, 0)
        if tries >= self._OBSTACLE_TRIES_PER_TYPE:
            logger.info(
                f"[AGENT][STEP {iteration+1}][ASSESS] {detected_type} obstacle already handled "
                f"{tries}x this task; leaving it to THINK"
            )
            return "clear"

        def _counted(outcome: str) -> str:
            self._obstacle_counts[detected_type] = tries + 1
            return outcome

        logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Obstacle detected: {detected_type}")

        # Stage 1: Fast model handles known obstacles with simple actions
        if detected_type == "permission":
            # Permission dialogs: try Escape, then look for Block/Deny button
            screen.hotkey("Escape")
            _time.sleep(0.5)
            logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Tried Escape for permission dialog")
            return _counted("handled")

        elif detected_type == "restore":
            # Restore session bars: click the X dismiss button (far right)
            screen.hotkey("Escape")
            _time.sleep(0.3)
            # The X is typically at the far right of the notification bar
            # (1000, 100) is a safe bet for a 1024px screen
            screen.click(1000, 100)
            _time.sleep(0.5)
            logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Dismissed restore session bar")
            return _counted("handled")

        elif detected_type == "cookie":
            screen.hotkey("Escape")
            _time.sleep(0.5)
            logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Tried Escape for cookie banner")
            return _counted("handled")

        elif detected_type == "error":
            # 404 or connection error — this is informational, not blocking
            # Let the THINK step handle it (might need to navigate elsewhere)
            logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Error page detected, letting THINK handle")
            return "clear"

        # Stage 2: Unknown popup — escalate to thinking model
        logger.info(f"[AGENT][STEP {iteration+1}][ASSESS] Unknown obstacle, escalating to thinking model")

        thinking_model = self._get_thinking_model()
        if not thinking_model:
            # No dedicated thinking model installed (the historical list named
            # two tags this box has never had). Use the brain if Ollama says it
            # can think; otherwise fall through to the Escape fallback below.
            try:
                from backend.services.model_capability_resolver import resolve
                be = getattr(self, "_brain_eye", None)
                if be and be.brain and resolve(be.brain, "agent_screen").supports_thinking:
                    thinking_model = be.brain
            except Exception:
                thinking_model = ""
        if not thinking_model:
            # No thinking model available, try Escape as fallback
            screen.hotkey("Escape")
            _time.sleep(0.5)
            return _counted("handled")

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
            logger.debug(f"[AGENT][STEP {iteration+1}][ASSESS][THINKING] {thinking_result.description[:200]}")
            decision = self._parse_decision(thinking_result.description)
            if ((decision.action.action_type == "click" and decision.action.target_description)
                    or (decision.action.action_type == "hotkey" and decision.action.keys)):
                # The same checks a THINK step gets: banned targets, cooldowns.
                refusal = self._refusal_for(
                    decision.action, training_mode=getattr(self, "_training_mode", False))
                if refusal:
                    logger.warning(
                        f"[AGENT][STEP {iteration+1}][ASSESS] Escalation "
                        f"{decision.action.action_type} not sent: {refusal}"
                    )
                    return "clear"
            if decision.action.action_type == "click" and decision.action.target_description:
                from backend.services.servo_controller import ServoController
                from backend.services.training_data_collector import TrainingDataCollector
                from backend.services.servo_knowledge_store import get_vision_config as _gvc2
                servo = ServoController(
                    screen,
                    analyzer,
                    collector=TrainingDataCollector(),
                    vision_config=_gvc2(getattr(analyzer, "default_model", "")),
                )
                servo.click_target(decision.action.target_description)
            elif decision.action.action_type == "hotkey" and decision.action.keys:
                screen.hotkey(*decision.action.keys)
            else:
                screen.hotkey("Escape")
            _time.sleep(0.5)
            return _counted("escalated")

        # Thinking model also failed — last resort Escape
        screen.hotkey("Escape")
        _time.sleep(0.5)
        return _counted("handled")

    def _refresh_dom_snapshot(self) -> None:
        """Pull a fresh DOM snapshot from Firefox once per iteration.

        Stored on the instance so the prompt builder and the click-time DOM-match
        guard see the same elements. Silently no-ops when DOM-assist is disabled
        (the default — see dom_metadata_extractor.dom_assist_enabled), when the
        unified model isn't gemma4, or when Bidi/Firefox isn't reachable. With
        snapshot=None, `_dom_match` returns True for everything so clicks aren't
        blocked on screens we can't introspect.
        """
        self._dom_snapshot = None
        try:
            from backend.services.dom_metadata_extractor import (
                DOMMetadataExtractor,
                dom_assist_enabled,
                dom_grounding_enabled,
            )
            # Grounding (prompt inventory) is model-agnostic — the inventory is
            # text any model reads. The coord click-shortcut (dom_assist) is the
            # gemma4-only, default-off path. Pull a snapshot if either wants it;
            # extract() is cached (1s TTL) so a second reader this iteration is free.
            if not (dom_grounding_enabled() or dom_assist_enabled()):
                return
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

    # ---- prompt blocks shared by the unified and split builders ----------
    # One copy each. Split mode used to carry its own weaker versions of
    # several of these (a loop warning at 3, when the loop breaker also fires
    # at 3) and none at all of the others.

    _STATE_MANAGEMENT = (
        "State Management: You must track task status (INITIAL -> IN_PROGRESS -> COMPLETE). "
        "IMPORTANT: If the goal state is visible (e.g., the window you wanted to open is open, the comment you posted is visible), "
        "you MUST immediately set status='COMPLETE' and action='done' without performing "
        "any additional waiting, scrolling, or hotkey actions. PRIORITIZE THE GOAL OVER THE PROCESS. "
        "Even if a previous step was marked [FAIL] in history, if the goal is now visible, the task is COMPLETE."
    )
    _TARGET_DESCRIPTION_RULES = (
        "target_description rules: SHORT label, ≤6 words, one distinctive adjective. Examples: \"primary submit button\", \"chat input field\", \"main navigation icon\", \"desktop background\". NOT a multi-clause description with position phrases — long descriptions break the vision detector and land at (0,0). Describe one shape (color, label, or icon), not a sentence. When an \"Interactive elements on this page\" list is present above, prefer a target_description that matches one of those real element labels; if the field you need is already marked (focused), skip the click and type directly."
    )
    _EXPECTED_EFFECT_RULE = (
        "For high-impact clicks (launch, submit, comment, modal dismiss), set expected_effect to the visible state that should appear after the click. Keep it short and vision-checkable."
    )
    _DONE_RULE = (
        "done rule: when action=\"done\", success_proof MUST describe the visible state that proves the task is complete (e.g. \"cursor inside text area\", \"comment now visible in thread\"). Empty or generic (\"n/a\", \"task done\") is rejected. This rule applies to all models and paths.\n"
        "When your most recent history step shows [OK] for a concrete target (e.g. \"first video thumbnail [OK]\" or servo DPC verified change), base the success_proof directly on that target + \"now visible/achieved\". Prior servo-verified clicks are strong evidence the goal state is real; use them to ground your proof rather than re-inventing a description."
    )
    _SCHEMA_FULL = (
        "{\"status\": \"IN_PROGRESS|COMPLETE\", \"action\": \"click|right_click|type|hotkey|scroll|wait|done|navigate|tool\", \"target_description\": \"...\", \"text\": \"literal value only\", \"keys\": [\"ctrl\",\"t\"], \"scroll_amount\": -5, \"url\": \"https://...\", \"reasoning\": \"why\", \"expected_effect\": \"visible result after this action\", \"success_proof\": \"visible state proving done (only when action=done)\", \"tool_name\": \"optional for action=tool\", \"tool_params\": {}}"
    )
    # The same with the exact-point actions, offered only to tasks that give
    # coordinates or are about drawing (see _points_wanted).
    _SCHEMA_FULL_POINTS = _SCHEMA_FULL.replace(
        "\"action\": \"click|right_click|",
        "\"action\": \"click|click_at|draw|right_click|",
    ).replace(
        "\"target_description\": \"...\", ",
        "\"target_description\": \"...\", \"x\": 420, \"y\": 320, "
        "\"points\": [[400, 500], [450, 530], [500, 500]], ",
    )
    # The sign follows LocalScreenBackend.scroll (negative is down). With no
    # amount in the reply the loop scrolls down 5; an amount of 0 used to send
    # no wheel clicks at all and still be logged as a scroll.
    _SCROLL_RULE = (
        "scroll_amount: wheel notches, negative scrolls DOWN, positive scrolls UP (e.g. -5 = down 5). "
        "type sends keys to whatever has focus: click the text field first, in the step before you type."
    )
    _DEFAULT_SCROLL = -5
    _MAX_SCROLL = 15
    # Exact-point actions. Without them a model could only name a target for
    # the eye to find: coordinates in a task never reached a click, and
    # nothing could draw a line (2026-10-02, a smiley face in a paint
    # program landed every dot where the eye put "the left eye").
    _POINT_RULE = (
        "click_at clicks the exact screen pixel (x, y). draw presses the mouse at the first of "
        "points, moves through the rest in order and releases: one stroke, for lines and curves "
        "(use 5-20 points for a curve). Use these when the task gives coordinates or asks you to "
        "draw; to click a named button, link or field, use click with target_description. "
        "Coordinates are screen pixels, (0, 0) is the top-left corner{size}."
    )
    _MAX_DRAW_POINTS = 200
    # Ornith-1.5 35B answered "draw_stroke" twice before using "draw"
    # (2026-10-02); each was an unknown action and a lost step.
    _ACTION_ALIASES = {
        "draw_stroke": "draw", "stroke": "draw", "draw_line": "draw", "line": "draw",
        "polyline": "draw", "draw_path": "draw", "draw_curve": "draw",
        "click_point": "click_at", "click_xy": "click_at", "click_coordinates": "click_at",
        "click_at_point": "click_at",
    }
    _SCHEMA_MOUSE_ONLY = (
        "{\"status\": \"IN_PROGRESS|COMPLETE\", \"action\": \"click|right_click|done\", \"target_description\": \"...\", \"reasoning\": \"why\", \"expected_effect\": \"visible result after this action\", \"success_proof\": \"visible state proving done (only when action=done)\"}"
    )
    _TOOLBOX_NOTE = (
        "Full toolbox awareness (SkillOpt-style skills + tools): You have access to a large agent toolbox (code, web search/scrape, media/music, batch generation, memory/lessons, general tools, and screen control via these recipes/skills). In /agent mode with capable models (Gemma4+), use natural language to invoke any -- e.g. describe a general task to trigger tool calling, or screen task to use recipes + actions here. Recipes (skills) are optimized deterministic shortcuts for common screen patterns (triggers match natural language); the system auto-matches and executes them for reliability. For full list or self-introspection, call agent_status. Skills/recipes + self-knowledge are external (like SkillOpt .md artifacts) and can be auto-tuned from trajectories without changing model weights. Prioritize matching/using recipes for screen reliability; fall back to structured actions or general tools (use action=\"tool\", tool_name=..., tool_params=...) as needed. Cross-model: these skills make even smaller models effective at complex multi-step work."
    )

    _CLICK_FAMILY = ("click", "right_click", "double_click")

    # Effects the click verifiers record when the click site did not change.
    _NO_CHANGE_EFFECTS = ("not_observed", "no_visible_change")

    # Neutral on purpose. "It probably missed" sent a model that had hit all
    # five trainer dots back for a sixth click: on that page a hit changes
    # nothing at the dot, only the score in the header (2026-09-24 live run).
    _NO_CHANGE_LEGEND = (
        "[NO CHANGE] = the click was sent but nothing changed where it landed. That "
        "is not proof of a miss: check the screen (a counter, a new page, a marker) "
        "before clicking that target again.\n"
    )

    @classmethod
    def _step_status(cls, step) -> str:
        """The tag a history line carries: FAIL, NO CHANGE or OK.

        A click whose site showed no change is not OK. Tagged [OK], five
        missed dots read to the model as five hits, and it argued "done"
        from its own log while the screen said otherwise (2026-09-23 trainer
        runs, where every dot click was no_visible_change).
        """
        if step.failed:
            r = step.result or {}
            if r.get("refused"):
                return "NOT SENT"
            if (step.action.action_type in cls._CLICK_FAMILY
                    and r.get("reason") == "target_not_visible" and not r.get("target_found", False)):
                return "NOT ON SCREEN"
            return "FAIL"
        effect = str((step.result or {}).get("post_action_effect") or "")
        if step.action.action_type in cls._CLICK_FAMILY and effect in cls._NO_CHANGE_EFFECTS:
            return "NO CHANGE"
        return "OK"

    @classmethod
    def _repetition_verdict(cls, repeated) -> Tuple[bool, str]:
        """(success, reason) for a task whose last three steps were one action.

        Success only when none of them failed and at least one changed the
        screen (verified, or an effect other than no change): the work
        happened and the model fixated on it. A click that changed nothing
        is not a failed step, since [NO CHANGE] is advisory, so it is checked
        here.
        """
        if any(st.failed for st in repeated):
            return False, "loop_detected_no_progress"
        for st in repeated:
            r = st.result or {}
            if bool(r.get("verified")) or str(r.get("post_action_effect") or "") not in cls._NO_CHANGE_EFFECTS:
                return True, "completed_with_repetition"
        return False, "loop_detected_no_progress"

    @classmethod
    def _task_has_verified_click(cls, history) -> bool:
        """Did the latest click in this task change the screen where it landed.

        Gates the advisory "done" that overrides a proof the verifier could
        not see. Only clicks count: a hotkey is marked verified whatever the
        screen does, so a pressed Home key used to turn a rejected "done"
        into success after five clicks that changed nothing (2026-09-23).
        A click_at or draw counts too: it is verified only when pixels next
        to its points changed. A smiley drawn exactly as asked was otherwise
        refused "done" four times, because the eye would not call two dots
        and a line "a complete smiley face" (2026-10-02).
        Only the most recent click since the last navigate is read, failed or
        not: a verified click on a composer says nothing about the Post click
        after it.
        ``history`` is the current task's, reset when each task starts.
        """
        for st in reversed(history):
            kind = st.action.action_type
            if kind == "navigate":
                return False
            if kind not in cls._CLICK_FAMILY + ("click_at", "draw"):
                continue
            r = st.result or {}
            return not st.failed and (
                bool(r.get("verified")) or str(r.get("post_action_effect") or "") == "verified")
        return False

    _NUMBER_WORDS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
        "twenty": 20,
    }
    _BUDGET_WORDS_RE = re.compile(
        r"\b(?:budget|total|only|limit|max|maximum|at most|no more than|up to)\b")
    _CLICK_COUNT_RE = re.compile(
        r"\b(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")\s+(?:total\s+)?(?:mouse\s+)?"
        r"click(?:s|\s+attempts?)?\b"
        r"(?!\s+(?:attempts?\s+)?(?:per|each|for\s+each|on\s+each)\b)")

    @classmethod
    def _click_budget_from_task(cls, task: str) -> Optional[int]:
        """The total number of clicks the task allows, when it states one.

        "You have a budget of 5 click attempts total" and "You only have a
        total of 5 click attempts" give 5. A per-target limit ("One click
        attempt per dot") is not a total and gives nothing, and neither does
        a count with no limiting word near it ("click 3 buttons").
        """
        for sentence in re.split(r"(?<=[.!?;\n])\s*", (task or "").lower()):
            if not cls._BUDGET_WORDS_RE.search(sentence):
                continue
            m = cls._CLICK_COUNT_RE.search(sentence)
            if m:
                word = m.group(1)
                n = int(word) if word.isdigit() else cls._NUMBER_WORDS[word]
                if n > 0:
                    return n
        return None

    @staticmethod
    def _history_block(history, n: int, click_budget: Optional[int] = None) -> str:
        """Every step of this task, oldest first, with step and click counts.

        The model needs the whole task, not a window onto it: shown only its
        last three steps, a five-target task cycled through the first four
        targets until the timeout, each time "forgetting" the one that had
        just scrolled out of view (2026-09-23 dots run). ``n`` is the cap the
        caller declares (``config.max_iterations``); only training mode, whose
        history can reach a thousand steps, ever truncates, and the header
        says so. ``click_budget`` is the task's own stated click limit, shown
        beside the count so the model sees how many it has left.
        """
        if not history:
            return ""
        clicks = sum(1 for h in history
                     if h.action.action_type in AgentControlService._CLICK_FAMILY + ("click_at",))
        header = f"Done (steps: {len(history)}, click attempts: {clicks}"
        if click_budget is not None:
            header += f" of {click_budget} allowed"
        if len(history) > n:
            header += f"; showing last {n}"
        header += "):"
        steps = []
        for h in history[-n:]:
            status = AgentControlService._step_status(h)
            desc = h.action.text or h.action.target_description or str(h.action.keys or "")
            if h.action.action_type == "click_at" and h.action.coordinates:
                desc = f"at ({h.action.coordinates[0]}, {h.action.coordinates[1]})"
            elif h.action.action_type == "draw" and h.action.points:
                p0, pn = h.action.points[0], h.action.points[-1]
                desc = f"{len(h.action.points)} points from ({p0[0]}, {p0[1]}) to ({pn[0]}, {pn[1]})"
            steps.append(f"  {h.action.action_type}: {desc} [{status}]")
        legend = (AgentControlService._NO_CHANGE_LEGEND
                  if any(AgentControlService._step_status(h) == "NO CHANGE" for h in history[-n:])
                  else "")
        return header + "\n" + "\n".join(steps) + "\n" + legend

    @classmethod
    def _step_progress(cls, step: ActionStep, prior: List[ActionStep]) -> bool:
        """Did this step move the task: a verified screen change, or a click
        on a target the task had not clicked [OK] before. Failed steps never
        count as progress."""
        if step.failed:
            return False
        r = step.result or {}
        if bool(r.get("verified")) or str(r.get("post_action_effect") or "") == "verified":
            return True
        a = step.action
        if a.action_type in cls._CLICK_FAMILY:
            key = (a.target_description or "").strip().lower()
            seen = {
                (h.action.target_description or "").strip().lower()
                for h in prior
                if not h.failed and h.action.action_type in cls._CLICK_FAMILY
            }
            if key and key not in seen:
                return True
        return False

    def _note_progress(self, step: ActionStep, training_mode: bool = False) -> bool:
        """Update the stall counter with the step just recorded (the last entry
        of _action_history). True when the loop should stop as stalled."""
        if training_mode:
            return False
        prior = self._action_history[:-1] if self._action_history and self._action_history[-1] is step else list(self._action_history)
        if self._step_progress(step, prior):
            self._stall_steps = 0
        elif not step.failed:
            self._stall_steps += 1
        return self._stall_steps >= self.config.max_stall_steps

    @staticmethod
    def _repeat_block(history) -> str:
        """One line when a target already clicked [OK] was clicked [OK] again.

        Catches the non-consecutive repeat (A, B, C, D, A) that neither the
        pivot block nor the loop breaker can see: both look only at adjacent
        identical actions. Informational rather than a prohibition, because
        some tasks legitimately re-click a target ("next page" three times).
        Keyed on the normalised target string, so a target the model renames
        between steps is not counted; the full Done list still shows both.
        """
        counts: Dict[str, int] = {}
        names: Dict[str, str] = {}
        for h in history:
            if h.failed or h.action.action_type not in AgentControlService._CLICK_FAMILY:
                continue
            key = (h.action.target_description or "").strip().lower()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            names.setdefault(key, h.action.target_description.strip())
        repeats = [f'"{names[k]}" x{c}' for k, c in counts.items() if c >= 2]
        if not repeats:
            return ""
        return ("Already clicked [OK] more than once: " + ", ".join(repeats)
                + ". Every target marked [OK] above is done; if the task wants each "
                  "target once, pick one NOT in the Done list, or say done.\n")

    @staticmethod
    def _pivot_block(history) -> str:
        # Fire at 2 identical actions, not 3. The loop breaker also trips at
        # 3, so a warning at 3 never reaches the model; at 2 it gets one
        # iteration to actually pivot before the task is aborted.
        if not history or len(history) < 2:
            return ""
        last_full = [
            (h.action.action_type, h.action.target_description, h.action.text, tuple(h.action.keys or []),
             h.action.scroll_amount, h.action.url, AgentControlService._point_identity(h.action))
            for h in history[-2:]
        ]
        if len(set(last_full)) != 1:
            return ""
        a_type, a_target, a_text, a_keys, a_scroll, a_url, _points = last_full[0]
        a_desc = a_target or a_text or (str(list(a_keys)) if a_keys else "") or a_url or (str(a_scroll) if a_scroll else "")
        # Concrete options the model can copy: gemma4:e4b acknowledged a soft
        # "pick something different" and repeated itself anyway. Keys only:
        # a named click target here is one the page may not have, and the
        # list used to offer "comment input field" to a model stuck on
        # exactly that target.
        options = [
            ("Escape", "release focus from search/address bar so scroll reaches page"),
            ("Home", "jump to top of page"),
            ("End", "jump to bottom"),
            ("Page_Up", "page up"),
            ("Page_Down", "page down"),
        ]
        repeated_keys = [k.lower() for k in a_keys] if a_type == "hotkey" else []
        lines = "".join(
            f"  • {{\"action\": \"hotkey\", \"keys\": [\"{key}\"], \"reasoning\": \"{why}\"}}\n"
            for key, why in options if [key.lower()] != repeated_keys
        )
        return (
            f"STOP. \"{a_type}: {a_desc}\" already failed TWICE in a row. "
            f"Doing it a third time will hard-abort the task — you will not "
            f"reach the goal by repeating this action.\n"
            f"YOUR NEXT ACTION MUST BE EXACTLY ONE OF THESE, or a click on something "
            f"you can actually see on the screen now:\n"
            f"{lines}"
            f"Pick one. Do not pick the exact same \"{a_type}: {a_desc}\" again.\n\n"
        )

    def _not_found_block(self) -> str:
        """The click targets held back because the eye could not find them."""
        if not self._banned_targets:
            return ""
        names = ", ".join(
            f"\"{name}\" ({self._not_found_counts.get(key, 0)} looks)"
            for key, name in self._banned_targets.items()
        )
        return (
            f"NOT ON SCREEN: {names}. The eye looked and did not find these, and the "
            f"screen has not changed since. A click on them will not be sent. Look at "
            f"the screen: scroll, go back, or click something that is there.\n\n"
        )

    @staticmethod
    def _training_override(training_mode: bool) -> str:
        return "\nTRAINING MODE: NEVER say done. Click the next target.\n" if training_mode else ""

    @staticmethod
    def _confidence_line(task: str, desktop_state: str) -> str:
        # Terse on purpose: verbose prompt text leaks into typed actions when
        # the model parrots context.
        confidence = (
            "Screen mid-load or transient: wait, do not quit. "
            "If the goal is already visible, you may output done on Step 1. Otherwise, Step 1 done is forbidden."
        )
        browser_visible = "firefox" in desktop_state.lower()
        is_web_task = any(w in task.lower() for w in ["google", "youtube", "reddit", "search", "navigate", "url", "http", "browser", "website", "web page"])
        if not browser_visible and is_web_task:
            return (
                "NO BROWSER VISIBLE: You MUST click the Firefox icon on the desktop first. "
                "The navigate action will NOT work until a browser window is on screen and focused. "
                "If the goal is already visible, you may output done on Step 1. Otherwise, Step 1 done is forbidden."
            )
        if not browser_visible:
            return "No browser visible, but task doesn't explicitly require one. If the goal is already visible, you may output done on Step 1. Otherwise, Step 1 done is forbidden."
        return "Browser is visible. " + confidence

    def _consume_world_observed_block(self) -> str:
        # Re-grounding output, when a stuck cluster fired one. Cleared once the
        # model has seen it so the next prompt isn't padded with a stale
        # observation. Split mode used to set this and never clear it.
        if not self._pending_world_observed:
            return ""
        block = self._pending_world_observed + "\n\n"
        self._pending_world_observed = ""
        return block

    @staticmethod
    def _chat_context_block(chat_context: str) -> str:
        return f"Recent conversation context:\n{chat_context}\n\n" if chat_context else ""

    def _build_unified_prompt(
        self,
        task: str,
        history,
        world_state: Optional[WorldState] = None,
        training_mode: bool = False,
        chat_context: str = "",
    ) -> str:
        """Build a compact prompt for unified vision+decision models.

        Shorter prompts = better detection accuracy. Only include what the model
        needs to pick the next action, and that includes every step it has
        already taken this task: a three-step window made a five-target task
        cycle through the first four targets until the timeout (2026-09-23).
        Assembled from the shared blocks above; the output is pinned
        byte-for-byte by tests/fixtures/unified_prompt_golden.txt.
        """
        # This prompt asks for success_proof; the done-guard keys on that fact.
        self._proof_contract = True
        done_lines = self._history_block(
            history, self.config.max_iterations, getattr(self, "_click_budget", None))
        repeat_block = "" if training_mode else self._repeat_block(history)
        prior_block = self._prior_run_block()
        pivot_block = self._pivot_block(history) + self._not_found_block() + self._steer_block()
        desktop_state = AgentControlService._get_desktop_state()
        training_override = self._training_override(training_mode)
        confidence = self._confidence_line(task, desktop_state)
        world_block = self._format_world_state_for_prompt(world_state or self._world_state)
        dom_grounding_block = self._format_dom_grounding_for_prompt()
        failure_block = self._format_failure_history()
        if failure_block:
            failure_block = failure_block + "\n\n"
        world_observed_block = self._consume_world_observed_block()
        chat_context_block = self._chat_context_block(chat_context)

        return f"""{pivot_block}{chat_context_block}Task: {task}

{desktop_state}
{world_block}
{dom_grounding_block}{world_observed_block}{failure_block}{prior_block}{done_lines}{repeat_block}{training_override}Step {len(history) + 1}. ONE next action. After Act the system ALWAYS re-captures the screen (re-See) before your next Think. {confidence}

{self._STATE_MANAGEMENT}

{self._TARGET_DESCRIPTION_RULES}

{self._EXPECTED_EFFECT_RULE}

{self._SCROLL_RULE}

{self._point_block()}{self._DONE_RULE}

Reply ONLY with JSON:
{self._schema_full()}

{self._TOOLBOX_NOTE}
"""

    @staticmethod
    def _get_unified_model(active: str = "", screen=None) -> str:
        """Pick a model that can both see the screen and decide what to do on it.

        This used to be the literal list ``["gemma4:e4b"]``, which made the whole
        agent loop depend on one exact tag being installed. Three things went
        wrong with that beyond the obvious inflexibility:

        * Switching the chat model changed nothing here, so the dropdown was a
          lie as far as the screen agent was concerned.
        * When the tag was absent it returned "", and the empty string was also
          being used as the switch for done-proof enforcement — so an unrelated
          model choice silently turned off a safety check.
        * Nobody could evaluate an alternative, because an unconfigured model
          was read with the wrong axis order and therefore measured as useless.

        Ranking is: the user's own model if it can drive the screen, then by how
        well we know its coordinate convention, then by size. The confidence
        ordering is what keeps this a no-op where it matters — a hand-measured
        row outranks a probe, which outranks a family default.
        """
        try:
            from backend.services.model_capability_resolver import (
                MEASURED_CONFIDENCE, _installed, sees_natively, coords_for,
            )
        except Exception:  # resolver unavailable — keep the old floor
            return "gemma4:e4b"

        def _drivable(tag: str) -> bool:
            # Cheap: vision truth + convention only. resolve() would also
            # compute who lends this tag eyes, which for a blind tag ranks
            # every installed model — n² HTTP and a log line per candidate.
            try:
                if not sees_natively(tag):
                    return False
                c = coords_for(tag, screen)
                return bool(c.order and c.confidence >= MEASURED_CONFIDENCE)
            except Exception:
                return False

        if active and _drivable(active):
            return active

        from backend.services.model_capability_resolver import rank_eyes
        drivable = [tag for tag in _installed() if _drivable(tag)]
        if not drivable:
            return ""
        return rank_eyes(drivable, screen)[0]["tag"]

    @staticmethod
    def resolve_brain_eye(active: str = "", screen_size=None) -> BrainEye:
        """Decide who thinks and who looks for this task.

        The brain is the user's chosen model, always; GUAARDVARK_DECISION_MODEL
        is the one explicit override. If that model can see and point, it is
        also the eye and the loop runs unified. Otherwise the resolver lends
        the best measured eye and the loop runs split: the eye describes and
        points, the brain decides. Before this, a blind user model meant a
        third, auto-picked text model did the deciding — the dropdown was
        decorative as far as the screen agent was concerned.
        """
        from backend.services.model_capability_resolver import resolve, eyes_for

        active = active or _read_saved_active_model()
        brain = os.environ.get("GUAARDVARK_DECISION_MODEL") or active
        screen = tuple(screen_size) if screen_size else None

        def _drivable(tag: str) -> bool:
            try:
                p = resolve(tag, "agent_screen", screen=screen)
                return bool(p.sees_natively and p.can_drive_screen)
            except Exception:
                return False

        pick = AgentControlService._get_unified_model(active=brain, screen=screen)
        if brain and pick == brain:
            # Seeing is not pointing. A model measured to miss by more than a
            # control's width keeps deciding, and a model measured to point
            # well does the looking (the stock gemma4:e2b misses by a median
            # 255px; gemma4:12b hits within 6).
            try:
                from backend.services.model_capability_resolver import better_eye_for
                lend = better_eye_for(brain, screen)
            except Exception:
                lend = None
            if lend and _drivable(lend["tag"]):
                return BrainEye(
                    brain, lend["tag"], False, "sibling_vlm",
                    f"{brain} sees but points ~{lend['own_px']:.0f}px off; "
                    f"{lend['tag']} points within ~{lend['eye_px']:.0f}px, so it looks",
                )
            return BrainEye(brain, brain, True, "native", "model sees and points for itself")
        loaned = None
        if brain:
            try:
                loaned = eyes_for(brain, "agent_screen", screen).model
            except Exception:
                loaned = None
        eye = loaned if (loaned and _drivable(loaned)) else pick
        if not eye:
            return BrainEye(brain, "", False, "none", "no drivable eye installed")
        if not brain:
            return BrainEye(eye, eye, True, "native", "no saved model; best eye drives")
        return BrainEye(brain, eye, False, "sibling_vlm",
                        f"{brain} is blind or unprobed; {eye} looks and points")

    @staticmethod
    def _get_thinking_model() -> str:
        """Find the best available thinking model for obstacle escalation."""
        try:
            import requests as _requests
            response = _requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                for preferred in ["lfm2.5-thinking:1.2b-bf16", "deepseek-r1:8b"]:
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
            # A recipe runs before any model reads the request, so one that fails
            # validation (including the safety bounds in agent_knowledge_validator)
            # is never loaded. If the validator cannot run, none are: the agent
            # still works through its loop.
            refused = {}
            try:
                from backend.services.agent_knowledge_validator import validate_recipe
                warnings = []
                for name, recipe in data.items():
                    if name.startswith("_"):
                        continue
                    result = validate_recipe(name, recipe)
                    if not result.ok:
                        refused[name] = result.error_messages()
                    warnings.extend(i for i in result.issues if i.severity != "error")
                if refused:
                    logger.warning(
                        "[AGENT][RECIPE] not loading %d recipe(s) that fail validation: %s",
                        len(refused),
                        "; ".join(m for msgs in refused.values() for m in msgs[:2])[:1200],
                    )
                if warnings:
                    # Only debug-level for migration warnings (legacy waits, missing proof on old recipes)
                    logger.debug(
                        "[AGENT][RECIPE] validation warnings: %s",
                        "; ".join(f"{i.severity}:{i.path}:{i.message}" for i in warnings[:8]),
                    )
            except Exception as ve:
                logger.warning(f"[AGENT][RECIPE] validator unavailable, loading no recipes: {ve}")
                refused = {k: [] for k in data if not k.startswith("_")}
            cls._recipe_cache = {
                k: v for k, v in data.items() if not k.startswith("_") and k not in refused
            }
            cls._recipe_mtime = mtime
            logger.info(f"Loaded {len(cls._recipe_cache)} recipes from {path}")
            return cls._recipe_cache
        except Exception as e:
            logger.warning(f"Failed to load recipes: {e}")
            cls._recipe_cache = {}
            return {}

    @staticmethod
    def _recipe_disabled(name: str) -> bool:
        """Feedback can switch a recipe off (data/agent/recipe_stats.json)."""
        try:
            from backend.services import recipe_stats
            return recipe_stats.is_disabled(name)
        except Exception:
            return False

    @classmethod
    def _load_recipe_index(cls) -> str:
        """Render the recipe library as a one-line-per-recipe index for the
        agent's decision prompt. Recipes that match a task are auto-executed
        before the LLM ever sees the prompt — but listing them by description
        tells the LLM what shortcuts exist, so it can phrase its own steps the
        same way (vision-actionable description of what's on screen) instead of
        pixel hunting.
        """
        recipes = cls._load_recipes()
        if not recipes:
            return ""
        # Disabled recipes are not advertised (the matcher will not run them);
        # provisional ones, auto-induced and not yet confirmed, say so, and so
        # does one whose click target sessions keep not finding (verify_first,
        # proposed by the lesson reconciler and applied by a person).
        try:
            from backend.services import recipe_stats
            stats = recipe_stats.load()
        except Exception:
            stats = {}
        lines = []
        for name, recipe in recipes.items():
            desc = (recipe.get("description") or "").strip()
            st = stats.get(name) or {}
            if not desc or st.get("disabled"):
                continue
            suffix = " (provisional)" if st.get("provisional") else ""
            note = str(recipe.get("verify_first") or "").strip()
            if note:
                suffix += f" (verify first: {note})"
            lines.append(f"- {name}: {desc}{suffix}")
        return "\n".join(lines)

    # Generic UI words that carry no page-identity — a match on these alone must
    # NOT confirm a target (else "button"/"visible" false-positives everything).
    _VERIFY_STOPWORDS = frozenset({
        # Chrome / layout vocabulary
        "visible", "button", "field", "input", "page", "screen", "window", "icon",
        "open", "click", "now", "then", "should", "appear", "appears", "view",
        "area", "bar", "the", "and", "with", "into", "from", "this", "that",
        "your", "will", "show", "shows", "text", "box", "form", "link", "menu",
        "tab", "panel", "dialog", "modal", "header", "footer", "list", "item",
        "items", "loading", "load", "loaded",
        # State-change verbs. Without them "Firefox browser window opens and
        # becomes visible." left "becomes" over, missed the window check and
        # went to the vision model, which said no for 12s with Firefox up.
        "become", "becomes", "appeared", "displayed", "shown",
        # "URL address bar": browser chrome, not page content. With a blank
        # page loaded the eye said no to it for 15s while Firefox was up.
        "address",
        # Generic UI ACTION/label words — present on countless pages, so a match on
        # these alone must not confirm that a specific effect actually happened.
        "submit", "comment", "reply", "post", "send", "search", "save", "cancel",
        "close", "next", "back", "done", "enter", "sign", "share", "upload",
        "download", "play", "pause", "results", "result",
    })

    def _dom_confirms_target(self, target_description: str) -> bool:
        """Cheap, POSITIVE-ONLY verification via the live DOM (~50-200ms).

        True only when the target's distinctive (non-generic) words actually
        appear in the current page's title/url/element text — a real substring
        match, never a guess. False means "the DOM can't confirm it" (fall
        through to the vision poll), NOT "it isn't there". So this can only
        *short-circuit success*; it can never wrongly declare failure, and on
        pages the DOM can't introspect (desktop, canvas, PDF) it simply defers
        to vision. Pulls a FRESH snapshot so it reflects post-action state.
        """
        try:
            from backend.services.dom_metadata_extractor import (
                DOMMetadataExtractor,
                dom_grounding_enabled,
            )
            if not dom_grounding_enabled():
                return False
            snap = DOMMetadataExtractor.get_instance().extract()
        except Exception:
            return False
        if not snap or not getattr(snap, "success", False):
            return False

        target = (target_description or "").strip().lower()
        words = [w for w in re.split(r"[^a-z0-9]+", target)
                 if len(w) >= 4 and w not in self._VERIFY_STOPWORDS]
        if not words:
            return False

        url = (getattr(snap, "url", "") or "").lower()
        haystack = " ".join(filter(None, [
            (getattr(snap, "title", "") or ""),
            url,
            *[((getattr(el, "text", "") or "") + " " + (getattr(el, "element_type", "") or ""))
              for el in (getattr(snap, "elements", None) or [])],
        ])).lower()

        # A distinctive word appearing in the URL (e.g. "youtube", "reddit") is a
        # rock-solid navigation confirm on its own. Otherwise require a MAJORITY
        # of the distinctive words to appear — one shared token isn't enough.
        if any(w in url for w in words):
            return True
        hits = sum(1 for w in words if w in haystack)
        return hits >= max(1, (len(words) + 1) // 2)

    # Browser-chrome vocabulary. A verify target made ONLY of these + generic
    # stopwords ("Firefox browser window …") is asking about the WINDOW, not page
    # content — answerable by xdotool in ~10ms instead of a 12s vision poll.
    _BROWSER_CHROME_WORDS = frozenset({
        "firefox", "browser", "chrome", "chromium", "mozilla", "application",
        "launched", "launcher", "opened", "opens", "gains", "focus", "focused",
        "desktop", "mapped", "running",
    })

    def _is_pure_window_target(self, target_description: str) -> bool:
        """True when the target describes only browser-window/launch state.

        Requires at least one browser word AND no distinctive content words left
        after stripping chrome + generic vocabulary. "Firefox browser window with
        URL bar visible" → True (xdotool can answer). "YouTube page visible in
        browser" → False ("youtube" is content — stay on the DOM/vision path)."""
        target = (target_description or "").strip().lower()
        words = [w for w in re.split(r"[^a-z0-9]+", target) if len(w) >= 3]
        if not any(w in self._BROWSER_CHROME_WORDS for w in words):
            return False
        leftover = [
            w for w in words
            if len(w) >= 4
            and w not in self._VERIFY_STOPWORDS
            and w not in self._BROWSER_CHROME_WORDS
        ]
        return not leftover

    def _wait_until_visible(
        self,
        target_description: str,
        screen,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.6,
        allow_dom_fastpath: bool = True,
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
        # The task's eye verifies its own gates: the same model that looked and
        # pointed. Anything else (the analyzer's default is "whichever sighted
        # model is resident") judges a different picture than the one the loop
        # acted on, and a bake-off model left loaded by a sweep once vetoed a
        # correct "done". Resolved once per call (not per poll).
        be = getattr(self, "_brain_eye", None)
        verify_model = (be.eye if be is not None and be.eye else None) or analyzer.default_model
        deadline = _time.monotonic() + timeout_s
        polls = 0
        last_err = None

        # Classified once — the target string doesn't change across polls.
        pure_window_target = allow_dom_fastpath and self._is_pure_window_target(target_description)

        while _time.monotonic() < deadline:
            polls += 1
            # Window fast-path: launch-type gates ("Firefox browser window …")
            # can NEVER DOM-confirm (those words aren't in any page's DOM) and
            # the vision model is unreliable on them — live runs burned 12s+8s
            # gates while the window was demonstrably up. xdotool answers window
            # existence in ~10ms, checked EVERY poll so a window that maps 3s
            # after the click confirms at 3s (the old one-shot pre-check at the
            # launcher call site missed exactly that case).
            if pure_window_target:
                if self._is_firefox_running(screen):
                    logger.info(
                        f"[AGENT][GATE] visible via window-check: \"{target_description}\" (poll {polls})"
                    )
                    return {
                        "success": True,
                        "action": "wait_until_visible",
                        "target": target_description,
                        "polls": polls,
                        "via": "window",
                    }
                # Window not mapped yet. Don't burn a 1-2s GPU vision inference
                # per poll just to wait on a window xdotool checks in ~10ms —
                # that churns VRAM against the think model. Poll xdotool at full
                # speed; run the vision backstop only every 5th poll (in case
                # xdotool is broken or the window title doesn't match).
                if polls % 5 != 0:
                    _time.sleep(poll_interval_s)
                    continue
            # DOM fast-path: a cheap (~50-200ms) positive-only check BEFORE the
            # 1-2s vision inference. When the grounded DOM already shows the
            # target (navigation landed, results rendered, content present), this
            # short-circuits in one poll instead of burning the whole timeout on
            # vision — the fix for the 60s task-budget blowouts. Never confirms on
            # a guess; a miss just falls through to the vision poll below.
            if allow_dom_fastpath and self._dom_confirms_target(target_description):
                logger.info(
                    f"[AGENT][GATE] visible via DOM: \"{target_description}\" (poll {polls})"
                )
                return {
                    "success": True,
                    "action": "wait_until_visible",
                    "target": target_description,
                    "polls": polls,
                    "via": "dom",
                }
            try:
                img, _ = screen.capture()
                # Ask about a STATE, not a thing. Proofs arrive as sentences
                # ("Example Domain heading now visible"); asked "is the
                # following visible: <sentence>" three gemma sizes read it
                # literally and said no on a loaded example.com (measured
                # 2026-09-22, 3 of 4 eyes), and the loop rejected a correct
                # done eight times. Asked whether the screenshot shows that
                # state, all four said yes, and all four still said no on a
                # blank page.
                result = analyzer.analyze(
                    img,
                    prompt=(
                        f"Does this screenshot show the following state right now: \"{target_description}\"?\n"
                        "Answer with EXACTLY one word on the first line: yes or no.\n"
                        "Only say yes if the image itself shows it; do not guess."
                    ),
                    num_predict=8,
                    temperature=0.0,
                    model=verify_model,
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

    # Save-verb + learning-noun ("save your learnings", "store lessons in
    # memory", "update your knowledge") or a bare remember-this. Deliberately
    # narrow — a screen verb anywhere disqualifies (see _try_meta_memory_task).
    _META_MEMORY_RE = re.compile(
        r"\b(save|store|persist|write|commit|update)\b.{0,50}\b(learn\w*|lessons?|memor\w*|knowledge)\b"
        r"|\bremember\b.{0,30}\b(this|that|these|what|it)\b",
        re.IGNORECASE,
    )
    _SCREEN_VERB_RE = re.compile(
        r"\b(click|navigate|open|scroll|type|search|browser|page|website|url|"
        r"youtube|reddit|firefox|window|button|field|screen)\b",
        re.IGNORECASE,
    )

    def _try_meta_memory_task(self, task: str) -> 'AgentResult | None':
        """Route memory/meta requests off the screen loop entirely.

        Returns a completed AgentResult for tasks like "save your learnings to
        memory" — flushing session beliefs through the existing
        _write_session_lessons machinery — or None for everything else
        (including anything mentioning a screen element, which stays with the
        loop). Conservative on purpose: a false None costs a timeout, a false
        match would silently skip a real screen task.
        """
        t = (task or "").strip()
        if not t or len(t) > 140:
            return None
        if not self._META_MEMORY_RE.search(t):
            return None
        if self._SCREEN_VERB_RE.search(t):
            return None

        persisted = 0
        try:
            persisted = self._write_session_lessons()
        except Exception as e:
            logger.debug(f"[AGENT][META] lesson flush failed (non-fatal): {e}")

        logger.info(
            f"[AGENT][META] memory task handled off-screen: flushed {persisted} "
            f"session lesson(s); lessons also auto-persist at every task exit"
        )
        return AgentResult(
            success=True,
            reason=(
                f"learnings_saved ({persisted} new lesson(s) persisted this flush; "
                "lessons also save automatically at the end of every task)"
            ),
            verified=True,
            verified_reason="internal memory write — no screen proof applicable",
        )

    def _try_recipe(self, task: str, screen) -> 'AgentResult | None':
        """Match task against recipe library and execute deterministically."""
        import re
        task_stripped = task.strip()
        task_lower = task_stripped.lower()

        # Normalize common chat prefixes so natural language tasks like
        # "The task is to open Firefox and navigate to YouTube" still match
        # anchored recipe triggers such as "^(open|launch) firefox...".
        task_for_match = re.sub(
            r'^(the task is to |i (need|want|have) to |please |help me to |just |now )+',
            '',
            task_stripped,
            flags=re.IGNORECASE,
        ).strip()

        # Skip recipes for multi-step or compound tasks (still use the raw lower for detection),
        # *except* launch-first compounds like "open firefox and navigate...".
        # The open_firefox recipe includes its own wait_until_visible for the window effect.
        # This lets the good recipe (vision click icon + 12s wait) run for the prerequisite, preventing
        # the adaptive loop from repeatedly deciding "click Firefox icon" and failing the gate.
        is_launcher_compound = bool(re.search(r'\b(open|launch|start)\s+(firefox|browser|chrome)\b', task_lower))
        if not is_launcher_compound:
            if re.search(r'step\s*\d|^\d+\.\s|\n\d+\.|then\s+(?:type|press|click|open|navigate)', task_lower):
                return None
            if re.search(r'(?:go to|navigate to)\s+\S+.*\b(?:and|then)\b.*\b(?:click|check|find|look|tell|scroll|type|search|read|describe|suggest)', task_lower):
                return None
        if len(task_lower) > 200:
            return None

        # task_effective is what we feed to recipe trigger regexes. Original case
        # by default so capture groups preserve case-sensitive things like
        # YouTube video IDs. Page-route shorthand below replaces it with a
        # synthetic navigation phrase when "go to <known page>" matches.
        task_effective = task_for_match or task_stripped

        # Also handle "go to X page" → this install's web UI at localhost:<VITE_PORT>/X
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
                from backend.utils.cors_policy import vite_port
                task_effective = f"navigate to localhost:{vite_port()}{page_routes[page]}"

        recipes = self._load_recipes()
        for recipe_name, recipe in recipes.items():
            if self._recipe_disabled(recipe_name):
                logger.info(f"[AGENT][RECIPE] Skipping '{recipe_name}' — disabled by feedback")
                continue
            for pattern in recipe.get("triggers", []):
                match = re.search(pattern, task_effective, re.IGNORECASE)
                if match:
                    # If the recipe wants to LAUNCH Firefox but it's already running,
                    # just focus the existing window instead of the desktop-menu dance.
                    if recipe_name in ("open_firefox",) and self._is_firefox_running(screen):
                        logger.info(f"[AGENT][RECIPE] Skipping '{recipe_name}' — Firefox already running, focusing it")
                        return self._focus_firefox(screen)
                    # A recipe that posts publicly runs only on the site it is
                    # for. Anywhere else it refuses, before the gates below can
                    # defer it to the loop, which would post on the open page.
                    refusal = self._page_host_refusal(recipe_name)
                    if refusal:
                        logger.warning(f"[AGENT][RECIPE] Refusing '{recipe_name}' — {refusal}")
                        return AgentResult(
                            success=False,
                            reason=f"{RECIPE_REFUSED}: {recipe_name} {refusal}",
                            task=task,
                        )
                    # Recipes can declare preconditions for the UI state they assume.
                    # When the world doesn't match (e.g. Firefox is already up but the
                    # recipe wants to click a desktop launcher), skip — the see-think-act
                    # loop will handle it via vision instead of running a brittle script
                    # against a screen that doesn't match the recipe's assumptions.
                    if not self._preconditions_pass(recipe, screen):
                        logger.warning(
                            f"[AGENT][RECIPE] Skipping '{recipe_name}' — preconditions not met "
                            f"({recipe.get('preconditions')}); deferring to vision loop"
                        )
                        continue
                    logger.info(f"[AGENT][RECIPE] Matched '{recipe_name}': {recipe['description']}")
                    return self._execute_recipe(recipe_name, recipe, match, screen)

        return None

    def _preconditions_pass(self, recipe: dict, screen) -> bool:
        """Cheap precondition check before a recipe runs.

        Recipes declare a `preconditions` list of named gates. Each gate
        answers a yes/no question about the world; if any gate says no,
        the recipe is skipped and control falls back to the agent's
        see-think-act loop. Keep gates *cheap* — they run on every
        trigger match, on the hot path. No vision calls here.
        """
        conditions = recipe.get("preconditions") or []
        if not conditions:
            return True
        for cond in conditions:
            if cond == "firefox_not_running":
                if self._is_firefox_running(screen):
                    return False
            elif cond == "firefox_running":
                if not self._is_firefox_running(screen):
                    return False
            elif cond == "desktop_visible":
                # Best-effort heuristic: a Firefox window on top usually
                # covers the desktop, so treat firefox_running as
                # "desktop probably not visible". Cheaper than a VLM call.
                if self._is_firefox_running(screen):
                    return False
            else:
                # Unknown precondition — log and proceed; don't block
                # the recipe on a typo'd gate name.
                logger.warning(f"[AGENT][RECIPE] Unknown precondition '{cond}' — ignoring")
        return True

    # Recipes that post under the user's name, and the sites they post to.
    _RECIPE_PAGE_HOSTS = {"youtube_comment": ("youtube.com",)}

    def _page_host_refusal(self, recipe_name: str) -> str:
        """Why `recipe_name` may not run on the open page, or "" when it may.

        Only recipes in _RECIPE_PAGE_HOSTS are checked. A page whose address
        cannot be read is refused too: a public post needs a known target.
        """
        hosts = self._RECIPE_PAGE_HOSTS.get(recipe_name)
        if not hosts:
            return ""
        from backend.utils.hosts import host_matches, url_host
        wanted = " or ".join(hosts)
        url = self._current_page_url()
        if not url:
            return f"posts publicly and needs a {wanted} page open; the current page address could not be read"
        host = url_host(url)
        if host_matches(host, *hosts):
            return ""
        return f"posts publicly and needs a {wanted} page open; the current page is {host or url[:80]}"

    def _current_page_url(self) -> str:
        """Address of the page open in the agent Firefox, or "" when it cannot be read."""
        try:
            from backend.services.dom_metadata_extractor import DOMMetadataExtractor
            snap = DOMMetadataExtractor.get_instance().extract()
        except Exception:
            return ""
        if not snap or not getattr(snap, "success", False):
            return ""
        return getattr(snap, "url", "") or ""

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
        """Focus the existing Firefox window instead of launching a new one.

        Succeeds only when a Firefox window was found and windowactivate
        returned 0. On failure the reason stays "recipe:focus_firefox" (the
        recipe name is read from it) and the why is on the one failed step.
        """
        import subprocess, time as _time
        display = getattr(screen, 'display', os.environ.get('DISPLAY', ':99'))
        env = {**os.environ, "DISPLAY": display}
        start = _time.time()
        why = ""
        try:
            # Get Firefox window ID and activate it
            result = subprocess.run(
                ["xdotool", "search", "--name", "Mozilla Firefox"],
                capture_output=True, text=True, timeout=3, env=env,
            )
            wids = (result.stdout or "").strip().split()
            if not wids:
                why = "no Firefox window"
            else:
                activated = subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wids[0]],
                    capture_output=True, text=True, timeout=3, env=env,
                )
                if activated.returncode != 0:
                    why = ((activated.stderr or "").strip()
                           or f"windowactivate exited {activated.returncode}")
                else:
                    _time.sleep(0.5)
                    logger.info("[AGENT][RECIPE] Focused existing Firefox window")
        except Exception as e:
            why = str(e) or type(e).__name__

        elapsed = _time.time() - start
        if why:
            logger.warning(f"[AGENT][RECIPE] Firefox focus failed: {why}")
            return AgentResult(
                success=False, reason="recipe:focus_firefox",
                steps=[ActionStep(
                    scene_description="recipe:focus_firefox",
                    action=AgentAction(action_type="focus_window", target_description="Firefox"),
                    result={"success": False, "reason": why},
                    failed=True,
                )],
                total_time_seconds=elapsed,
            )
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
                from backend.services.training_data_collector import TrainingDataCollector
                # Point with the task's eye, as the loop does. The analyzer's
                # default is whichever sighted model is resident, which can be a
                # different model with its own calibration: gemma4:e4b's fit
                # sent a correct top-of-screen answer for "Firefox icon",
                # (299, 61), to (288, 0) while the task's eye was gemma4:12b.
                be = getattr(self, "_brain_eye", None)
                eye = be.eye if be is not None and be.eye else ""
                if not eye:
                    from backend.utils.vision_analyzer import VisionAnalyzer
                    eye = VisionAnalyzer().default_model
                _, servo_box["servo"] = build_servo(screen, eye, collector=TrainingDataCollector())
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
        #
        # 2026-05-14: ADVISORY when steps already reported OK. Same disease
        # as the click-level expected_effect verifier (false negatives on
        # slow-launching apps and modal dialogs covering the proof string).
        # If every step's servo result was [OK] but the proof string can't
        # be visually confirmed in time, log it loudly but DO NOT flip the
        # whole recipe to failed — the actions clearly happened. The next
        # see-think-act SEE pass observes actual screen state, which is
        # the real ground truth.
        proof = recipe.get("success_proof")
        proof_failed = False
        proof_unverified = False
        if all_steps_ok and proof:
            verify_timeout = float(recipe.get("success_proof_timeout_s", 8.0))
            verify = self._wait_until_visible(
                proof, screen, timeout_s=verify_timeout,
            )
            verify_ok = bool(verify.get("success", False))
            action_steps.append(ActionStep(
                iteration=step_num, scene_description=f"recipe:{name}:verify",
                action=AgentAction(
                    action_type="wait_until_visible",
                    target_description=proof,
                ),
                # Failed if the proof was not verified
                result=verify, failed=not verify_ok,
            ))
            if not verify_ok:
                proof_unverified = True
                all_steps_ok = False
                logger.warning(
                    f"[AGENT][RECIPE] {name}: steps OK; final verify "
                    f"UNCONFIRMED within {verify_timeout:.0f}s — '{proof}' not "
                    f"visible. Recipe marked as failed."
                )
                # Surface the unverified state to the chat thinking trail
                try:
                    self._emit_thinking(
                        iteration=step_num,
                        label=f"recipe verify unconfirmed: {name}",
                        reasoning=(
                            f"All recipe steps executed OK, but the final "
                            f"vision-check for '{proof}' didn't confirm in "
                            f"{verify_timeout:.0f}s. The recipe failed. "
                            f"Falling back to adaptive loop."
                        ),
                    )
                except Exception:
                    pass

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
            self.note_recipe_usage(getattr(self, "_current_task", "") or "", name, fallback=True)
            try:
                from backend.services import recipe_stats
                recipe_stats.touch_run(name, fallback=True)
            except Exception:
                pass
            self._action_history = action_steps
            # Tell the model what was just attempted so it pivots instead of
            # repeating the same recipe step blindly.
            try:
                step_summary = ", ".join(
                    f"{s.action.action_type}:{s.action.target_description or s.action.text or 'n/a'}"
                    f"[{'FAIL' if s.failed else 'OK'}]"
                    for s in action_steps[-4:]
                )
                self._emit_thinking(
                    iteration=len(action_steps),
                    label=f"recipe fallback: {name}",
                    reasoning=(
                        f"Recipe '{name}' didn't fully complete. Recent "
                        f"steps: {step_summary}. Falling back to adaptive "
                        f"see-think-act. Pick an action based on what is "
                        f"actually visible NOW — do not assume the recipe "
                        f"path is still the right approach."
                    ),
                )
            except Exception:
                pass
            return None

        reason = f"recipe:{name}"

        self._action_history = action_steps
        return AgentResult(
            success=all_succeeded,
            reason=reason,
            steps=action_steps, total_time_seconds=elapsed
        )

    @staticmethod
    def _fill_knowledge_placeholders(text: str) -> str:
        """Put this install's values into a knowledge file's {VITE_PORT} placeholders.

        A plain replace: the files also hold literal braces (JSON examples).
        """
        if "{VITE_PORT}" not in text:
            return text
        from backend.utils.cors_policy import vite_port
        return text.replace("{VITE_PORT}", vite_port())

    @staticmethod
    def _load_self_knowledge() -> str:
        """Load the Guaardvark self-knowledge map for agent context."""
        import os
        from backend.config import GUAARDVARK_ROOT
        path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "self_knowledge.md")
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    text = f.read().strip()
                return AgentControlService._fill_knowledge_placeholders(text) + "\n\n"
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
                    return AgentControlService._fill_knowledge_placeholders(f.read().strip())
        except Exception as e:
            logger.warning(f"Failed to load compact self-knowledge: {e}")
        return ""

    @classmethod
    def _build_persistent_knowledge_system(cls, task: str = "", full: bool = False) -> str:
        """Build the system-message content carrying the agent's persistent
        knowledge — compact facts plus distilled lessons, and with ``full``
        the long self-knowledge, recipe index and example traces too.
        Routed via Ollama's system role so it doesn't compete with the
        per-step user prompt for action-format conditioning. This is the
        cross-session memory slot: anything in here survives reboots and
        primes every decision.

        ``full=False`` is exactly what the unified path has always received.
        ``full=True`` is what split mode used to inline into its user prompt,
        now carried in the system slot so the brain gets the lessons as well.
        """
        parts = []
        sk = cls._load_self_knowledge_compact()
        if sk:
            parts.append(sk)
        lessons = cls._load_lesson_memories()
        if lessons:
            parts.append(lessons)
        if full:
            long_sk = cls._load_self_knowledge()
            if long_sk:
                parts.append("## Screen-Control Knowledge (hypotheses; current screen wins)\n" + long_sk.strip())
            recipe_index = cls._load_recipe_index()
            if recipe_index:
                parts.append(
                    "## Available Recipes (the system auto-executes these on matching task strings; "
                    "knowing they exist tells you what shortcuts the environment offers)\n" + recipe_index)
            traces = cls._load_example_traces(task) if task else ""
            if traces:
                parts.append(traces.strip())
        return "\n\n".join(parts)

    @staticmethod
    def _load_lesson_memories(max_rows: int = 6, max_chars: int = 2500) -> str:
        """Pull distilled lessons + belief updates from the unified recall layer.

        Thin shim over `memory_api.get_lessons_for_agent_prompt` — the SQL,
        source filtering, belief-update merge, and structured Markdown format
        all live there so the chat path and the agent path stay in lockstep.
        Keep this method on the class for callers that already import it.
        """
        try:
            from backend.api.memory_api import get_lessons_for_agent_prompt
        except Exception as e:
            logger.debug(f"memory_api import failed in lesson loader: {e}")
            return ""
        return get_lessons_for_agent_prompt(
            max_rows=max_rows,
            max_chars=max_chars,
            include_belief_updates=True,
        )

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
                if step.get("scroll_amount"):
                    detail += f' scroll_amount={step["scroll_amount"]}'
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
            status = self._step_status(last)
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
            last_action_status = self._step_status(step)

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
            timestamp_iso=utcnow().isoformat(timespec="seconds") + "Z",
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
            "action_refused",
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
        if action_type == "scroll":
            # By direction: down failing at the bottom of a page says nothing about up.
            return "scroll:up" if (action.scroll_amount or 0) > 0 else "scroll:down"
        return action_type or "unknown"

    def _record_failure_report(
        self,
        iteration: int,
        action: AgentAction,
        result: Dict[str, Any],
        pixel_diff: Optional[float],
        failed: bool,
    ) -> None:
        """Collate evidence about a failed step into a FailureReport.

        Stores in a rolling window the model can read on its next THINK pass.
        No-op on successful actions — the report is a *failure* artifact;
        recording successes here would just dilute the signal.
        """
        if not failed:
            return
        action_type = (action.action_type or "").strip().lower()
        target = (action.target_description or "").strip()

        # Servo records click coords in result on success; on failure the
        # result may carry a "last_attempted" or just be empty. Don't
        # invent coords — leave (0, 0) when we don't know.
        coords = (0, 0)
        if action_type == "click":
            try:
                x = int(result.get("x") or result.get("last_x") or 0)
                y = int(result.get("y") or result.get("last_y") or 0)
                coords = (x, y)
            except (TypeError, ValueError):
                coords = (0, 0)

        # DOM match: check whether the cached DOM snapshot contains any
        # element whose text matches the target. Cheap substring scan —
        # the snapshot was built earlier this iteration.
        dom_match = False
        if target and self._dom_snapshot:
            try:
                elements = getattr(self._dom_snapshot, "elements", None) or []
                target_lc = target.lower()
                for el in elements:
                    text = ((getattr(el, "text", "") or "") + " "
                            + (getattr(el, "tag", "") or ""))
                    if target_lc in text.lower():
                        dom_match = True
                        break
            except Exception:
                dom_match = False

        # Derive a one-line cause hypothesis from the other fields. The
        # model gets this as a quick read; it can still override based
        # on its own reasoning.
        # pixel_diff is None when we never measured it (the click branch
        # doesn't run a pre/post pixel comparison). In that case, do NOT
        # invent a "no pixel change" hypothesis — that primes the model
        # to abandon a click that may well have succeeded.
        delta_known = pixel_diff is not None
        delta_zero = delta_known and pixel_diff < 0.005
        reason = (result.get("reason") or "").strip()
        if result.get("refused"):
            cause = f"not sent — {result.get('refusal', '')}"
        elif action_type == "click" and reason == "target_not_visible":
            cause = "target not on screen — the eye looked and found nothing; no click was sent"
        elif action_type == "type" and reason == "typed_text_not_in_field":
            cause = "text did not appear near the field; the keys went to the page"
        elif action_type == "click" and reason == "vision_call_failed":
            cause = (
                "vision model itself failed twice (Ollama timeout/unresponsive) — "
                "this is NOT evidence the target is missing; retry shortly"
            )
        elif action_type == "click" and reason == "expected_effect_not_observed":
            cause = "click registered; expected visible effect not observed yet (may still be loading)"
        elif action_type == "click" and not delta_known:
            cause = "click reported failed by servo (target not found or click didn't issue)"
        elif action_type == "click" and not dom_match and delta_zero:
            cause = "target likely not on screen (no DOM match, no pixel change)"
        elif action_type == "click" and delta_zero:
            cause = "click registered but no screen change"
        elif action_type == "click":
            cause = "click landed but didn't produce the expected outcome"
        elif action_type == "type" and delta_zero:
            cause = "type produced no visible change — field probably wasn't focused"
        elif action_type == "scroll" and delta_zero:
            cause = "scroll did nothing — viewport at limit or not focused"
        else:
            cause = f"{action_type} reported failed"

        report = FailureReport(
            iteration=iteration,
            action_type=action_type,
            expected_target=target,
            attempted_at_coords=coords,
            screen_delta=float(pixel_diff) if delta_known else None,
            visibility_check="",  # filled by Phase-3 re-grounding when it fires
            dom_match=dom_match,
            cause_hypothesis=cause,
        )
        self._failure_reports.append(report)
        # Keep only the most recent N — older failures lose context value.
        if len(self._failure_reports) > self._failure_reports_cap:
            self._failure_reports = self._failure_reports[-self._failure_reports_cap:]

        # Maintain the stuck-target counter for Phase-3 re-grounding.
        if target and target == self._stuck_target:
            self._stuck_target_count += 1
        else:
            self._stuck_target = target
            self._stuck_target_count = 1

    def _format_failure_reports_for_prompt(self) -> str:
        """Render failure reports as structured text for the LLM."""
        if not self._failure_reports:
            return ""

        lines = ["## Recent Failures (Detailed Evidence):"]
        for r in self._failure_reports[-3:]:  # Show last 3
            lines.append(f"- Step {r.iteration}: {r.action_type} \"{r.expected_target}\"")
            if r.action_type == "click":
                lines.append(f"  coords: {r.attempted_at_coords}")
                lines.append(f"  dom_match: {'yes' if r.dom_match else 'no'}")
            delta_str = f"{r.screen_delta:.4f}" if r.screen_delta is not None else "n/a (not measured)"
            lines.append(f"  screen_delta: {delta_str}")
            lines.append(f"  hypothesis: {r.cause_hypothesis}")
        lines.append("")
        return "\n".join(lines)

    def _observe_only_pass(self, screen) -> str:
        """Re-grounding vision call with no task bias.

        Captures a fresh screenshot and asks the vision model to enumerate
        the prominent interactive elements it sees, with no system prompt,
        no self_knowledge context, and no task. The model's own observation
        becomes a WORLD_OBSERVED block injected into the next THINK prompt,
        so the decider can see its own un-primed list of what's on screen.

        Returns "" on any failure — re-grounding is best-effort; if it
        fails the loop continues with stale state rather than blocking.
        Fires only when the loop detects a stuck cluster (≥2 consecutive
        same-target failures); see _record_failure_report bookkeeping.
        """
        try:
            shot, _ = self._capture_with_retry(screen)
        except Exception as e:
            logger.warning(f"[AGENT][REGROUND] capture failed: {e}")
            return ""

        try:
            from backend.utils.vision_analyzer import VisionAnalyzer
            # Use the unified VLM if available (gemma4 can see itself); the
            # VisionAnalyzer default (moondream) is the safe fallback if not.
            unified = AgentControlService._get_unified_model()
            analyzer = VisionAnalyzer(default_model=unified) if unified else VisionAnalyzer()
            prompt = (
                "Enumerate every interactive element, icon, and window you "
                "can actually see in this screenshot. No task context, no "
                "guessing about elements that might be there. Describe exactly "
                "what is visible. Use short labels of 3 to 5 words each. "
                "One per line. No bullets, no numbering, no commentary."
            )
            # NB: explicitly no `system=` arg — the whole point is observation
            # without the agent's own priming. Temperature low for a faithful
            # readout, not a creative list.
            result = analyzer.analyze(
                shot, prompt, num_predict=512, temperature=0.1, think=False,
            )
        except Exception as e:
            logger.warning(f"[AGENT][REGROUND] vision call raised: {e}")
            return ""

        if not result.success or not result.description:
            logger.warning(
                f"[AGENT][REGROUND] vision call returned empty: {result.error or 'no content'}"
            )
            return ""

        # Trim to first 12 non-empty lines defensively; some models ignore
        # the "no numbering" instruction and emit a stream that runs long.
        observed_lines = [ln.strip(" -*•\t") for ln in result.description.splitlines()]
        observed_lines = [ln for ln in observed_lines if ln][:12]
        if not observed_lines:
            return ""
        body = "\n".join(f"- {ln}" for ln in observed_lines)
        logger.warning(
            f"[AGENT][REGROUND] observed {len(observed_lines)} element(s) "
            f"(stuck target was '{self._stuck_target}')"
        )
        return (
            "WORLD_OBSERVED (fresh capture, no task bias, no priming):\n"
            f"{body}\n"
            "When WORLD_OBSERVED contradicts what you remembered or expected, "
            "trust WORLD_OBSERVED. Pick a target from this list or describe "
            "what you actually see — do not retry an element that isn't here."
        )

    def _format_failure_history(self) -> str:
        """Render the recent FailureReport window as a compact prompt block.

        Returns "" when no failures recorded — keep the prompt clean. The
        model only needs evidence when there's evidence; padding the prompt
        with "no recent failures" trains it to gloss over the section.
        """
        if not self._failure_reports:
            return ""
        lines = ["Recent failures (read as evidence, not a story):"]
        for r in self._failure_reports[-self._failure_reports_cap:]:
            target_str = f'"{r.expected_target}"' if r.expected_target else "(none)"
            coords_str = (
                f" at {r.attempted_at_coords}" if r.attempted_at_coords != (0, 0) else ""
            )
            dom_str = "dom_match=true" if r.dom_match else "dom_match=false"
            delta_str = f"delta={r.screen_delta:.3f}" if r.screen_delta is not None else "delta=n/a"
            lines.append(
                f"- Step {r.iteration} {r.action_type} {target_str}{coords_str} "
                f"→ {delta_str}, {dom_str} → {r.cause_hypothesis}"
            )
        lines.append(
            "If a failure says the target was not on screen, do NOT retry the "
            "same target — describe what you actually see and pick a different "
            "action."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Phase 4 — session belief tracker + lesson generation
    # ------------------------------------------------------------------
    #
    # The agent's knowledge files (data/agent/self_knowledge_compact.md,
    # data/agent/recipes.json) claim certain UI elements are present on
    # the XFCE desktop. Phase 1 already softened "always visible" into
    # "typically visible" hedging language, but the elements are still
    # listed — and the model still primes on them. Phase 4 closes the
    # gap by tracking which claims survive contact with the actual screen:
    #
    #   1. _derive_session_expectations() parses the knowledge files once
    #      per session into a list of Expectation rows with source provenance.
    #   2. After each Phase-3 re-grounding pass, _record_expectation_contradictions
    #      compares the claimed elements against the fresh WORLD_OBSERVED
    #      block. Each "claimed visible, not observed" pair appends to
    #      _expectation_log. Stuck-target hallucinations (model said X, X is
    #      not in any doc, X is not on screen) are also logged with
    #      source="model_belief" for next-session context.
    #   3. At task end, _distill_lessons() collapses _expectation_log into
    #      <=5 lessons, dedup'd by element name. Each lesson becomes an
    #      AgentMemory row of type "belief_update" via memory_api.add_memory.
    #   4. Future sessions see the lesson through the existing
    #      get_memories_for_context loader — no extra wiring needed.
    #
    # Phase 5 (lesson_reconciler) consumes the belief_update memories
    # across sessions and proposes pending_fixes when ≥3 sessions agree
    # the same source-line claim was wrong.

    _DESKTOP_ICON_HEADER_PATTERNS = (
        "desktop icons typically present",
        "desktop icons present along",
    )

    def _derive_session_expectations(self) -> List[Expectation]:
        """Parse agent knowledge files into structured Expectation rows.

        Walks data/agent/self_knowledge_compact.md for the "Desktop icons …"
        bullet block — each bullet becomes an Expectation with the source
        file and the line number where the bullet lives. Deduped by
        lowercased element name. Cached on the service instance so the
        parser only runs once per task.

        Errors (file missing, permission denied, malformed) degrade to an
        empty list — Phase 4 is opportunistic. If we can't derive
        expectations, we still record model_belief contradictions from
        stuck_target.
        """
        if self._session_expectations is not None:
            return self._session_expectations

        expectations: List[Expectation] = []
        seen: set = set()

        try:
            from backend.config import GUAARDVARK_ROOT
            path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "self_knowledge_compact.md")
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except Exception as e:
            logger.warning(f"[AGENT][BELIEF] could not load self_knowledge_compact.md: {e}")
            self._session_expectations = expectations
            return expectations

        # Find the "Desktop icons …" header and collect subsequent bullet lines
        # until a blank line / non-bullet line / next header.
        in_block = False
        for idx, raw in enumerate(lines, start=1):
            line = raw.strip()
            lower = line.lower()
            if not in_block:
                if any(p in lower for p in self._DESKTOP_ICON_HEADER_PATTERNS):
                    in_block = True
                continue
            # In the bullet block — collect until exit.
            if not line:
                # Blank line ends the block (markdown convention).
                break
            if line.startswith("#"):
                break
            if not line.startswith("-"):
                # Bullet block over.
                break
            element = line.lstrip("- ").strip()
            if not element:
                continue
            key = element.lower()
            if key in seen:
                continue
            seen.add(key)
            expectations.append(Expectation(
                element=element,
                expected_visible=True,
                observed_visible=False,
                source="self_knowledge_compact.md",
                source_line=idx,
                confidence=0.5,  # hedge language softens the assertion
            ))

        # Current compact knowledge is prose-first, not a fixed icon bullet
        # block. Pull only short, concrete UI objects from those hypothesis
        # lines so contradiction tracking still has useful file-backed claims.
        #
        # CLAIM-SHAPED lines only (Wave 1, 2026-08-01): the old bare-word match
        # hedged ANY line containing "Firefox icon"/"desktop" — including the
        # conditional teleport instruction ("If you are on the desktop,
        # navigate will silently fail"), which is not a visibility claim. That
        # manufactured 8 junk belief rows + 4 junk PendingFixes (the poison
        # quarantined in Wave 0). A line only yields an expectation when it
        # ASSERTS the element's presence and is not a conditional.
        prose_patterns = [
            (r"\bFirefox icon\b", "Firefox icon"),
            (r"\bdesktop\b", "desktop"),
        ]
        _assertive = re.compile(
            r"\b(is|are|appears?|shows?|visible|present|located|sits)\b", re.IGNORECASE
        )
        _conditional = re.compile(r"^(if|when|unless|should you|in case)\b", re.IGNORECASE)
        for idx, raw in enumerate(lines, start=1):
            line = raw.strip()
            if _conditional.match(line) or not _assertive.search(line):
                continue
            for pattern, element in prose_patterns:
                if not re.search(pattern, line, re.IGNORECASE):
                    continue
                key = element.lower()
                if key in seen:
                    continue
                seen.add(key)
                expectations.append(Expectation(
                    element=element,
                    expected_visible=True,
                    observed_visible=False,
                    source="self_knowledge_compact.md",
                    source_line=idx,
                    confidence=0.35,
                ))

        # Recipes are also knowledge: target_description says what the servo is
        # expected to find when that recipe applies. Keep these as low-confidence
        # hypotheses because recipes have preconditions and page-specific scope.
        #
        # Tag with the recipe key's LINE NUMBER in recipes.json (Wave 1): the
        # old `source=recipes.json:<name>` + source_line=None produced tags the
        # reconciler's group-key parser can't consume (int() on a key name) —
        # 10 belief rows accumulated that could never do anything (archived in
        # Wave 0). Line numbers make the evidence reconcilable like any other
        # editable source.
        try:
            recipe_lines: Dict[str, int] = {}
            try:
                from pathlib import Path as _Path
                _recipes_path = _Path(__file__).resolve().parents[2] / "data" / "agent" / "recipes.json"
                for _idx, _raw in enumerate(_recipes_path.read_text().splitlines(), start=1):
                    m = re.match(r'\s*"([^"]+)"\s*:\s*\{', _raw)
                    if m and m.group(1) not in recipe_lines:
                        recipe_lines[m.group(1)] = _idx
            except Exception:
                pass
            for recipe_name, recipe in self._load_recipes().items():
                line_no = recipe_lines.get(recipe_name)
                if line_no is None:
                    continue  # unlocatable key → skip rather than emit junk tags
                for step in recipe.get("steps", []) or []:
                    if not isinstance(step, dict) or step.get("action") != "click":
                        continue
                    element = (step.get("target_description") or "").strip()
                    if not element:
                        continue
                    key = element.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    expectations.append(Expectation(
                        element=element,
                        expected_visible=True,
                        observed_visible=False,
                        source="recipes.json",
                        source_line=line_no,
                        confidence=0.25,
                    ))
        except Exception as e:
            logger.debug(f"[AGENT][BELIEF] recipe expectation extraction skipped: {e}")

        self._session_expectations = expectations
        return expectations

    @staticmethod
    def _significant_tokens(text: str) -> List[str]:
        """Strip stopwords / UI-noise words; return the rest, lowercased.

        Used by the substring-match step in _record_expectation_contradictions.
        "Firefox flame icon" → ["firefox", "flame"]. Lets a WORLD_OBSERVED
        entry of "firefox window in focus" still count as 'observed'."""
        noise = {
            "the", "a", "an", "and", "or", "of", "on", "in", "to", "with",
            "icon", "button", "panel", "menu", "section", "area", "bar",
            "item", "element", "control", "widget", "label", "field",
            "this", "that", "it",
        }
        cleaned = re.sub(r"[(),./\\]+", " ", text.lower())
        return [t for t in cleaned.split() if t and len(t) > 2 and t not in noise]

    @staticmethod
    def _word_in(token: str, text: str) -> bool:
        """``token`` starts a word in ``text`` ("icon" in "icons", not "red"
        in "remembered")."""
        return re.search(r"\b" + re.escape(token), text) is not None

    def _record_expectation_contradictions(
        self,
        expectations: List[Expectation],
        world_observed: str,
    ) -> None:
        """Compare expectations against a WORLD_OBSERVED block.

        Each expected-visible element that has no token overlap with the
        observed list becomes a contradiction row in _expectation_log.
        Also logs the current stuck_target (if any) as a model_belief
        contradiction so we capture hallucinated targets that aren't
        listed in any knowledge file.

        Empty world_observed → no-op. Re-grounding failed; we don't have
        evidence either way and false-positive contradictions would poison
        Phase 5.
        """
        body = (world_observed or "").strip()
        if not body:
            return

        # The element list only: the block's header and closing instruction
        # are fixed prose, and matched against them "red" was "seen" inside
        # "remembered", so a missing red button never became a lesson.
        listed = [ln[2:] for ln in body.splitlines() if ln.startswith("- ")]
        observed_text = ("\n".join(listed) if listed else body).lower()

        for exp in expectations:
            if not exp.expected_visible:
                continue
            tokens = self._significant_tokens(exp.element)
            if not tokens:
                continue
            element_seen = any(self._word_in(tok, observed_text) for tok in tokens)
            if element_seen:
                continue
            # Contradiction — copy the expectation with observed_visible=False
            # so we preserve the source provenance for Phase 5.
            self._expectation_log.append(Expectation(
                element=exp.element,
                expected_visible=True,
                observed_visible=False,
                source=exp.source,
                source_line=exp.source_line,
                confidence=exp.confidence,
            ))

        # Model-belief contradiction: the model's stuck target isn't in any
        # knowledge file (so not in `expectations`) and isn't on screen either.
        # Record it under source="model_belief" so the lesson reaches the
        # next session but Phase 5 skips it (no file to edit).
        stuck = (self._stuck_target or "").strip()
        if stuck and self._stuck_target_count >= 2:
            stuck_tokens = self._significant_tokens(stuck)
            already_logged = any(
                e.element.lower() == stuck.lower() for e in self._expectation_log
            )
            stuck_seen = any(self._word_in(tok, observed_text) for tok in stuck_tokens) if stuck_tokens else True
            if not stuck_seen and not already_logged:
                self._expectation_log.append(Expectation(
                    element=stuck,
                    expected_visible=True,
                    observed_visible=False,
                    source="model_belief",
                    source_line=None,
                    confidence=0.3,
                ))

    _MAX_LESSONS_PER_SESSION = 10

    def _distill_lessons(self) -> List[Dict[str, Any]]:
        """Collapse _expectation_log into <=10 unique-element lessons.

        Filters to actual contradictions (expected_visible=True AND
        observed_visible=False). Dedups by lowercased element name.
        Returns a list of dicts with the fields agent_control_service
        needs to write a belief_update memory:
          {element, source, source_line, content}

        The content string is the human-readable lesson body that lands
        in AgentMemory.content and gets picked up by the next session's
        prompt builder.
        """
        seen: set = set()
        lessons: List[Dict[str, Any]] = []
        for exp in self._expectation_log:
            if not exp.expected_visible or exp.observed_visible:
                continue
            key = exp.element.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            content = (
                f"\"{exp.element}\" was not visible during this session — "
                f"verify it's actually on screen before assuming it's there."
            )
            lessons.append({
                "element": exp.element,
                "source": exp.source,
                "source_line": exp.source_line,
                "content": content,
            })
            if len(lessons) >= self._MAX_LESSONS_PER_SESSION:
                break
        return lessons

    _TARGETING_ACTIONS = ("click", "right_click", "double_click", "triple_click", "hover", "drag")

    def _task_target_tokens(self) -> List[set]:
        """The significant words of each thing this task aimed an action at."""
        out: List[set] = []
        for st in self._action_history:
            a = st.action
            if a.action_type not in self._TARGETING_ACTIONS:
                continue
            for desc in (a.target_description, getattr(a, "drag_to_description", "")):
                toks = set(self._significant_tokens(desc or ""))
                if toks:
                    out.append(toks)
        return out

    def _is_task_target(self, element: str, targets: List[set]) -> bool:
        """``element`` names something the task aimed at: its words are
        within one target's words, or a target's words within its own."""
        toks = set(self._significant_tokens(element or ""))
        if not toks:
            return False
        return any(toks <= t or t <= toks for t in targets)

    def _write_session_lessons(self, session_id: Optional[str] = None) -> int:
        """Persist distilled lessons as belief_update memories.

        Called at task end. Each lesson becomes one AgentMemory row via
        the in-process memory_api.add_memory helper. Tags carry the
        source provenance (file:line) so Phase 5's reconciler can group
        by source-line. Errors are logged but don't bubble — a failed
        memory write must never break task completion.

        Returns the number of memories actually persisted.
        """
        lessons = self._distill_lessons()
        # Only lessons about what this task aimed at. The rest named things
        # the knowledge files mention ("desktop", "Firefox icon", "thumbs up
        # icon left of dislike") that were simply not part of a task on a web
        # page, and every model read them as advice in every later run: 35
        # such rows had built up by 2026-10-02 and were deleted.
        targets = self._task_target_tokens()
        kept = [l for l in lessons if self._is_task_target(l.get("element", ""), targets)]
        if len(kept) < len(lessons):
            logger.warning(
                f"[AGENT][BELIEF] {len(lessons) - len(kept)} lesson(s) not about this task's "
                f"targets left unwritten: {[l.get('element') for l in lessons if l not in kept]}"
            )
        lessons = kept
        if not lessons:
            return 0

        try:
            from backend.api.memory_api import add_memory
        except Exception as e:
            logger.warning(f"[AGENT][BELIEF] memory_api unavailable: {e}")
            return 0

        # execute_task is callable from threads/callers that don't push a Flask
        # app context (chat-tool path via agent_control_tools, agent_brain,
        # social_outreach scripts). add_memory's db.session.commit() needs one,
        # so push defensively here. Redundant stacking is documented harmless
        # in commit 268387d.
        from flask import has_app_context
        from contextlib import nullcontext
        if has_app_context():
            ctx = nullcontext()
        else:
            from backend.app import app as _flask_app
            ctx = _flask_app.app_context()

        written = 0
        with ctx:
            for lesson in lessons:
                src = lesson.get("source") or ""
                src_line = lesson.get("source_line")
                element_tag = (lesson.get("element") or "").strip().lower()
                tags = ["belief_update"]
                if element_tag:
                    tags.append(element_tag)
                if src:
                    tags.append(
                        f"src:{src}:{src_line}" if src_line is not None else f"src:{src}"
                    )
                try:
                    mem = add_memory(
                        content=lesson["content"],
                        memory_type="belief_update",
                        source="agent",
                        importance=0.55,
                        session_id=session_id,
                        tags=tags,
                        metadata={
                            "element": lesson.get("element"),
                            "source": src,
                            "source_line": src_line,
                        },
                    )
                    if mem is not None:
                        written += 1
                except Exception as e:
                    # Don't let a DB hiccup crash task completion.
                    logger.warning(
                        f"[AGENT][BELIEF] failed to write lesson for "
                        f"{lesson.get('element')!r}: {e}"
                    )

        if written:
            logger.warning(
                f"[AGENT][BELIEF] wrote {written} belief_update memor"
                f"{'y' if written == 1 else 'ies'} this session"
            )
        return written

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
        """Track repeated failed strategies and apply short cooldowns.

        Keyed by _action_recovery_key (a click with its target, a hotkey with
        its keys), not the action class: two failed clicks on one target used
        to block every click, on any target, for the next steps.
        """
        action_type = (action.action_type or "").strip().lower()
        if action_type in ("", "done", "wait"):
            return
        key = self._action_recovery_key(action)
        if failed:
            if self._last_failed_strategy == key:
                self._same_strategy_failures += 1
            else:
                self._last_failed_strategy = key
                self._same_strategy_failures = 1
            if self._same_strategy_failures >= 2:
                # Short cooldown so the model must try a different tactic.
                self._strategy_cooldowns[key] = max(
                    self._strategy_cooldowns.get(key, 0),
                    2,
                )
        else:
            if self._last_failed_strategy == key:
                self._last_failed_strategy = ""
                self._same_strategy_failures = 0

    # A click target the eye has not found this many times is held back
    # until the screen changes; at the second number the run stops.
    _NOT_FOUND_HOLD = 3
    _NOT_FOUND_STOP = 6
    # Hotkeys that put the keyboard focus in a text field.
    _FOCUS_HOTKEYS = {
        ("ctrl", "l"), ("ctrl", "k"), ("alt", "d"), ("f6",), ("/",), ("slash",),
        ("tab",), ("shift", "tab"),
    }
    _SUBMIT_KEYS = {"return", "enter", "kp_enter"}
    # Words that say a click target is somewhere text can go. A type after a
    # click on anything else (a video, a button) sends the keys to the page,
    # where single-key shortcuts act on them: on YouTube "c", "k", "t", "i"
    # and "/" toggled captions, paused, went to theater and miniplayer, and
    # moved the rest of the text into the search box.
    # Words such as "title", "name", "caption", "search" or "comments" are
    # left out on purpose: a video title, a channel name, the captions
    # button, a search result and a comments header are all things a model
    # clicks before typing by mistake. "search box" and "comment field" still
    # match on their second word.
    _FIELD_WORDS = re.compile(
        r"\b(?:(?:field|box|input|textbox|textarea|text|composer|editor|bar|area|message|"
        r"chat|prompt|address|url|query|e-?mail|user(?:name)?|password|subject|entry)s?|"
        r"reply|add a comment)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _target_key(target: str) -> str:
        key = " ".join((target or "").lower().split())
        return re.sub(r"^(?:the|a|an)\s+", "", key)

    def _note_not_found(self, target: str) -> None:
        """Count one look for ``target`` that found nothing."""
        key = self._target_key(target)
        if not key:
            return
        n = self._not_found_counts.get(key, 0) + 1
        self._not_found_counts[key] = n
        if n >= self._NOT_FOUND_HOLD:
            self._banned_targets.setdefault(key, (target or "").strip())

    def _target_given_up(self) -> Optional[Tuple[str, int]]:
        """(name, looks) for a target looked for _NOT_FOUND_STOP times, else None."""
        for key, n in self._not_found_counts.items():
            if n >= self._NOT_FOUND_STOP:
                return self._banned_targets.get(key, key), n
        return None

    def _last_acted_step(self) -> Optional[ActionStep]:
        for st in reversed(self._action_history):
            if st.action.action_type != "wait":
                return st
        return None

    def _dom_has_focused_field(self) -> bool:
        """The page snapshot (when the browser exposes one) shows a focused text field."""
        snap = self._dom_snapshot
        for el in (getattr(snap, "elements", None) or []):
            if not getattr(el, "focused", False):
                continue
            tag = (getattr(el, "tag", "") or "").lower()
            kind = (getattr(el, "element_type", "") or "").lower()
            if tag == "textarea" or kind in ("textbox", "composer", "searchbox", "combobox"):
                return True
            if tag == "input" and kind in ("", "text", "search", "email", "url", "password", "tel", "number"):
                return True
        return False

    def _type_focus(self) -> Tuple[bool, Optional[Tuple[int, int]], str]:
        """Is there a field for a type to go into: (yes, where it was clicked, why not)."""
        last = self._last_acted_step()
        if last is not None and not last.failed:
            a = last.action
            r = last.result or {}
            coords = tuple(a.coordinates) if a.coordinates else None
            if a.action_type in self._CLICK_FAMILY and coords and coords != (0, 0) and r.get("click_issued", True):
                if self._FIELD_WORDS.search(a.target_description or ""):
                    return True, coords, ""
                if self._dom_has_focused_field():
                    return True, None, ""
                return False, None, (
                    f"the last click was on '{a.target_description}', which is not a text field; "
                    f"click the field the text belongs in (call it a field, box or input), then type")
            if a.action_type == "click_at" and coords and coords != (0, 0):
                return True, coords, ""
            if a.action_type == "type":
                return True, self._field_point, ""
            if a.action_type == "hotkey" and tuple(k.lower() for k in (a.keys or [])) in self._FOCUS_HOTKEYS:
                return True, None, ""
        if self._dom_has_focused_field():
            return True, None, ""
        return False, None, (
            "no text field has been clicked, so the keys would go to the page itself "
            "(pages read single keys as shortcuts); click the field first, then type")

    def _refusal_for(self, action: AgentAction, training_mode: bool = False) -> str:
        """Why this action should not be sent, or "" to send it."""
        kind = (action.action_type or "").strip().lower()
        if kind == "hotkey" and not [k for k in (action.keys or []) if str(k).strip()]:
            return "the hotkey had no keys"
        if training_mode or kind in ("done", "wait", ""):
            return ""
        if kind == "navigate" and not self._outside_allowed(action.url):
            return ("web access is off in Settings, so this browser opens local pages only; "
                    "the site was not opened")
        if kind in ("click_at", "draw") and not self._points_allowed:
            return (f"{kind} is for coordinates the task gives or for drawing; to click something "
                    f"on screen, use click with a target_description for the eye to find")
        if kind in self._CLICK_FAMILY:
            key = self._target_key(action.target_description)
            if key in self._banned_targets:
                n = self._not_found_counts.get(key, 0)
                return (f"'{action.target_description}' was not on screen the last {n} times the eye "
                        f"looked, and the screen has not changed since; scroll, go back, or pick "
                        f"something you can see")
        block = self._strategy_cooldowns.get(self._action_recovery_key(action), 0)
        if block > 0:
            return (f"'{self._action_recovery_key(action)}' failed twice in a row; "
                    f"try a different action first")
        if getattr(self, "_mouse_only", False):
            return ""
        if kind == "type":
            ok, point, why = self._type_focus()
            if not ok:
                return why
            self._field_point = point
            return ""
        if kind == "hotkey" and str(action.keys[-1]).lower() in self._SUBMIT_KEYS:
            last = self._last_acted_step()
            if last is not None and last.action.action_type == "type" and last.failed:
                return ("the text before it did not go into a field, so Enter would act on the "
                        "page instead; click the field and type again first")
        return ""

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
            if result.get("refused"):
                return ProgressSignal(
                    label="action_refused",
                    confidence=0.95,
                    evidence=f"not sent: {result.get('refusal', '')}",
                    next_hint="do what the reason says, or choose a different action",
                )
            if reason == "target_not_visible" and action_type in self._CLICK_FAMILY:
                target = (action.target_description or "").strip()
                return ProgressSignal(
                    label="target_not_visible",
                    confidence=0.95,
                    evidence=f"'{target}' is not on screen: the eye looked and found nothing, no click was sent",
                    next_hint="scroll to it, or click something you can see",
                )
            if reason == "typed_text_not_in_field":
                return ProgressSignal(
                    label="input_not_applied",
                    confidence=0.9,
                    evidence="the screen changed, but not near the field: the keys went to the page",
                    next_hint="click the text field, check it is focused, then type",
                )
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
            if reason == "expected_effect_not_observed":
                return ProgressSignal(
                    label="completion_unproven",
                    confidence=0.9,
                    evidence="post-click expected visible effect was not observed",
                    next_hint="re-observe and choose a different visible route to the same goal",
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

    def _format_dom_grounding_for_prompt(self) -> str:
        """Render the cached DOM element inventory for the decision prompt.

        This is the "grounded eye" — it tells the brain what interactable
        elements actually exist on the page (tag/role/label/focused) so it stops
        hallucinating field contents from history. Coordinate-free on purpose:
        the model picks a target by label, and virtual-display coords are
        unreliable (see dom_metadata_extractor.dom_assist_enabled). Empty string
        when grounding is off or no snapshot is available (e.g. desktop/OS
        screens Firefox can't introspect), so non-web tasks are unaffected.
        """
        snap = self._dom_snapshot
        if not snap or not getattr(snap, "elements", None):
            return ""
        try:
            from backend.services.dom_metadata_extractor import (
                DOMMetadataExtractor,
                dom_grounding_enabled,
            )
            if not dom_grounding_enabled():
                return ""
            block = DOMMetadataExtractor.format_for_prompt(snap, include_coords=False)
            return block + "\n\n" if block else ""
        except Exception:
            return ""

    def _build_decision_prompt(self, task, scene, history, world_state: Optional[WorldState] = None,
                               training_mode: bool = False, chat_context: str = ""):
        """The brain's prompt in split mode: same blocks as the unified prompt,
        plus the eye's scene description in place of the screenshot.

        Until 2026-09-22 this carried its own weaker versions of a few of the
        shared blocks and none of the rest: no budget, no pivot, no chat
        context, no target-description rules, no success_proof in the schema,
        no tool action. Its knowledge was inlined into the user prompt instead
        of riding the system slot, so lessons never reached it. Its "done" was
        never verified. Split mode is the path a blind user model takes, so
        that was the pillar's own path being the degraded one.
        """
        # This prompt asks for success_proof; the done-guard keys on that fact.
        self._proof_contract = True
        mouse_only = getattr(self, '_mouse_only', False)
        done_lines = self._history_block(
            history, self.config.max_iterations, getattr(self, "_click_budget", None))
        repeat_block = "" if training_mode else self._repeat_block(history)
        prior_block = self._prior_run_block()
        pivot_block = self._pivot_block(history) + self._not_found_block() + self._steer_block()
        desktop_state = self._get_desktop_state()
        training_override = self._training_override(training_mode)
        confidence = self._confidence_line(task, desktop_state)
        world_block = self._format_world_state_for_prompt(world_state or self._world_state)
        dom_grounding_block = self._format_dom_grounding_for_prompt()
        failures_block = self._format_failure_reports_for_prompt()
        world_observed_block = self._consume_world_observed_block()
        chat_context_block = self._chat_context_block(chat_context)

        if mouse_only:
            rules = f"MOUSE ONLY. Actions: click, right_click, done.\n{self._STATE_MANAGEMENT}"
            schema = self._SCHEMA_MOUSE_ONLY
        else:
            rules = (f"One action per step. After typing a URL, press Return.\n{self._STATE_MANAGEMENT}"
                     f"\n\n{self._SCROLL_RULE}"
                     + (f"\n\n{self._point_rule()}" if self._points_allowed else ""))
            schema = self._schema_full()

        return f"""{pivot_block}{chat_context_block}{failures_block}---

Task: {task}

{desktop_state}
{world_block}

Screen (as described by the vision model): {scene}

{dom_grounding_block}{world_observed_block}{prior_block}{done_lines}{repeat_block}{training_override}Step {len(history) + 1}. ONE next action. After Act the system ALWAYS re-captures the screen (re-See) before your next Think. {confidence}

{rules}

{self._TARGET_DESCRIPTION_RULES}

{self._EXPECTED_EFFECT_RULE}

{self._DONE_RULE}

Reply ONLY with JSON:
{schema}

{self._TOOLBOX_NOTE}
"""

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
            # Names models invent for the point actions.
            action_type = self._ACTION_ALIASES.get(action_type, action_type)
            status = (data.get("status") or "IN_PROGRESS").upper().strip()
            decision.status = status

            if action_type == "done" or status == "COMPLETE":
                decision.task_complete = True
                decision.action.action_type = "done"
                decision.action.reasoning = data.get("reasoning", "")
                decision.action.expected_effect = (data.get("expected_effect") or "").strip()
                decision.action.success_proof = (data.get("success_proof") or "").strip()
                if status == "COMPLETE" and action_type != "done":
                    logger.warning(f"[AGENT][PARSER] Forced completion: status='COMPLETE' but action='{action_type}'")
                return decision

            if action_type == "tool":
                decision.action.action_type = "tool"
                decision.action.tool_name = data.get("tool_name", "") or data.get("name", "")
                decision.action.tool_params = data.get("tool_params", {}) or data.get("parameters", {})
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

            # Sanitize keys — models sometimes output just the modifier (e.g. ["ctrl"])
            keys = data.get("keys", [])
            if action_type == "hotkey" and keys:
                modifiers = {"ctrl", "alt", "shift", "super", "win", "meta"}
                if len(keys) == 1 and keys[0].lower() in modifiers:
                    logger.warning(f"[AGENT][PARSER] Rejecting modifier-only hotkey: {keys}")
                    keys = []
                    # If it was just a modifier, it's effectively a 'wait' or a 'stuck' signal
                    action_type = "wait"

            scroll_amount = data.get("scroll_amount", 0)
            if action_type == "scroll":
                try:
                    scroll_amount = int(scroll_amount or 0)
                except (TypeError, ValueError):
                    scroll_amount = 0
                direction = str(data.get("direction") or "").lower()
                if not scroll_amount:
                    scroll_amount = abs(self._DEFAULT_SCROLL) if direction == "up" else self._DEFAULT_SCROLL
                elif direction in ("up", "down"):
                    scroll_amount = abs(scroll_amount) if direction == "up" else -abs(scroll_amount)
                scroll_amount = max(-self._MAX_SCROLL, min(self._MAX_SCROLL, scroll_amount))

            action = AgentAction(
                action_type=action_type,
                target_cell=data.get("target_cell", ""),
                target_description=data.get("target_description", ""),
                drag_to_description=(data.get("drag_to_description") or data.get("drag_to") or ""),
                text=raw_text,
                keys=keys,
                points=self._parse_points(data.get("points")) if action_type == "draw" else [],
                scroll_amount=scroll_amount,
                url=data.get("url", ""),
                reasoning=data.get("reasoning", ""),
                expected_effect=(data.get("expected_effect") or data.get("success_proof") or "").strip(),
            )
            if action_type == "click_at":
                point = self._parse_point(data)
                if point is not None:
                    action.coordinates = point
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
