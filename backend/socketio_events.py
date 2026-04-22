import logging
import time
import io
import numpy as np

from flask_socketio import emit, join_room

# Import the socketio instance from the shared instance file
from backend.socketio_instance import socketio

logger = logging.getLogger(__name__)

# --- Voice Streaming Events ---
# In-memory buffer for continuous voice streaming
voice_stream_buffers = {}

@socketio.on("voice:stream_start")
def handle_voice_stream_start(data):
    """Initialize a new voice stream session."""
    session_id = data.get("session_id", "default")
    voice_stream_buffers[session_id] = bytearray()
    join_room(f"voice_{session_id}")
    logger.info(f"Voice stream started for session: {session_id}")
    emit("voice:stream_ack", {"status": "started", "session_id": session_id})

@socketio.on("voice:stream_chunk")
def handle_voice_stream_chunk(data):
    """Receive a chunk of audio data and perform partial STT."""
    session_id = data.get("session_id", "default")
    chunk = data.get("audio") # Expected to be bytes (WebM or PCM)
    
    if not chunk:
        return
        
    if session_id not in voice_stream_buffers:
        voice_stream_buffers[session_id] = bytearray()
        
    voice_stream_buffers[session_id].extend(chunk)
    
    # We can perform partial STT here if the buffer is large enough
    # For simplicity and performance, we'll wait for stream_end or process every N bytes
    # A full real-time sliding window would decode the accumulated WebM bytes to PCM
    # and run faster-whisper.
    
    # Just acknowledge receipt for now to keep it lightweight
    # Real-time partials would require decoding the incomplete WebM stream, which is complex.
    pass

@socketio.on("voice:stream_end")
def handle_voice_stream_end(data):
    """Process the complete audio buffer and return final transcript."""
    session_id = data.get("session_id", "default")
    
    if session_id not in voice_stream_buffers or not voice_stream_buffers[session_id]:
        emit("voice:final_transcript", {"text": "", "session_id": session_id})
        return
        
    audio_bytes = voice_stream_buffers.pop(session_id)
    logger.info(f"Voice stream ended for session: {session_id}, processing {len(audio_bytes)} bytes")
    
    try:
        from faster_whisper.audio import decode_audio
        from backend.utils.faster_whisper_utils import transcribe_audio_faster, FASTER_WHISPER_AVAILABLE
        
        if FASTER_WHISPER_AVAILABLE:
            audio_io = io.BytesIO(audio_bytes)
            audio_array = decode_audio(audio_io)
            
            # Use tiny.en for fastest streaming response
            final_text, processing_time = transcribe_audio_faster(audio_array, model_size="tiny.en")
            
            emit("voice:final_transcript", {
                "text": final_text,
                "session_id": session_id,
                "processing_time": processing_time
            }, room=f"voice_{session_id}")
        else:
            emit("voice:error", {"message": "faster-whisper not available"}, room=f"voice_{session_id}")
    except Exception as e:
        logger.error(f"Voice stream processing failed: {e}")
        emit("voice:error", {"message": str(e)}, room=f"voice_{session_id}")

@socketio.on("subscribe")
def handle_subscribe(data):
    """Allow clients to join a room for job updates."""
    job_id = data.get("job_id")
    if not job_id:
        emit("error", {"message": "job_id required"})
        return
    
    # Handle special global_progress room
    if job_id == "global_progress":
        join_room("global_progress")
        logger.info("Client joined global progress room")
        emit("status", {"data": "Subscribed to global progress updates"}, room="global_progress")
    else:
        join_room(job_id)
        logger.info(f"Client joined room for job_id: {job_id}")
        emit("status", {"data": f"Subscribed to updates for job {job_id}"}, room=job_id)


# --- WebRTC Signaling Events ---
@socketio.on("callUser")
def handle_call_user(data):
    """Relay a call attempt to another user."""
    logger.info(f"Relaying call from {data.get('from')} to {data.get('userToCall')}")
    socketio.emit(
        "hey",
        {"signal": data["signalData"], "from": data["from"]},
        room=data["userToCall"],
    )


@socketio.on("answerCall")
def handle_answer_call(data):
    """Relay an answer back to the original caller."""
    logger.info(f"Relaying answer from {data.get('from')} to {data.get('to')}")
    socketio.emit("callAccepted", data["signal"], room=data["to"])


