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
    ensure_history_file,
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
    prompt_colors,
)
from llx.working_memory import (
    apply_attachments,
    apply_todos,
    apply_user_intent,
    build_cli_context,
    empty_working_memory,
    expected_edit_target,
    normalize_working_memory,
    record_recommendation_summary,
    should_demote_rag,
)
from llx.local_tools import get_git_info, list_dir
from llx.todo import TodoStore
import re
from pathlib import Path

from llx.utils import find_project_root, load_guaardvark_instructions, populate_project_context


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
    """Build the prompt string as prompt_toolkit HTML, using the active theme."""
    c = prompt_colors()
    parts = [f"<style fg='{c['brand']}'><b>gv</b></style>"]

    online = ctx.is_online()
    if online:
        model = ctx.get_model_name()
        if model and model != "unknown":
            parts.append(f" <style fg='{c['dim']}'>{model}</style>")

        jobs = ctx.get_active_jobs_count()
        if jobs > 0:
            parts.append(f" <style fg='{c['warning']}'>({jobs} jobs)</style>")
    else:
        parts.append(f" <style fg='{c['error']}'>(offline)</style>")

    scope = get_project_scope()
    if scope:
        name = scope.get("name") or f"id:{scope.get('id')}"
        parts.append(f" <style fg='{c['info']}'>[{name}]</style>")

    if state.get("agent_mode"):
        parts.append(f" <style fg='{c['agent']}'>[agent]</style>")

    mem = normalize_working_memory(state.get("working_memory"))
    git = mem.get("git") or {}
    if git.get("branch"):
        dirty = "*" if git.get("dirty") else ""
        parts.append(f" <style fg='{c['accent']}'>{git['branch']}{dirty}</style>")
    todos = mem.get("todos") or []
    open_t = sum(1 for t in todos if not t.get("done"))
    if open_t > 0:
        parts.append(f" <style fg='{c['warning']}'>({open_t} todo{'s' if open_t != 1 else ''})</style>")

    tool_count = len(state.get("_tool_names", [])) or len(state.get("working_memory", {}).get("_tool_names", []))
    if tool_count:
        parts.append(f" <style fg='{c['info']}'>({tool_count} tools)</style>")

    recent = mem.get("recent_tools") or []
    if recent:
        last = recent[-1].get("tool", "")
        if last:
            parts.append(f" <style fg='{c['accent_bright']}'>[{last}]</style>")

    cwd = mem.get("cwd") or str(Path.cwd())
    proj_name = mem.get("project_name")
    try:
        short = Path(cwd).name or cwd
    except Exception:
        short = cwd
    if proj_name and proj_name != short:
        parts.append(f" <style fg='{c['muted']}'>[{proj_name} @ {short}]</style>")
    else:
        parts.append(f" <style fg='{c['muted']}'>[{short}]</style>")

    parts.append(f" <style fg='{c['brand']}'><b>&gt;</b></style> ")
    return HTML("".join(parts))


def _make_dynamic_completions(state_ref):
    """Factory for dynamic completions that can see live state (e.g. tool list after /tools)."""
    def _dynamic_completions(command: str, sub_text: str):
        """Provide dynamic completions for certain commands."""
        if command == "theme":
            from llx.theme import get_theme_names

            names = get_theme_names()
            prefix = sub_text.strip().lower()
            return [n for n in names if n.startswith(prefix)] if prefix else list(names)
        # Tool name completion (populated after /tools call)
        if command in ("tool", "tools"):
            tools = state_ref.get("_available_tools") or state_ref.get("_tool_names") or []
            if tools:
                prefix = sub_text.strip().lower()
                matches = [n for n in tools if n.lower().startswith(prefix)]
                if matches:
                    return matches[:20]
            # fallback common coding agent tools
            common = ["read_code", "edit_code", "search_code", "list_files", "execute_python",
                      "grep_search", "save_memory", "search_memory", "get_repository_map",
                      "verify_change", "agent_task_execute", "list_code_files", "read_file"]
            prefix = sub_text.strip().lower()
            return [n for n in common if n.startswith(prefix)] if prefix else common[:10]
        return None
    return _dynamic_completions


# ── Chat handler ──────────────────────────────────────────────


