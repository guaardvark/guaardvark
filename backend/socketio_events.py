import logging
import time
import io
import numpy as np
import threading

from flask_socketio import emit, join_room, leave_room

# Import the socketio instance from the shared instance file
from backend.socketio_instance import socketio

logger = logging.getLogger(__name__)

_voice_stream_lock = threading.Lock()


def _prune_voice_buffers():
    """Enforce max streams, max per-buffer size, and TTL. Called on every voice event."""
    global voice_stream_buffers, _voice_stream_meta
    now = time.time()
    with _voice_stream_lock:
        # TTL first
        expired = [sid for sid, meta in _voice_stream_meta.items()
                   if now - meta.get("last", meta.get("created_at", 0)) > VOICE_STREAM_TTL_S]
        for sid in expired:
            voice_stream_buffers.pop(sid, None)
            _voice_stream_meta.pop(sid, None)
        # Enforce max streams (evict oldest)
        if len(voice_stream_buffers) > MAX_VOICE_STREAMS:
            # oldest by last activity
            sorted_sids = sorted(_voice_stream_meta.items(), key=lambda kv: kv[1].get("last", 0))
            for sid, _ in sorted_sids[:len(voice_stream_buffers) - MAX_VOICE_STREAMS]:
                voice_stream_buffers.pop(sid, None)
                _voice_stream_meta.pop(sid, None)
        # Cap individual buffer sizes (drop oldest bytes if over)
        for sid, buf in list(voice_stream_buffers.items()):
            if len(buf) > MAX_BUFFER_BYTES:
                # keep tail (recent)
                voice_stream_buffers[sid] = buf[-MAX_BUFFER_BYTES:]
                if sid in _voice_stream_meta:
                    _voice_stream_meta[sid]["size"] = len(voice_stream_buffers[sid])

# --- Voice Streaming Events ---
# In-memory buffer for continuous voice streaming
# Bounded + TTL to prevent unbounded growth on Redis loss or long-lived sessions (infra rec).
MAX_VOICE_STREAMS = 64
MAX_BUFFER_BYTES = 64 * 1024 * 1024  # 64 MiB per session cap
VOICE_STREAM_TTL_S = 300  # 5 min idle TTL

voice_stream_buffers = {}
_voice_stream_meta = {}  # session_id -> {"created_at": ts, "last": ts, "size": int}

@socketio.on("voice:stream_start")
def handle_voice_stream_start(data):
    """Initialize a new voice stream session."""
    _prune_voice_buffers()
    session_id = data.get("session_id", "default")
    with _voice_stream_lock:
        if len(voice_stream_buffers) >= MAX_VOICE_STREAMS:
            # evict one oldest before adding
            if _voice_stream_meta:
                oldest = min(_voice_stream_meta.items(), key=lambda kv: kv[1].get("last", 0))[0]
                voice_stream_buffers.pop(oldest, None)
                _voice_stream_meta.pop(oldest, None)
        voice_stream_buffers[session_id] = bytearray()
        _voice_stream_meta[session_id] = {"created_at": time.time(), "last": time.time(), "size": 0}
    join_room(f"voice_{session_id}")
    logger.info(f"Voice stream started for session: {session_id}")
    emit("voice:stream_ack", {"status": "started", "session_id": session_id})

@socketio.on("voice:stream_chunk")
def handle_voice_stream_chunk(data):
    """Receive a chunk of audio data and perform partial STT."""
    _prune_voice_buffers()
    session_id = data.get("session_id", "default")
    chunk = data.get("audio") # Expected to be bytes (WebM or PCM)
    
    if not chunk:
        return
        
    with _voice_stream_lock:
        if session_id not in voice_stream_buffers:
            voice_stream_buffers[session_id] = bytearray()
            _voice_stream_meta.setdefault(session_id, {"created_at": time.time(), "last": time.time(), "size": 0})
        buf = voice_stream_buffers[session_id]
        buf.extend(chunk)
        meta = _voice_stream_meta[session_id]
        meta["last"] = time.time()
        meta["size"] = len(buf)
        # Hard cap per buffer (drop head if needed to keep tail)
        if len(buf) > MAX_BUFFER_BYTES:
            voice_stream_buffers[session_id] = buf[-MAX_BUFFER_BYTES:]
            meta["size"] = len(voice_stream_buffers[session_id])
        
    # We can perform partial STT here if the buffer is large enough
    # For simplicity and performance, we'll wait for stream_end or process every N bytes
    # A full real-time sliding window would decode the accumulated WebM bytes to PCM
    # and run faster-whisper.
    
    # Just acknowledge receipt for now to keep it lightweight
    # Real-time partials would require decoding the incomplete WebM stream, which is complex.
    pass

