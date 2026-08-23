"""
Unified Chat API
Single endpoint that gives the LLM tool access + RAG + conversation history.
Response streamed via Socket.IO events.
"""

import logging
import threading
import uuid

from flask import Blueprint, current_app, request, jsonify

logger = logging.getLogger(__name__)

unified_chat_bp = Blueprint("unified_chat", __name__, url_prefix="/api/chat/unified")

# Per-session in-flight chat threads. Without this, a stuck Ollama vision call
# would let retries pile up forever — every retry spawned a fresh thread that
# also hit Ollama, compounding the wedge. New requests for a session that's
# already running get a 409 instead.
_inflight: dict = {}
_inflight_lock = threading.Lock()


def _merge_session_mode_options(session_id: str, options: dict | None) -> dict:
    """Merge persisted session mode into per-request routing options."""
    merged = dict(options or {})
    client_agent_screen_active = bool(merged.get("agent_screen_active", False))
    merged["agent_screen_active"] = client_agent_screen_active

    try:
        from backend.models import LLMSession, db

        session = db.session.get(LLMSession, session_id)
        session_mode = (session.mode if session and session.mode else "chat").strip()
        merged["session_mode"] = session_mode
        if session_mode == "agent":
            merged["agent_screen_active"] = True
    except Exception as exc:
        logger.warning(
            "[UNIFIED_CHAT] Failed to load session mode for %s: %s",
            session_id,
            exc,
        )

    return merged


