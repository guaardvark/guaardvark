import logging
import os

from flask_socketio import SocketIO

# Configure SocketIO with secure CORS settings
FRONTEND_URL = os.getenv("VITE_FRONTEND_URL", "http://localhost:5173")

# Environment-specific CORS configuration
if os.getenv("FLASK_ENV") == "production":
    allowed_origins = [FRONTEND_URL]
else:
    allowed_origins = [
        FRONTEND_URL,
        "http://localhost:5173",  # Vite default
        "http://localhost:5175",  # Vite alternate
        "http://localhost:3000",  # React default
        "http://127.0.0.1:5173",  # Alternative localhost
        "http://127.0.0.1:5175",  # Alternative localhost
        "http://127.0.0.1:3000",  # Alternative localhost
    ]

# Redis-backed message queue — lets emits from Celery workers reach clients
# connected to the main Flask process. Without this, `socketio.emit()` from
# outside the Flask server process just disappears into the void.
# Falls back to CELERY_BROKER_URL since we're already running Redis for Celery.
_message_queue = (
    os.getenv("SOCKETIO_MESSAGE_QUEUE")
    or os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL")
)

_socketio_kwargs = {
    "cors_allowed_origins": allowed_origins,
    "ping_timeout": 60,
    "ping_interval": 25,
    "max_http_buffer_size": 1024 * 1024,
    "async_mode": "threading",
    "logger": False,
    "engineio_logger": False,
}
if _message_queue:
    _socketio_kwargs["message_queue"] = _message_queue

socketio = SocketIO(**_socketio_kwargs)
logger = logging.getLogger(__name__)
if _message_queue:
    logger.info("SocketIO message queue wired to Redis (cross-process emits enabled)")
else:
    logger.warning("SocketIO has no message queue — emits from Celery workers will be dropped")