@socketio.on("ice-candidate")
def handle_ice_candidate(data):
    """Forward ICE candidates between peers."""
    logger.info(f"Forwarding ICE candidate from {data.get('from')} to {data.get('to')}")
    socketio.emit("ice-candidate", data, room=data["to"])


# --- Health Monitoring Events ---
@socketio.on("subscribe_health")
def handle_subscribe_health():
    """Allow clients to subscribe to health status updates."""
    join_room("health_updates")
    logger.info("Client subscribed to health updates")
    emit("status", {"message": "Subscribed to health updates"})


def emit_health_status_change(service, status, details=None):
    """Emit health status changes to subscribed clients."""
    event_data = {
        "service": service,
        "status": status,
        "timestamp": time.time(),
        "details": details or {}
    }
    socketio.emit("health_status_change", event_data, room="health_updates")
    logger.info(f"Emitted health status change: {service} -> {status}")


# --- Unified Chat Events ---
@socketio.on("chat:join")
def handle_chat_join(data):
    """Client joins their session room for streaming chat events."""
    session_id = data.get("session_id")
    if not session_id:
        emit("error", {"message": "session_id required"})
        return
    join_room(session_id)
    logger.info(f"Client joined chat room: {session_id}")
    emit("chat:joined", {"session_id": session_id, "status": "ok"})


@socketio.on("chat:abort")
def handle_chat_abort(data):
    """Client requests to abort current generation."""
    session_id = data.get("session_id")
    if not session_id:
        emit("error", {"message": "session_id required"})
        return
    try:
        from backend.services.unified_chat_engine import set_abort_flag
        set_abort_flag(session_id)
        logger.info(f"Abort requested for chat session: {session_id}")
        emit("chat:aborted", {"session_id": session_id})
    except Exception as e:
        logger.error(f"Failed to abort chat session {session_id}: {e}")
        emit("error", {"message": f"Abort failed: {str(e)}"})


@socketio.on("chat:tool_approval_response")
def handle_tool_approval_response(data):
    """User approves or rejects a tool execution."""
    session_id = data.get("session_id")
    approved = data.get("approved", False)
    if not session_id:
        emit("error", {"message": "session_id required"})
        return
    try:
        from backend.services.unified_chat_engine import set_approval_response
        set_approval_response(session_id, approved)
        logger.info(f"Tool approval response received for session {session_id}: approved={approved}")
    except Exception as e:
        logger.error(f"Failed to set tool approval response for session {session_id}: {e}")
        emit("error", {"message": f"Approval response failed: {str(e)}"})


def emit_celery_worker_event(event_type, worker_info=None):
    """Emit Celery worker lifecycle events."""
    event_data = {
        "event_type": event_type,  # 'started', 'stopped', 'error', 'heartbeat'
        "timestamp": time.time(),
        "worker_info": worker_info or {}
    }
    socketio.emit("celery_worker_event", event_data, room="health_updates")
    logger.info(f"Emitted Celery worker event: {event_type}")


def emit_self_improvement_event(event_type: str, data: dict):
    """Emit self-improvement status events."""
    socketio.emit(f"self_improvement:{event_type}", {
        "event_type": event_type,
        "timestamp": time.time(),
        **data,
    })
    logger.info(f"Emitted self_improvement:{event_type}")


def emit_uncle_directive(directive: str, reason: str):
    """Emit Uncle Claude directive to all connected clients."""
    socketio.emit("uncle:directive", {
        "directive": directive,
        "reason": reason,
        "timestamp": time.time(),
    })
    logger.info(f"Emitted uncle:directive: {directive}")


def emit_family_learning(learning_data: dict):
    """Emit family learning update to all connected clients."""
    socketio.emit("family:learning", {
        "timestamp": time.time(),
        **learning_data,
    })
    logger.info(f"Emitted family:learning")


# --- GPU Memory Orchestrator Events ---
@socketio.on("subscribe_gpu")
def handle_subscribe_gpu():
    """Allow clients to subscribe to GPU VRAM status updates."""
    join_room("gpu_status")
    logger.info("Client subscribed to GPU status updates")
    # Send immediate snapshot
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        snapshot = get_orchestrator().get_registry_snapshot()
        emit("gpu:status", snapshot)
    except Exception as e:
        logger.debug(f"Could not send initial GPU status: {e}")


