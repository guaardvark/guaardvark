"""Chat command — conversation with the LLM."""

import sys
import uuid
import time
from pathlib import Path

import typer
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.live import Live

from llx.client import get_client, LlxError, LlxConnectionError
from llx.config import save_session, get_last_session_id, load_sessions
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()


def chat(
    message: str = typer.Argument(None, help="Message to send"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Continue last conversation"),
    session: str = typer.Option(None, "--session", help="Resume a specific session ID"),
    list_sessions: bool = typer.Option(False, "--list", "-l", help="List recent chat sessions"),
    export: bool = typer.Option(False, "--export", "-e", help="Export conversation to markdown (requires --session or --resume)"),
    output_file: Path | None = typer.Option(None, "--output", "-o", help="Write export to file (default: stdout)"),
    project: int = typer.Option(None, "--project", "-p", help="Scope RAG context to a project ID"),
    no_rag: bool = typer.Option(False, "--no-rag", help="Disable RAG context"),
    stream: bool = typer.Option(False, "--stream", help="Use Socket.IO streaming (experimental)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Chat with the LLM. Supports piped input and session management."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    # List sessions
    if list_sessions:
        sessions = load_sessions()
        if not sessions:
            output.print_warning("No chat sessions found.")
            return
        rows = [{"id": s["id"][:8] + "...", "full_id": s["id"], "preview": s["preview"]} for s in sessions]
        output.print_table(rows, columns=["id", "preview"], title="Recent Sessions")
        return

    # Export conversation
    if export:
        export_session_id = session or get_last_session_id()
        if not export_session_id:
            output.print_error("Export requires --session ID or --resume (no previous session).")
            raise typer.Exit(1)
        _chat_export(export_session_id, server, output_file, json_out)
        return

    # Determine session ID
    if session:
        session_id = session
    elif resume:
        session_id = get_last_session_id()
        if not session_id:
            output.print_error("No previous session to resume.")
            raise typer.Exit(1)
    else:
        session_id = str(uuid.uuid4())

    # Read piped input
    piped_input = ""
    if not sys.stdin.isatty():
        piped_input = sys.stdin.read()

    # Build final message
    from llx.utils import parse_file_mentions
    
    if piped_input and message:
        full_message = f"{message}\n\n---\n{piped_input}"
    elif piped_input:
        full_message = piped_input
    elif message:
        full_message = message
    else:
        output.print_error("No message provided. Usage: guaardvark chat \"your message\"")
        raise typer.Exit(1)

    full_message = parse_file_mentions(full_message)

    # Headless project analysis support: detect leading absolute path in message
    # (for "guaardvark --non-interactive chat '/path' analyze this project")
    # Set project context so backend gets project_root for GUAARDVARK.md etc.
    project_root = None
    import re
    from pathlib import Path
    from llx.utils import populate_project_context, find_project_root
    from llx.working_memory import empty_working_memory
    path_match = re.match(r"^['\"]?(\/[^\s'\"]+)['\"]?\s*(.*)$", full_message)
    if path_match:
        candidate = path_match.group(1)
        if Path(candidate).is_dir():
            try:
                os.chdir(Path(candidate).expanduser().resolve())
            except:
                pass
            rest = path_match.group(2).strip() or "analyze this project"
            full_message = rest
            project_root = str(find_project_root())
            # Populate for local context if needed
            try:
                mem = empty_working_memory()
                populate_project_context(mem, Path(project_root))
            except:
                pass

    if stream:
        _chat_streaming(session_id, full_message, no_rag, server, json_out, project_id=project, project_root=project_root)
    else:
        _chat_sync(session_id, full_message, no_rag, server, json_out, project_id=project, project_root=project_root)


def _chat_sync(session_id: str, message: str, no_rag: bool, server: str | None, json_out: bool, project_id: int | None = None, project_root: str | None = None):
    """Send chat via synchronous /api/enhanced-chat endpoint."""
    try:
        client = get_client(server)
        start_time = time.time()

        body = {
            "session_id": session_id,
            "message": message,
            "use_rag": not no_rag,
        }
        if project_id:
            body["project_id"] = project_id
        if project_root:
            body["project_root"] = project_root
            # Build rich context like REPL for external project analysis
            try:
                from llx.utils import populate_project_context, build_cli_context as build_ctx
                from llx.working_memory import empty_working_memory
                mem = empty_working_memory()
                populate_project_context(mem, Path(project_root))
                body["context"] = build_ctx("", mem)
                body["cli_working_memory"] = mem
            except Exception:
                pass

        # Show spinner in interactive mode
        if not json_out and not output.is_pipe():
            with Live(Spinner("dots", text="[llx.dim]Thinking...[/llx.dim]"), console=console, transient=True):
                data = client.post("/api/enhanced-chat", json=body)
        else:
            data = client.post("/api/enhanced-chat", json=body)

        elapsed = time.time() - start_time

        # Extract response text from API response
        result = data.get("data", data)
        if isinstance(result, str):
            response_text = result
        else:
            response_text = (
                result.get("response", "")
                or result.get("message", "")
                or result.get("content", "")
                or str(result)
            )

        if json_out or output.is_pipe():
            output.print_json(
                {
                    "status": "success",
                    "data": {
                        "session_id": session_id,
                        "response": response_text,
                        "elapsed": round(elapsed, 2),
                    },
                }
            )
        else:
            console.print()
            console.print(Markdown(response_text))
            console.print(f"\n[llx.dim]Session: {session_id[:8]}  |  {elapsed:.1f}s[/llx.dim]")

        save_session(session_id, message[:80])

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)


def _chat_export(session_id: str, server: str | None, output_file: Path | None, json_out: bool):
    """Export conversation history to markdown."""
    try:
        client = get_client(server)
        data = client.get(f"/api/enhanced-chat/{session_id}/history", limit=500)
        messages = data.get("messages", [])
        if isinstance(messages, dict):
            messages = []

        if json_out or output.is_pipe():
            output.print_json(
                {"status": "success", "data": {"session_id": session_id, "messages": messages}}
            )
            return

        lines = [f"# Chat Export — Session {session_id[:8]}...", ""]
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            prefix = "## User" if role == "user" else "## Assistant"
            if ts:
                lines.append(f"{prefix} ({ts})")
            else:
                lines.append(prefix)
            lines.append("")
            lines.append(content.strip())
            lines.append("")

        markdown = "\n".join(lines)
        if output_file:
            output_file.write_text(markdown, encoding="utf-8")
            output.print_success(f"Exported {len(messages)} messages to {output_file}")
        else:
            print(markdown)

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)


def _chat_streaming(session_id: str, message: str, no_rag: bool, server: str | None, json_out: bool, project_id: int | None = None, project_root: str | None = None):
    """Send chat via /api/chat/unified with Socket.IO streaming."""
    import signal

    try:
        client = get_client(server)
        from llx.streaming import LlxStreamer
        streamer = LlxStreamer(server_url=client.server_url)

        response_parts = []
        start_time = time.time()
        live_holder = {"live": None}

        def on_token(content):
            response_parts.append(content)
            live = live_holder.get("live")
            if live is not None:
                try:
                    live.update(Markdown("".join(response_parts)))
                except Exception:
                    pass

        def on_complete(data):
            pass

        def on_error(msg):
            response_parts.append(f"\n[ERROR] {msg}")

        def on_tool_output_chunk(data):
            chunk = data.get("chunk", "")
            if not json_out and not output.is_pipe():
                console.print(chunk, end="")

        # Connect streaming first. Approval requests are pulled from the
        # streamer in wait_for_completion — never from the socketio receive
        # thread, which would deadlock the event stream.
        streamer.stream_chat(
            session_id=session_id,
            on_token=on_token,
            on_tool_output_chunk=on_tool_output_chunk,
            on_complete=on_complete,
            on_error=on_error,
        )

        # Handle Ctrl+C
        original_sigint = signal.getsignal(signal.SIGINT)

        def sigint_handler(sig, frame):
            streamer.hard_abort(session_id, client)
            console.print("\n[llx.warning]Aborted.[/llx.warning]")
            streamer.disconnect()
            signal.signal(signal.SIGINT, original_sigint)
            raise typer.Exit(0)

        signal.signal(signal.SIGINT, sigint_handler)

        # Post the message to unified chat (streaming endpoint).
        # One-shot `guaardvark chat` has no persistent /agent toggle context,
        # so agent_screen_active defaults to False — backend routes through
        # the normal ReACT path with web/tool access, not screen actions.
        body = {
            "session_id": session_id,
            "message": message,
            "options": {"use_rag": not no_rag, "agent_screen_active": False},
        }
        if project_id:
            body["project_id"] = project_id
        if project_root:
            body["project_root"] = project_root
            try:
                from llx.utils import populate_project_context, build_cli_context as build_ctx
                from llx.working_memory import empty_working_memory
                mem = empty_working_memory()
                populate_project_context(mem, Path(project_root))
                body["options"]["context"] = build_ctx("", mem)
                body["options"]["cli_working_memory"] = mem
            except Exception:
                pass

        for attempt in range(2):
            try:
                client.post("/api/chat/unified", json=body)
                break
            except LlxError as e:
                if e.status_code == 409 and attempt == 0:
                    try:
                        client.abort_session(session_id)
                    except Exception:
                        pass
                    continue
                raise

        def _approval_handler(pending):
            from llx.working_memory import extract_approval_targets

            tools_str = ", ".join(pending.get("tools", [])) or "(unknown tools)"
            targets = extract_approval_targets(pending)
            console.print(f"\n[bold yellow]\u26a0 Approval Required[/bold yellow]")
            console.print(f"  Tool(s): [bold]{tools_str}[/bold]")
            if targets:
                console.print(f"  Actual target(s): [bold]{', '.join(targets)}[/bold]")
            try:
                approved = typer.confirm("Allow execution?", default=False)
            except (KeyboardInterrupt, EOFError, typer.Abort):
                console.print("[red]\u2717 Aborted.[/red]\n")
                raise KeyboardInterrupt
            console.print(
                "[green]\u2713 Approved.[/green]\n" if approved
                else "[red]\u2717 Rejected.[/red]\n"
            )
            return approved

        # Stream output
        if json_out or output.is_pipe():
            completed = streamer.wait_for_completion(approval_handler=None)
            if not completed:
                streamer.hard_abort(session_id, client)
            full_response = "".join(response_parts)
            if json_out:
                output.print_json(
                    {
                        "status": "success" if completed else "timeout",
                        "data": {
                            "session_id": session_id,
                            "response": full_response,
                            "elapsed": round(time.time() - start_time, 2),
                        },
                    }
                )
            else:
                print(full_response)
        else:
            completed = False
            try:
                with Live("", console=console, refresh_per_second=15, transient=False) as live:
                    live_holder["live"] = live
                    completed = streamer.wait_for_completion(
                        approval_handler=_approval_handler,
                    )
                    current = "".join(response_parts)
                    if current:
                        live.update(Markdown(current))
            except KeyboardInterrupt:
                streamer.hard_abort(session_id, client)
                console.print("[llx.dim]Chat aborted.[/llx.dim]")
                completed = True
            finally:
                live_holder["live"] = None

            if not completed:
                streamer.hard_abort(session_id, client)
                console.print(
                    "\n[llx.error]No response after 5 minutes of silence — session aborted.[/llx.error]"
                )

            elapsed = time.time() - start_time
            console.print(f"\n[llx.dim]Session: {session_id[:8]}  |  {elapsed:.1f}s[/llx.dim]")

        signal.signal(signal.SIGINT, original_sigint)
        save_session(session_id, message[:80])
        streamer.disconnect()

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)
