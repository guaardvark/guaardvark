"""Socket.IO streaming client for chat and job progress."""

import socketio
import threading
import time
from typing import Callable

from llx.config import get_server_url


class LlxStreamer:
    """Handles Socket.IO connections for streaming chat and job progress."""

    def __init__(self, server_url: str | None = None):
        self.server_url = server_url or get_server_url()
        self.sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        self._connected = False
        self._done = threading.Event()

    def stream_chat(
        self,
        session_id: str,
        on_token: Callable[[str], None],
        on_thinking: Callable[[dict], None] | None = None,
        on_tool_call: Callable[[dict], None] | None = None,
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """
        Connect to Socket.IO, join session, and listen for chat events.
        Call this BEFORE posting the chat message via HTTP.
        Returns when chat:complete or chat:error fires.
        """
        self._done.clear()

        @self.sio.on("chat:token")
        def handle_token(data):
            content = data.get("content", "")
            if content:
                on_token(content)

        @self.sio.on("chat:thinking")
        def handle_thinking(data):
            if on_thinking:
                on_thinking(data)

        @self.sio.on("chat:tool_call")
        def handle_tool_call(data):
            if on_tool_call:
                on_tool_call(data)

        @self.sio.on("chat:complete")
        def handle_complete(data):
            if on_complete:
                on_complete(data)
            self._done.set()

        @self.sio.on("chat:error")
        def handle_error(data):
            if on_error:
                on_error(data.get("error", "Unknown error"))
            self._done.set()

        @self.sio.on("chat:aborted")
        def handle_aborted(data):
            if on_error:
                on_error("Chat aborted")
            self._done.set()

        try:
            self.sio.connect(self.server_url, transports=["polling", "websocket"])
            self._connected = True
            self.sio.emit("chat:join", {"session_id": session_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect for streaming: {e}")
            self._done.set()
            return

    def wait(self, timeout: float = 300.0) -> bool:
        """Block until streaming is done. Returns True if completed, False on timeout."""
        return self._done.wait(timeout=timeout)

    def abort(self, session_id: str):
        """Send abort signal for current chat."""
        if self._connected:
            try:
                self.sio.emit("chat:abort", {"session_id": session_id})
            except Exception:
                pass

    def disconnect(self):
        """Disconnect from Socket.IO."""
        if self._connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
            self._connected = False

    def watch_job(
        self,
        job_id: str,
        on_progress: Callable[[dict], None],
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """Subscribe to job progress updates via Socket.IO."""
        self._done.clear()

        @self.sio.on("progress")
        def handle_progress(data):
            on_progress(data)
            status = data.get("status", "")
            if status in ("completed", "done", "failed", "error"):
                if status in ("failed", "error") and on_error:
                    on_error(data.get("message", "Job failed"))
                elif on_complete:
                    on_complete(data)
                self._done.set()

        try:
            self.sio.connect(self.server_url, transports=["polling", "websocket"])
            self._connected = True
            self.sio.emit("subscribe", {"job_id": job_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect: {e}")
            self._done.set()