@socketio.on("gpu:intent")
def handle_gpu_intent(data):
    """Frontend signals navigation intent for predictive GPU model management."""
    route = data.get("route", "/") if isinstance(data, dict) else "/"
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        result = get_orchestrator().prepare_for_route(route)
        emit("gpu:intent_ack", result)
    except Exception as e:
        logger.debug(f"GPU intent handling failed: {e}")


def emit_gpu_status():
    """Emit GPU status snapshot to all subscribed clients."""
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        snapshot = get_orchestrator().get_registry_snapshot()
        socketio.emit("gpu:status", snapshot, room="gpu_status")
    except Exception as e:
        logger.debug(f"GPU status emission failed: {e}")


# --- Interactive Learning Events ---

def emit_learning_mode_started(demonstration_id: int, name: str = None):
    """Notify clients that learning mode has started."""
    socketio.emit("agent:learning_mode_started", {
        "demonstration_id": demonstration_id,
        "name": name,
    })


def emit_learning_mode_stopped(demonstration_id: int, step_count: int):
    """Notify clients that recording has finished."""
    socketio.emit("agent:learning_mode_stopped", {
        "demonstration_id": demonstration_id,
        "step_count": step_count,
    })


def emit_learning_question(question_id: str, question_type: str, text: str,
                           demonstration_id: int, step_index: int = None,
                           options: list = None):
    """Ask the user a learning question."""
    socketio.emit("agent:learning_question", {
        "question_id": question_id,
        "question_type": question_type,
        "text": text,
        "demonstration_id": demonstration_id,
        "step_index": step_index,
        "options": options,
    })


def emit_step_preview(demonstration_id: int, step_index: int,
                      target_description: str, action_type: str,
                      confidence: float):
    """Preview the next action for GUIDED mode confirmation."""
    socketio.emit("agent:step_preview", {
        "demonstration_id": demonstration_id,
        "step_index": step_index,
        "target_description": target_description,
        "action_type": action_type,
        "confidence": confidence,
    })


def emit_step_executed(demonstration_id: int, step_index: int,
                       success: bool, action_type: str):
    """Notify that a step was executed during an attempt."""
    socketio.emit("agent:step_executed", {
        "demonstration_id": demonstration_id,
        "step_index": step_index,
        "success": success,
        "action_type": action_type,
    })


def emit_attempt_complete(demonstration_id: int, success: bool,
                          steps_completed: int, total_steps: int):
    """Notify that an attempt has finished."""
    socketio.emit("agent:attempt_complete", {
        "demonstration_id": demonstration_id,
        "success": success,
        "steps_completed": steps_completed,
        "total_steps": total_steps,
    })


@socketio.on("agent:learning_answer")
def handle_learning_answer(data):
    """Receive answer to a learning question from the user."""
    from backend.services.agent_control_service import get_agent_control_service
    service = get_agent_control_service()
    if hasattr(service, '_learning_answer_queue'):
        service._learning_answer_queue.put(data)
    logger.info(f"Learning answer received: question_id={data.get('question_id')}")


@socketio.on("agent:step_confirm")
def handle_step_confirm(data):
    """User confirms a previewed step in GUIDED mode."""
    from backend.services.agent_control_service import get_agent_control_service
    service = get_agent_control_service()
    if hasattr(service, '_step_confirm_event'):
        service._step_confirm_data = data
        service._step_confirm_event.set()
    logger.info(f"Step confirmed: step_index={data.get('step_index')}")


@socketio.on("agent:step_correct")
def handle_step_correct(data):
    """User corrects a previewed step in GUIDED mode."""
    from backend.services.agent_control_service import get_agent_control_service
    service = get_agent_control_service()
    if hasattr(service, '_step_confirm_event'):
        service._step_confirm_data = data
        service._step_confirm_event.set()
    logger.info(f"Step corrected: step_index={data.get('step_index')}, correction={data.get('correction')}")


# --- Swarm Events ---

@socketio.on("subscribe_swarm")
def handle_subscribe_swarm(data=None):
    """Allow clients to subscribe to real-time agent swarm updates."""
    join_room("swarm_updates")
    logger.info("Client subscribed to swarm updates")
    emit("status", {"message": "Subscribed to swarm updates"}, room="swarm_updates")