@unified_chat_bp.route("", methods=["POST"])
def unified_chat():
    """
    POST /api/chat/unified
    Body: { session_id, message, options: { use_rag, chat_mode } }
    Returns immediate ack; actual response streamed via Socket.IO.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    message = data.get("message", "").strip()
    image_data = data.get("image")  # Optional base64-encoded image
    raw_options = data.get("options", {})
    options = raw_options if isinstance(raw_options, dict) else {}
    has_direct_tool = bool(
        options.get("direct_tool")
        or options.get("slash_command")
    )

    if not message and not image_data and not has_direct_tool:
        return jsonify({"success": False, "error": "Message or image is required"}), 400

    # If image provided but no message, set a default
    if not message and image_data:
        message = "Describe this image."
    if not message and has_direct_tool:
        slash = (options.get("slash_command") or options.get("direct_tool") or "").strip()
        args = (options.get("slash_args") or "").strip()
        message = f"/{slash.lstrip('/')} {args}".strip() if slash else "direct tool"

    session_id = data.get("session_id") or str(uuid.uuid4())
    is_voice_message = bool(data.get("is_voice_message", False))
    request_id = str(uuid.uuid4())
    project_id = data.get("project_id")
    # Support project_root for loading GUAARDVARK.md and project-specific context.
    # This enables consistent "analyze dragged folder" experience in CLI and GUI.
    project_root = data.get("project_root") or data.get("projectRoot") or options.get("project_root")
    if project_root:
        options["project_root"] = str(project_root)
    options = _merge_session_mode_options(session_id, options)

    # Also forward to direct-tool for slash commands etc.
    if project_root and "project_root" not in options:
        options["project_root"] = str(project_root)

    # Abort any still-running generation on this session — a new message from
    # the user means "stop what you're doing and listen to this instead."
    # Without this, the old thread keeps running (and its agent_task_execute
    # keeps the agent locked, so the new task gets "Agent already active").
    from backend.services.unified_chat_engine import set_abort_flag
    set_abort_flag(session_id)

    logger.info(
        f"[UNIFIED_CHAT] request_id={request_id[:8]} session={session_id} "
        f"project={project_id} message={message[:80]!r}"
    )
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            project_id = None

    # Phase 2.2: AgentBrain is the sole canonical router (when AGENT_BRAIN_ENABLED, default true).
    # Legacy unified_chat_engine is bridge ONLY for explicit disable (GUAARDVARK_AGENT_BRAIN=false)
    # or when brain_state not ready. No silent fallback when enabled.
    use_agent_brain = False
    agent_brain = None
    agent_brain_enabled = False
    try:
        from backend.config import AGENT_BRAIN_ENABLED
        agent_brain_enabled = AGENT_BRAIN_ENABLED
        brain_state = getattr(current_app, 'brain_state', None)
        if AGENT_BRAIN_ENABLED and brain_state and brain_state.is_ready:
            from backend.services.agent_brain import AgentBrain
            agent_brain = AgentBrain(state=brain_state)
            use_agent_brain = True
            logger.info(f"[UNIFIED_CHAT] Using AgentBrain (sole canonical router per Phase 2.2)")
    except Exception as e:
        logger.debug(f"AgentBrain not available: {e}")

    engine = None
    if not use_agent_brain:
        if agent_brain_enabled:
            # Enforce sole: do not fall back when explicitly enabled. Surface clear error.
            logger.error("[UNIFIED_CHAT] AGENT_BRAIN_ENABLED but brain_state not ready - no legacy fallback")
            return jsonify({"success": False, "error": "AgentBrain enabled but not ready (brain_state missing or !is_ready)."}), 503
        # Legacy bridge path only when disabled (deprecated during 2.2)
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if not llm:
            logger.warning("LLAMA_INDEX_LLM not in app config, creating on demand (legacy)")
            try:
                from backend.utils.llm_service import get_llm_for_startup
                llm = get_llm_for_startup()
                current_app.config["LLAMA_INDEX_LLM"] = llm
            except Exception as e:
                logger.error(f"Failed to create LLM instance: {e}")
                return jsonify({"success": False, "error": "LLM not available. Check Ollama is running."}), 503

        try:
            from backend.tools.tool_registry_init import initialize_all_tools
            registry = initialize_all_tools()
        except Exception as e:
            logger.error(f"Failed to initialize tool registry: {e}")
            return jsonify({"success": False, "error": "Tool registry unavailable"}), 503

        from backend.services.unified_chat_engine import UnifiedChatEngine
        engine = UnifiedChatEngine(registry, llm)
        logger.info("Using legacy UnifiedChatEngine bridge (AgentBrain disabled or not ready; Phase 2.2 unification)")

    # Build emit function
    from backend.socketio_instance import socketio

    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        if socketio.server is None:
            logger.warning(f"SocketIO server not initialized, dropping event {event} for session {session_id}")
            return
        try:
            is_think = (event == "chat:thinking")
            is_agent_think = is_think and data_payload.get("source") == "agent_loop"
            log_payload = {
                "iter": data_payload.get("iteration"),
                "status": data_payload.get("status"),
                "has_reasoning": bool(data_payload.get("reasoning")) if is_think else None,
                "reasoning_preview": (str(data_payload.get("reasoning", ""))[:80] + "...") if is_agent_think else None,
                "response_preview": (str(data_payload.get("response", ""))[:60] + "...") if event == "chat:complete" else None,
            }
            logger.info(f"[UNIFIED-EMIT] {event} room={session_id} agent_think={is_agent_think} keys={list(data_payload.keys()) if isinstance(data_payload,dict) else type(data_payload)} payload={log_payload}")
            socketio.emit(event, data_payload, room=session_id)
        except Exception as emit_err:
            logger.warning(f"Failed to emit {event}: {emit_err}")

    # Run in background thread with app context
    app = current_app._get_current_object()

    # Save image file for chat history if provided
    image_url = None
    if image_data:
        try:
            import os, base64 as b64mod, imghdr
            from backend.config import UPLOAD_DIR
            img_dir = os.path.join(UPLOAD_DIR, "chat_images")
            os.makedirs(img_dir, exist_ok=True)
            raw_bytes = b64mod.b64decode(image_data)
            # Detect actual image format from magic bytes
            ext = "png"  # default
            if raw_bytes[:4] == b"\x89PNG":
                ext = "png"
            elif raw_bytes[:3] == b"\xff\xd8\xff":
                ext = "jpg"
            elif raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
                ext = "webp"
            elif b"ftypavif" in raw_bytes[:32] or b"ftypavis" in raw_bytes[:32]:
                ext = "avif"
            elif b"ftypheic" in raw_bytes[:32] or b"ftypheix" in raw_bytes[:32]:
                ext = "heic"
            fname = f"chat_image_{uuid.uuid4().hex[:12]}.{ext}"
            with open(os.path.join(img_dir, fname), "wb") as f:
                f.write(raw_bytes)
            image_url = f"/api/enhanced-chat/vision/image/{fname}"
            logger.info(f"Saved unified chat image: {fname} ({ext})")
        except Exception as img_err:
            logger.warning(f"Failed to save chat image: {img_err}")

    # Vision pipeline: attach latest frame if active and no explicit image
    if not image_data:
        try:
            from backend.utils.vision_context_utils import get_vision_context, get_latest_frame
            vision_ctx = get_vision_context()
            if vision_ctx:
                latest_frame = get_latest_frame()
                if latest_frame:
                    image_data = latest_frame
        except Exception:
            pass

    def run_engine():
        try:
            logger.info(f"[SOCKET-CHAT] BACKEND THREAD START for session={session_id}; first chat:thinking may emit BEFORE client join_room completes (race window open)")
            # Wire the emit_fn into the thread-local so that agent_control tools
            # (agent_task_execute etc) can stream live "chat:thinking" events with
            # source=agent_loop for the see-think-act steps. This was only done
            # inside legacy engine before; AgentBrain/Tier3 paths bypassed it.
            from backend.services.agent_control_service import set_chat_emit_fn
            set_chat_emit_fn(emit_fn)
            logger.debug(
                f"[EMIT-HANDOFF][UNIFIED_API] set_chat_emit_fn called for session={session_id} "
                f"thread={threading.get_ident()} emit_fn_id={id(emit_fn)} use_agent_brain={use_agent_brain}"
            )

            # Direct tool / slash intercept ( /imagine etc ) — do this BEFORE routing
            # to brain or legacy. This guarantees the bypass even when AgentBrain
            # is the sole router. Avoids LLM involvement and the associated eviction EOFs.
            try:
                from backend.services.slash_command_executor import resolve_slash_direct_tool
                from backend.services.unified_chat_engine import UnifiedChatEngine
                from backend.utils.llm_service import get_llm_for_startup
                from backend.tools.tool_registry_init import initialize_all_tools
                d_tool, d_params = resolve_slash_direct_tool(options)
                if d_tool:
                    logger.info(f"[UNIFIED_API] Direct slash/direct intercept for {d_tool} before router")
                    reg = initialize_all_tools()
                    llm = None
                    try:
                        llm = get_llm_for_startup()
                    except Exception:
                        pass
                    eng = UnifiedChatEngine(tool_registry=reg, llm_instance=llm or object())
                    eng.app = app
                    rid = str(uuid.uuid4())
                    disp = message or f"/{options.get('slash_command', d_tool)}"
                    with app.app_context():
                        eng._run_direct_tool_execution(d_tool, d_params, session_id, emit_fn, rid, disp, options)
                    return  # done, direct path emitted everything
            except Exception as _dir_e:
                logger.debug(f"Direct intercept pre-check skipped or failed (will route normally): {_dir_e}")

            if use_agent_brain and agent_brain:
                logger.debug(
                    f"[EMIT-HANDOFF][UNIFIED_API] dispatching to AgentBrain.process session={session_id} "
                    f"emit_fn_id={id(emit_fn)}"
                )
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
            elif engine:
                # legacy bridge only
                logger.debug(
                    f"[EMIT-HANDOFF][UNIFIED_API] dispatching to legacy UnifiedChatEngine.chat session={session_id} "
                    f"emit_fn_id={id(emit_fn)}"
                )
                engine.chat(session_id, message, options, emit_fn, app=app,
                           project_id=project_id, image_data=image_data, image_url=image_url,
                           is_voice_message=is_voice_message)
            else:
                raise RuntimeError("No routing engine available")
        except Exception as e:
            logger.error(f"Chat engine thread error: {e}", exc_info=True)
            emit_fn("chat:error", {"error": str(e)})
        finally:
            # Always clear the thread-local emitter when this chat turn ends
            # (success, error, or abort) so the next turn on this thread gets a fresh one.
            try:
                from backend.services.agent_control_service import set_chat_emit_fn
                set_chat_emit_fn(None)
                logger.debug(
                    f"[EMIT-HANDOFF][UNIFIED_API] cleared chat_emit_fn in finally for session={session_id} "
                    f"thread={threading.get_ident()}"
                )
            except Exception:
                pass
            # Drop ourselves from the in-flight map so the next request can
            # take this slot. Guarded by identity check so a (defensive) race
            # with a replacement entry doesn't clobber it.
            with _inflight_lock:
                if _inflight.get(session_id) is threading.current_thread():
                    _inflight.pop(session_id, None)

    # Snapshot the existing thread (if any) so we can wait for it to unwind
    # outside the lock — we already set the abort flag above, the old thread
    # just needs a moment to notice and exit. Without this grace period, the
    # new request races the abort signal and almost always loses with 409,
    # even though we explicitly asked the old thread to step aside.
    with _inflight_lock:
        existing = _inflight.get(session_id)

    if existing is not None and existing.is_alive():
        existing.join(timeout=1.5)

    # Now claim the slot. If the old thread is *still* alive after the grace
    # period, it's genuinely wedged (stuck Ollama call, frozen tool, etc.) —
    # reject and tell the user to hard-abort or start a new session.
    with _inflight_lock:
        existing = _inflight.get(session_id)
        if existing is not None and existing.is_alive():
            logger.warning(
                f"[UNIFIED_CHAT] Rejecting request {request_id[:8]} — session "
                f"{session_id} has a wedged chat thread that didn't unwind "
                f"after abort signal"
            )
            return jsonify({
                "success": False,
                "error": "A previous request for this session is still running "
                         "and didn't respond to the abort signal. "
                         "Use /abort (CLI), POST /api/chat/unified/<session_id>/abort, "
                         "or /new for a fresh session.",
                "request_id": request_id,
            }), 409
        thread = threading.Thread(target=run_engine, daemon=True, name=f"unified-chat-{request_id[:8]}")
        _inflight[session_id] = thread
        thread.start()

    return jsonify({
        "success": True,
        "request_id": request_id,
        "session_id": session_id,
    })


@unified_chat_bp.route("/direct-tool", methods=["POST"])
def direct_tool_sync():
    """
    POST /api/chat/unified/direct-tool
    Synchronous direct tool execution (CLI /imagine parity with browser slash commands).
    Body: { tool, params, session_id?, slash_command? }
    """
    data = request.get_json(silent=True) or {}
    from backend.services.slash_command_executor import resolve_slash_direct_tool

    options = {
        "direct_tool": data.get("tool"),
        "direct_tool_params": data.get("params") or {},
        "slash_command": data.get("slash_command"),
        "slash_args": data.get("slash_args") or "",
    }
    # Forward project_root for GUAARDVARK.md etc. in direct tool calls too
    if data.get("project_root"):
        options["project_root"] = str(data.get("project_root"))
    tool_name, params = resolve_slash_direct_tool(options)
    if not tool_name:
        return jsonify({"success": False, "error": "tool or slash_command required"}), 400

    session_id = data.get("session_id") or "cli_direct"

    # Sanitize image prompts at the API boundary so CLI /imagine and browser
    # direct-tool share the same pure-visual text (and never hit NameError on
    # an undefined project_root).
    if tool_name == "generate_image" and isinstance(params, dict) and params.get("prompt"):
        try:
            from backend.services.image_prompt_sanitize import sanitize_image_prompt
            cleaned = sanitize_image_prompt(params.get("prompt"))
            if cleaned:
                params = {**params, "prompt": cleaned}
        except Exception:
            pass

    if data.get("project_root"):
        options["project_root"] = str(data.get("project_root"))

    try:
        from backend.tools.tool_registry_init import initialize_all_tools
        from backend.services.unified_chat_engine import UnifiedChatEngine
        from backend.utils.llm_service import get_llm_for_startup

        registry = initialize_all_tools()
        engine = UnifiedChatEngine(tool_registry=registry, llm_instance=get_llm_for_startup())
        engine.app = current_app._get_current_object()

        def _noop_emit(_event, _payload):
            pass

        display = data.get("message") or f"/{data.get('slash_command', tool_name)}"
        with current_app.app_context():
            result = engine._run_direct_tool_execution(
                tool_name, params, session_id, _noop_emit, str(uuid.uuid4()), display,
                options,
            )
        return jsonify({"success": result.get("success", False), **result})
    except Exception as exc:
        logger.error("direct-tool sync failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


@unified_chat_bp.route("/<session_id>/history", methods=["GET"])
def get_history(session_id):
    """
    GET /api/chat/unified/<session_id>/history
    Returns conversation history for a session.
    """
    limit = request.args.get("limit", 50, type=int)

    try:
        from backend.models import LLMSession, LLMMessage, db

        session = db.session.get(LLMSession, session_id)
        if not session:
            return jsonify({"success": True, "messages": []})

        messages = (
            LLMMessage.query
            .filter_by(session_id=session_id)
            .order_by(LLMMessage.timestamp.asc())
            .limit(limit)
            .all()
        )

        return jsonify({
            "success": True,
            "messages": [m.to_dict() for m in messages],
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"Failed to get history for {session_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@unified_chat_bp.route("/<session_id>/abort", methods=["POST"])
def abort_chat(session_id):
    """
    POST /api/chat/unified/<session_id>/abort

    Hard-kill the current generation for a session: set the cooperative abort
    flag, kill any active agent task, and clear the in-flight slot so a new
    request can claim it even if the old thread is wedged.
    """
    from backend.services.unified_chat_engine import set_abort_flag
    set_abort_flag(session_id)
    # Also kill any running agent task
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        if service._active:
            service.kill()
    except Exception:
        pass

    # Force-release the session slot. The old thread's finally block uses an
    # identity check, so a late exit will not clobber a newer claim.
    cleared = False
    with _inflight_lock:
        if session_id in _inflight:
            _inflight.pop(session_id, None)
            cleared = True

    try:
        from backend.socketio_instance import socketio
        socketio.emit("chat:aborted", {"session_id": session_id}, room=session_id)
    except Exception:
        pass

    return jsonify({
        "success": True,
        "message": f"Abort requested for {session_id}",
        "inflight_cleared": cleared,
    })

def resume_chat_background(session_id: str, state_id: int):
    """Resume a suspended chat session in a background thread."""
    from backend.socketio_instance import socketio
    from flask import current_app
    import threading
    
    app = current_app._get_current_object()
    
    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        if socketio.server is None:
            return
        socketio.emit(event, data_payload, room=session_id)
        
    def run_resume():
        with app.app_context():
            from backend.utils.llm_service import get_llm_for_startup
            from backend.tools.tool_registry_init import initialize_all_tools
            from backend.services.unified_chat_engine import UnifiedChatEngine
            
            llm = app.config.get("LLAMA_INDEX_LLM")
            if not llm:
                llm = get_llm_for_startup()
            registry = initialize_all_tools()
            engine = UnifiedChatEngine(registry, llm)
            
            # The message, options, etc are pulled from state, so we pass empty placeholders
            engine.chat(
                session_id=session_id,
                message="",
                options={},
                emit_fn=emit_fn,
                app=app,
                resume_state_id=state_id
            )
            
    socketio.start_background_task(run_resume)
