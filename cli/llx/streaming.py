"""Socket.IO streaming client for chat and job progress."""

# python-engineio 4.x caps polling payloads at 16 packets per HTTP response and
# aborts the whole connection if the server exceeds that. The backend happily
# batches 20–40 chat:token events into one poll when Gemma4 streams a short
# reply, so without this bump the CLI drops its socket after the first few
# tokens and then waits 5 minutes for a chat:complete that never lands. Monkey-
# patch the class attribute BEFORE socketio imports anything that caches it.
from engineio import payload as _engineio_payload
_engineio_payload.Payload.max_decode_packets = 10000

import socketio
import json
import threading
import time
from typing import Callable

from llx.config import get_api_key, get_server_url
from llx.working_memory import approval_target_mismatch, extract_approval_targets

# Idle silence before treating a stream as stalled (activity resets this).
DEFAULT_IDLE_TIMEOUT = 300.0
# Absolute ceiling so a continuously chatty but never-completing stream ends.
DEFAULT_HARD_TIMEOUT = 1800.0


class LlxStreamer:
    """Handles Socket.IO connections for streaming chat and job progress."""

    def __init__(self, server_url: str | None = None):
        self.server_url = server_url or get_server_url()
        self.sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        self._connected = False
        self._done = threading.Event()
        # Approval requests get stashed here for the main thread to pick up.
        # Doing the prompt inside the socketio receive thread freezes the
        # whole event loop and the user can't see what they're answering.
        self._approval_pending = threading.Event()
        self._approval_data: dict | None = None
        self._approval_lock = threading.Lock()
        self._session_id: str | None = None
        self._last_activity = time.monotonic()
        self._activity_lock = threading.Lock()
        self._closing = False

    def _connect_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_key = get_api_key()
        if api_key:
            headers["X-API-Key"] = api_key
        return headers

    def _touch_activity(self):
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def stream_chat(
        self,
        session_id: str,
        on_token: Callable[[str], None],
        on_thinking: Callable[[dict], None] | None = None,
        on_tool_call: Callable[[dict], None] | None = None,
        on_tool_output_chunk: Callable[[dict], None] | None = None,
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """
        Connect to Socket.IO, join session, and listen for chat events.
        Call this BEFORE posting the chat message via HTTP.

        Approvals are NOT handled via callback — they're stashed in the
        streamer and the main thread picks them up via pop_pending_approval()
        or wait_for_completion(approval_handler=...). This keeps blocking
        prompts off the socketio receive thread.
        """
        self._done.clear()
        self._approval_pending.clear()
        with self._approval_lock:
            self._approval_data = None
        self._session_id = session_id
        self._closing = False
        self._touch_activity()

        @self.sio.on("chat:token")
        def handle_token(data):
            self._touch_activity()
            content = data.get("content", "")
            if content:
                on_token(content)

        @self.sio.on("chat:thinking")
        def handle_thinking(data):
            self._touch_activity()
            if on_thinking:
                on_thinking(data)

        @self.sio.on("chat:tool_call")
        def handle_tool_call(data):
            self._touch_activity()
            if on_tool_call:
                on_tool_call(data)

        @self.sio.on("chat:tool_approval_request")
        def handle_tool_approval_request(data):
            self._touch_activity()
            # Stash and signal — never block this thread on user I/O.
            with self._approval_lock:
                self._approval_data = data
            self._approval_pending.set()

        @self.sio.on("chat:tool_output_chunk")
        def handle_tool_output_chunk(data):
            self._touch_activity()
            if on_tool_output_chunk:
                on_tool_output_chunk(data)

        @self.sio.on("chat:complete")
        def handle_complete(data):
            self._touch_activity()
            if on_complete:
                on_complete(data)
            self._done.set()

        @self.sio.on("chat:error")
        def handle_error(data):
            self._touch_activity()
            if on_error:
                on_error(data.get("error", "Unknown error"))
            self._done.set()

        @self.sio.on("chat:aborted")
        def handle_aborted(data):
            self._touch_activity()
            if on_error:
                on_error("Chat aborted")
            self._done.set()

        @self.sio.event
        def disconnect():
            if self._closing or self._done.is_set():
                return
            if on_error:
                on_error("Stream disconnected before completion")
            self._done.set()

        try:
            self.sio.connect(
                self.server_url,
                transports=["polling", "websocket"],
                headers=self._connect_headers(),
            )
            self._connected = True
            self.sio.emit("chat:join", {"session_id": session_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect for streaming: {e}")
            self._done.set()
            return

    def wait(self, timeout: float = DEFAULT_IDLE_TIMEOUT) -> bool:
        """Block until streaming is done. Returns True if completed, False on timeout."""
        return self._done.wait(timeout=timeout)

    def pop_pending_approval(self) -> dict | None:
        """Atomically retrieve and clear any pending approval request.
        Safe to call from the main thread; returns None if nothing is pending."""
        with self._approval_lock:
            if not self._approval_pending.is_set():
                return None
            data = self._approval_data
            self._approval_data = None
            self._approval_pending.clear()
        return data

    def wait_for_completion(
        self,
        approval_handler: Callable[[dict], bool] | None = None,
        timeout: float = DEFAULT_IDLE_TIMEOUT,
        hard_timeout: float = DEFAULT_HARD_TIMEOUT,
    ) -> bool:
        """Block until chat is done, dispatching approval requests to the
        current thread via approval_handler(data) -> bool.

        ``timeout`` is idle silence (activity resets it). ``hard_timeout`` is
        an absolute ceiling from the start of the wait.

        If approval_handler is None, any approval request is auto-rejected
        (suitable for non-interactive / json mode). KeyboardInterrupt raised
        from the handler aborts the chat and propagates up.

        Returns True if chat completed, False on timeout.
        """
        started = time.monotonic()
        self._touch_activity()
        while True:
            now = time.monotonic()
            if now - started >= hard_timeout:
                return False
            with self._activity_lock:
                idle_for = now - self._last_activity
            if idle_for >= timeout:
                return False
            # Wake up regularly to check for pending approvals.
            slice_timeout = min(0.25, timeout - idle_for, hard_timeout - (now - started))
            if slice_timeout <= 0:
                return False
            if self._done.wait(timeout=slice_timeout):
                return True
            data = self.pop_pending_approval()
            if data is None:
                continue
            self._touch_activity()
            if approval_handler is None:
                approved = False
            else:
                try:
                    approved = bool(approval_handler(data))
                except KeyboardInterrupt:
                    if self._session_id:
                        self.send_approval_response(self._session_id, False)
                        self.abort(self._session_id)
                    raise
                except Exception as e:
                    import sys
                    sys.stderr.write(f"\nApproval handler failed; rejecting tool call: {e}\n")
                    sys.stderr.flush()
                    approved = False
            if self._session_id:
                self.send_approval_response(self._session_id, approved)

    def abort(self, session_id: str):
        """Send abort signal for current chat over Socket.IO."""
        if self._connected:
            try:
                self.sio.emit("chat:abort", {"session_id": session_id})
            except Exception:
                pass

    def hard_abort(self, session_id: str, client=None):
        """Abort via Socket.IO (if connected) and HTTP hard-kill endpoint."""
        self.abort(session_id)
        if client is not None:
            try:
                client.abort_session(session_id)
            except Exception:
                pass
        else:
            try:
                from llx.client import get_client
                get_client(self.server_url).abort_session(session_id)
            except Exception:
                pass

    def send_approval_response(self, session_id: str, approved: bool):
        """Send a tool approval response back to the server."""
        if self._connected:
            try:
                self.sio.emit("chat:tool_approval_response", {"session_id": session_id, "approved": approved})
            except Exception:
                pass

    def disconnect(self):
        """Disconnect from Socket.IO."""
        self._closing = True
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
            self.sio.connect(
                self.server_url,
                transports=["polling", "websocket"],
                headers=self._connect_headers(),
            )
            self._connected = True
            self.sio.emit("subscribe", {"job_id": job_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect: {e}")
            self._done.set()


# ── Chat Renderer ─────────────────────────────────────────────

import sys
import threading

from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text
from rich.console import Group

from llx.theme import make_console

_ICON_TOOL  = "\u27e1"   # ⟡
_ICON_OK    = "\u2713"   # ✓
_ICON_ASSISTANT = "\u276f"  # ❯  (brand mark — Unicode has no aardvark)

# 8-step "shining cursor" spinner — edge sweeps clockwise around a block
_SPINNER_FRAMES = ["▀", "▜", "▐", "▟", "▄", "▙", "▌", "▛"]

_WEB_ACCESS_HINT = (
    "Web access is disabled. Enable it in Settings (allow_web_search), then retry."
)


def _maybe_web_access_hint(message: str) -> str | None:
    lowered = (message or "").lower()
    if "web access is disabled" in lowered or "web search is disabled" in lowered:
        return _WEB_ACCESS_HINT
    return None


def _set_title(title: str):
    """Set the terminal tab title via ANSI escape.

    Writes to /dev/tty directly to bypass Rich's Live display capture.
    Falls back to stderr if /dev/tty is unavailable.
    """
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(f"\033]0;{title}\007")
            tty.flush()
    except OSError:
        sys.stderr.write(f"\033]0;{title}\007")
        sys.stderr.flush()


class ChatRenderer:
    """Renders streaming chat responses with live markdown and tool-call UI."""

    def __init__(self):
        self._console = make_console()
        self._tokens: list[str] = []
        self._tool_lines: list[str] = []
        self._tool_outputs: dict[str, list[str]] = {}
        self._thinking_steps: list[dict] = []
        self._complete_data: dict | None = None
        self._error: str | None = None
        self._live: Live | None = None
        self._thinking = False
        self._spinner_thread: threading.Thread | None = None
        self._spinner_stop = threading.Event()
        self._last_render_time = 0.0
        self._render_throttle = 0.05  # 50ms throttle for large documents
        self._live_status = "thinking"

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Clear state and begin a Live display with thinking spinner."""
        self._tokens = []
        self._tool_lines = []
        self._tool_outputs = {}
        self._thinking_steps = []
        self._complete_data = None
        self._error = None
        self._thinking = True
        self._live_status = "thinking"
        self._spinner_stop.clear()
        self._last_render_time = 0.0
        self._live = Live(
            Text(""),
            console=self._console,
            refresh_per_second=15,
            transient=True,
        )
        self._live.start()
        self._start_spinner()

    def stop(self):
        """Stop the Live display and print final pretty output."""
        self._stop_spinner()

        if self._live is not None:
            self._live.stop()
            self._live = None

        # Reset terminal title
        _set_title("agent")

        self._print_thinking_trail()

        # Print tool call lines
        for line in self._tool_lines:
            self._console.print(line)

        # Print tool outputs
        for tool, chunks in self._tool_outputs.items():
            if chunks:
                out_text = "".join(chunks)
                lines = out_text.splitlines()
                if len(lines) > 10:
                    out_text = "...\n" + "\n".join(lines[-10:])
                # Pretty diff if it looks like one
                if tool in ("edit", "edit_code", "apply") or out_text.lstrip().startswith("--- ") or "diff --git" in out_text[:200]:
                    try:
                        from rich.syntax import Syntax
                        self._console.print(Syntax(out_text, "diff", line_numbers=False))
                        continue
                    except Exception:
                        pass
                self._console.print(f"[dim][{tool} output]\n{out_text}[/dim]")

        # Print final accumulated text as rich Markdown with llama prefix
        full_text = "".join(self._tokens)
        if full_text.strip():
            self._console.print(Text(f"{_ICON_ASSISTANT} ", style="llx.brand"), end="")
            self._console.print(Markdown(full_text))
            hint = _maybe_web_access_hint(full_text)
            if hint:
                self._console.print(f"[llx.dim]{hint}[/llx.dim]")

        # Print error if any
        if self._error:
            self._console.print(f"[llx.error]{self._error}[/llx.error]")
            hint = _maybe_web_access_hint(self._error)
            if hint:
                self._console.print(f"[llx.dim]{hint}[/llx.dim]")

        self._console.print()

    # ── Event Callbacks ───────────────────────────────────────

    def on_token(self, content: str):
        """Append a token and refresh the live display."""
        # Filter out raw tool-call markup leaked by the LLM
        if content.startswith("<tool") or content.startswith("</tool"):
            return
        if self._tokens and self._tokens[-1].startswith("<tool"):
            # Previous token was a partial tool marker — drop both
            self._tokens.pop()
            return
        if self._thinking:
            self._thinking = False
            self._stop_spinner()
        self._tokens.append(content)
        self._refresh()

    def on_thinking(self, data: dict):
        """Record agent thinking steps (single trail, web UI parity)."""
        if not isinstance(data, dict):
            return
        iteration = data.get("iteration")
        status = (data.get("status") or data.get("label") or "").strip()
        reasoning = (data.get("reasoning") or "").strip()
        step = {
            "iteration": iteration,
            "status": status,
            "reasoning": reasoning,
        }
        if iteration is not None:
            for idx, existing in enumerate(self._thinking_steps):
                if existing.get("iteration") == iteration:
                    merged = dict(existing)
                    if status:
                        merged["status"] = status
                    if reasoning:
                        merged["reasoning"] = reasoning
                    self._thinking_steps[idx] = merged
                    break
            else:
                self._thinking_steps.append(step)
        else:
            self._thinking_steps.append(step)
        if status:
            self._live_status = status
        self._refresh()

    def on_tool_call(self, data: dict):
        """Record a tool call and refresh the live display."""
        if self._thinking:
            self._thinking = False
            self._stop_spinner()
        # Tokens before a tool call are the model's reasoning, not the
        # response. Clear them so the final answer comes through clean.
        self._tokens.clear()
        name = data.get("name") or data.get("tool", "unknown")
        args = data.get("params")
        if args is None:
            args = data.get("arguments")
        if args is None:
            args = data.get("args", "")
        if isinstance(args, (dict, list)):
            args = json.dumps(args, sort_keys=True)
        args_preview = str(args)
        if len(args_preview) > 120:
            args_preview = args_preview[:117] + "..."
        line = f"[dim]{_ICON_TOOL} Calling: {name}({args_preview})[/dim]"
        self._tool_lines.append(line)
        self._live_status = f"tool: {name}"
        self._refresh()

    def prompt_for_approval(self, data: dict, expected_target: str | None = None) -> bool:
        """Ask the user whether to allow the listed tools.

        MUST be called from the main thread (not a socketio callback).
        Pauses the spinner and Live display before prompting so the user
        actually sees the question, then resumes them so the rest of the
        response can keep streaming. Raises KeyboardInterrupt if the user
        aborts at the prompt — caller should treat that as 'cancel chat'.
        """
        tools = data.get("tools", [])
        tools_str = ", ".join(tools) if tools else "(unknown tools)"

        # Snapshot what's running before we tear it down
        spinner_was_running = (
            self._spinner_thread is not None
            and not self._spinner_stop.is_set()
        )
        live_was_active = self._live is not None

        # Stop spinner FIRST so it can't race the prompt by writing escapes
        self._stop_spinner()
        # Stop Live so the prompt isn't erased by the transient region
        if live_was_active:
            try:
                self._live.stop()
            except Exception:
                pass

        _set_title("agent — awaiting approval")

        import typer
        self._console.print()
        self._console.print("[bold yellow]\u26a0 Approval Required[/bold yellow]")
        self._console.print(f"  Tool(s): [bold]{tools_str}[/bold]")
        actual_targets = extract_approval_targets(data)
        if expected_target:
            self._console.print(f"  Expected target: [bold]{expected_target}[/bold]")
        if actual_targets:
            self._console.print(f"  Actual target(s): [bold]{', '.join(actual_targets)}[/bold]")

        mismatch, _targets = approval_target_mismatch(data, expected_target)
        if mismatch:
            self._console.print("[red]\u2717 Rejected: edit target does not match the active file.[/red]\n")
            if live_was_active:
                try:
                    self._live.start()
                except Exception:
                    pass
            if spinner_was_running:
                self._thinking = True
                self._spinner_stop.clear()
                self._start_spinner()
            return False

        aborted = False
        approved = False
        try:
            approved = typer.confirm("Allow execution?", default=False)
        except (KeyboardInterrupt, EOFError, typer.Abort):
            aborted = True

        if aborted:
            self._console.print("[red]\u2717 Aborted.[/red]\n")
        elif approved:
            self._console.print("[green]\u2713 Approved.[/green]\n")
        else:
            self._console.print("[red]\u2717 Rejected.[/red]\n")

        # Always resume display so further events can render — even on abort
        if live_was_active:
            try:
                self._live.start()
            except Exception:
                pass
        if spinner_was_running:
            self._thinking = True
            self._spinner_stop.clear()
            self._start_spinner()

        if aborted:
            raise KeyboardInterrupt
        return approved

    def on_tool_output_chunk(self, data: dict):
        """Record tool output chunk."""
        tool = data.get("tool", "unknown")
        chunk = data.get("chunk", "")
        if tool not in self._tool_outputs:
            self._tool_outputs[tool] = []
        self._tool_outputs[tool].append(chunk)
        self._refresh()

    def on_complete(self, data: dict):
        """Store completion data. If no tokens were streamed, use the response."""
        self._complete_data = data
        # If no tokens arrived via streaming (e.g. tool call consumed the
        # response), pull the final text from the complete event
        if not self._tokens and isinstance(data, dict):
            response = data.get("response", "")
            if response:
                self._tokens.append(response)

    def on_error(self, message: str):
        """Store an error message."""
        self._error = message

    def _print_thinking_trail(self):
        """Print one collapsed thinking-trail block (never per-step accordions)."""
        if not self._thinking_steps:
            return
        n = len(self._thinking_steps)
        label = "step" if n == 1 else "steps"
        self._console.print(
            f"\n[bold dim]\u25b8 Agent thinking[/bold dim] [dim]({n} {label})[/dim]"
        )
        for step in self._thinking_steps:
            iteration = step.get("iteration", "?")
            status = step.get("status") or "thinking"
            self._console.print(f"[dim]  [{iteration}] {status}[/dim]")
            reasoning = (step.get("reasoning") or "").strip()
            if reasoning:
                preview = reasoning[:240] + ("…" if len(reasoning) > 240 else "")
                self._console.print(f"[dim]    {preview}[/dim]")

    # ── Spinner ───────────────────────────────────────────────

    def _start_spinner(self):
        """Start the thinking spinner in a background thread."""
        def spin():
            frame_idx = 0
            while not self._spinner_stop.is_set():
                f = _SPINNER_FRAMES[frame_idx % len(_SPINNER_FRAMES)]
                status = self._live_status or "thinking"
                _set_title(f"{f} agent — {status}...")
                # Update inline display
                if self._live is not None and self._thinking:
                    self._live.update(Text(f" {f} ", style="bold cyan"))
                frame_idx += 1
                self._spinner_stop.wait(0.1)

        self._spinner_thread = threading.Thread(target=spin, daemon=True)
        self._spinner_thread.start()

    def _stop_spinner(self):
        """Stop the thinking spinner."""
        self._spinner_stop.set()
        if self._spinner_thread is not None:
            self._spinner_thread.join(timeout=1)
            self._spinner_thread = None

    # ── Internal ──────────────────────────────────────────────

    def _refresh(self):
        """Update the Live display with tool lines + streaming text + cursor."""
        if self._live is None:
            return

        now = time.time()
        # Throttle rendering if updating too frequently
        if now - self._last_render_time < self._render_throttle:
            return
        self._last_render_time = now

        parts = []

        if self._thinking_steps:
            last = self._thinking_steps[-1]
            iteration = last.get("iteration", "?")
            status = last.get("status") or "thinking"
            reasoning = (last.get("reasoning") or "").strip()
            preview = reasoning[:160] + ("…" if len(reasoning) > 160 else "")
            trail = f"[dim]▸ step {iteration} · {status}[/dim]"
            if preview:
                trail += f"\n[dim]  {preview}[/dim]"
            parts.append(Text.from_markup(trail))

        # Tool call lines rendered as markup
        for line in self._tool_lines:
            parts.append(Text.from_markup(line))

        # Tool output chunks
        for tool, chunks in self._tool_outputs.items():
            if chunks:
                out_text = "".join(chunks)
                # Keep only last 10 lines to prevent terminal overload
                lines = out_text.splitlines()
                if len(lines) > 10:
                    out_text = "...\n" + "\n".join(lines[-10:])
                parts.append(Text(f"[{tool} output]\n{out_text}", style="dim"))

        # Streaming text shown as plain text with block cursor (not Markdown)
        streaming_text = "".join(self._tokens) + "\u2588"
        parts.append(Text(streaming_text))

        # Update title bar with progress
        _set_title(f"{_ICON_ASSISTANT} agent — responding...")

        self._live.update(Group(*parts))
