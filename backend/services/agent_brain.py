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

        try:
            # -- Gemma4 direct path: no chains, no routing, no bloated prompts --
            # Gemma4 has native vision + pointing + tool use. Just send it the
            # user's message with a screenshot and let it decide what to do.
            # Other models go through the legacy tier routing below.
            if (self.state.model_caps.is_vision_model
                    and "gemma4" in self.state.active_model.lower()
                    and not force_tier):
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

            # -- Vision routing (unchanged — own subsystem) --
            if self._is_vision_task(message, image_data):
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
            import ollama as _ollama

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

            # Minimal system prompt — identity, state, self-knowledge
            system = (
                "You are Guaardvark, a local AI assistant with a virtual screen (DISPLAY=:99).\n\n"
                f"{desktop_state}\n"
            )
            if self_knowledge:
                system += f"\n{self_knowledge}\n"
            if memory_block:
                system += f"\n{memory_block}\n"

            # Tool instructions — only if the message looks action-oriented
            system += (
                "\nYou have these capabilities:\n"
                "- See the virtual screen (screenshots are attached to your messages)\n"
                "- Click UI elements by describing what to click\n"
                "- Type text, press keyboard shortcuts\n"
                "- Search the web\n"
                "- Answer questions from knowledge\n\n"
                "When you need to perform an action on the virtual screen, respond with JSON:\n"
                '{"action": "click", "target": "the blue Quit button"}\n'
                '{"action": "type", "text": "youtube.com"}\n'
                '{"action": "hotkey", "keys": ["ctrl", "l"]}\n'
                '{"action": "done", "summary": "what you accomplished"}\n\n'
                "For regular conversation, just respond normally — no JSON needed.\n"
                "Keep responses concise. The user can see the screen too."
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

            # Attach agent screen screenshot for any message
            # Gemma4 can see — let it decide if the screenshot is relevant
            try:
                from backend.services.local_screen_backend import LocalScreenBackend
                screen = LocalScreenBackend()
                screenshot, _ = screen.capture()
                import base64
                from io import BytesIO
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

            stream = _ollama.chat(
                model=model,
                messages=messages,
                stream=True,
                options={"num_ctx": 8192, "num_predict": 1024, "temperature": 0.4},
            )

            for chunk in stream:
                msg = chunk.get("message", {})
                token = msg.get("content", "")
                if token:
                    accumulated.append(token)
                if chunk.get("done"):
                    input_tokens = chunk.get("prompt_eval_count", 0) or 0
                    output_tokens = chunk.get("eval_count", 0) or 0

            response = "".join(accumulated).strip()
            # Strip <think>...</think> blocks
            thinking = ""
            think_match = re.search(r'<think>([\s\S]*?)</think>', response)
            if think_match:
                thinking = think_match.group(1).strip()
            response = re.sub(r'<think>[\s\S]*?</think>\s*', '', response).strip()

            # Check if Gemma4 wants to perform an action
            action_result = self._parse_gemma4_action(response)

            if action_result:
                # Gemma4 is requesting a screen action — execute it first,
                # then show the user what happened (not the raw JSON).
                action_type = action_result.get("action", "")
                target = action_result.get("target", action_result.get("text", ""))

                # Stream the thinking/reasoning to the user
                reasoning = action_result.get("reasoning", "")
                if thinking:
                    emit_fn("chat:token", {"content": f"*Thinking: {thinking[:200]}*\n\n", "session_id": session_id})
                if reasoning:
                    emit_fn("chat:token", {"content": f"*{reasoning}*\n\n", "session_id": session_id})

                # Show what we're about to do
                if action_type == "click":
                    emit_fn("chat:token", {"content": f"Clicking: {target}\n", "session_id": session_id})
                elif action_type == "type":
                    emit_fn("chat:token", {"content": f"Typing: {target}\n", "session_id": session_id})
                elif action_type == "hotkey":
                    keys = action_result.get("keys", [])
                    emit_fn("chat:token", {"content": f"Pressing: {'+'.join(keys)}\n", "session_id": session_id})

                # Execute the action
                emit_fn("chat:thinking", {"iteration": 2, "status": f"Executing {action_type}..."})
                exec_result = self._execute_gemma4_action(
                    action_result, message, session_id, emit_fn, app
                )
                if exec_result:
                    emit_fn("chat:token", {"content": f"\n{exec_result}", "session_id": session_id})
                    response = exec_result
                else:
                    emit_fn("chat:token", {"content": "\nAction completed.", "session_id": session_id})
                    response = "Action completed."
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
            })

            # Save assistant response
            if app and response:
                with app.app_context():
                    try:
                        from backend.utils.chat_utils import save_message_to_db
                        clean = re.sub(r'<[^>]*>', '', response).strip()
                        # Don't save raw JSON actions — save the result
                        if not clean.startswith("{"):
                            save_message_to_db(session_id, "assistant", clean)
                        else:
                            save_message_to_db(session_id, "assistant", f"[Action: {action_result.get('action', '?')}] {response}")
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

    def _parse_gemma4_action(self, response: str) -> Optional[Dict]:
        """Check if Gemma4's response contains an action JSON."""
        try:
            # Look for JSON in the response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                candidate = response[start:end]
                data = json.loads(candidate)
                if "action" in data:
                    return data
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def _execute_gemma4_action(
        self, action: Dict, original_task: str,
        session_id: str, emit_fn: Callable, app=None,
    ) -> Optional[str]:
        """Execute an action that Gemma4 requested."""
        action_type = action.get("action", "").lower()

        if action_type == "done":
            return action.get("summary", "Done.")

        if action_type in ("click", "right_click"):
            target = action.get("target", "")
            if not target:
                return None
            emit_fn("chat:tool_call", {
                "tool": "agent_task_execute",
                "params": {"task": f"Click the {target}"},
                "iteration": 1,
            })
            try:
                result = self.state.tool_registry.execute_tool(
                    "agent_task_execute", task=f"Click the {target}"
                )
                emit_fn("chat:tool_result", {
                    "tool": "agent_task_execute",
                    "result": {"success": result.success, "output": str(result.output)[:500]},
                })
                return str(result.output) if result.success else f"Click failed: {result.error}"
            except Exception as e:
                return f"Action failed: {e}"

        if action_type == "type":
            text = action.get("text", "")
            if not text:
                return None
            try:
                from backend.services.local_screen_backend import LocalScreenBackend
                screen = LocalScreenBackend()
                screen.type_text(text)
                return f"Typed: {text}"
            except Exception as e:
                return f"Type failed: {e}"

        if action_type == "hotkey":
            keys = action.get("keys", [])
            if not keys:
                return None
            try:
                from backend.services.local_screen_backend import LocalScreenBackend
                screen = LocalScreenBackend()
                screen.hotkey(*keys)
                return f"Pressed: {'+'.join(keys)}"
            except Exception as e:
                return f"Hotkey failed: {e}"

        if action_type == "navigate":
            url = action.get("url", "")
            if url:
                try:
                    result = self.state.tool_registry.execute_tool(
                        "agent_task_execute", task=f"Navigate to {url}"
                    )
                    return str(result.output) if result.success else f"Navigate failed: {result.error}"
                except Exception as e:
                    return f"Navigate failed: {e}"

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
