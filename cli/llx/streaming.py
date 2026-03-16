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


# ── Chat Renderer ─────────────────────────────────────────────

from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text
from rich.console import Group

from llx.theme import make_console

_ICON_TOOL = "\u27e1"   # ⟡
_ICON_OK   = "\u2713"   # ✓


class ChatRenderer:
    """Renders streaming chat responses with live markdown and tool-call UI."""

    def __init__(self):
        self._console = make_console()
        self._tokens: list[str] = []
        self._tool_lines: list[str] = []
        self._complete_data: dict | None = None
        self._error: str | None = None
        self._live: Live | None = None

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Clear state and begin a Live display."""
        self._tokens = []
        self._tool_lines = []
        self._complete_data = None
        self._error = None
        self._live = Live(
            Text(""),
            console=self._console,
            refresh_per_second=12,
            transient=False,
        )
        self._live.start()

    def stop(self):
        """Stop the Live display and print final pretty output."""
        if self._live is not None:
            self._live.stop()
            self._live = None

        # Print tool call lines
        for line in self._tool_lines:
            self._console.print(line)

        # Print final accumulated text as rich Markdown
        full_text = "".join(self._tokens)
        if full_text.strip():
            self._console.print(Markdown(full_text))

        # Print error if any
        if self._error:
            self._console.print(f"[llx.error]{self._error}[/llx.error]")

        self._console.print()

    # ── Event Callbacks ───────────────────────────────────────

    def on_token(self, content: str):
        """Append a token and refresh the live display."""
        self._tokens.append(content)
        self._refresh()

    def on_tool_call(self, data: dict):
        """Record a tool call and refresh the live display."""
        name = data.get("name") or data.get("tool", "unknown")
        args = data.get("arguments") or data.get("args", "")
        line = f"[dim]{_ICON_TOOL} Calling: {name}({args})[/dim]"
        self._tool_lines.append(line)
        self._refresh()

    def on_complete(self, data: dict):
        """Store completion data."""
        self._complete_data = data

    def on_error(self, message: str):
        """Store an error message."""
        self._error = message

    # ── Internal ──────────────────────────────────────────────

    def _refresh(self):
        """Update the Live display with tool lines + streaming text + cursor."""
        if self._live is None:
            return

        parts = []

        # Tool call lines rendered as markup
        for line in self._tool_lines:
            parts.append(Text.from_markup(line))

        # Streaming text shown as plain text with block cursor (not Markdown)
        streaming_text = "".join(self._tokens) + "\u2588"
        parts.append(Text(streaming_text))

        self._live.update(Group(*parts))
