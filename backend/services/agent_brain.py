#!/usr/bin/env python3
"""
AgentBrain — Three-tier instinctual agent router.

Tier 1 (Reflexes):     <100ms, 0 LLM calls — pattern-matched direct actions
Tier 2 (Instinct):     1-3s,   1 LLM call  — single pre-warmed shot
Tier 3 (Deliberation): 5-30s,  3-10 calls  — full ReACT loop

Every message enters at Tier 1 and escalates only if needed.
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
    TierTelemetry,
)
from backend.services.unified_chat_engine import (
    clear_abort_flag,
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
]

# Conversational patterns (bare affirmations route to Tier 2 with skip_tools)
CONVERSATIONAL_PASSTHROUGH = re.compile(
    r"^(yes|no|yeah|nah|nope|yep|ok(ay)?|sure|cool|nice|great|awesome|"
    r"got it|sounds good|makes sense|right|correct|exactly|absolutely|"
    r"of course|definitely|certainly|perfect|agreed|fine|alright)[\s?!.,]*$",
    re.IGNORECASE,
)

# Vision task detection
VISION_PATTERNS = re.compile(
    r"(?i)(?:virtual\s+(?:screen|display|computer|browser|machine)|"
    r"agent\s+(?:screen|mode|vision)|on\s+(?:the|your)\s+(?:screen|display)|"
    r"(?:your|the)\s+virtual|use\s+(?:the|your)\s+screen|/vision|/agent)",
)

# Pure-chat openers that don't need a screenshot. Attaching one starves
# inference for ~minutes on small VRAM, so we skip the eyes for these.
NO_SCREEN_CONTEXT = re.compile(
    r"^(hi|hello|hey|howdy|yo|sup|hiya|"
    r"good\s+(morning|afternoon|evening|night)|"
    r"thanks|thank\s+you|ty|tysm|cheers|"
    r"bye|goodbye|see\s+(ya|you)|later|gn|"
    r"how\s+are\s+you|how('s|\s+is)\s+it\s+going|what'?s\s+up|"
    r"who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do)"
    r"[\s?!.,]*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# AgentBrain
# ---------------------------------------------------------------------------

class AgentBrain:
    """
    Three-tier agent router.  Single entry point for all chat/agent
    interactions.  Sits in front of existing code without modifying it.
    """

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
        request_id = str(uuid.uuid4())
        tier_used = 0
        tools_called: List[str] = []
        tool_params_log: List[Dict] = []
        escalated_from = None
        escalation_reason = None
        success = True

        # Clear any abort flag from a previous request on this session
        # so we don't immediately abort ourselves.
        clear_abort_flag(session_id)

        # Agent screen gate — when nobody is actively watching the virtual
        # screen, vision models should behave like any other model (ReACT +
        # tools) instead of clicking through Firefox for every request.
        _screen_active = bool(options and options.get("agent_screen_active", False))

        try:
            # -- Gemma4 direct path: no chains, no routing, no bloated prompts --
            # Gemma4 has native vision + pointing + tool use. Just send it the
            # user's message with a screenshot and let it decide what to do.
            # Gated on _screen_active — inactive screen = fall through to
            # normal tier routing so the model uses tools like web_search and
            # analyze_website instead of emitting JSON click actions.
            if (self.state.model_caps.is_vision_model
                    and "gemma4" in self.state.active_model.lower()
                    and not force_tier
                    and _screen_active):
                result = self._gemma4_direct(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    request_id=request_id, **kwargs,
                )
                if result is not None:
                    tier_used = result.get("tier", 2)
                    return result
                # None means Gemma4 direct couldn't handle it — fall through to legacy

            # Force tier if requested (e.g., agent_chat_api always uses Tier 3)
            if force_tier == 3:
                tier_used = 3
                return self._deliberate(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    **kwargs,
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
            #    (1) image_data present — user pasted an image. ALWAYS use the
            #        vision prompt regardless of agent-screen state, because
            #        the user explicitly wants the model to look at the image.
            #    (2) vision-sounding text ("click the Firefox button") AND the
            #        agent screen is being watched. Without the screen, those
            #        requests route through normal Instinct so the model can
            #        explain that nothing is being viewed, rather than
            #        silently attempting clicks on a screen nobody sees.
            _screen_viewer_open = bool(options and options.get("screen_viewer_open", False))
            if self._is_vision_task(message, image_data) and (image_data or _screen_active or _screen_viewer_open):
                tier_used = 2  # Vision goes through Tier 2 with vision prompt
                return self._instinct(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    prompt_key="vision", **kwargs,
                )

            # -- Conversational pass-through (Tier 2, no tools) --
            if CONVERSATIONAL_PASSTHROUGH.match(message.strip()):
                tier_used = 2
                return self._instinct(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, is_voice_message=is_voice_message,
                    skip_tools=True, **kwargs,
                )

            # -- Check if Tier 3 is needed --
            if self._needs_deliberation(message):
                tier_used = 3
                return self._deliberate(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    **kwargs,
                )

            # -- Default: Tier 2 (single-shot with tools) --
            tier_used = 2
            result = self._instinct(
                session_id, message, options, emit_fn, app,
                project_id=project_id, image_data=image_data,
                image_url=image_url, is_voice_message=is_voice_message,
                **kwargs,
            )

            # Check for escalation signals in the response
            if result.get("needs_escalation"):
                escalated_from = 2
                escalation_reason = result.get(
                    "escalation_reason", "model signaled multi-step needed"
                )
                tier_used = 3
                result = self._deliberate(
                    session_id, message, options, emit_fn, app,
                    project_id=project_id, image_data=image_data,
                    image_url=image_url, is_voice_message=is_voice_message,
                    initial_context=result,
                    **kwargs,
                )

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
            )
            _log_telemetry(telemetry)

    # -- Tier 2: Instinct ---------------------------------------------------

    # -- Gemma4 direct path -------------------------------------------------

    def _gemma4_direct(
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

            # Delegate straight to the robust execute_task loop
            agent_result = acs.execute_task(
                task=message, 
                screen=screen, 
                emit_fn=emit_fn, 
                chat_context=history_str
            )

            # Narrate the outcome in Guaardvark's voice
            response = self._narrate_agent_outcome(
                user_message=message,
                agent_result=agent_result,
                self_knowledge=self_knowledge,
                emit_fn=emit_fn,
                session_id=session_id,
            )

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
                with app.app_context():
                    try:
                        from backend.models import LLMMessage, db
                        from datetime import datetime as _dt
                        clean = re.sub(r'<[^>]*>', '', response).strip()
                        extra = {}
                        if generated_images:
                            extra["generatedImages"] = generated_images
                        try:
                            agent_thinking_steps = acs.drain_thinking_steps()
                            if agent_thinking_steps:
                                extra["agentThinkingSteps"] = agent_thinking_steps
                        except Exception:
                            pass
                        content = clean if not clean.startswith("{") else f"[Action] {response}"
                        msg = LLMMessage(
                            session_id=session_id,
                            role="assistant",
                            content=content,
                            extra_data=extra or None,
                            timestamp=_dt.now(),
                        )
                        db.session.add(msg)
                        db.session.commit()
                    except Exception:
                        pass

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

        persona = (self_knowledge or "").strip()[:600]
        system_prompt = (
            "You are Guaardvark. "
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

        # Gemma4 spends 100+ tokens on internal reasoning before emitting visible
        # content. Buffer the full response, strip <think> blocks, then emit —
        # streaming each token live would leak the reasoning to the user.
        # Two attempts: right after a vision-heavy action loop, Ollama occasionally
        # returns a zero-chunk stream on the first call. A second try resolves it.
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
            text = re.sub(r'<think>[\s\S]*?</think>\s*', '', raw).strip()
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
            "killed": "Stopped that one.",
        }
        if r in mapping:
            return mapping[r]
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
                        from backend.services.servo_controller import ServoController
                        from backend.services.training_data_collector import TrainingDataCollector
                        from backend.services.servo_knowledge_store import get_vision_config
                        from backend.utils.vision_analyzer import VisionAnalyzer
                        servo = ServoController(
                            screen, VisionAnalyzer(),
                            collector=TrainingDataCollector(),
                            vision_config=get_vision_config(),
                        )
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
                from backend.utils.vision_analyzer import VisionAnalyzer

                screenshot, cursor_pos = screen.capture()
                os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
                filename = f"agent_capture_{int(_sc_time.time() * 1000)}.webp"
                filepath = os.path.join(SCREENSHOTS_DIR, filename)
                screenshot.save(filepath, format="WEBP", quality=80)
                image_url = f"/api/tools/screenshots/{filename}"
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
                    "http://localhost:11434/api/generate",
                    json={"model": self.state.active_model, "keep_alive": 0},
                    timeout=5,
                )
            except Exception:
                pass

            try:
                from backend.tools.image_tools import ImageGeneratorTool
                tool = ImageGeneratorTool()
                result = tool.execute(
                    prompt=prompt,
                    style=action.get("style", "realistic"),
                    width=int(action.get("width", 512)),
                    height=int(action.get("height", 512)),
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
            engine = UnifiedChatEngine(
                tool_registry=self.state.tool_registry,
                llm_instance=self.state.llm,
                max_iterations=1 if skip_tools else 5,
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
            )

            # Post-response narration check
            response_text = result.get("response", "")
            if response_text and not result.get("tools_used"):
                narrated = self._extract_narrated_tool_intent(
                    response_text, message
                )
                if narrated:
                    tool_name, params = narrated
                    logger.info(
                        f"Narration detected: '{tool_name}' — executing directly"
                    )
                    tool_result = self.state.tool_registry.execute_tool(
                        tool_name, **params
                    )
                    if tool_result.success:
                        # Re-emit with actual tool result
                        output = tool_result.output
                        if isinstance(output, dict):
                            formatted = "\n".join(
                                f"{k}: {v}" for k, v in output.items()
                                if v and k != "metadata"
                            )
                        else:
                            formatted = str(output)
                        self._emit_response(
                            emit_fn, session_id, formatted,
                            result.get("request_id", ""),
                        )
                        result["response"] = formatted
                        result["narration_intercepted"] = True

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
            return self._instinct(
                session_id, message, options, emit_fn, app,
                project_id=project_id, image_data=image_data,
                image_url=image_url, is_voice_message=is_voice_message,
                **kwargs,
            )

        try:
            from backend.services.agent_executor import AgentExecutor

            executor = AgentExecutor(
                tool_registry=self.state.tool_registry,
                llm=self.state.llm,
                max_iterations=self.state.max_agent_iterations,
            )

            # Build session context from initial Tier 2 result if escalated
            session_context = ""
            if initial_context:
                prev_response = initial_context.get("response", "")
                prev_tools = initial_context.get("tools_used", [])
                if prev_response:
                    session_context += f"\nPrevious attempt (single-shot): {prev_response[:500]}"
                if prev_tools:
                    session_context += f"\nTools already tried: {', '.join(prev_tools)}"

            result = executor.execute(
                user_query=message,
                session_context=session_context,
            )

            response_text = result.final_answer if result.success else (
                result.error or "I wasn't able to complete that task."
            )

            self._emit_response(emit_fn, session_id, response_text, "")

            return {
                "success": result.success,
                "response": response_text,
                "iterations": result.iterations,
                "tier": 3,
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
        self, emit_fn: Callable, session_id: str, response: str, request_id: str
    ):
        """Emit a complete response via Socket.IO."""
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