def emit_swarm_event(event_type: str, task_id: str, data: dict):
    """Emit a swarm event to all subscribed clients."""
    event_data = {
        "event_type": event_type,
        "task_id": task_id,
        "timestamp": time.time(),
        "data": data,
    }
    socketio.emit("swarm:event", event_data, room="swarm_updates")
    logger.debug(f"Emitted swarm event: {event_type} for {task_id}")


# ---- Cluster chat:send bridge (Task 21) ------------------------------------

def _handle_chat_send_local(payload):
    """Process a chat:send payload on this node — same path as POST /api/chat/unified
    but entered via Socket.IO (used when the cluster bridge forwards here)."""
    import threading
    import uuid
    from flask import current_app, request as _req
    from backend.socketio_instance import socketio as _sio

    if not isinstance(payload, dict):
        return
    session_id = payload.get("session_id") or str(uuid.uuid4())
    message = (payload.get("message") or "").strip()
    image_data = payload.get("image")
    if not message and not image_data:
        return

    if not message and image_data:
        message = "Describe this image."

    options = payload.get("options", {})
    is_voice_message = bool(payload.get("is_voice_message", False))
    project_id = payload.get("project_id")
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            project_id = None

    # Abort any already-running generation for this session
    try:
        from backend.services.unified_chat_engine import set_abort_flag
        set_abort_flag(session_id)
    except Exception:
        pass

    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        try:
            _sio.emit(event, data_payload, room=session_id)
        except Exception as _e:
            logger.warning("[BRIDGE-LOCAL] emit %s failed: %s", event, _e)

    app = current_app._get_current_object()

    use_agent_brain = False
    agent_brain = None
    engine = None
    try:
        from backend.config import AGENT_BRAIN_ENABLED
        brain_state = getattr(app, "brain_state", None)
        if AGENT_BRAIN_ENABLED and brain_state and brain_state.is_ready:
            from backend.services.agent_brain import AgentBrain
            agent_brain = AgentBrain(state=brain_state)
            use_agent_brain = True
    except Exception:
        pass

    if not use_agent_brain:
        try:
            llm = app.config.get("LLAMA_INDEX_LLM")
            if not llm:
                from backend.utils.llm_service import get_llm_for_startup
                llm = get_llm_for_startup()
                app.config["LLAMA_INDEX_LLM"] = llm
            from backend.tools.tool_registry_init import initialize_all_tools
            registry = initialize_all_tools()
            from backend.services.unified_chat_engine import UnifiedChatEngine
            engine = UnifiedChatEngine(registry, llm)
        except Exception as _e:
            logger.error("[BRIDGE-LOCAL] engine init failed: %s", _e)
            emit_fn("chat:error", {"error": "LLM not available"})
            return

    def _run():
        try:
            if use_agent_brain:
                agent_brain.process(
                    session_id=session_id, message=message,
                    options=options, emit_fn=emit_fn, app=app,
                    project_id=project_id, image_data=image_data,
                )
            else:
                engine.chat(session_id, message, options, emit_fn, app=app,
                            project_id=project_id, image_data=image_data,
                            is_voice_message=is_voice_message)
        except Exception as _e:
            logger.error("[BRIDGE-LOCAL] engine error: %s", _e, exc_info=True)
            emit_fn("chat:error", {"error": str(_e)})

    threading.Thread(target=_run, daemon=True, name=f"bridge-chat-{session_id[:8]}").start()


