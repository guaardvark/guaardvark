"""Interactive REPL — chat-first with slash commands."""

import time
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
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
    """Send a chat message with streaming or synchronous response."""
    console = make_console()
    server = state["server"]
    session_id = state["session_id"]
    agent_mode = state.get("agent_mode", False)
    lite_mode = state.get("lite_mode", False)

    # Freshen context in background
    ctx.refresh_async()

    if agent_mode:
        message = f"[AGENT MODE: You are an autonomous agent. Use your tools to fulfill this request.]\n\n{message}"

    # The /agent slash command toggles this. Tells the backend whether to
    # route Gemma4 through its screen-action direct path and to expose
    # desktop/agent-control tools. Defaults False — CLI users aren't watching
    # the agent screen unless they explicitly opted in.
    screen_active = bool(state.get("agent_screen_active", False))

    if lite_mode:
        # Lite mode: synchronous chat (no Socket.IO)
        try:
            client = get_client(server)
            response = client.post("/api/chat/unified", json={
                "session_id": session_id,
                "message": message,
                "options": {
                    "use_rag": False,
                    "agent_screen_active": screen_active,
                },
            })
            result = response.get("data", response)
            content = result.get("response", str(result))
            from rich.markdown import Markdown
            console.print()
            console.print(Markdown(content))
            console.print()
        except (LlxConnectionError, LlxError, Exception) as e:
            console.print(f"[llx.error]Chat error: {e}[/llx.error]")
    else:
        # Full mode: streaming via Socket.IO
        context_block = ctx.format_context_block()
        renderer = ChatRenderer()
        streamer = LlxStreamer(server)

        streamer.stream_chat(
            session_id,
            on_token=renderer.on_token,
            on_tool_call=renderer.on_tool_call,
            on_tool_output_chunk=renderer.on_tool_output_chunk,
            on_complete=renderer.on_complete,
            on_error=renderer.on_error,
        )
        renderer.start()

        try:
            client = get_client(server)
            client.post("/api/chat/unified", json={
                "session_id": session_id,
                "message": message,
                "options": {
                    "use_rag": True,
                    "context": context_block,
                    "agent_screen_active": screen_active,
                },
            })
        except (LlxConnectionError, LlxError, Exception) as e:
            renderer.stop()
            console.print(f"[llx.error]Chat error: {e}[/llx.error]")
            streamer.disconnect()
            return

        completed = False
        try:
            completed = streamer.wait_for_completion(
                approval_handler=renderer.prompt_for_approval,
                timeout=300,
            )
        except KeyboardInterrupt:
            # User hit Ctrl+C at the approval prompt — chat already aborted
            completed = True
            console.print("[llx.dim]Chat aborted.[/llx.dim]")
        finally:
            renderer.stop()
            streamer.disconnect()

        if not completed:
            console.print(
                "[llx.error]No response after 5 minutes — server may be stalled "
                "(check backend log / Ollama). Returning to prompt.[/llx.error]"
            )

    # Track session
    state["message_count"] = state.get("message_count", 0) + 1
    save_session(session_id, message[:80], state["message_count"])


# ── Main entry point ──────────────────────────────────────────


def launch_repl():
    """Start the interactive REPL."""
    console = make_console()
    config = load_config()
    server = get_server_url()

    # Detect lite mode — only if the config file actually exists and says lite.
    # No config file = user is running the full stack directly, not via launch.
    _lite_mode = False
    try:
        from llx.launch_config import _config_path
        if _config_path().exists():
            from llx.launch_config import load_launch_config
            _lcfg = load_launch_config()
            _lite_mode = _lcfg.get("mode") == "lite"
    except Exception:
        pass

    # Shared state dict
    state = {
        "session_id": str(uuid.uuid4()),
        "server": server,
        "message_count": 0,
        "agent_mode": False,
        "lite_mode": _lite_mode,
    }

    # Create context snapshot and start background population
    ctx = ContextSnapshot(server)
    ctx.refresh_async()

    # Auto-start lite server if backend is offline and config says to
    if not ctx.is_online():
        try:
            from llx.launch_config import load_launch_config
            lcfg = load_launch_config()
            if lcfg.get("auto_start_services") and lcfg.get("mode") == "lite":
                from llx.commands.launch import _start_lite_mode
                _start_lite_mode(console, port=5000)
                time.sleep(0.5)
                ctx.refresh_async()
        except Exception:
            pass

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
        # Rich's console.print() from inside a prompt_toolkit key handler
        # crashes the event loop because prompt_toolkit owns the terminal.
        # run_in_terminal pauses rendering, runs the callable, then resumes.
        run_in_terminal(
            lambda: console.print("\n[llx.dim]Press Ctrl+C again to exit.[/llx.dim]")
        )

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
            from llx.utils import parse_file_mentions
            line = parse_file_mentions(line)
            _handle_chat(state, ctx, line)
