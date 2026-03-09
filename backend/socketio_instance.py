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

# Configure SocketIO with memory leak prevention
socketio = SocketIO(
    cors_allowed_origins=allowed_origins,
    ping_timeout=60,  # 60 second ping timeout
    ping_interval=25,  # 25 second ping interval
    max_http_buffer_size=1024 * 1024,  # 1MB max buffer size
    async_mode='threading',  # Use threading for better memory management
    logger=False,  # Disabled to prevent log flooding
    engineio_logger=False  # Disabled to prevent log flooding
)
logger = logging.getLogger(__name__) 