@socketio.on("voice:stream_end")
def handle_voice_stream_end(data):
    """Transcribe a finished stream and answer with the stream id it was started with.

    Each stream has its own room (joined in voice:stream_start); it is left
    here on every path, or rooms pile up for the life of the connection.
    """
    _prune_voice_buffers()
    session_id = data.get("session_id", "default")
    room = f"voice_{session_id}"

    with _voice_stream_lock:
        if session_id not in voice_stream_buffers or not voice_stream_buffers[session_id]:
            _voice_stream_meta.pop(session_id, None)
            emit("voice:final_transcript", {"text": "", "session_id": session_id})
            leave_room(room)
            return

        audio_bytes = voice_stream_buffers.pop(session_id)
        _voice_stream_meta.pop(session_id, None)
    logger.info(f"Voice stream ended for session: {session_id}, processing {len(audio_bytes)} bytes")

    try:
        from backend.utils.faster_whisper_utils import decode_audio
        from backend.utils.faster_whisper_utils import transcribe_audio_faster, FASTER_WHISPER_AVAILABLE

        if FASTER_WHISPER_AVAILABLE:
            audio_io = io.BytesIO(audio_bytes)
            audio_array = decode_audio(audio_io)

            # The model chosen in Settings → Voice (tiny.en unless changed).
            from backend.api.voice_api import resolve_speech_model
            final_text, processing_time = transcribe_audio_faster(
                audio_array, model_size=resolve_speech_model()
            )

            emit("voice:final_transcript", {
                "text": final_text,
                "session_id": session_id,
                "processing_time": processing_time
            }, room=room)
        else:
            emit("voice:error", {"message": "faster-whisper not available", "session_id": session_id}, room=room)
    except Exception as e:
        logger.error(f"Voice stream processing failed: {e}")
        emit("voice:error", {"message": str(e), "session_id": session_id}, room=room)
    finally:
        leave_room(room)

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
    from flask import request as _flask_req
    session_id = data.get("session_id")
    sid = getattr(_flask_req, 'sid', None)
    if not session_id:
        emit("error", {"message": "session_id required"})
        return
    logger.info(f"[SOCKET-CHAT] RECV chat:join session={session_id} from_sid={sid}")
    join_room(session_id)
    # Debug: note current rooms for this sid if accessible
    try:
        rooms = list(_flask_req.namespace.rooms.get(sid, set())) if hasattr(_flask_req, 'namespace') else []
        logger.info(f"[SOCKET-CHAT] AFTER join_room: session={session_id} sid={sid} rooms={rooms}")
    except Exception:
        pass
    logger.info(f"Client joined chat room: {session_id}")
    emit("chat:joined", {"session_id": session_id, "status": "ok"})
    logger.info(f"[SOCKET-CHAT] SENT chat:joined for session={session_id}")


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
        from backend.api.unified_chat_api import release_turn_slot
        release_turn_slot(session_id)
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
    scope = data.get("approval_scope") or data.get("scope")
    tools = data.get("tools") or data.get("approved_tools")
    if not session_id:
        emit("error", {"message": "session_id required"})
        return
    try:
        from backend.services.unified_chat_engine import set_approval_response
        applied = set_approval_response(
            session_id, approved, scope=scope, tools=tools,
            request_id=data.get("request_id"), approval_id=data.get("approval_id"),
        )
        if not applied:
            emit("chat:tool_approval_ignored", {
                "session_id": session_id,
                "approval_id": data.get("approval_id"),
                "error": "No approval card matching this answer is waiting.",
            })
        logger.info(
            f"Tool approval response received for session {session_id}: "
            f"approved={approved} scope={scope} applied={applied}"
        )
        
        # Check if this session is Suspended and needs to be resumed
        from backend.models import SuspendedChatState, db
        state = SuspendedChatState.query.filter_by(session_id=session_id).first()
        if state:
            from backend.api.unified_chat_api import resume_chat_background
            resume_chat_background(session_id, state.id)
            logger.info(f"Triggered background resume for suspended session {session_id}")
            
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


def emit_lesson_event(event_type: str, data: dict):
    """Emit lesson lifecycle events — started, pearl_added, ended.
    Used by the Begin/End Lesson flow so the floater can show pearls in real time.
    """
    socketio.emit(f"lesson:{event_type}", {
        "event_type": event_type,
        "timestamp": time.time(),
        **data,
    })
    logger.info(f"Emitted lesson:{event_type}")


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
    logger.debug("Client subscribed to GPU status updates")
    # Send immediate snapshot
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        snapshot = get_orchestrator().get_registry_snapshot()
        emit("gpu:status", snapshot)
    except Exception as e:
        logger.debug(f"Could not send initial GPU status: {e}")


