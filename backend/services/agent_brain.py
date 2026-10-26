#!/usr/bin/env python3
"""
AgentBrain — Three-tier instinctual agent router.

Tier 1 (Reflexes):     <100ms, 0 LLM calls — pattern-matched direct actions
Tier 2 (Instinct):     1-3s,   1 LLM call  — single pre-warmed shot
Tier 3 (Deliberation): 5-30s,  3-10 calls  — full ReACT loop

Every message enters at Tier 1; a reflex that fails falls through to Tier 2.
Tier 3 is chosen up front for multi-step requests (_needs_deliberation); a
Tier 2 reply is not escalated after it has been sent.
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.services.brain_state import (
    BrainState,
    ReflexResult,
)
from backend.services.step_budget import StepBudget, TierTelemetry
from backend.utils.llm_debug_logger import log_decision
from backend.utils.request_phrases import declines_media
from backend.services.unified_chat_engine import (
    begin_turn,
    end_turn,
    is_aborted,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telemetry logger (append-only JSONL)
# ---------------------------------------------------------------------------

_TELEMETRY_DIR = None


def _get_telemetry_path() -> str:
    """Resolve telemetry log path lazily."""
    global _TELEMETRY_DIR
    if _TELEMETRY_DIR is None:
        try:
            from backend.config import LOG_DIR
            _TELEMETRY_DIR = LOG_DIR
        except Exception:
            _TELEMETRY_DIR = "logs"
    return os.path.join(_TELEMETRY_DIR, "tier_telemetry.jsonl")


def _log_telemetry(telemetry: TierTelemetry):
    """Append one telemetry record to the JSONL log."""
    try:
        path = _get_telemetry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(telemetry.to_dict()) + "\n")
    except Exception as e:
        logger.debug(f"Telemetry write failed (non-critical): {e}")


# ---------------------------------------------------------------------------
# Narration extraction patterns (for the narration-instead-of-action bug)
# ---------------------------------------------------------------------------

NARRATION_PATTERNS = [
    re.compile(r"I (?:should|will|need to|can) use (?:the )?(\w+)"),
    re.compile(r"Let me (?:use|call|invoke) (?:the )?(\w+)"),
    re.compile(r"I'll (?:use|call) (\w+) to"),
    re.compile(r"I(?:'m going to| will) (\w+)"),
]

# Parameter inference rules per tool type
TOOL_PARAM_EXTRACTORS = {
    "web_search": lambda msg: {"query": msg},
    "analyze_website": lambda msg: {
        "url": m.group(0) if (m := re.search(r"https?://\S+", msg)) else
        (m2.group(0) if (m2 := re.search(r"\b\w+\.\w+\.\w+\b", msg)) else None)
    },
    "generate_image": lambda msg: {"prompt": msg},
    "codegen": lambda msg: {"description": msg},
}

# ---------------------------------------------------------------------------
# Deliberation heuristic patterns
# ---------------------------------------------------------------------------

DELIBERATION_SIGNALS = [
    re.compile(r"(?:first|step\s*1).*(?:then|next|step\s*2)", re.IGNORECASE | re.DOTALL),
    re.compile(r"research\s+.{3,50}?\s+(?:and\s+)?(?:then\s+)?(?:create|generate|write)", re.IGNORECASE),
    re.compile(r"analyze.*(?:and|then).*(?:improve|optimize|refactor)", re.IGNORECASE),
    re.compile(r"compare.*(?:and|then).*(?:recommend|suggest)", re.IGNORECASE),
    re.compile(r"find\s+.*(?:and|then).*(?:create|generate|write)", re.IGNORECASE),
    re.compile(r"help\s+me\s+(?:figure\s+out|understand|decide)", re.IGNORECASE),
    # Self-description / codebase overview queries benefit from multi-step (list repos, map, read key files)
    re.compile(r"(?:tell me|describe|what is|overview of|about).*?(?:codebase|your code|the code|your source|own source)", re.IGNORECASE),
]

# Pure social / affirmation messages → Tier 2 LLM (skip_tools), never hardcoded pools
SOCIAL_TIER2_PATTERNS = re.compile(
    r"^(hi|hello|hey|howdy|yo|sup|hiya|"
    r"good\s+(morning|afternoon|evening|night)|"
    r"thanks?|thank\s+you|ty|thx|cheers|"
    r"bye|goodbye|see\s+(ya|you)|later|gn|"
    r"how\s+are\s+you|how('s|\s+is)\s+it\s+going|what'?s\s+up|"
    r"who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|"
    r"yes|no|yeah|nah|nope|yep|ok(ay)?|sure|cool|nice|great|awesome|"
    r"got\s+it|sounds\s+good|makes\s+sense|right|correct|exactly|"
    r"absolutely|of\s+course|definitely|certainly|perfect|agreed|fine|alright)"
    r"[\s?!.,]*$",
    re.IGNORECASE,
)

# Back-compat alias (tests / imports)
CONVERSATIONAL_PASSTHROUGH = SOCIAL_TIER2_PATTERNS

# A bare "yes" answers whatever the assistant just offered. On the social path
# it reached the model with no tools, so "Would you like me to search your
# files?" / "Yes" got a reply saying a search was under way, and none ran.
AFFIRMATION_PATTERNS = re.compile(
    r"^(yes|yeah|yep|yup|y|ok(ay)?|sure|please|please do|yes please|go ahead|please go ahead|"
    r"do it|go for it|absolutely|of course|definitely|certainly|alright|fine|sounds good)"
    r"[\s?!.,]*$",
    re.IGNORECASE,
)

# An offer to act, or an announcement that an action is under way, in the
# assistant's last reply.
_OFFER_PATTERNS = re.compile(
    r"would you like me to|do you want me to|shall i\b|should i\b|want me to\b|"
    r"\bif you(?:'d| would)? like me to\b|\bif you want me to\b|"
    r"\bi can\b[^.?!\n]{0,160}\bif you(?:'d| would)? like|"
    r"\bi(?: am|'m|’m) (?:now |currently |still )?(?:searching|looking|checking|scanning)\b|"
    r"\bi(?:'ll|’ll| will) let you know\b",
    re.IGNORECASE,
)


def pending_offer(assistant_text: Optional[str]) -> Optional[str]:
    """The sentence of the assistant's last reply that offered or announced an
    action, or None. Offers close a reply, so only the tail is read."""
    if not assistant_text:
        return None
    tail = str(assistant_text).strip()[-800:]
    for sentence in reversed(re.split(r"(?<=[.?!])\s+", tail)):
        if _OFFER_PATTERNS.search(sentence):
            return sentence.strip()[:300]
    return None


def _last_assistant_text(app, session_id: str) -> Optional[str]:
    """Content of the newest assistant message in the session, or None."""
    if not app or not session_id:
        return None
    try:
        with app.app_context():
            from backend.models import LLMMessage
            row = (LLMMessage.query
                   .filter_by(session_id=session_id, role="assistant")
                   .order_by(LLMMessage.timestamp.desc())
                   .first())
            return row.content if row else None
    except Exception as e:
        logger.debug("last assistant message unavailable for %s: %s", session_id, e)
        return None

# Pure-chat openers that don't need a screenshot (subset used by gemma4 direct)
NO_SCREEN_CONTEXT = SOCIAL_TIER2_PATTERNS


_PURE_IMAGE_PHRASES = ("generate image", "make a picture", "picture of", "image of", "create image",
                       "generate a photo", "visualize", "make an image")
_SCREEN_WORDS = re.compile(r"\b(?:screen|click|canvas|paint|pencil|brush)")


def is_pure_image_request(message: str, options: Optional[Dict[str, Any]] = None) -> bool:
    """A request for a generated picture, which goes to the image tool even
    when the agent screen is active.

    Never in sticky /agent mode, where every message is a screen task (the
    input says so): "draw a smiley face on this canvas" there went to image
    generation instead of the screen. "draw" counts as a word ("drawer" and
    "withdraw" are not image requests), and a message about the screen, a
    click, a canvas, a paint program or a pencil is about the screen.
    """
    if options and options.get("agent_mode"):
        return False
    if declines_media(message):
        return False
    msg = (message or "").lower()
    wants_image = any(p in msg for p in _PURE_IMAGE_PHRASES) or re.search(r"\bdraw\b", msg) is not None
    return wants_image and _SCREEN_WORDS.search(msg) is None

# Vision task detection
VISION_PATTERNS = re.compile(
    r"(?i)(?:virtual\s+(?:screen|display|computer|browser|machine)|"
    r"agent\s+(?:screen|mode|vision)|on\s+(?:the|your)\s+(?:screen|display)|"
    r"(?:your|the)\s+virtual|use\s+(?:the|your)\s+screen|/vision|/agent)",
)


def vision_prompt_key(message: str, image_data=None) -> str:
    """The prompt for a vision-routed turn: the screen prompt for a screen task,
    the chat prompt for a picture pasted into the conversation."""
    if image_data and not VISION_PATTERNS.search(message or ""):
        return "chat"
    return "vision"


# ---------------------------------------------------------------------------
# AgentBrain
# ---------------------------------------------------------------------------

def _brain_provenance(request_id: str, tier: int, model, thinking_steps, eye=None) -> Dict[str, Any]:
    """Provenance for a reply the brain saved itself (reflex, screen-direct,
    deliberate): the same keys the chat engine writes, so feedback treats
    every path alike. Recipe usage and agent tasks are drained from the
    agent service, as the engine does."""
    prov: Dict[str, Any] = {"request_id": request_id or "", "tier": int(tier)}
    if model:
        prov["model"] = model
    if eye:
        prov["eye"] = eye
    tools = []
    for st in thinking_steps or []:
        for name in (st or {}).get("tools") or []:
            if name not in tools:
                tools.append(name)
    if tools:
        prov["tools"] = tools
    try:
        from backend.services.agent_control_service import get_agent_control_service
        prov.update(get_agent_control_service().drain_recipe_usage())
    except Exception:
        pass
    return prov


def _capture_memory(app, message: str, session_id: str, project_id=None) -> None:
    """Keep a "remember that …" fact from a user turn saved outside the engine.

    The engine runs memory_capture right after it saves the user message; a
    brain path that saves the turn itself runs the same capture, so a fact
    the person asked to be remembered is kept whichever tier answered. Only
    the explicit forms ("remember that", "from now on", "for future
    reference"): these paths carry task requests, and "my goal is to …" is
    not a fact to file. Best effort, like _persist_turn: a failure is logged
    and the reply goes on.
    """
    if not app or not message:
        return
    try:
        from backend.services.memory_capture import capture_from_message
        with app.app_context():
            capture_from_message(message, session_id=session_id, project_id=project_id,
                                 explicit_only=True)
    except Exception:
        logger.exception("Chat memory auto-capture failed for session %s", session_id)


def _persist_turn(app, session_id: str, role: str, content: str, extra: Optional[Dict[str, Any]],
                  emit_fn: Optional[Callable] = None, request_id: str = "", project_id=None) -> Optional[int]:
    """Write one chat row and, for an assistant row, announce its id.

    One helper for the three brain paths that used to carry their own copy
    of this block; `chat:message_saved` is what lets the client attach a
    thumb to the row rather than to a content prefix. Best effort: a failed
    save is logged and never surfaces to the user.
    """
    if not app or not content:
        return None
    new_id = None
    try:
        with app.app_context():
            from backend.models import LLMMessage, LLMSession, db
            # A new chat whose first turn is answered here has no session row
            # yet, and the message's foreign key needs one.
            if db.session.get(LLMSession, session_id) is None:
                db.session.add(LLMSession(id=session_id, user="default", project_id=project_id))
                db.session.flush()
            msg = LLMMessage(
                session_id=session_id,
                role=role,
                content=content,
                extra_data=extra or None,
                project_id=project_id,
                timestamp=datetime.now(),
            )
            db.session.add(msg)
            db.session.commit()
            new_id = msg.id
    except Exception:
        logger.exception("Failed to persist %s turn for session %s", role, session_id)
        return None
    if role == "assistant":
        try:
            from backend.services.unified_chat_engine import emit_message_saved
            emit_message_saved(emit_fn, session_id, request_id, new_id, role)
        except Exception:
            pass
    return new_id


class AgentBrain:
    """
    Three-tier agent router.  Single entry point for all chat/agent
    interactions.  Sits in front of existing code without modifying it.
    """

    TOTAL_STEP_CAP = 20  # cross-tier inherited budget (per agentic-tooling R1) to cap total steps across tiers and prevent accumulation on escalation from Instinct->Deliberation or Gemma direct.

    def __init__(self, state: Optional[BrainState] = None):
        self.state = state or BrainState.get_instance()

    # -- Main entry point ---------------------------------------------------

    def process(
        self,
        session_id: str,
        message: str,
        options: Dict[str, Any],
        emit_fn: Callable,
        app=None,
        project_id: int = None,
        image_data: str = None,
        image_url: str = None,
        is_voice_message: bool = False,
        force_tier: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Route a message to the appropriate tier and return the response.

        This is the single entry point replacing the scattered routing
        across IntentClassifier, AgentRouter, and UnifiedChatEngine.

        Args:
            session_id: Conversation session ID
            message: User's message
            options: Dict with use_rag, chat_mode, etc.
            emit_fn: Socket.IO emit callback
            app: Flask app for app context
            force_tier: Override tier routing (for agent_chat_api, testing)
        """
        start_time = time.monotonic()
        # Reuse the id the HTTP layer minted (it is already in the client's
        # ack); mint one only for callers that did not. Every exit below emits
        # and saves under this one id.
        request_id = str((options or {}).get("request_id") or "") or str(uuid.uuid4())
        if isinstance(options, dict):
            options["request_id"] = request_id
        self._decide_turn(session_id, message, options, app, image_data, force_tier,
                          is_voice_message, request_id)
        tier_used = 0
        tools_called: List[str] = []
        tool_params_log: List[Dict] = []
        escalated_from = None
        escalation_reason = None
        success = True
        route_intent = ""

        # === Explicit first-class cross-tier StepBudget ===
        # This replaces the previous fragile remaining_steps closure usage.
        # The budget is the "solidification" of agentic constraints — the agent
        # should eventually be made aware of it so it develops real personality-level
        # caution instead of burning steps until something hard-kills the loop.
        budget = StepBudget.from_total(
            kwargs.pop('max_steps', self.TOTAL_STEP_CAP)
        )
        # Phase 2.1: dynamic total from hw policy (vram, tier)
        try:
            from backend.services.hardware_policy import _load_hardware
            hw = _load_hardware()
            if hw:
                hw_b = StepBudget.from_hw_policy(hw)
                budget = StepBudget(total=hw_b.total)
        except Exception:
            pass

        # === Direct slash / direct_tool bypass (e.g. /imagine) ===
        # Must short-circuit BEFORE any LLM or tier routing. This avoids the
        # chat model being involved at all (prevents the Ollama EOF / reload
        # storms seen when image gen evicts the LLM and then continuation tries
        # to speak). The legacy direct path does correct history + emits.
        try:
            from backend.services.slash_command_executor import resolve_slash_direct_tool
            tool_name, params = resolve_slash_direct_tool(options)
            if tool_name and getattr(self.state, 'tool_registry', None):
                tool = self.state.tool_registry.get_tool(tool_name)
                if tool:
                    logger.info(f"[AGENT_BRAIN] Direct slash/direct-tool: {tool_name} (bypassing LLM)")
                    try:
                        from backend.services.unified_chat_engine import UnifiedChatEngine
                        from backend.utils.llm_service import get_llm_for_startup
                        # LLM instance only for engine construction; direct exec does not call it
                        llm = None
                        try:
                            llm = get_llm_for_startup()
                        except Exception:
                            pass  # some directs don't need it
                        temp_engine = UnifiedChatEngine(
                            tool_registry=self.state.tool_registry,
                            llm_instance=llm or object()  # dummy if none
                        )
                        if app:
                            temp_engine.app = app
                        temp_engine._project_id = project_id
                        rid = str(uuid.uuid4())
                        display = message or f"/{ (options or {}).get('slash_command', tool_name) }"
                        # Execute with the caller's emit_fn so UI sees tool_call/result/image/complete
                        return temp_engine._run_direct_tool_execution(
                            tool_name, params, session_id, emit_fn, rid, display, options or {}
                        )
                    except Exception as de:
                        logger.warning(f"[AGENT_BRAIN] Direct tool exec via temp engine failed for {tool_name}: {de} — will fall through")
        except Exception as _de:
            logger.debug(f"[AGENT_BRAIN] Direct tool precheck skipped: {_de}")

        # Agent screen gate — when nobody is actively watching the virtual
        # screen, vision models should behave like any other model (ReACT +
        # tools) instead of clicking through Firefox for every request.
        _screen_active = bool(options and options.get("agent_screen_active", False))

        # For pure image generation requests (draw/create/generate image etc.),
        # force the standard tool-calling path (LLM must output generate_image tool call)
        # so that the selected /imagemodel is respected via injection, and behavior
        # is consistent with direct /imagine. Skip gemma-direct even if screen active.
        force_standard_image = is_pure_image_request(message, options)

        begin_turn(session_id)
        try:
            # -- Gemma4 direct path: no chains, no routing, no bloated prompts --
            # Gemma4 has native vision + pointing + tool use. Just send it the
            # user's message with a screenshot and let it decide what to do.
            # Gated on _screen_active — inactive screen = fall through to
            # normal tier routing so the model uses tools like web_search and
            # analyze_website instead of emitting JSON click actions.
            # Direct screen path for ANY brain the loop can drive with: the
            # active model itself when it can see and point, or the active
            # model deciding with a borrowed eye when it cannot. The old gate
            # tested the literal string "gemma4", which excluded every other
            # model from the screen — including one whose name contained
            # "gemma4" and had no vision at all.
            if (not force_tier
                    and _screen_active
                    and not force_standard_image
                    and self._screen_drivable()):
                logger.debug(
                    f"[EMIT-HANDOFF][BRAIN] entering _screen_direct session={session_id} "
                    f"emit_fn_id={id(emit_fn)} threadlocal_get? (will log inside ACS if used)"
                )
                result = self._screen_direct(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    request_id=request_id, budget=budget, **kwargs,
                )
                if result is not None:
                    tier_used = result.get("tier", 2)
                    return result
                # None means Gemma4 direct couldn't handle it — fall through to legacy

            # Force tier if requested (e.g., agent_chat_api always uses Tier 3)
            if force_tier == 3:
                tier_used = 3
                budget.charge(1, 3, "forced Tier 3")
                return self._deliberate(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    budget=budget, request_id=request_id,
                )

            # -- Tier 1: Reflexes (<1ms check, <100ms execute) --
            if self.state.health.reflexes_loaded:
                reflex_match = self.state.match_reflex(message)
                if reflex_match:
                    reflex_action, match = reflex_match
                    result = reflex_action.handler(message, match, {})
                    if result.success:
                        tier_used = 1
                        if result.tool_called:
                            tools_called.append(result.tool_called)
                            tool_params_log.append(result.tool_params or {})
                        self._emit_response(
                            emit_fn, session_id, result.response, request_id
                        )
                        budget.charge(1, 1, "tier1 reflex")
                        # A reflex reply is still a reply: persisted (after the
                        # emit, so latency is unchanged) so it survives a
                        # refresh and can be thumbed like any other.
                        if app and (options or {}).get("persist", True) is not False:
                            _persist_turn(app, session_id, "user", message, None,
                                          project_id=project_id)
                            _persist_turn(
                                app, session_id, "assistant", result.response,
                                {"provenance": {
                                    "request_id": request_id, "tier": 1,
                                    "reflex": reflex_action.name,
                                    "tools": [result.tool_called] if result.tool_called else [],
                                }},
                                emit_fn=emit_fn, request_id=request_id, project_id=project_id,
                            )
                        return self._build_result(
                            result.response, session_id, request_id, tier=1,
                        )
                    # Reflex failed — fall through to Tier 2
                    logger.info(
                        f"Reflex '{reflex_action.name}' failed, escalating to Tier 2"
                    )
                    escalated_from = 1
                    escalation_reason = f"reflex '{reflex_action.name}' failed"

            # -- Vision routing. Two triggers:
            #    (1) image_data present — user pasted an image. It rides on the
            #        user message whatever the prompt; the screen prompt is used
            #        only when the message is about the screen. A pasted picture
            #        otherwise gets the chat prompt, so it is read against the
            #        conversation instead of under screen-control rules.
            #    (2) vision-sounding text ("click the Firefox button") AND the
            #        agent screen is being watched. Without the screen, those
            #        requests route through normal Instinct so the model can
            #        explain that nothing is being viewed, rather than
            #        silently attempting clicks on a screen nobody sees.
            _screen_viewer_open = bool(options and options.get("screen_viewer_open", False))
            if self._is_vision_task(message, image_data) and (image_data or _screen_active or _screen_viewer_open):
                tier_used = 2  # Vision goes through Tier 2 with vision prompt
                budget.charge(1, 2, "vision routing")
                return self._instinct(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    prompt_key=vision_prompt_key(message, image_data), budget=budget,
                )

            # -- "Yes" to the assistant's own offer: do it, with tools --
            if AFFIRMATION_PATTERNS.fullmatch(message.strip()):
                offer = pending_offer(_last_assistant_text(app, session_id))
                if offer:
                    tier_used = 2
                    route_intent = "accepted_offer"
                    budget.charge(1, 2, "accepted offer")
                    return self._instinct(
                        session_id, message, {**(options or {}), "accepted_offer": offer},
                        emit_fn, app, project_id=project_id,
                        is_voice_message=is_voice_message, budget=budget,
                    )

            # -- Social / conversational (Tier 2, skip_tools, real LLM) --
            if SOCIAL_TIER2_PATTERNS.fullmatch(message.strip()):
                tier_used = 2
                route_intent = "social"
                budget.charge(1, 2, "social tier2 skip_tools")
                return self._instinct(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, is_voice_message=is_voice_message,
                    skip_tools=True, budget=budget, intent="social",
                )

            # -- Check if Tier 3 is needed --
            # Not inside a step of an approved plan: the plan already did the
            # deliberating, and Tier 3 cannot show the approval cards a plan
            # run may need.
            if not (options or {}).get("plan_step") and self._needs_deliberation(message):
                tier_used = 3
                budget.charge(1, 3, "deliberation signal")
                return self._deliberate(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    budget=budget, request_id=request_id,
                )

            # -- Default: Tier 2 (single-shot with tools) --
            tier_used = 2
            budget.charge(1, 2, "default tier 2")
            result = self._instinct(
                session_id, message, options, emit_fn, app,
                project_id=project_id, image_data=image_data,
                image_url=image_url, is_voice_message=is_voice_message,
                budget=budget,
            )

            # Tier 2 cannot escalate from here: the engine has already emitted
            # and saved its reply, so a Tier 3 run now would answer twice.
            # Escalation has to be decided inside the engine before it replies.
            return result

        except Exception as e:
            logger.error(f"AgentBrain.process error: {e}", exc_info=True)
            success = False
            error_msg = f"An error occurred: {e}"
            emit_fn("chat:error", {"error": error_msg, "session_id": session_id})
            return {
                "success": False,
                "error": str(e),
                "request_id": request_id,
            }

        finally:
            end_turn(session_id)
            # Record telemetry
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            telemetry = TierTelemetry(
                tier=tier_used,
                latency_ms=elapsed_ms,
                tools_called=tools_called,
                tool_params=tool_params_log,
                escalated_from=escalated_from,
                escalation_reason=escalation_reason,
                message_hash=TierTelemetry.hash_message(message),
                success=success,
                model=self.state.active_model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_agent_steps=budget.used if budget is not None else 0,
                budget_remaining=budget.remaining if budget is not None else 0,
                budget_total=budget.total if budget is not None else 20,
                budget_charges=len(budget.history) if budget is not None else 0,
                intent=route_intent,
            )
            _log_telemetry(telemetry)

    # -- Tier 2: Instinct ---------------------------------------------------

    # -- Gemma4 direct path -------------------------------------------------

    def _screen_direct(
        self,
        session_id: str,
        message: str,
        options: Dict[str, Any],
        emit_fn: Callable,
        app=None,
        project_id: int = None,
        image_data: str = None,
        image_url: str = None,
        is_voice_message: bool = False,
        request_id: str = "",
        budget: Optional[StepBudget] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """Direct Gemma4 path — no tier routing, no tool schema bloat.

        Gemma4 has native vision, pointing, and reasoning. We route directly to
        the AgentControlService execute_task loop, skipping the redundant pre-check 
        vision call to save latency.
        """
        # If it's purely conversational, let the normal instinct path handle it
        if NO_SCREEN_CONTEXT.match(message.strip()):
            return None

        try:
            from backend.services.local_screen_backend import LocalScreenBackend
            from backend.services.agent_control_service import get_agent_control_service
            import re

            generated_images = []

            # Load self-knowledge — the agent's own manual
            self_knowledge = ""
            try:
                from pathlib import Path
                from backend.config import GUAARDVARK_ROOT
                sk_path = Path(GUAARDVARK_ROOT) / "data" / "agent" / "self_knowledge.md"
                if sk_path.exists():
                    self_knowledge = sk_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass

            screen = LocalScreenBackend()
            acs = get_agent_control_service()

            # Persist the user message BEFORE we kick off the agent loop.
            # If execute_task crashes or the user refreshes mid-run, the
            # incoming prompt still survives in history.
            history_str = ""
            if app and message:
                with app.app_context():
                    try:
                        from backend.models import LLMSession, LLMMessage, db
                        from sqlalchemy import select
                        from datetime import datetime as _dt
                        
                        # Fetch recent history BEFORE saving the new message
                        # so we get clean context for short commands like "try again"
                        prev_msgs = db.session.execute(
                            select(LLMMessage)
                            .filter_by(session_id=session_id)
                            .order_by(LLMMessage.timestamp.desc())
                            .limit(4)
                        ).scalars().all()
                        
                        if prev_msgs:
                            prev_msgs.reverse()
                            hist_lines = []
                            for m in prev_msgs:
                                if m.role in ["user", "assistant"] and m.content:
                                    content = str(m.content)[:500]
                                    hist_lines.append(f"{m.role.capitalize()}: {content}")
                            if hist_lines:
                                history_str = "\n".join(hist_lines)

                        sess = db.session.get(LLMSession, session_id)
                        if not sess:
                            sess = LLMSession(id=session_id, user="default", project_id=project_id)
                            db.session.add(sess)
                            db.session.flush()
                        user_extra = None
                        if image_data:
                            user_extra = {
                                "hasImage": True,
                                "imageUrl": image_url,
                                "messageType": "image_upload",
                            }
                        db.session.add(LLMMessage(
                            session_id=session_id,
                            role="user",
                            content=message,
                            extra_data=user_extra,
                            project_id=project_id,
                            timestamp=_dt.now(),
                        ))
                        db.session.commit()
                    except Exception:
                        logger.exception("Failed to persist user message in gemma4_direct")
                # The same memory capture the engine runs after its save, so a
                # "remember that …" spoken with the agent screen open is kept.
                if (options or {}).get("persist", True) is not False \
                        and not (options or {}).get("skip_memory_capture"):
                    _capture_memory(app, message, session_id, project_id=project_id)

            # Delegate straight to the robust execute_task loop
            # Pass the explicit budget (gemma direct counts against the cross-tier cap).
            if budget is None:
                budget = StepBudget.from_total(self.TOTAL_STEP_CAP)
            budget.charge(1, 0, "gemma4 direct entry")  # count the direct path
            # actively query memory/entity for context (Phase 2.1)
            try:
                from backend.api.memory_api import get_memories_for_context
                get_memories_for_context(
                    limit=5, max_tokens=200, query=message
                )
                budget.charge(1, 0, "gemma context query")
            except Exception:
                pass
            # The screen loop stops on its own stall rule and ceilings; a
            # "[BUDGET: n/20 steps left]" line beside a task that states its
            # own budget only misled the model, so it no longer rides along.
            screen_context = history_str or ""
            logger.debug(
                f"[EMIT-HANDOFF][BRAIN_GEMMA] calling acs.execute_task DIRECT (bypasses agent_task_execute tool) "
                f"with explicit emit_fn_id={id(emit_fn)} session={session_id}"
            )
            agent_result = acs.execute_task(
                task=message, 
                screen=screen, 
                emit_fn=emit_fn, 
                chat_context=screen_context,
                budget=budget,
                session_id=session_id,
            )

            # Narrate the outcome in Guaardvark's voice
            response = self._narrate_agent_outcome(
                user_message=message,
                agent_result=agent_result,
                self_knowledge=self_knowledge,
                emit_fn=emit_fn,
                session_id=session_id,
            )
            late = list(getattr(agent_result, "late_notes", None) or [])
            if late:
                # Said plainly rather than left to the narration model: the
                # user should know a note was never acted on.
                quoted = ", ".join(f"“{t[:120]}”" for t in late)
                noun, verb = ("note", "it") if len(late) == 1 else ("notes", "them")
                response = ((response or "").rstrip()
                            + f"\n\nYour {noun} {quoted} came in after I'd finished, so I didn't act on "
                              f"{verb}. Send {verb} again if you still want {verb}.")

            # Emit complete
            emit_fn("chat:complete", {
                "response": response,
                "iterations": 1,
                "steps": [],
                "session_id": session_id,
                "request_id": request_id,
                "token_usage": {"input_tokens": 0, "output_tokens": 0},
                "generated_images": generated_images,
            })

            # Save assistant response (with generated images for persistence)
            if app and response:
                clean = re.sub(r'<[^>]*>', '', response).strip()
                extra = {}
                if generated_images:
                    extra["generatedImages"] = generated_images
                agent_thinking_steps = []
                try:
                    agent_thinking_steps = acs.drain_thinking_steps()
                    logger.debug(
                        f"[EMIT-HANDOFF][BRAIN_DRAIN] screen_direct drain returned {len(agent_thinking_steps)} steps"
                    )
                    if agent_thinking_steps:
                        extra["agentThinkingSteps"] = agent_thinking_steps
                except Exception:
                    pass
                be = getattr(acs, "_brain_eye", None)
                extra["provenance"] = _brain_provenance(
                    request_id, 0, getattr(be, "brain", None), agent_thinking_steps,
                    eye=getattr(be, "eye", None))
                content = clean if not clean.startswith("{") else f"[Action] {response}"
                _persist_turn(app, session_id, "assistant", content, extra,
                              emit_fn=emit_fn, request_id=request_id, project_id=project_id)

            return {
                "success": True,
                "response": response,
                "tier": 0,
                "request_id": request_id,
                "session_id": session_id,
            }

        except Exception as e:
            logger.error(f"Gemma4 direct path failed: {e}", exc_info=True)
            return None  # Fall through to legacy

    def _narrate_agent_outcome(
        self,
        user_message: str,
        agent_result,
        self_knowledge: str,
        emit_fn: Callable,
        session_id: str,
    ) -> str:
        """Turn a finished agent task into a 1-2 sentence reply in Guaardvark's voice.

        Streams tokens via emit_fn so the reply types in naturally. Returns the
        accumulated text for the chat:complete payload and DB save. Falls back
        to a small templated mapping if the narration call fails.
        """
        import httpx as _httpx
        import ollama

        reason = (getattr(agent_result, "reason", "") or "").strip()
        success = bool(getattr(agent_result, "success", False))

        actions_taken: List[str] = []
        last_scene = ""
        try:
            steps = getattr(agent_result, "steps", None) or []
            for step in steps[-5:]:
                action_type = getattr(getattr(step, "action", None), "action_type", "") or ""
                if action_type:
                    actions_taken.append(action_type)
            if steps:
                last_scene = (getattr(steps[-1], "scene_description", "") or "")[:200]
        except Exception:
            pass

        from backend.config import GUAARDVARK_PROJECT_NAME

        persona = (self_knowledge or "").strip()[:600]
        system_prompt = (
            f"You are {GUAARDVARK_PROJECT_NAME}. "
            + (persona + "\n\n" if persona else "")
            + "Reply in 1-2 short sentences about what just happened on screen. "
              "Direct, no corporate filler, no exclamation marks unless something "
              "genuinely went wrong. Don't restate the user's request verbatim, "
              "don't quote internal status codes — just say what you did, in your voice."
        )
        user_prompt = (
            f'I just asked: "{user_message}"\n'
            f"You took these actions: {', '.join(actions_taken) or 'none recorded'}\n"
            f"Outcome: {'success' if success else 'failure'} — internal reason code: {reason or 'unknown'}\n"
        )
        if last_scene:
            user_prompt += f"Last thing visible on screen: {last_scene}\n"
        user_prompt += "\nReply to me now."

        # Thinking is off for this call: a thinking model reasons in Ollama's
        # separate ``message.thinking`` field, which the loop below never reads,
        # and with an 800-token cap the whole budget can go there and leave the
        # narration empty. Buffer the full response, strip any inline <think>
        # block, then emit; streaming each token live would leak reasoning.
        # Two attempts: right after a vision-heavy action loop, Ollama occasionally
        # returns a zero-chunk stream on the first call. A second try resolves it.
        from backend.utils.ollama_resource_manager import think_payload
        client = ollama.Client(
            timeout=_httpx.Timeout(connect=5.0, read=25.0, write=25.0, pool=25.0),
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        text = ""
        for attempt in (1, 2):
            accumulated: List[str] = []
            try:
                stream = client.chat(
                    model=self.state.active_model,
                    messages=messages,
                    stream=True,
                    keep_alive="10m",
                    options={"num_ctx": 4096, "num_predict": 800, "temperature": 0.6},
                    **think_payload(self.state.active_model),
                )
                for chunk in stream:
                    if is_aborted(session_id):
                        break
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        accumulated.append(token)
            except Exception as e:
                logger.warning(f"[narration_fallback] narration call failed (attempt {attempt}): {e}")
                continue
            raw = "".join(accumulated)
            from backend.utils.inline_reasoning import split_inline_reasoning
            text = split_inline_reasoning(raw)[1]
            logger.info(
                f"[narration] attempt={attempt} reason={reason!r} success={success} "
                f"chunks={len(accumulated)} raw_len={len(raw)} clean_len={len(text)} "
                f"raw_preview={raw[:200]!r}"
            )
            if text:
                emit_fn("chat:token", {"content": f"\n{text}", "session_id": session_id})
                return text
            if is_aborted(session_id):
                break
            # Empty response — pause a beat and retry once. Gemma4 sometimes
            # returns nothing immediately after a vision-heavy action loop.
            time.sleep(0.4)
        logger.warning("[narration_fallback] both narration attempts empty; using template")

        fallback = self._fallback_outcome_text(reason, success)
        emit_fn("chat:token", {"content": f"\n{fallback}", "session_id": session_id})
        return fallback

    def _fallback_outcome_text(self, reason: str, success: bool) -> str:
        """Templated reply for when the narration LLM call doesn't pan out."""
        r = (reason or "").strip().lower()
        if r.startswith("recipe:"):
            name = r.split(":", 1)[1]
            if name == "open_youtube":
                return "Opened YouTube."
            if name == "focus_firefox":
                return "Brought Firefox to the front."
            return f"Ran the `{name}` recipe — done."
        mapping = {
            "completed": "Done.",
            "timeout": "I ran out of time on that one — want me to retry?",
            "max_iterations": "I tried a few times and couldn't get there. Tell me more about what you wanted?",
            "max_failures": "I tried a few times and couldn't get there. Tell me more about what you wanted?",
            "stalled_no_progress": "I kept acting but the screen stopped changing and I had no new target, so I stopped rather than loop. Tell me what to try next?",
            "killed": "Stopped that one.",
        }
        if r in mapping:
            return mapping[r]
        if r.startswith("target_not_found"):
            return ("I kept looking for what I needed to click and it wasn't on the screen, "
                    "so I stopped rather than keep asking. Tell me where it is or what to try?")
        return "Task completed." if success else "Task failed."

    def _parse_gemma4_actions(self, response: str) -> List[Dict]:
        """Extract all action JSONs from Gemma4's response.

        Gemma4 may return one action or a sequence of actions.
        Returns a list of action dicts (may be empty).
        """
        actions = []
        # Find all JSON objects in the response
        i = 0
        while i < len(response):
            start = response.find("{", i)
            if start == -1:
                break
            # Find matching closing brace
            depth = 0
            end = start
            for j in range(start, len(response)):
                if response[j] == "{":
                    depth += 1
                elif response[j] == "}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            if depth != 0:
                break
            try:
                data = json.loads(response[start:end])
                if "action" in data:
                    actions.append(data)
            except (json.JSONDecodeError, ValueError):
                pass
            i = end
        return actions

    def _parse_gemma4_action(self, response: str) -> Optional[Dict]:
        """Check if Gemma4's response contains an action JSON. Returns first action."""
        actions = self._parse_gemma4_actions(response)
        return actions[0] if actions else None

    def _execute_gemma4_action(
        self, action: Dict, original_task: str,
        session_id: str, emit_fn: Callable, app=None,
    ) -> Optional[str]:
        """Execute an action Gemma4 requested. Direct screen control — no servo,
        no agent_task_execute, no tool registry. Gemma4 said what to do, we do it."""
        from backend.services.local_screen_backend import LocalScreenBackend
        screen = LocalScreenBackend()

        action_type = action.get("action", "").lower()

        if action_type == "done":
            return action.get("summary", "Done.")

        if action_type in ("click", "right_click"):
            x = action.get("x")
            y = action.get("y")
            target = action.get("target", "")
            button = "right" if action_type == "right_click" else "left"

            if x is not None and y is not None:
                x, y = int(x), int(y)
                # Gemma4 sees the FULL 1024x1024 screenshot (no resize in this path).
                # It returns raw pixel coordinates in the image's own space.
                # DO NOT apply scale factors — they push coords off target.
                # Empirically verified 2026-04-10: raw pixels = 10-16px error (HIT),
                # scaled by 1.28/0.72 = 300px+ error (MISS).
                logger.info(f"Gemma4 direct: raw coords ({x},{y}) — no scaling applied")
            else:
                # Gemma4 gave a target but no coords — try DOM lookup if enabled.
                # Disabled by default (see dom_metadata_extractor.dom_assist_enabled).
                try:
                    from backend.services.dom_metadata_extractor import (
                        DOMMetadataExtractor,
                        dom_assist_enabled,
                    )
                    if dom_assist_enabled():
                        snap = DOMMetadataExtractor.get_instance().extract()
                        for el in (snap.elements if snap.success else []):
                            if target.lower() in (el.text or "").lower():
                                x, y = el.cx, el.cy
                                break
                except Exception:
                    pass

            if x is None or y is None:
                # Vision-driven fallback: chat-side clicks used to bail here
                # ("Cannot click — no coordinates") which forced the user to
                # hand-feed pixels. Wrong philosophy: the agent has eyes; let
                # them work. Per data/agent/LEARNING_PRINCIPLES.md.
                if target:
                    try:
                        from backend.services.training_data_collector import TrainingDataCollector
                        from backend.services.agent_control_service import (
                            AgentControlService, build_servo)
                        # Same eye, same config, same measured accuracy as the
                        # loop. This path used to build its own eye with a
                        # bare VisionAnalyzer() and a model-less config.
                        be = AgentControlService.resolve_brain_eye(
                            self.state.active_model, screen.screen_size())
                        _, servo = build_servo(screen, be.eye, collector=TrainingDataCollector())
                        result = servo.click_target(target, button=button)
                        if result.get("success"):
                            cx, cy = result.get("x"), result.get("y")
                            logger.info(f"Gemma4 vision-fallback click: {target!r} at ({cx},{cy})")
                            import time as _t
                            _t.sleep(0.5)  # match the coord-path settle
                            return f"Clicked {target} at ({cx},{cy})"
                        return (
                            f"Tried to click '{target}' via vision but couldn't find "
                            f"it on the current screen. Try a shorter, more "
                            f"distinctive label (one short phrase naming what you "
                            f"actually see — color, shape, or label text) or "
                            f"check whether the target is actually visible."
                        )
                    except Exception as e:
                        logger.warning(f"Vision-fallback click failed for {target!r}: {e}")
                return f"Cannot click '{target}'."

            screen.click(x, y, button=button)
            import time as _t
            _t.sleep(0.5)  # let the UI react before the next action
            logger.info(f"Gemma4 click at ({x},{y}) target=\"{target}\"")
            return f"Clicked {target} at ({x},{y})"

        if action_type == "type":
            text = action.get("text", "")
            if not text:
                return None
            screen.type_text(text)
            import time as _t
            _t.sleep(0.3)  # brief settle after typing
            return f"Typed: {text}"

        if action_type == "hotkey":
            keys = action.get("keys", [])
            if not keys:
                return None
            screen.hotkey(*keys)
            logger.info(f"Gemma4 hotkey: {'+'.join(keys)}")
            return f"Pressed: {'+'.join(keys)}"

        if action_type == "scroll":
            amount = int(action.get("amount", -3))
            x = int(action.get("x", 640))
            y = int(action.get("y", 360))
            screen.scroll(x, y, amount=amount)
            return f"Scrolled {amount} at ({x},{y})"

        if action_type == "navigate":
            url = action.get("url", "")
            if url:
                screen.hotkey("ctrl", "l")
                import time as _t
                _t.sleep(0.3)
                screen.hotkey("ctrl", "a")
                _t.sleep(0.1)
                screen.type_text(url)
                _t.sleep(0.2)
                screen.hotkey("Return")
                return f"Navigating to {url}"

        if action_type == "screenshot":
            # Capture, save, and emit to chat so user sees the screen
            try:
                import time as _sc_time
                from backend.tools.agent_control_tools import SCREENSHOTS_DIR, _prune_old_screenshots
                from backend.utils.screenshot_urls import screenshot_url
                from backend.utils.vision_analyzer import VisionAnalyzer

                screenshot, cursor_pos = screen.capture()
                os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
                filename = f"agent_capture_{int(_sc_time.time() * 1000)}.webp"
                filepath = os.path.join(SCREENSHOTS_DIR, filename)
                screenshot.save(filepath, format="WEBP", quality=80)
                image_url = screenshot_url(filename)
                _prune_old_screenshots(SCREENSHOTS_DIR)

                emit_fn("chat:image", {
                    "image_url": image_url,
                    "alt": "Agent screen capture",
                    "caption": "",
                    "session_id": session_id,
                })

                # Quick vision analysis so the agent can describe what's on screen
                try:
                    analyzer = VisionAnalyzer()
                    analysis = analyzer.analyze(screenshot, prompt="Describe what is on the screen.", num_predict=128)
                    if analysis.success:
                        return analysis.description
                except Exception:
                    pass
                return "Screenshot captured and shown in chat."
            except Exception as e:
                logger.error(f"Gemma4 screenshot action failed: {e}")
                return f"Screenshot failed: {e}"

        if action_type == "generate_image":
            prompt = action.get("prompt", "")
            if not prompt:
                return "No prompt provided for image generation."

            # Evict Gemma4 from VRAM so Stable Diffusion can load without OOM
            try:
                import requests as _req
                _req.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": self.state.active_model, "keep_alive": 0},
                    timeout=5,
                )
            except Exception:
                pass

            try:
                from backend.tools.image_tools import ImageGeneratorTool
                tool = ImageGeneratorTool()
                # Same defaults as chat ImageGeneratorTool (1024 + family sampling).
                result = tool.execute(
                    prompt=prompt,
                    style=action.get("style", "realistic"),
                    width=int(action.get("width", 1024)),
                    height=int(action.get("height", 1024)),
                    model=action.get("model") or "auto",
                )

                if result.success and result.metadata.get("image_url"):
                    # Stash URL for caller's generated_images tracking
                    action["_image_url"] = result.metadata["image_url"]
                    # Emit chat:image so the frontend displays it in chat
                    emit_fn("chat:image", {
                        "image_url": result.metadata["image_url"],
                        "alt": f"Generated: {prompt[:60]}",
                        "caption": prompt,
                        "session_id": session_id,
                    })
                    return f"Image generated successfully. {result.output}"
                else:
                    return f"Image generation failed: {result.error or 'unknown error'}"
            except Exception as e:
                logger.error(f"Gemma4 generate_image failed: {e}", exc_info=True)
                return f"Image generation error: {e}"

        return None

    # -- Tier 2: Instinct ---------------------------------------------------

    def _instinct(
        self,
        session_id: str,
        message: str,
        options: Dict[str, Any],
        emit_fn: Callable,
        app=None,
        skip_tools: bool = False,
        prompt_key: str = "chat",
        project_id: int = None,
        image_data: str = None,
        image_url: str = None,
        is_voice_message: bool = False,
        budget: Optional[StepBudget] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Tier 2: Single pre-warmed LLM call.

        All the preparation that used to happen per-request (tool schema
        serialization, system prompt construction, model capability detection,
        intent classification, semantic tool selection) is already done and
        cached in BrainState.
        """
        if not self.state.health.llm_available:
            return {
                "success": False,
                "error": "Model not loaded. Check that Ollama is running.",
                "tier": 2,
            }

        # For now, delegate to UnifiedChatEngine which handles streaming,
        # history, RAG, and Socket.IO integration.  The key win is that
        # BrainState has already pre-computed everything the engine needs.
        #
        # In Phase 2, this method will contain the optimized single-shot
        # path that bypasses the engine's per-request ceremony.
        try:
            from backend.services.unified_chat_engine import UnifiedChatEngine
            if budget is None:
                budget = StepBudget.from_total(self.TOTAL_STEP_CAP)
            iters = 1 if skip_tools else 5
            iters = min(iters, budget.remaining)
            budget.charge(1, 2, "tier2 instinct entry")
            engine = UnifiedChatEngine(
                tool_registry=self.state.tool_registry,
                llm_instance=self.state.llm,
                max_iterations=iters,
            )
            result = engine.chat(
                session_id=session_id,
                message=message,
                options=options,
                emit_fn=emit_fn,
                app=app,
                project_id=project_id,
                image_data=image_data,
                image_url=image_url,
                is_voice_message=is_voice_message,
                brain_state=self.state,
                skip_tools=skip_tools,
                prompt_role=prompt_key,
                budget=budget,
            )

            # A reply that names a tool without calling it is logged, never run.
            # By this point the reply has been streamed and saved, the tool's
            # arguments could only be guessed from the user's message, and the
            # engine already re-asks inside its own loop when a search was
            # skipped. The engine reports tool calls per step, not as tools_used.
            response_text = result.get("response", "")
            ran_tools = any(
                isinstance(s, dict) and s.get("tool_calls")
                for s in (result.get("steps") or [])
            )
            if response_text and not skip_tools and not ran_tools:
                narrated = self._extract_narrated_tool_intent(
                    response_text, message
                )
                if narrated:
                    logger.info(
                        f"[narration] reply named '{narrated[0]}' without calling it "
                        "(logged, not executed)"
                    )
                    # backend.log keeps WARNING and up; the per-session trail in
                    # llm_debug.log (Settings > LLM debug) is where this is read.
                    # That setting is read from the database, so the call needs
                    # an app context; engine.chat has already left its own.
                    decision = {"session_id": session_id, "tool": narrated[0]}
                    if app is not None:
                        with app.app_context():
                            log_decision("agent_brain", "NARRATED_TOOL_NOT_RUN", decision)
                    else:
                        log_decision("agent_brain", "NARRATED_TOOL_NOT_RUN", decision)

            result["tier"] = 2
            return result

        except Exception as e:
            logger.error(f"Tier 2 instinct failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "tier": 2,
            }

    # -- Tier 3: Deliberation -----------------------------------------------

    def _deliberate(
        self,
        session_id: str,
        message: str,
        options: Dict[str, Any],
        emit_fn: Callable,
        app=None,
        initial_context: Optional[Dict] = None,
        project_id: int = None,
        image_data: str = None,
        image_url: str = None,
        is_voice_message: bool = False,
        budget: Optional[StepBudget] = None,
        request_id: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Tier 3: Full ReACT loop via AgentExecutor.

        The executor receives pre-computed state from BrainState instead of
        detecting and building everything from scratch.
        """
        if not self.state.health.llm_available:
            return {
                "success": False,
                "error": "Model not loaded. Check that Ollama is running.",
                "tier": 3,
            }

        if not self.state.health.tools_available:
            # No tools → can't run agent loop, fall back to Tier 2
            logger.warning("Tier 3 unavailable (no tools), falling back to Tier 2")
            if budget is None:
                budget = StepBudget.from_total(self.TOTAL_STEP_CAP)
            budget.charge(1, 3, "tier3 fallback to instinct (no tools)")
            return self._instinct(
                session_id, message, options, emit_fn, app,
                project_id=project_id, image_data=image_data,
                image_url=image_url, is_voice_message=is_voice_message,
                budget=budget,
            )

        try:
            from backend.services.agent_executor import AgentExecutor

            # Tier 3 bypasses the engine, which saves the question for the other
            # tiers; save it before the run so it survives a crash or a reload,
            # and run the engine's memory capture on it, so "remember that …"
            # is kept when the message also reads as a task.
            if app and message and (options or {}).get("persist", True) is not False:
                _persist_turn(app, session_id, "user", message, None, project_id=project_id)
                if not (options or {}).get("skip_memory_capture"):
                    _capture_memory(app, message, session_id, project_id=project_id)

            if budget is None:
                budget = StepBudget.from_total(self.TOTAL_STEP_CAP)
            iters = min(self.state.max_agent_iterations, budget.remaining)
            budget.charge(1, 3, "tier3 deliberation entry")
            executor = AgentExecutor(
                tool_registry=self.state.tool_registry,
                llm=self.state.llm,
                max_iterations=iters,
            )
            # Give the executor the session id so it can live-stream per-iteration
            # reasoning as chat:thinking (source=agent_loop). Without this, Tier-3
            # ReACT runs that don't drive the desktop produced NO live steps — the
            # trail only appeared from the DB drain on refresh.
            executor.set_tool_context(session_id=session_id)

            # Build session context from initial Tier 2 result if escalated.
            # The step budget reaches the model through the executor's prompts.
            session_context = ""
            if initial_context:
                prev_response = initial_context.get("response", "")
                prev_tools = initial_context.get("tools_used", [])
                if prev_response:
                    session_context += f"\nPrevious attempt (single-shot): {prev_response[:500]}"
                if prev_tools:
                    session_context += f"\nTools already tried: {', '.join(prev_tools)}"

            logger.debug(
                f"[EMIT-HANDOFF][BRAIN_TIER3] Tier3 _deliberate calling AgentExecutor (will use threadlocal for agent_* tools) "
                f"session={session_id} emit_fn_id={id(emit_fn)} (outer set in api must be live)"
            )
            result = executor.execute(
                user_query=message,
                session_context=session_context,
                max_steps=budget.remaining if budget else None,
                budget=budget,
            )

            response_text = result.final_answer if result.success else (
                result.error or "I wasn't able to complete that task."
            )
            # The executor's facts check: False when the answer names things
            # the tool results do not contain. The chat shows a note for it.
            verified = result.verified if result.success else None

            # Drain agent thinking steps (from any agent_task_execute that ran
            # inside the executor). Live streaming of steps relies on the
            # thread-local emit_fn (now wired in unified_chat_api); this drain
            # ensures the trail is persisted to DB so it survives refresh.
            agent_thinking_steps = []
            try:
                from backend.services.agent_control_service import get_agent_control_service
                agent_thinking_steps = get_agent_control_service().drain_thinking_steps()
                logger.debug(
                    f"[EMIT-HANDOFF][BRAIN_DRAIN] Tier3 drain returned {len(agent_thinking_steps)} steps for session={session_id}"
                )
            except Exception:
                pass

            self._emit_response(
                emit_fn, session_id, response_text, request_id or "",
                complete_extra={"verified": verified} if verified is not None else None,
            )

            # Persist the assistant turn (Tier 3 direct path bypasses legacy
            # UnifiedChatEngine which normally does the save + drain). Mirrors
            # the save block in the screen-direct path and the legacy engine.
            if app and response_text:
                clean = re.sub(r'<[^>]*>', '', response_text).strip()
                extra = {}
                if agent_thinking_steps:
                    extra["agentThinkingSteps"] = agent_thinking_steps
                if verified is not None:
                    extra["verified"] = verified
                extra["provenance"] = _brain_provenance(
                    request_id, 3, getattr(self.state.llm, "model", None), agent_thinking_steps)
                _persist_turn(app, session_id, "assistant", clean or response_text, extra,
                              emit_fn=emit_fn, request_id=request_id, project_id=project_id)

            return {
                "success": result.success,
                "response": response_text,
                "iterations": result.iterations,
                "tier": 3,
                "agentThinkingSteps": agent_thinking_steps,
                "verified": verified,
            }

        except Exception as e:
            logger.error(f"Tier 3 deliberation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "tier": 3,
            }

    # -- Narration extraction -----------------------------------------------

    def _extract_narrated_tool_intent(
        self, response_text: str, original_message: str
    ) -> Optional[tuple]:
        """
        Detect when the model narrated a tool call instead of executing it.

        Returns (tool_name, params) if narration is detected and the tool
        exists, otherwise None.
        """
        if not self.state.tool_registry:
            return None

        for pattern in NARRATION_PATTERNS:
            match = pattern.search(response_text)
            if match:
                tool_name = match.group(1).lower()
                # Normalize common variations
                tool_name = tool_name.replace("-", "_").replace(" ", "_")

                # Check if tool exists
                tool = self.state.tool_registry.get_tool(tool_name)
                if not tool:
                    # Try with common suffixes/prefixes
                    for candidate in [
                        tool_name,
                        f"{tool_name}_tool",
                        f"search_{tool_name}",
                    ]:
                        tool = self.state.tool_registry.get_tool(candidate)
                        if tool:
                            tool_name = candidate
                            break

                if not tool:
                    continue

                # Try parameter inference
                extractor = TOOL_PARAM_EXTRACTORS.get(tool_name)
                if extractor:
                    params = extractor(original_message)
                    # Validate params aren't empty/None
                    if params and all(v is not None for v in params.values()):
                        return (tool_name, params)
                else:
                    # No extractor for this tool — can't infer params safely,
                    # let Tier 3 handle it
                    return None

        return None

    # -- Routing helpers ----------------------------------------------------

    def _decide_turn(self, session_id, message, options, app, image_data, force_tier,
                     is_voice_message, request_id):
        """Build the turn's TurnDecision (turn_router, legacy mode) and hand it to
        the engine as options["turn"]; route_turn also writes its telemetry row.

        The route below is still chosen by this method's own chain, which the
        decision reproduces; nothing dispatches on it yet. Never raises.
        """
        try:
            from backend.services.turn_router import route_turn
            turn = route_turn(
                message, options, surface=(options or {}).get("surface"), request_id=request_id,
                session_id=session_id, image_data=image_data, app=app, state=self.state,
                force_tier=force_tier, screen_drivable=self._screen_drivable,
                is_voice_message=is_voice_message,
            )
        except Exception as e:
            logger.debug(f"turn decision skipped: {e}")
            return None
        if isinstance(options, dict):
            options["turn"] = turn
        return turn

    # Historical name; the path is no longer Gemma-specific.
    _gemma4_direct = None  # rebound below the class body

    def _screen_drivable(self) -> bool:
        """Can the agent loop drive the screen with the active model as brain?"""
        try:
            from backend.services.agent_control_service import AgentControlService
            return bool(AgentControlService.resolve_brain_eye(self.state.active_model).eye)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"screen-drivable check failed: {e}")
            return False

    def _is_vision_task(self, message: str, image_data: str = None) -> bool:
        """Check if this is a vision/screen task."""
        if image_data:
            return True
        return bool(VISION_PATTERNS.search(message))

    def _needs_deliberation(self, message: str) -> bool:
        """
        Fast heuristic (~0.1ms) to detect messages needing multi-step reasoning.

        Returns True only for clearly multi-step requests.  Single-step
        requests go to Tier 2 and can escalate if needed.
        """
        for pattern in DELIBERATION_SIGNALS:
            if pattern.search(message):
                return True
        return False

    # -- Response formatting ------------------------------------------------

    def _emit_response(
        self, emit_fn: Callable, session_id: str, response: str, request_id: str,
        complete_extra: Optional[Dict[str, Any]] = None,
    ):
        """Emit a complete response via Socket.IO. complete_extra adds
        fields to the chat:complete payload (e.g. the Tier 3 facts check)."""
        emit_fn("chat:response", {
            "response": response,
            "session_id": session_id,
            "request_id": request_id,
        })
        emit_fn("chat:complete", {
            "session_id": session_id,
            "request_id": request_id,
            "response": response,
            "steps": [],
            **(complete_extra or {}),
        })

    def _build_result(
        self,
        response: str,
        session_id: str,
        request_id: str,
        tier: int = 1,
        **extra,
    ) -> Dict[str, Any]:
        """Build a standard result dict."""
        return {
            "success": True,
            "response": response,
            "session_id": session_id,
            "request_id": request_id,
            "tier": tier,
            **extra,
        }

# Kept for callers and tests that still use the old name.
AgentBrain._gemma4_direct = AgentBrain._screen_direct
