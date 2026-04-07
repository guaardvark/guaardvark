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

    if not message and not image_data:
        return jsonify({"success": False, "error": "Message or image is required"}), 400

    # If image provided but no message, set a default
    if not message and image_data:
        message = "Describe this image."

    session_id = data.get("session_id") or str(uuid.uuid4())
    options = data.get("options", {})
    is_voice_message = bool(data.get("is_voice_message", False))
    request_id = str(uuid.uuid4())
    project_id = data.get("project_id")

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

    # Check if AgentBrain is available (pre-computed state, three-tier routing)
    use_agent_brain = False
    agent_brain = None
    try:
        from backend.config import AGENT_BRAIN_ENABLED
        brain_state = getattr(current_app, 'brain_state', None)
        if AGENT_BRAIN_ENABLED and brain_state and brain_state.is_ready:
            from backend.services.agent_brain import AgentBrain
            agent_brain = AgentBrain(state=brain_state)
            use_agent_brain = True
            logger.info(f"[UNIFIED_CHAT] Using AgentBrain (three-tier routing)")
    except Exception as e:
        logger.debug(f"AgentBrain not available, using legacy path: {e}")

    engine = None
    if not use_agent_brain:
        # Legacy path: create UnifiedChatEngine per-request
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if not llm:
            logger.warning("LLAMA_INDEX_LLM not in app config, creating on demand")
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

    # Build emit function
    from backend.socketio_instance import socketio

    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        if socketio.server is None:
            logger.warning(f"SocketIO server not initialized, dropping event {event} for session {session_id}")
            return
        try:
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
            if use_agent_brain:
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
            else:
                engine.chat(session_id, message, options, emit_fn, app=app,
                           project_id=project_id, image_data=image_data, image_url=image_url,
                           is_voice_message=is_voice_message)
        except Exception as e:
            logger.error(f"Chat engine thread error: {e}", exc_info=True)
            emit_fn("chat:error", {"error": str(e)})

    thread = threading.Thread(target=run_engine, daemon=True, name=f"unified-chat-{request_id[:8]}")
    thread.start()

    return jsonify({
        "success": True,
        "request_id": request_id,
        "session_id": session_id,
    })


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
    Abort the current generation for a session.
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
    return jsonify({"success": True, "message": f"Abort requested for {session_id}"})
