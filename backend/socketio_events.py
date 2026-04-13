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
