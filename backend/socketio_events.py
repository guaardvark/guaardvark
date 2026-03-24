import logging
import time

from flask_socketio import emit, join_room

# Import the socketio instance from the shared instance file
from backend.socketio_instance import socketio

logger = logging.getLogger(__name__)


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
