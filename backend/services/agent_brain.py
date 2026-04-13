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

            # -- Vision routing — only when the agent screen is being watched.
            # Otherwise vision-sounding messages like "click the Firefox button"
            # route through normal Instinct so the model can explain that the
            # screen isn't being viewed, rather than silently attempting clicks.
            if self._is_vision_task(message, image_data) and _screen_active:
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

        Gemma4 has native vision, pointing, and reasoning. We send it:
        1. A minimal system prompt (identity + desktop state + memories)
        2. Conversation history
        3. The user's message — unmodified
        4. A screenshot of the agent display (if relevant)

        Gemma4 decides what to do. If it wants to use a tool, it says so
        in structured JSON and we execute it. If it just wants to chat, it chats.

        Returns None if this path can't handle the message (falls through to legacy).
        """
        try:
            import ollama as ollama

            # Track generated images for chat persistence (same pattern as unified engine)
            generated_images = []

            # Build minimal context — just what Gemma4 needs to know
            desktop_state = ""
            try:
                desktop_state = AgentControlService._get_desktop_state()
            except Exception:
                desktop_state = "Desktop state: unknown"

            memory_block = ""
            try:
                from backend.api.memory_api import get_memories_for_context
                memory_block = get_memories_for_context(limit=10, max_tokens=300)
            except Exception:
                pass

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

            # Extract DOM metadata from Firefox (interactive elements with coordinates)
            dom_metadata = ""
            try:
                from backend.services.dom_metadata_extractor import DOMMetadataExtractor
                snapshot = DOMMetadataExtractor.get_instance().extract()
                if snapshot.success and snapshot.elements:
                    dom_metadata = DOMMetadataExtractor.format_for_prompt(snapshot)
            except Exception as e:
                logger.debug(f"DOM metadata extraction unavailable: {e}")

            # System prompt — identity, state, DOM, actions
            system = (
                "You are Guaardvark, a local AI assistant with a virtual screen.\n\n"
                f"{desktop_state}\n"
            )
            if dom_metadata:
                system += f"\n{dom_metadata}\n"
            if self_knowledge:
                system += f"\n{self_knowledge}\n"
            if memory_block:
                system += f"\n{memory_block}\n"

            system += (
                "\nFor screen actions, respond with JSON:\n"
                '{"action": "click", "target": "the Search button"}\n'
                '{"action": "type", "text": "youtube.com"}\n'
                '{"action": "hotkey", "keys": ["ctrl", "l"]}\n'
                '{"action": "done", "summary": "what you accomplished"}\n'
                '{"action": "generate_image", "prompt": "description of image to create"}\n'
                '{"action": "screenshot"}\n\n'
                "If DOM elements with coordinates are listed above, include x and y in clicks.\n"
                "For conversation, respond normally without JSON."
            )

            # Load history
            history = []
            if app:
                with app.app_context():
                    try:
                        from backend.services.unified_chat_engine import UnifiedChatEngine
                        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
                        engine.app = app
                        history = engine._load_history(session_id, limit=20)
                    except Exception:
                        pass

            # Build messages
            messages = [{"role": "system", "content": system}]
            for msg in history:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["content"]})

            # User message — attach screenshot if this looks like a screen task
            user_msg = {"role": "user", "content": message}

            # Gemma4's eyes — but only when the message could plausibly be
            # about screen state. Attaching a 1280x720 screenshot to every
            # "hello" was starving inference (5+ minutes for first token on
            # CPU/GPU split). For pure chat we skip the screenshot entirely;
            # for everything else we downscale hard before sending.
            needs_screen = not NO_SCREEN_CONTEXT.match(message.strip())
            if needs_screen:
                try:
                    from backend.services.local_screen_backend import LocalScreenBackend
                    screen = LocalScreenBackend()
                    screenshot, _ = screen.capture()
                    import base64
                    from io import BytesIO
                    # Quarter the pixel count vs native 1280x720 — keeps UI
                    # elements legible to the model but cuts vision-token cost.
                    screenshot.thumbnail((640, 360))
                    buf = BytesIO()
                    screenshot.save(buf, format="JPEG", quality=70)
                    user_msg["images"] = [base64.b64encode(buf.getvalue()).decode()]
                except Exception as e:
                    logger.debug(f"Gemma4 direct: screenshot capture failed (non-fatal): {e}")

            # If user pasted an image, use that instead
            if image_data:
                user_msg["images"] = [image_data]

            messages.append(user_msg)

            # Save user message to DB
            if app:
                with app.app_context():
                    try:
                        from backend.utils.chat_utils import save_message_to_db, get_or_create_session
                        get_or_create_session(session_id)
                        save_message_to_db(session_id, "user", message)
                    except Exception:
                        pass

            # Call Gemma4 — buffer first, execute actions, THEN stream to user.
            # This way the user sees the thinking + result, not raw JSON.
            emit_fn("chat:thinking", {"iteration": 1, "status": "Gemma4 is looking..."})

            model = self.state.active_model
            accumulated = []
            input_tokens = 0
            output_tokens = 0

            # Hard read deadline on the Ollama stream so a wedged vision call
            # can't hold the GPU slot forever. 90s is generous for first-token
            # on CPU/GPU split inference but cuts true wedges loose quickly.
            import httpx as _httpx
            client = ollama.Client(
                timeout=_httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=30.0),
            )
            try:
                stream = client.chat(
                    model=model,
                    messages=messages,
                    stream=True,
                    options={"num_ctx": 8192, "num_predict": 4096, "temperature": 0.4},
                )

                for chunk in stream:
                    # Check if a newer message aborted us
                    if is_aborted(session_id):
                        logger.info(f"Gemma4 direct: aborted mid-stream for session {session_id}")
                        return None
                    msg = chunk.get("message", {})
                    token = msg.get("content", "")
                    if token:
                        accumulated.append(token)
                    if chunk.get("done"):
                        input_tokens = chunk.get("prompt_eval_count", 0) or 0
                        output_tokens = chunk.get("eval_count", 0) or 0
            except (_httpx.ReadTimeout, _httpx.WriteTimeout, _httpx.ConnectTimeout, _httpx.PoolTimeout) as timeout_err:
                logger.error(
                    f"Gemma4 direct: Ollama stream timed out for session {session_id} "
                    f"({type(timeout_err).__name__}: {timeout_err}). Emitting chat:error."
                )
                emit_fn("chat:error", {
                    "error": "Vision model timed out (no token in 90s). Try a shorter "
                             "message, or restart Ollama if this keeps happening.",
                    "session_id": session_id,
                })
                # Return a real result dict — NOT None — so process() doesn't
                # fall through to the legacy path and call Ollama all over again.
                return {
                    "success": False,
                    "error": "stream_timeout",
                    "tier": 0,
                    "request_id": request_id,
                    "session_id": session_id,
                }

            response = "".join(accumulated).strip()
            # Strip <think>...</think> blocks
            thinking = ""
            think_match = re.search(r'<think>([\s\S]*?)</think>', response)
            if think_match:
                thinking = think_match.group(1).strip()
            response = re.sub(r'<think>[\s\S]*?</think>\s*', '', response).strip()

            # Check if Gemma4 wants to perform actions
            actions = self._parse_gemma4_actions(response)

            if actions:
                import time as _action_time
                # Gemma4 is requesting screen actions — execute them all in sequence
                if thinking:
                    emit_fn("chat:token", {"content": f"*{thinking[:200]}*\n\n", "session_id": session_id})

                results = []
                for i, action_item in enumerate(actions):
                    action_type = action_item.get("action", "")
                    target = action_item.get("target", action_item.get("text", ""))

                    # Show what we're about to do
                    if action_type == "click":
                        x, y = action_item.get("x", "?"), action_item.get("y", "?")
                        emit_fn("chat:token", {"content": f"Clicking: {target} ({x},{y})\n", "session_id": session_id})
                    elif action_type == "type":
                        emit_fn("chat:token", {"content": f"Typing: {target}\n", "session_id": session_id})
                    elif action_type == "hotkey":
                        keys = action_item.get("keys", [])
                        emit_fn("chat:token", {"content": f"Pressing: {'+'.join(keys)}\n", "session_id": session_id})
                    elif action_type == "navigate":
                        emit_fn("chat:token", {"content": f"Navigating: {action_item.get('url', target)}\n", "session_id": session_id})
                    elif action_type == "generate_image":
                        emit_fn("chat:token", {"content": f"Generating image: {action_item.get('prompt', '')[:80]}\n", "session_id": session_id})
                    elif action_type == "screenshot":
                        emit_fn("chat:token", {"content": "Taking screenshot...\n", "session_id": session_id})

                    # Execute
                    exec_result = self._execute_gemma4_action(
                        action_item, message, session_id, emit_fn, app
                    )
                    if exec_result:
                        results.append(exec_result)

                    # Track generated images for chat persistence
                    if action_type == "generate_image" and exec_result and "failed" not in exec_result.lower():
                        generated_images.append({
                            "url": action_item.get("_image_url", ""),
                            "alt": f"Generated: {action_item.get('prompt', '')[:60]}",
                            "caption": action_item.get("prompt", ""),
                        })

                    # Pause between actions — gives the screen time to update
                    # and makes demo recordings watchable. 1s is enough for
                    # humans to follow along without feeling sluggish.
                    if i < len(actions) - 1:
                        _action_time.sleep(1.0)

                response = "\n".join(results) if results else "Actions completed."
                emit_fn("chat:token", {"content": f"\n{response}", "session_id": session_id})
            else:
                # Regular conversation — stream the buffered response to the user
                # Strip any residual JSON that looks like an action attempt
                for token in accumulated:
                    clean_token = token
                    if "<think>" in clean_token or "</think>" in clean_token:
                        continue
                    emit_fn("chat:token", {"content": clean_token, "session_id": session_id})

            # Emit complete
            emit_fn("chat:complete", {
                "response": response,
                "iterations": 1,
                "steps": [],
                "session_id": session_id,
                "request_id": request_id,
                "token_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
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
                "tier": 0,  # Tier 0 = direct path, no routing overhead
                "request_id": request_id,
                "session_id": session_id,
            }

        except Exception as e:
            logger.error(f"Gemma4 direct path failed: {e}", exc_info=True)
            return None  # Fall through to legacy

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
                # Gemma4 gave a target but no coords — try DOM lookup
                try:
                    from backend.services.dom_metadata_extractor import DOMMetadataExtractor
                    snap = DOMMetadataExtractor.get_instance().extract()
                    for el in (snap.elements if snap.success else []):
                        if target.lower() in (el.text or "").lower():
                            x, y = el.cx, el.cy
                            break
                except Exception:
                    pass

            if x is None or y is None:
                return f"Cannot click '{target}' — no coordinates. Try again with x and y."

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
