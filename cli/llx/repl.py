"""Interactive REPL — chat-first with slash commands."""

import time
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from llx import __version__
from llx.client import get_client, LlxError, LlxConnectionError
from llx.completer import make_completer
from llx.config import (
    get_project_scope,
    get_recent_session,
    get_server_url,
    load_config,
    save_session,
)
from llx.context import ContextSnapshot
from llx.slash import SlashRouter
from llx.streaming import ChatRenderer, LlxStreamer
from llx.theme import (
    ICON_OFFLINE,
    ICON_ONLINE,
    THEMES,
    get_banner,
    make_console,
)


# ── Helpers ───────────────────────────────────────────────────


def _format_age(timestamp: float) -> str:
    """Format a Unix timestamp as a human-readable age string."""
    if not timestamp:
        return "unknown"

    delta = time.time() - timestamp
    if delta < 0:
        return "just now"

    if delta < 60:
        return "just now"
    elif delta < 3600:
        minutes = int(delta / 60)
        return f"{minutes}m ago"
    elif delta < 86400:
        hours = int(delta / 3600)
        return f"{hours}h ago"
    else:
        days = int(delta / 86400)
        return f"{days}d ago"


def _build_prompt(ctx: ContextSnapshot, state: dict) -> HTML:
    """Build the prompt string as prompt_toolkit HTML."""
    parts = ["<b>guaardvark</b>"]

    # Online / offline / model info
    online = ctx.is_online()
    if online:
        model = ctx.get_model_name()
        if model and model != "unknown":
            parts.append(f" <style color='#8880b0'>{model}</style>")

        # Active jobs
        jobs = ctx.get_active_jobs_count()
        if jobs > 0:
            parts.append(f" <style color='#fdcb6e'>({jobs} jobs)</style>")
    else:
        parts.append(" <style color='#ff6b6b'>(offline)</style>")

    # Project scope
    scope = get_project_scope()
    if scope:
        name = scope.get("name") or f"id:{scope.get('id')}"
        parts.append(f" <style color='#74b9ff'>[{name}]</style>")

    parts.append(" <b>&gt;</b> ")
    return HTML("".join(parts))


def _dynamic_completions(command: str, sub_text: str):
    """Provide dynamic completions for certain commands."""
    if command == "theme":
        prefix = sub_text.strip().lower()
        return [n for n in THEMES if n.startswith(prefix)] if prefix else list(THEMES.keys())
    return None


# ── Chat handler ──────────────────────────────────────────────


def _handle_chat(state: dict, ctx: ContextSnapshot, message: str):
    """Send a chat message with streaming response."""
    console = make_console()
    server = state["server"]
    session_id = state["session_id"]

    # Freshen context in background
    ctx.refresh_async()

    # Build context block for LLM injection
    context_block = ctx.format_context_block()

    # Set up streaming
    renderer = ChatRenderer()
    streamer = LlxStreamer(server)

    # Connect to Socket.IO and join session BEFORE posting
    streamer.stream_chat(
        session_id,
        on_token=renderer.on_token,
        on_tool_call=renderer.on_tool_call,
        on_complete=renderer.on_complete,
        on_error=renderer.on_error,
    )

    # Start the live renderer
    renderer.start()

    # POST the chat message
    try:
        client = get_client(server)
        client.post("/api/chat/unified", json={
            "session_id": session_id,
            "message": message,
            "options": {
                "use_rag": True,
                "context": context_block,
            },
        })
    except (LlxConnectionError, LlxError, Exception) as e:
        renderer.stop()
        console.print(f"[llx.error]Chat error: {e}[/llx.error]")
        streamer.disconnect()
        return

    # Wait for the streaming response to finish
    streamer.wait(timeout=300)

    # Clean up
    renderer.stop()
    streamer.disconnect()

    # Track session
    state["message_count"] = state.get("message_count", 0) + 1
    save_session(session_id, message[:80], state["message_count"])


# ── Main entry point ──────────────────────────────────────────


def launch_repl():
    """Start the interactive REPL."""
    console = make_console()
    config = load_config()
    server = get_server_url()

    # Shared state dict
    state = {
        "session_id": str(uuid.uuid4()),
        "server": server,
        "message_count": 0,
    }

    # Create context snapshot and start background population
    ctx = ContextSnapshot(server)
    ctx.refresh_async()

    # Create slash router
    router = SlashRouter(state)

    # Create completer with dynamic theme completions
    completer = make_completer(get_dynamic=_dynamic_completions)

    # Brief pause to let background context populate
    time.sleep(0.3)

    # Determine connection status from cached context
    if ctx.is_online():
        model = ctx.get_model_name()
        status_line = f"[llx.status.online]{ICON_ONLINE} Connected[/llx.status.online]  {server}"
        model_line = f"[llx.accent]{model}[/llx.accent]"
    else:
        # Fall back to direct health check
        try:
            client = get_client(server)
            health = client.get("/api/health")
            status_line = f"[llx.status.online]{ICON_ONLINE} Connected[/llx.status.online]  {server}"
            model_line = "[llx.dim]model unknown[/llx.dim]"
        except (LlxConnectionError, LlxError, Exception):
            status_line = f"[llx.status.offline]{ICON_OFFLINE} Offline[/llx.status.offline]  {server}"
            model_line = "[llx.dim]not connected[/llx.dim]"

    # Print banner
    console.print(get_banner(__version__, status_line, model_line))

    # Check for recent session to resume
    recent = get_recent_session(3600)
    if recent:
        age = _format_age(recent.get("timestamp", 0))
        preview = recent.get("preview", "")
        msgs = recent.get("message_count", 0)
        console.print(
            f"[llx.dim]Resume previous session? ({msgs} msgs, {age})[/llx.dim]"
        )
        if preview:
            console.print(f"[llx.dim]  Last: {preview}[/llx.dim]")
        console.print("[llx.dim]Press Enter to resume, or type to start fresh.[/llx.dim]\n")
        state["pending_resume"] = recent
    else:
        console.print()

    # Key bindings — double Ctrl+C to exit
    kb = KeyBindings()
    _last_ctrl_c = {"time": 0.0}

    @kb.add("c-c")
    def _handle_ctrl_c(event):
        now = time.time()
        if now - _last_ctrl_c["time"] < 2.0:
            raise EOFError()
        _last_ctrl_c["time"] = now
        console.print("\n[llx.dim]Press Ctrl+C again to exit.[/llx.dim]")

    # Create prompt session
    history_file = Path.home() / ".llx" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        key_bindings=kb,
    )

    # ── Main loop ─────────────────────────────────────────────
    while True:
        try:
            prompt_text = _build_prompt(ctx, state)
            line = session.prompt(prompt_text).strip()
        except EOFError:
            console.print("\n[llx.dim]Goodbye.[/llx.dim]")
            break
        except KeyboardInterrupt:
            continue

        if not line:
            # Empty input — resume pending session if any
            pending = state.get("pending_resume")
            if pending:
                state["session_id"] = pending["id"]
                state["message_count"] = pending.get("message_count", 0)
                state.pop("pending_resume", None)
                console.print(
                    f"[llx.success]Resumed session {pending['id'][:8]}...[/llx.success]"
                )
                preview = pending.get("preview", "")
                if preview:
                    console.print(f"[llx.dim]{preview}[/llx.dim]\n")
            continue

        # Any typed input clears pending resume
        state.pop("pending_resume", None)

        if line.startswith("/"):
            # Slash command
            keep_going = router.dispatch(line)
            if not keep_going:
                break
        else:
            # Chat message
            _handle_chat(state, ctx, line)
