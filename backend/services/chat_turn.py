"""Start and route one chat turn, the same way for every entry point.

POST /api/chat/unified, the chat:send socket event and the plan routes all
begin turns here, so a gate added in ``route_turn`` (plan mode, for one)
covers every path into the brain, including the slash-command shortcut that
runs before any routing.
"""

import contextlib
import logging
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def build_router(app) -> Tuple[Any, Any, Optional[str]]:
    """(agent_brain, legacy_engine, error) for a turn.

    AgentBrain is the router whenever it is enabled; the legacy engine is
    used only when GUAARDVARK_AGENT_BRAIN=false. An enabled brain that is not
    ready is an error, never a silent fallback.
    """
    from backend.config import AGENT_BRAIN_ENABLED

    brain_state = getattr(app, "brain_state", None)
    if AGENT_BRAIN_ENABLED:
        if brain_state and brain_state.is_ready:
            try:
                from backend.services.agent_brain import AgentBrain
                return AgentBrain(state=brain_state), None, None
            except Exception as e:
                logger.error(f"AgentBrain could not be built: {e}", exc_info=True)
        return None, None, "AgentBrain enabled but not ready (brain_state missing or !is_ready)."

    llm = app.config.get("LLAMA_INDEX_LLM")
    if not llm:
        try:
            from backend.utils.llm_service import get_llm_for_startup
            llm = get_llm_for_startup()
            app.config["LLAMA_INDEX_LLM"] = llm
        except Exception as e:
            logger.error(f"Failed to create LLM instance: {e}")
            return None, None, "LLM not available. Check Ollama is running."
    try:
        from backend.tools.tool_registry_init import initialize_all_tools
        registry = initialize_all_tools()
    except Exception as e:
        logger.error(f"Failed to initialize tool registry: {e}")
        return None, None, "Tool registry unavailable"
    from backend.services.unified_chat_engine import UnifiedChatEngine
    return None, UnifiedChatEngine(registry, llm), None


def make_session_emit(session_id: str, turn_mark: int, log_tag: str = "UNIFIED-EMIT") -> Callable:
    """An emit function for a turn: events go to the session's room until a
    newer message replaces the turn."""
    from backend.services.tool_activity import broadcast_tool_activity
    from backend.services.unified_chat_engine import turn_replaced
    from backend.socketio_instance import socketio

    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        if turn_replaced(session_id, turn_mark):
            # Stopped and replaced by a newer message: the room belongs to that
            # turn now, and this one's late events would end or garble it.
            return
        if socketio.server is None:
            logger.warning(f"SocketIO server not initialized, dropping event {event} for session {session_id}")
            return
        try:
            is_think = event == "chat:thinking"
            is_agent_think = is_think and data_payload.get("source") == "agent_loop"
            log_payload = {
                "iter": data_payload.get("iteration"),
                "status": data_payload.get("status"),
                "has_reasoning": bool(data_payload.get("reasoning")) if is_think else None,
                "reasoning_preview": (str(data_payload.get("reasoning", ""))[:80] + "...") if is_agent_think else None,
                "response_preview": (str(data_payload.get("response", ""))[:60] + "...") if event == "chat:complete" else None,
            }
            logger.info(
                f"[{log_tag}] {event} room={session_id} agent_think={is_agent_think} "
                f"keys={list(data_payload.keys()) if isinstance(data_payload, dict) else type(data_payload)} "
                f"payload={log_payload}"
            )
            socketio.emit(event, data_payload, room=session_id)
            broadcast_tool_activity(event, data_payload)
        except Exception as emit_err:
            logger.warning(f"Failed to emit {event}: {emit_err}")

    return emit_fn


def route_turn(
    *,
    session_id: str,
    message: str,
    options: Dict[str, Any],
    emit_fn: Callable,
    app,
    project_id=None,
    image_data=None,
    image_url=None,
    is_voice_message: bool = False,
    agent_brain=None,
    engine=None,
    log_tag: str = "UNIFIED_API",
) -> None:
    """Route one turn: plan mode first, then slash shortcuts, then the brain
    (or the legacy engine)."""
    # Plan mode sits in front of everything that can act, the no-model slash
    # shortcut included. The flag comes from the session row, never the client.
    if options.get("plan_mode") and not options.get("plan_step"):
        from backend.services.plan_mode import handle_turn
        if handle_turn(session_id=session_id, message=message, options=options, emit_fn=emit_fn,
                       app=app, project_id=project_id, image_data=image_data, image_url=image_url,
                       is_voice_message=is_voice_message, agent_brain=agent_brain, engine=engine):
            return

    # Direct tool / slash intercept (/imagine etc.) runs before any routing,
    # with no model involved.
    try:
        from backend.services.slash_command_executor import run_direct_tool_turn
        if run_direct_tool_turn(options, message, session_id, emit_fn, app,
                                project_id=project_id, log_tag=log_tag):
            return
    except Exception as _dir_e:
        logger.debug(f"Direct intercept pre-check skipped or failed (will route normally): {_dir_e}")

    if agent_brain is not None:
        agent_brain.process(
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
    elif engine is not None:
        engine.chat(session_id, message, options, emit_fn, app=app,
                    project_id=project_id, image_data=image_data, image_url=image_url,
                    is_voice_message=is_voice_message)
    else:
        raise RuntimeError("No routing engine available")


def run_chat_turn(
    *,
    session_id: str,
    turn_mark: int,
    emit_fn: Callable,
    is_voice_message: bool,
    body: Callable[[], None],
    log_tag: str = "UNIFIED_API",
) -> None:
    """Run ``body`` as the session's chat turn on the current thread.

    Binds the turn so Stop reaches it, holds background work while it runs,
    lets the screen agent stream its steps into the chat, and reports a crash
    as chat:error.
    """
    from backend.services import job_arbiter
    from backend.services.agent_control_service import set_chat_emit_fn, set_chat_stop_check
    from backend.services.unified_chat_engine import begin_turn, end_turn, is_aborted

    begin_turn(session_id, turn_mark)
    interactive_turn = contextlib.ExitStack()
    interactive_turn.enter_context(
        job_arbiter.interactive("voice_chat" if is_voice_message else "chat", session_id)
    )
    try:
        logger.info(f"[{log_tag}] chat turn thread start session={session_id}")
        # agent_control tools (agent_task_execute etc.) stream their
        # see-think-act steps through this thread-local emitter.
        set_chat_emit_fn(emit_fn)
        set_chat_stop_check(lambda: is_aborted(session_id))
        body()
    except Exception as e:
        logger.error(f"[{log_tag}] chat turn error: {e}", exc_info=True)
        emit_fn("chat:error", {"error": str(e)})
    finally:
        interactive_turn.close()
        end_turn(session_id)
        # The next turn on this thread gets a fresh emitter.
        try:
            set_chat_emit_fn(None)
            set_chat_stop_check(None)
        except Exception:
            pass