def _handle_chat(state: dict, ctx: ContextSnapshot, message: str, raw_message: str | None = None, attachments: list[dict] | None = None):
    """Send a chat message with streaming or synchronous response."""
    console = make_console()
    server = state["server"]
    session_id = state["session_id"]
    agent_mode = state.get("agent_mode", False)
    lite_mode = state.get("lite_mode", False)
    raw_message = raw_message or message
    attachments = attachments or []
    memory = normalize_working_memory(state.get("working_memory"))
    state["working_memory"] = memory
    use_rag = not should_demote_rag(raw_message, memory, attachments)

    # Freshen context in background
    ctx.refresh_async()

    # Refresh local git/cwd snapshot into working memory for every chat turn
    mem = normalize_working_memory(state.get("working_memory"))
    if state.get("cwd"):
        mem["cwd"] = str(state["cwd"])
    mem["git"] = get_git_info(Path(mem.get("cwd") or Path.cwd()))
    # sync open todos
    if state.get("_todo_store"):
        apply_todos(mem, state["_todo_store"].all())
    state["working_memory"] = mem

    # If the message starts with an absolute path (common for "drag folder" then query),
    # treat it as project_root for this turn (so backend loads GUAARDVARK.md etc.)
    path_match = re.match(r"^['\"]?(\/[^\s'\"]+)['\"]?\s*(.*)$", message)
    if path_match:
        candidate = path_match.group(1)
        if Path(candidate).is_dir():
            mem["project_root"] = candidate
            state["cwd"] = Path(candidate)
            rest = path_match.group(2).strip()
            if rest:
                message = rest  # strip the path from the query sent to LLM
            else:
                message = "analyze this project"
            # re-populate local context for the new root
            try:
                from llx.utils import populate_project_context
                populate_project_context(mem, Path(candidate))
            except Exception:
                pass
            state["working_memory"] = mem

    if agent_mode:
        message = f"[AGENT MODE: You are an autonomous agent. Use your tools to fulfill this request.]\n\n{message}"

    # The /agent slash command toggles this. Tells the backend whether to
    # route Gemma4 through its screen-action direct path and to expose
    # desktop/agent-control tools. Defaults False — CLI users aren't watching
    # the agent screen unless they explicitly opted in.
    screen_active = bool(state.get("agent_screen_active", False))

    if lite_mode:
        # Lite mode: synchronous chat (no Socket.IO)
        assistant_text = ""
        try:
            client = get_client(server)
            response = client.post("/api/chat/unified", json={
                "session_id": session_id,
                "message": message,
                "options": {
                    "use_rag": False,
                    "context": build_cli_context(ctx.format_context_block(), memory),
                    "agent_screen_active": screen_active,
                    "cli_working_memory": memory,
                },
            })
            result = response.get("data", response)
            content = result.get("response", str(result))
            assistant_text = content
            from rich.markdown import Markdown
            console.print()
            console.print(Markdown(content))
            console.print()
        except (LlxConnectionError, LlxError, Exception) as e:
            console.print(f"[llx.error]Chat error: {e}[/llx.error]")
    else:
        # Full mode: streaming via Socket.IO
        context_block = build_cli_context(ctx.format_context_block(), memory)
        renderer = ChatRenderer()
        streamer = LlxStreamer(server)
        client = get_client(server)
        proj_root = memory.get("project_root") or state.get("cwd")
        chat_body = {
            "session_id": session_id,
            "message": message,
            "options": {
                "use_rag": use_rag,
                "context": context_block,
                "agent_screen_active": screen_active,
                "cli_working_memory": memory,
                "project_root": str(proj_root) if proj_root else None,
            },
        }

        streamer.stream_chat(
            session_id,
            on_token=renderer.on_token,
            on_thinking=renderer.on_thinking,
            on_tool_call=renderer.on_tool_call,
            on_tool_output_chunk=renderer.on_tool_output_chunk,
            on_complete=renderer.on_complete,
            on_error=renderer.on_error,
        )
        renderer.start()

        posted = False
        for attempt in range(2):
            try:
                client.post("/api/chat/unified", json=chat_body)
                posted = True
                break
            except LlxError as e:
                if e.status_code == 409 and attempt == 0:
                    console.print(
                        "[llx.dim]Previous request still running — aborting and retrying…[/llx.dim]"
                    )
                    try:
                        client.abort_session(session_id)
                    except Exception:
                        pass
                    continue
                renderer.stop()
                console.print(f"[llx.error]Chat error: {e}[/llx.error]")
                if e.status_code == 409:
                    console.print(
                        "[llx.dim]Try /abort, then send again — or /new for a fresh session.[/llx.dim]"
                    )
                streamer.disconnect()
                return
            except (LlxConnectionError, Exception) as e:
                renderer.stop()
                console.print(f"[llx.error]Chat error: {e}[/llx.error]")
                streamer.disconnect()
                return

        if not posted:
            renderer.stop()
            streamer.disconnect()
            return

        completed = False
        aborted_by_user = False
        try:
            completed = streamer.wait_for_completion(
                approval_handler=lambda data: renderer.prompt_for_approval(
                    data,
                    expected_target=expected_edit_target(memory),
                ),
            )
        except KeyboardInterrupt:
            aborted_by_user = True
            streamer.hard_abort(session_id, client)
            console.print("[llx.dim]Chat aborted.[/llx.dim]")
        finally:
            renderer.stop()
            if not completed and not aborted_by_user:
                streamer.hard_abort(session_id, client)
            streamer.disconnect()

        if aborted_by_user:
            pass
        elif not completed:
            console.print(
                "[llx.error]No response after 5 minutes of silence — session aborted. "
                "Try again, or /abort / /new if it stays stuck.[/llx.error]"
            )
        assistant_text = "".join(renderer._tokens)

    record_recommendation_summary(memory, raw_message, assistant_text)
    # Track session
    state["message_count"] = state.get("message_count", 0) + 1
    save_session(session_id, raw_message[:80], state["message_count"], working_memory=memory)


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
    initial_cwd = Path.cwd()
    initial_mem = empty_working_memory()
    initial_mem["cwd"] = str(initial_cwd)
    initial_mem["git"] = get_git_info(initial_cwd)

    # Auto detect project root and load GUAARDVARK.md + explore (for dragged folders / website projects)
    # Leverages backend architecture (code tools, memory, unified context) when connected.
    try:
        proj_root = find_project_root(initial_cwd)
        if proj_root != initial_cwd:
            initial_mem["cwd"] = str(proj_root)
            initial_cwd = proj_root
        populate_project_context(initial_mem, proj_root)
        if initial_mem.get("guaardvark_instructions"):
            console.print(f"[llx.dim]Loaded GUAARDVARK.md from {proj_root.name}[/llx.dim]")
    except Exception:
        pass

    state = {
        "session_id": str(uuid.uuid4()),
        "server": server,
        "message_count": 0,
        "agent_mode": False,
        "lite_mode": _lite_mode,
        "working_memory": initial_mem,
        "cwd": initial_cwd,
        "_todo_store": TodoStore(),  # session todos
    }
    # seed working memory todos from store if any
    apply_todos(initial_mem, state["_todo_store"].all())

    # Create context snapshot and start background population
    ctx = ContextSnapshot(server)
    ctx.refresh_async()

    # Auto-start backend if offline (full stack mode; lite uses embedded server via launch --lite)
    if not ctx.is_online() and not _lite_mode:
        try:
            from llx.backend_bootstrap import ensure_backend_running

            ensure_backend_running(console, quiet=True)
            time.sleep(0.5)
            ctx.refresh_async()
            server = get_server_url()
            state["server"] = server
        except Exception as e:
            console.print(f"[llx.warning]Could not auto-start backend: {e}[/llx.warning]")
            console.print("[llx.dim]Try: guaardvark start --backend-only or ./start.sh[/llx.dim]")
    elif not ctx.is_online() and _lite_mode:
        try:
            from llx.launch_config import load_launch_config
            lcfg = load_launch_config()
            if lcfg.get("auto_start_services"):
                from llx.commands.launch import _start_lite_mode
                _start_lite_mode(console, port=5002)
                time.sleep(0.5)
                ctx.refresh_async()
        except Exception as e:
            console.print(f"[llx.warning]Could not start lite server: {e}[/llx.warning]")
    router = SlashRouter(state)

    # Create completer with dynamic completions that can see tool list etc.
    dynamic_getter = _make_dynamic_completions(state)
    completer = make_completer(get_dynamic=dynamic_getter)

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
    history_file = ensure_history_file()
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
                mem = normalize_working_memory(pending.get("working_memory"))
                # Preserve or refresh local cwd/git if not present in old session
                if not mem.get("cwd"):
                    mem["cwd"] = str(state.get("cwd", Path.cwd()))
                if not mem.get("git") or not mem["git"].get("branch"):
                    mem["git"] = get_git_info(Path(mem["cwd"]))
                state["working_memory"] = mem
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
            keep_going = router.dispatch(line)
            if not keep_going:
                break
            continue

        from llx.intent_router import resolve_repl_line

        cli_route = resolve_repl_line(line)
        if cli_route:
            cmd, cmd_args = cli_route
            slash_line = f"/{cmd}"
            if cmd_args:
                slash_line = f"{slash_line} {' '.join(cmd_args)}"
            keep_going = router.dispatch(slash_line)
            if not keep_going:
                break
            continue

        # Chat message
        from llx.utils import parse_file_mentions_with_metadata
        raw_line = line
        line, attachments = parse_file_mentions_with_metadata(line)
        memory = normalize_working_memory(state.get("working_memory"))
        apply_attachments(memory, attachments)
        apply_user_intent(memory, raw_line)
        state["working_memory"] = memory
        _handle_chat(state, ctx, line, raw_message=raw_line, attachments=attachments)
