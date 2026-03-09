"""
LLM Debug Logger — comprehensive logging of LLM prompts, responses, tool calls, and decisions.
Gated by the 'llm_debug' setting. All functions are no-ops when disabled.
Output: logs/llm_debug.log
"""

import json
import logging

logger = logging.getLogger("guaardvark.llm_debug")


def _is_enabled() -> bool:
    """Check if LLM debug logging is enabled (cached per call)."""
    try:
        from backend.utils.settings_utils import get_llm_debug
        return get_llm_debug()
    except Exception:
        return False


def llm_log(event_type: str, data: dict):
    """Log an LLM debug event if llm_debug is enabled."""
    try:
        if not _is_enabled():
            return
        msg = f"[{event_type}] {json.dumps(data, default=str, ensure_ascii=False)}"
        logger.debug(msg)
    except Exception:
        pass  # Never break the main flow for debug logging


def log_system_prompt(pipeline: str, prompt: str, session_id: str = None):
    llm_log("SYSTEM_PROMPT", {
        "pipeline": pipeline, "session_id": session_id,
        "prompt": prompt,
    })


def log_user_message(pipeline: str, message: str, session_id: str = None):
    llm_log("USER_MESSAGE", {
        "pipeline": pipeline, "session_id": session_id,
        "message": message,
    })


def log_llm_response(pipeline: str, response: str, session_id: str = None, iteration: int = None):
    llm_log("LLM_RESPONSE", {
        "pipeline": pipeline, "session_id": session_id,
        "iteration": iteration, "response": response,
    })


def log_tool_call(pipeline: str, tool_name: str, params: dict, reasoning: str = None, iteration: int = None):
    llm_log("TOOL_CALL", {
        "pipeline": pipeline, "tool_name": tool_name,
        "params": params, "reasoning": reasoning,
        "iteration": iteration,
    })


def log_tool_result(pipeline: str, tool_name: str, success: bool, result: str, iteration: int = None):
    llm_log("TOOL_RESULT", {
        "pipeline": pipeline, "tool_name": tool_name,
        "success": success, "result": str(result)[:2000],
        "iteration": iteration,
    })


def log_guard_event(pipeline: str, event: str, tool_name: str, details: str = None):
    llm_log("GUARD_EVENT", {
        "pipeline": pipeline, "event": event,
        "tool_name": tool_name, "details": details,
    })


def log_decision(pipeline: str, decision: str, context: dict = None):
    llm_log("DECISION", {
        "pipeline": pipeline, "decision": decision,
        "context": context,
    })
