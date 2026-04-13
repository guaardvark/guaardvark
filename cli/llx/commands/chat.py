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

    if stream:
        _chat_streaming(session_id, full_message, no_rag, server, json_out, project_id=project)
    else:
        _chat_sync(session_id, full_message, no_rag, server, json_out, project_id=project)


def _chat_sync(session_id: str, message: str, no_rag: bool, server: str | None, json_out: bool, project_id: int | None = None):
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
            output.print_json({
                "session_id": session_id,
                "response": response_text,
                "elapsed": round(elapsed, 2),
            })
        else:
            console.print()
            console.print(Markdown(response_text))
            console.print(f"\n[llx.dim]Session: {session_id[:8]}  |  {elapsed:.1f}s[/llx.dim]")

        save_session(session_id, message[:80])

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
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
            output.print_json({"session_id": session_id, "messages": messages})
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
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


def _chat_streaming(session_id: str, message: str, no_rag: bool, server: str | None, json_out: bool, project_id: int | None = None):
    """Send chat via /api/chat/unified with Socket.IO streaming."""
    import signal

    try:
        client = get_client(server)
        from llx.streaming import LlxStreamer
        streamer = LlxStreamer(server_url=client.server_url)

        response_parts = []
        start_time = time.time()

        def on_token(content):
            response_parts.append(content)

        def on_complete(data):
            pass

        def on_error(msg):
            response_parts.append(f"\n[ERROR] {msg}")

        def on_tool_output_chunk(data):
            chunk = data.get("chunk", "")
            if not json_out and not output.is_pipe():
                console.print(chunk, end="")

        # Connect streaming first. Approval requests are pulled from the
        # streamer in the main render loop below — never from the socketio
        # receive thread, which would deadlock the event stream.
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
            streamer.abort(session_id)
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
        client.post("/api/chat/unified", json=body)

        # Stream output
        if json_out or output.is_pipe():
            # Non-interactive mode: auto-reject any approval requests
            streamer.wait_for_completion(approval_handler=None, timeout=300)
            full_response = "".join(response_parts)
            if json_out:
                output.print_json({
                    "session_id": session_id,
                    "response": full_response,
                    "elapsed": round(time.time() - start_time, 2),
                })
            else:
                print(full_response)
        else:
            deadline = time.time() + 300
            with Live("", console=console, refresh_per_second=15, transient=False) as live:
                last_render_time = 0.0
                while not streamer._done.is_set() and time.time() < deadline:
                    # Approval prompts must run in the main thread, not the
                    # socketio receive thread. Drain any pending request
                    # before doing the next render tick.
                    pending = streamer.pop_pending_approval()
                    if pending is not None:
                        live.stop()
                        tools_str = ", ".join(pending.get("tools", [])) or "(unknown tools)"
                        console.print(f"\n[bold yellow]\u26a0 Approval Required[/bold yellow]")
                        console.print(f"  Tool(s): [bold]{tools_str}[/bold]")
                        try:
                            approved = typer.confirm("Allow execution?", default=False)
                        except (KeyboardInterrupt, EOFError, typer.Abort):
                            streamer.send_approval_response(session_id, False)
                            streamer.abort(session_id)
                            console.print("[red]\u2717 Aborted.[/red]\n")
                            live.start()
                            break
                        streamer.send_approval_response(session_id, approved)
                        console.print(
                            "[green]\u2713 Approved.[/green]\n" if approved
                            else "[red]\u2717 Rejected.[/red]\n"
                        )
                        live.start()
                        continue

                    current = "".join(response_parts)
                    now = time.time()
                    if current and now - last_render_time > 0.05:
                        live.update(Markdown(current))
                        last_render_time = now
                    streamer._done.wait(timeout=0.05)
                current = "".join(response_parts)
                if current:
                    live.update(Markdown(current))

            elapsed = time.time() - start_time
            console.print(f"\n[llx.dim]Session: {session_id[:8]}  |  {elapsed:.1f}s[/llx.dim]")

        signal.signal(signal.SIGINT, original_sigint)
        save_session(session_id, message[:80])
        streamer.disconnect()

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