@socketio.on("subscribe_plugins")
def handle_subscribe_plugins():
    """Subscribe to plugin list pushes (replaces HTTP polling on /plugins)."""
    from flask import request as flask_request
    from backend.services.plugin_status_emitter import PLUGINS_STATUS_ROOM, emit_plugins_snapshot

    join_room(PLUGINS_STATUS_ROOM)
    logger.debug("Client subscribed to plugin status updates")
    try:
        emit_plugins_snapshot("subscribe", to_sid=flask_request.sid)
    except Exception as e:
        logger.debug(f"Could not send initial plugin status: {e}")


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
    import uuid
    from flask import current_app
    from backend.socketio_instance import socketio as _sio

    if not isinstance(payload, dict):
        return
    session_id = payload.get("session_id") or str(uuid.uuid4())
    message = (payload.get("message") or "").strip()
    from backend.socketio_instance import chat_attachments
    attached_images = chat_attachments(payload)
    image_data = attached_images[0] if attached_images else None
    if not message and not image_data:
        return

    if not message and image_data:
        message = "Describe these images." if len(attached_images) > 1 else "Describe this image."

    options = payload.get("options", {})
    if not isinstance(options, dict):
        options = {}
    # The server alone decides what the engine sees as attached this turn:
    # the first picture is the primary one, the rest ride along for the model.
    options.pop("attached_images", None)
    options.pop("attached_image_urls", None)
    # Saved for chat history exactly as POST /api/chat/unified does: one URL per
    # picture, None where a save failed, the first one is the turn's image_url.
    from backend.api.unified_chat_api import _save_chat_image
    image_urls = [_save_chat_image(img) for img in attached_images]
    image_url = image_urls[0] if image_urls else None
    if len(attached_images) > 1:
        options["attached_images"] = attached_images
        if any(image_urls):
            options["attached_image_urls"] = image_urls
    # The session's saved mode applies here exactly as on POST /api/chat/unified.
    from backend.api.unified_chat_api import _merge_session_mode_options
    options = _merge_session_mode_options(session_id, options)
    # Named here so the brain's turn decision and its telemetry row say where
    # the turn came from; AgentBrain reuses an id it is given.
    options["surface"] = "cluster"
    options.setdefault("request_id", str(uuid.uuid4()))
    is_voice_message = bool(payload.get("is_voice_message", False))
    project_id = payload.get("project_id")
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            project_id = None

    # Abort any already-running generation for this session
    from backend.services.unified_chat_engine import begin_new_turn, turn_replaced
    turn_mark = begin_new_turn(session_id)

    def emit_fn(event, data_payload):
        data_payload["session_id"] = session_id
        if turn_replaced(session_id, turn_mark):
            return  # a newer message owns the room now
        try:
            logger.info(f"[SOCKET-CHAT][BRIDGE-LOCAL] EMIT {event} -> room={session_id}")
            _sio.emit(event, data_payload, room=session_id)
        except Exception as _e:
            logger.warning("[BRIDGE-LOCAL] emit %s failed: %s", event, _e)

    app = current_app._get_current_object()

    # AgentBrain routes the turn whenever it is enabled; never a silent fallback.
    from backend.services.chat_turn import build_router, route_turn, run_chat_turn
    agent_brain, engine, router_error = build_router(app)
    if router_error:
        logger.error("[BRIDGE-LOCAL] %s", router_error)
        emit_fn("chat:error", {"error": router_error})
        return

    def _run():
        run_chat_turn(
            session_id=session_id,
            turn_mark=turn_mark,
            emit_fn=emit_fn,
            is_voice_message=is_voice_message,
            log_tag="SOCKET-BRIDGE",
            body=lambda: route_turn(
                session_id=session_id, message=message,
                options=options, emit_fn=emit_fn, app=app,
                project_id=project_id, image_data=image_data,
                image_url=image_url, is_voice_message=is_voice_message,
                agent_brain=agent_brain, engine=engine, log_tag="SOCKET-BRIDGE",
            ),
        )

    # One turn per session, shared with POST /api/chat/unified: the previous
    # turn (stopped by begin_new_turn above) gets a moment to exit first.
    from backend.api.unified_chat_api import start_turn_thread
    if start_turn_thread(session_id, _run, f"bridge-chat-{session_id[:8]}") is None:
        logger.warning("[BRIDGE-LOCAL] session %s: previous turn still running; refused", session_id)
        try:
            _sio.emit("chat:error", {
                "session_id": session_id,
                "error": "A previous request for this session is still running and didn't "
                         "respond to the abort signal. Stop it, or start a new chat.",
            }, room=session_id)
        except Exception as _e:
            logger.warning("[BRIDGE-LOCAL] emit chat:error failed: %s", _e)