@socketio.on("chat:send")
def handle_chat_send(payload):
    """Cluster-aware chat:send. Routes to a remote primary if cluster routing
    says so; falls through to local engine otherwise."""
    try:
        from flask import current_app, request as _req
        import os as _os
        if current_app.config.get("CLUSTER_ENABLED", False):
            from backend.services.cluster_routing import get_routing_store
            from backend.services.fleet_map import get_fleet_map
            from backend.services.cluster_socketio_bridge import SocketIOBridgeRegistry
            from backend.services.cluster_proxy import NodeTarget
            from backend.models import InterconnectorNode
            store = get_routing_store()
            table = store.get()
            if table is not None:
                model_name = (payload or {}).get("model") if isinstance(payload, dict) else None
                route = store.route_for_chat(model_name, fleet=get_fleet_map())
                local_id = _os.environ.get("CLUSTER_NODE_ID", "unknown")
                if (route is not None and route.primary is not None
                        and route.primary != local_id):
                    node = InterconnectorNode.query.filter_by(
                        node_id=route.primary).first()
                    if node is not None and node.online:
                        api_key = getattr(node, "api_key", None) or node.node_id
                        target = NodeTarget(node_id=node.node_id, host=node.host,
                                            port=node.port, api_key=api_key)
                        bridge = SocketIOBridgeRegistry.get_or_create(_req.sid, target)
                        bridge.forward_send(payload)
                        return
    except Exception as _e:
        logger.warning("[BRIDGE] cluster routing failed, handling locally: %s", _e)
    # Fall through to local handling
    _handle_chat_send_local(payload)


# ---- Cluster-foundation handlers (Task 14) ---------------------------------

def _cluster_auth_check(auth):
    """Returns True if the handshake should be accepted.
    Cluster OFF: always True (preserve solo behavior).
    Cluster ON: api_key → node peer; no cluster headers → local browser."""
    from flask import current_app, request as _req
    if not current_app.config.get("CLUSTER_ENABLED", False):
        return True  # solo — no auth enforcement
    key = None
    if isinstance(auth, dict):
        key = auth.get("api_key")
    if key is None:
        key = _req.headers.get("X-Guaardvark-API-Key")
    if key:
        if _is_valid_node_api_key(key):
            try:
                join_room("cluster:masters-broadcast")
            except Exception:
                pass
            return True
        return False  # key present but invalid
    # No cluster headers/auth = local browser session — allow
    return not _looks_like_node_handshake(_req)


def _is_valid_node_api_key(key: str) -> bool:
    """Check if the api_key matches any known InterconnectorNode.
    Note: the schema may or may not have an api_key column — check before asserting."""
    try:
        from backend.models import InterconnectorNode
        # Legacy: if api_key field doesn't exist on the model, fall back to node_id match
        if hasattr(InterconnectorNode, "api_key"):
            return InterconnectorNode.query.filter_by(api_key=key).first() is not None
        # Otherwise treat key as node_id-based auth (no hardening — matches whatever auth
        # the Interconnector uses today)
        return InterconnectorNode.query.filter_by(node_id=key).first() is not None
    except Exception:
        return False


def _looks_like_node_handshake(request) -> bool:
    """Heuristic: does this request look like a peer-node handshake that failed auth?
    Used to decide whether no-api-key means 'browser' (allow) or 'failed node' (reject).
    Nodes typically connect from different hostnames with no cookies; browsers have cookies."""
    has_cookie = bool(request.cookies)
    return not has_cookie  # no cookies + no api_key = likely a failed node handshake


@socketio.on("connect")
def on_connect(auth=None):
    """Gate cluster node-to-node handshakes behind api_key; browser sessions pass through."""
    if not _cluster_auth_check(auth):
        return False
    return True


@socketio.on("cluster:routing_table")
def handle_cluster_routing_table(data):
    """Worker-side receiver. Validates sender, persists the table."""
    import os as _os
    _logger = logging.getLogger(__name__)

    expected_master = _os.environ.get("CLUSTER_MASTER_NODE_ID")
    if expected_master and data.get("computed_by") != expected_master:
        _logger.warning(
            "[CLUSTER] rejected routing_table from unknown sender %s (expected %s)",
            data.get("computed_by"), expected_master)
        return

    try:
        from backend.services.cluster_routing import RoutingTable, get_routing_store
        table = RoutingTable.from_dict(data)
        get_routing_store().set(table, persist=True)
        _logger.info("[CLUSTER] accepted routing_table from %s (fleet_hash=%s)",
                     data.get("computed_by"), table.fleet_hash)
    except (KeyError, ValueError, TypeError) as e:
        _logger.warning("[CLUSTER] malformed routing_table payload: %s", e)


@socketio.on("disconnect")
def handle_disconnect():
    """Clean up any open cluster bridge when a client disconnects."""
    from flask import request
    try:
        from backend.services.cluster_socketio_bridge import SocketIOBridgeRegistry
        SocketIOBridgeRegistry.close_for_session(request.sid)
    except Exception:
        pass
