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
    request_id = str(uuid.uuid4())
    project_id = data.get("project_id")

    logger.info(
        f"[UNIFIED_CHAT] request_id={request_id[:8]} session={session_id} "
        f"project={project_id} message={message[:80]!r}"
    )
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            project_id = None

    # Get LLM instance - try app config first, then create one on demand
    llm = current_app.config.get("LLAMA_INDEX_LLM")
    if not llm:
        logger.warning("LLAMA_INDEX_LLM not in app config, creating on demand")
        try:
            from backend.utils.llm_service import get_llm_for_startup
            llm = get_llm_for_startup()
            # Cache it for next time
            current_app.config["LLAMA_INDEX_LLM"] = llm
        except Exception as e:
            logger.error(f"Failed to create LLM instance: {e}")
            return jsonify({"success": False, "error": "LLM not available. Check Ollama is running."}), 503

    # Get tool registry
    try:
        from backend.tools.tool_registry_init import initialize_all_tools
        registry = initialize_all_tools()
    except Exception as e:
        logger.error(f"Failed to initialize tool registry: {e}")
        return jsonify({"success": False, "error": "Tool registry unavailable"}), 503

    # Create engine
    from backend.services.unified_chat_engine import UnifiedChatEngine
    engine = UnifiedChatEngine(registry, llm)

    # Build emit function
    from backend.socketio_instance import socketio

    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        socketio.emit(event, data_payload, room=session_id)

    # Run in background thread with app context
    app = current_app._get_current_object()

    # Save image file for chat history if provided
    image_url = None
    if image_data:
        try:
            import os, base64 as b64mod
            from backend.config import UPLOAD_DIR
            img_dir = os.path.join(UPLOAD_DIR, "chat_images")
            os.makedirs(img_dir, exist_ok=True)
            fname = f"chat_image_{uuid.uuid4().hex[:12]}.png"
            with open(os.path.join(img_dir, fname), "wb") as f:
                f.write(b64mod.b64decode(image_data))
            image_url = f"/api/enhanced-chat/vision/image/{fname}"
            logger.info(f"Saved unified chat image: {fname}")
        except Exception as img_err:
            logger.warning(f"Failed to save chat image: {img_err}")

    def run_engine():
        try:
            engine.chat(session_id, message, options, emit_fn, app=app,
                       project_id=project_id, image_data=image_data, image_url=image_url)
        except Exception as e:
            logger.error(f"Unified chat engine thread error: {e}", exc_info=True)
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
    return jsonify({"success": True, "message": f"Abort requested for {session_id}"})