@socketio.on("chat:send")
def handle_chat_send(payload):
    """Cluster-aware chat:send. Routes to a remote primary if cluster routing
    says so; falls through to local engine otherwise."""
    # Size gate first, before any routing: an attachment over the declared
    # limit is answered on the caller's socket rather than forwarded or
    # decoded. Packets over the Socket.IO buffer never get this far (see
    # SOCKET_MAX_HTTP_BUFFER_SIZE), which is why the buffer is sized above it.
    if isinstance(payload, dict):
        from backend.socketio_instance import chat_attachment_too_large, chat_attachments
        reason = chat_attachment_too_large(chat_attachments(payload))
        if reason:
            emit("chat:error", {
                "error": reason,
                "code": "attachment_too_large",
                "session_id": payload.get("session_id"),
            })
            return
    try:
        from flask import current_app, request as _req
        from backend.services.plan_mode import session_in_plan_mode
        # A plan-mode chat stays here: its plans and their approval live in
        # this machine's database.
        if (current_app.config.get("CLUSTER_ENABLED", False)
                and not session_in_plan_mode((payload or {}).get("session_id") if isinstance(payload, dict) else None)):
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
                from backend.utils.node_identity import local_node_id
                local_id = local_node_id()
                if (route is not None and route.primary is not None
                        and route.primary != local_id):
                    node = InterconnectorNode.query.filter_by(
                        node_id=route.primary).first()
                    if node is not None and node.online:
                        target = NodeTarget(node_id=node.node_id, host=node.host,
                                            port=node.port)
                        bridge = SocketIOBridgeRegistry.get_or_create(_req.sid, target)
                        bridge.forward_send(payload)
                        return
    except Exception as _e:
        logger.warning("[BRIDGE] cluster routing failed, handling locally: %s", _e)
    # Fall through to local handling
    _handle_chat_send_local(payload)


# ---- Cluster-foundation handlers (Task 14) ---------------------------------

# Socket sessions opened by another machine with a signed handshake:
# sid -> {"node": its node id, "master": whether it is the fleet's master}.
_peer_sessions: dict = {}


def _cluster_auth_check(auth):
    """Returns True if the handshake should be accepted.
    Cluster OFF: always True (preserve solo behavior).
    Cluster ON: a handshake signed by an enrolled machine
    (backend/utils/node_auth.py) is a peer; one that carries the old
    api_key, or a bad signature, is refused; neither means a local browser."""
    from flask import current_app, request as _req
    if not current_app.config.get("CLUSTER_ENABLED", False):
        return True  # solo — no auth enforcement
    if isinstance(auth, dict) and auth.get("gx_sig"):
        from backend.utils import node_auth
        try:
            cert = node_auth.verify_socket_auth(auth)
        except Exception as exc:
            logger.warning("[CLUSTER] refused a peer handshake: %s", getattr(exc, "reason", exc))
            return False
        _peer_sessions[_req.sid] = {
            "node": cert.node,
            "master": node_auth.is_master_certificate(cert),
        }
        try:
            join_room("cluster:masters-broadcast")
        except Exception:
            pass
        return True
    if (isinstance(auth, dict) and auth.get("api_key")) or _req.headers.get("X-Guaardvark-API-Key"):
        logger.warning("[CLUSTER] refused a peer handshake without a signature")
        return False
    # No cluster headers/auth = local browser session — allow
    return not _looks_like_node_handshake(_req)


def _session_is_fleet_master() -> bool:
    """True when this socket session is the fleet's master, by its signed handshake."""
    from flask import request as _req
    try:
        sid = _req.sid
    except (AttributeError, RuntimeError):
        return False
    return bool(_peer_sessions.get(sid, {}).get("master"))


def _looks_like_node_handshake(request) -> bool:
    """Heuristic: does this request look like a peer-node handshake that failed auth?
    Used to decide whether no-api-key means 'browser' (allow) or 'failed node' (reject).
    Nodes typically connect from different hostnames with no cookies; browsers have cookies."""
    has_cookie = bool(request.cookies)
    return not has_cookie  # no cookies + no api_key = likely a failed node handshake


@socketio.on("connect")
def on_connect(auth=None):
    """Gate cluster node-to-node handshakes behind a signature; browser sessions pass through."""
    if not _cluster_auth_check(auth):
        return False
    return True


@socketio.on("cluster:routing_table")
def handle_cluster_routing_table(data):
    """Worker-side receiver. Validates sender, persists the table."""
    import os as _os
    _logger = logging.getLogger(__name__)

    # Only a session the master opened with its own signed handshake may
    # replace this machine's routing.
    if not _session_is_fleet_master():
        _logger.warning("[CLUSTER] rejected routing_table from a session that is not the master")
        return
    if not isinstance(data, dict):
        _logger.warning("[CLUSTER] malformed routing_table payload")
        return

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
    _peer_sessions.pop(getattr(request, "sid", None), None)
    try:
        from backend.services.cluster_socketio_bridge import SocketIOBridgeRegistry
        SocketIOBridgeRegistry.close_for_session(request.sid)
    except Exception:
        pass
