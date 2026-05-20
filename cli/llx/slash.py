"""Slash-command router for the REPL — maps /command args to handlers."""

import inspect
import os
import shlex
import time
import uuid
from typing import Callable

from llx import output
from llx.command_catalog import COMMAND_META, COMMAND_TREE
from llx.theme import make_console, THEMES, set_active_theme, get_active_theme_name

_HELP_GROUPS: list[tuple[str, list[str]]] = [
    ("Session Commands", ["new", "history", "export", "clear"]),
    ("System Commands", ["health", "status", "doctor", "start", "stop", "dashboard"]),
    ("Data Commands", ["files", "projects", "rules", "agents", "clients", "websites", "tasks"]),
    ("AI Commands", ["search", "models", "generate", "images", "videos", "index", "rag"]),
    ("Memory Commands", ["remember", "memory"]),
    ("Multi-Modal Commands", ["imagine", "video", "voice", "ingest", "agent", "web"]),
    ("Admin Commands", ["jobs", "logs", "backup", "family"]),
    ("Config Commands", ["config", "settings", "theme", "quality"]),
    ("REPL", ["help", "quit", "exit"]),
]


class SlashRouter:
    """Routes /command args to the appropriate handler.

    Two kinds of commands:
    1. Typer-backed — delegates to existing Typer command functions/apps
    2. REPL-only   — implemented inline (/new, /clear, /history, etc.)
    """

    def __init__(self, repl_state: dict):
        self._state = repl_state          # shared mutable dict
        self._console = make_console()

        # command name -> handler callable
        self._commands: dict[str, Callable] = {}

        self._register_repl_commands()
        self._register_typer_commands()

    # ── Public API ────────────────────────────────────────────────

    def get_command_names(self) -> list[str]:
        """Return sorted list of all command names (without leading slash)."""
        return sorted(self._commands.keys())

    def dispatch(self, line: str) -> bool:
        """Parse a slash-command line and route to handler.

        Returns False only if /quit or /exit was invoked (signal REPL to exit).
        Returns True for everything else.
        """
        # Strip leading slash
        raw = line.lstrip("/").strip()
        if not raw:
            self._console.print("[llx.dim]Type /help for available commands.[/llx.dim]")
            return True

        try:
            parts = shlex.split(raw)
        except ValueError as e:
            self._console.print(f"[llx.error]Parse error: {e}[/llx.error]")
            return True

        cmd = parts[0].lower()
        args = parts[1:]

        handler = self._commands.get(cmd)
        if handler is None:
            self._console.print(f"[llx.error]Unknown command: /{cmd}[/llx.error]")
            self._console.print("[llx.dim]Type /help for available commands.[/llx.dim]")
            return True

        try:
            result = handler(args)
            # Only /quit and /exit return False
            if result is False:
                return False
        except Exception as e:
            self._console.print(f"[llx.error]Error in /{cmd}: {e}[/llx.error]")

        return True

    # ── REPL-only command registration ────────────────────────────

    def _register_repl_commands(self):
        """Register REPL-only commands (implemented inline)."""
        self._commands["new"] = self._cmd_new
        self._commands["clear"] = self._cmd_clear
        self._commands["history"] = self._cmd_history
        self._commands["export"] = self._cmd_export
        self._commands["config"] = self._cmd_config
        self._commands["theme"] = self._cmd_theme
        self._commands["help"] = self._cmd_help
        self._commands["quit"] = self._cmd_quit
        self._commands["exit"] = self._cmd_quit
        self._commands["imagine"] = self._cmd_imagine
        self._commands["video"] = self._cmd_video
        self._commands["voice"] = self._cmd_voice
        self._commands["ingest"] = self._cmd_ingest
        self._commands["agent"] = self._cmd_agent
        self._commands["web"] = self._cmd_web
        self._commands["remember"] = self._cmd_remember
        self._commands["memory"] = self._cmd_memory

    # ── Typer-backed command registration ─────────────────────────

    def _register_typer_commands(self):
        """Register Typer-backed commands (lazy imports to avoid circulars)."""
        # Simple commands — direct function call with server/json_out kwargs
        from llx.commands.system import health, status, doctor, start, stop
        from llx.commands.search import search
        from llx.commands.dashboard import dashboard

        simple_commands = {
            "health": health,
            "status": status,
            "doctor": doctor,
            "start": start,
            "stop": stop,
            "search": search,
            "dashboard": dashboard,
        }
        for name, func in simple_commands.items():
            self._register_simple(name, func)

        # Typer sub-apps — dispatch via sys.argv mutation
        from llx.commands.files import files_app
        from llx.commands.projects import projects_app
        from llx.commands.rules import rules_app
        from llx.commands.agents import agents_app
        from llx.commands.generate import generate_app
        from llx.commands.jobs import jobs_app
        from llx.commands.settings import settings_app
        from llx.commands.index import index_app
        from llx.commands.backup import backup_app
        from llx.commands.family import family_app
        from llx.commands.logs import logs_app
        from llx.commands.rag import rag_app
        from llx.commands.clients import clients_app
        from llx.commands.websites import websites_app
        from llx.commands.tasks import tasks_app
        from llx.commands.images import images_app
        from llx.commands.videos import videos_app
        from llx.commands.system import models_app
        from llx.commands.quality import quality_app

        subapps = {
            "files": files_app,
            "projects": projects_app,
            "rules": rules_app,
            "agents": agents_app,
            "generate": generate_app,
            "jobs": jobs_app,
            "settings": settings_app,
            "index": index_app,
            "backup": backup_app,
            "family": family_app,
            "logs": logs_app,
            "rag": rag_app,
            "clients": clients_app,
            "websites": websites_app,
            "tasks": tasks_app,
            "images": images_app,
            "videos": videos_app,
            "models": models_app,
            "quality": quality_app,
        }
        for name, subapp in subapps.items():
            self._register_subapp(name, subapp)

    def _register_simple(self, name: str, func: Callable):
        """Register a simple Typer command (direct function call)."""
        sig = inspect.signature(func)

        def handler(args: list[str]):
            kwargs = {}
            # Inject server/json_out if the function accepts them
            if "server" in sig.parameters:
                kwargs["server"] = self._state.get("server")
            if "json_out" in sig.parameters:
                kwargs["json_out"] = False

            # For search, the first positional arg is 'query'
            # For other commands with positional args, pass them through
            params = list(sig.parameters.values())
            positional = [
                p for p in params
                if p.default is inspect.Parameter.empty
                or (hasattr(p.default, "__class__") and p.default.__class__.__name__ == "ArgumentInfo")
            ]

            # Feed positional args from user input
            pos_idx = 0
            for p in positional:
                if p.name in kwargs:
                    continue
                if pos_idx < len(args):
                    kwargs[p.name] = args[pos_idx]
                    pos_idx += 1

            try:
                func(**kwargs)
            except SystemExit:
                pass
            except Exception as e:
                self._console.print(f"[llx.error]Error: {e}[/llx.error]")

        self._commands[name] = handler

    def _register_subapp(self, name: str, typer_app):
        """Register a Typer sub-app without mutating process argv."""
        def handler(args: list[str]):
            from llx.global_opts import set_global_opts
            from llx.main import app

            server = self._state.get("server")
            set_global_opts(server=server, json_out=False)

            try:
                app(args=[name, *args], standalone_mode=False)
            except SystemExit:
                pass
            except Exception as e:
                self._console.print(f"[llx.error]Error: {e}[/llx.error]")

        self._commands[name] = handler

    # ── REPL-only command implementations ─────────────────────────

    def _cmd_new(self, args: list[str]):
        """Start a new chat session."""
        new_id = str(uuid.uuid4())
        self._state["session_id"] = new_id
        self._state["message_count"] = 0
        self._state["context"] = None
        self._console.print(f"[llx.success]New session started.[/llx.success]")
        self._console.print(f"[llx.dim]Session: {new_id[:8]}...[/llx.dim]")

    def _cmd_clear(self, args: list[str]):
        """Clear the console screen."""
        os.system("cls" if os.name == "nt" else "clear")

    def _cmd_history(self, args: list[str]):
        """List recent sessions or resume one by index."""
        from llx.config import load_sessions

        sessions = load_sessions()

        if not sessions:
            self._console.print("[llx.dim]No session history.[/llx.dim]")
            return

        # If a number was given, resume that session
        if args:
            try:
                idx = int(args[0])
            except ValueError:
                self._console.print("[llx.error]Usage: /history [index][/llx.error]")
                return

            if idx < 0 or idx >= len(sessions):
                self._console.print(f"[llx.error]Index out of range (0-{len(sessions) - 1})[/llx.error]")
                return

            session = sessions[idx]
            self._state["session_id"] = session["id"]
            self._state["message_count"] = session.get("message_count", 0)
            self._state["context"] = None
            self._console.print(f"[llx.success]Resumed session {session['id'][:8]}...[/llx.success]")
            preview = session.get("preview", "")
            if preview:
                self._console.print(f"[llx.dim]{preview}[/llx.dim]")
            return

        # List recent sessions
        self._console.print("\n[llx.brand_bright]Recent Sessions:[/llx.brand_bright]")
        for i, session in enumerate(sessions[:20]):
            preview = session.get("preview", "(no preview)")
            ts = session.get("timestamp")
            age = _format_age(ts) if ts else "?"
            msgs = session.get("message_count", 0)
            current = " [llx.success]*[/llx.success]" if session["id"] == self._state.get("session_id") else ""
            self._console.print(
                f"  [llx.accent]{i:>2}[/llx.accent]  "
                f"[llx.dim]{age:<12}[/llx.dim] "
                f"[llx.dim]({msgs} msgs)[/llx.dim]  "
                f"{preview}{current}"
            )
        self._console.print(f"\n[llx.dim]Usage: /history <index> to resume a session[/llx.dim]\n")

    def _cmd_export(self, args: list[str]):
        """Export current session as markdown."""
        session_id = self._state.get("session_id")
        if not session_id:
            self._console.print("[llx.error]No active session.[/llx.error]")
            return

        server = self._state.get("server")
        try:
            from llx.client import get_client, LlxError, LlxConnectionError
            client = get_client(server)
            data = client.get(f"/api/enhanced-chat/{session_id}/history")
        except Exception as e:
            self._console.print(f"[llx.error]Failed to fetch session history: {e}[/llx.error]")
            return

        # Format as markdown
        messages = data.get("messages", data.get("data", []))
        if isinstance(messages, dict):
            messages = messages.get("messages", [])

        lines = [f"# Chat Session {session_id[:8]}", ""]
        for msg in messages:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", msg.get("message", ""))
            ts = msg.get("timestamp", "")
            lines.append(f"## {role}")
            if ts:
                lines.append(f"*{ts}*")
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")

        md_text = "\n".join(lines)

        if args:
            # Write to file
            file_path = args[0]
            try:
                with open(file_path, "w") as f:
                    f.write(md_text)
                self._console.print(f"[llx.success]Session exported to {file_path}[/llx.success]")
            except OSError as e:
                self._console.print(f"[llx.error]Failed to write file: {e}[/llx.error]")
        else:
            # Print to console
            from rich.markdown import Markdown
            self._console.print(Markdown(md_text))

    def _cmd_config(self, args: list[str]):
        """Show or set configuration values."""
        from llx.config import load_config, save_config

        config = load_config()

        if not args:
            # Show all config
            output.print_kv(
                {k: str(v) for k, v in config.items()},
                title="Configuration",
            )
            return

        key = args[0]

        if len(args) == 1:
            # Show single key
            val = config.get(key)
            if val is None:
                self._console.print(f"[llx.dim]{key} is not set[/llx.dim]")
            else:
                output.print_kv({key: str(val)})
            return

        # Set key = value
        value_str = " ".join(args[1:])
        # Parse booleans and integers
        if value_str.lower() in ("true", "false"):
            parsed = value_str.lower() == "true"
        elif value_str.isdigit():
            parsed = int(value_str)
        elif value_str.lower() == "null" or value_str.lower() == "none":
            parsed = None
        else:
            parsed = value_str

        config[key] = parsed
        save_config(config)
        self._console.print(f"[llx.success]Set {key} = {parsed}[/llx.success]")

    def _cmd_theme(self, args: list[str]):
        """List or switch CLI themes."""
        if not args:
            # List available themes
            current = get_active_theme_name()
            self._console.print("\n[llx.brand_bright]Available themes:[/llx.brand_bright]")
            for name, data in THEMES.items():
                marker = " [llx.success]*[/llx.success]" if name == current else ""
                self._console.print(
                    f"  [llx.accent]{name:<12}[/llx.accent] "
                    f"[llx.dim]{data['description']}[/llx.dim]{marker}"
                )
            self._console.print(f"\n[llx.dim]Usage: /theme <name>[/llx.dim]\n")
            return

        name = args[0].lower()
        if name not in THEMES:
            self._console.print(f"[llx.error]Unknown theme: {name}[/llx.error]")
            self._console.print(f"[llx.dim]Available: {', '.join(THEMES.keys())}[/llx.dim]")
            return

        set_active_theme(name)

        # Persist to config
        from llx.config import set_theme_name
        set_theme_name(name)

        # Refresh consoles
        self._console = make_console()
        output.refresh_theme()

        label = THEMES[name]["label"]
        self._console.print(f"[llx.success]Theme switched to {label}[/llx.success]\n")

    def _cmd_help(self, args: list[str]):
        """Print comprehensive help for all commands."""
        self._console.print("[llx.brand_bright]Guaardvark REPL[/llx.brand_bright]\n")
        self._console.print("[llx.dim]In chat mode, type a message to chat with the LLM.[/llx.dim]")
        self._console.print("[llx.dim]Use slash commands to manage the system.[/llx.dim]\n")

        for section_title, commands in _HELP_GROUPS:
            self._console.print(f"[llx.brand_bright]{section_title}:[/llx.brand_bright]")
            for name in commands:
                if name not in self._commands:
                    continue
                meta = COMMAND_META.get(name, "")
                sub = COMMAND_TREE.get(name, [])
                suffix = f" ({', '.join(sub)})" if sub else ""
                self._console.print(
                    f"  [llx.accent]/{name}[/llx.accent]  [llx.dim]{meta}{suffix}[/llx.dim]"
                )
            self._console.print()

    def _cmd_imagine(self, args: list[str]):
        """Generate an image from a text prompt."""
        if not args:
            self._console.print("[llx.error]Usage: /imagine <prompt>[/llx.error]")
            self._console.print("[llx.dim]Example: /imagine a sunset over mountains[/llx.dim]")
            return

        prompt = " ".join(args)
        server = self._state.get("server")

        try:
            from llx.client import get_client, LlxError, LlxConnectionError
            client = get_client(server)
            data = client.post("/api/batch-image/generate/prompts", json={
                "prompts": [prompt],
                "steps": 20,
                "width": 512,
                "height": 512,
            })
            result = data.get("data", data)
            batch_id = result.get("batch_id", "unknown")
            self._console.print(f"[llx.success]Image generation started[/llx.success]")
            self._console.print(f"[llx.dim]Batch: {batch_id}[/llx.dim]")
            self._console.print(f"[llx.dim]Track: /images status {batch_id}[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]Image generation failed: {e}[/llx.error]")

    def _cmd_video(self, args: list[str]):
        """Generate a video from a text prompt."""
        if not args:
            self._console.print("[llx.error]Usage: /video <prompt>[/llx.error]")
            self._console.print("[llx.dim]Example: /video a cat playing piano[/llx.dim]")
            return

        prompt = " ".join(args)
        server = self._state.get("server")

        try:
            from llx.client import get_client, LlxError, LlxConnectionError
            client = get_client(server)
            data = client.post("/api/batch-video/generate/text", json={
                "prompts": [prompt],
            })
            result = data.get("data", data)
            batch_id = result.get("batch_id", "unknown")
            self._console.print(f"[llx.success]Video generation started[/llx.success]")
            self._console.print(f"[llx.dim]Batch: {batch_id}[/llx.dim]")
            self._console.print(f"[llx.dim]Track: /videos status {batch_id}[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]Video generation failed: {e}[/llx.error]")

    def _cmd_voice(self, args: list[str]):
        """Convert text to speech."""
        if not args:
            self._console.print("[llx.error]Usage: /voice <text>[/llx.error]")
            self._console.print("[llx.dim]Example: /voice Hello world[/llx.dim]")
            return

        text = " ".join(args)
        server = self._state.get("server")

        try:
            from llx.client import get_client, LlxError, LlxConnectionError
            client = get_client(server)
            data = client.post("/api/voice/text-to-speech", json={
                "text": text,
            })
            audio_url = data.get("audio_url", "")
            filename = data.get("filename", "output.wav")
            self._console.print(f"[llx.success]Audio generated: {filename}[/llx.success]")
            if audio_url:
                self._console.print(f"[llx.dim]{server}{audio_url}[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]TTS failed: {e}[/llx.error]")

    def _cmd_ingest(self, args: list[str]):
        """Index files or a directory for RAG-enhanced chat."""
        if not args:
            self._console.print("[llx.error]Usage: /ingest <path>[/llx.error]")
            self._console.print("[llx.dim]Example: /ingest ~/Documents/research[/llx.dim]")
            return

        path = " ".join(args)
        server = self._state.get("server")

        try:
            from llx.client import get_client, LlxError, LlxConnectionError
            client = get_client(server)
            data = client.post("/api/index/bulk", json={
                "paths": [path],
            })
            result = data.get("data", data)
            total = result.get("total_documents", 0)
            job_id = result.get("job_id", "")
            self._console.print(f"[llx.success]Indexing started: {total} documents[/llx.success]")
            if job_id:
                self._console.print(f"[llx.dim]Job: {job_id}[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]Indexing failed: {e}[/llx.error]")

    def _cmd_agent(self, args: list[str]):
        """Toggle agent mode (tool-using autonomous agent)."""
        current = self._state.get("agent_mode", False)
        self._state["agent_mode"] = not current
        # Mirror to agent_screen_active so chat POSTs flip the backend gate.
        # This tells the backend to route Gemma4 through its screen-action
        # direct path and to expose desktop/agent-control tools to every model.
        self._state["agent_screen_active"] = self._state["agent_mode"]

        if self._state["agent_mode"]:
            self._console.print("[llx.success]Agent mode ON[/llx.success]")
            self._console.print("[llx.dim]Chat messages will use tool-calling agent.[/llx.dim]")
        else:
            self._console.print("[llx.dim]Agent mode OFF — back to standard chat.[/llx.dim]")

    def _cmd_web(self, args: list[str]):
        """Open the Guaardvark web UI in the default browser."""
        import webbrowser
        url = "http://localhost:5175"
        webbrowser.open(url)
        self._console.print(f"[llx.success]Opening {url}[/llx.success]")

    def _cmd_remember(self, args: list[str]):
        """Save something to memory. Usage: /remember <text>"""
        if not args:
            self._console.print("[llx.error]Usage: /remember <text to save>[/llx.error]")
            self._console.print("[llx.dim]Example: /remember The API key for Stripe is in .env[/llx.dim]")
            return

        content = " ".join(args)
        server = self._state.get("server")
        session_id = self._state.get("session_id")

        try:
            from llx.client import get_client
            client = get_client(server)
            data = client.post("/api/memory", json={
                "content": content,
                "source": "cli",
                "session_id": session_id,
                "type": "note",
            })
            result = data.get("data", data)
            mem_id = result.get("memory", {}).get("id", "")
            self._console.print(f"[llx.success]Saved to memory[/llx.success]")
            if mem_id:
                self._console.print(f"[llx.dim]ID: {mem_id}[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]Failed to save: {e}[/llx.error]")

    def _cmd_memory(self, args: list[str]):
        """Manage memories. Usage: /memory [list|search <query>|delete <id>|clear]"""
        server = self._state.get("server")
        sub = args[0].lower() if args else "list"

        if sub == "list":
            try:
                from llx.client import get_client
                client = get_client(server)
                data = client.get("/api/memory", limit=20)
                result = data.get("data", data)
                memories = result.get("memories", [])
                total = result.get("total", len(memories))

                if not memories:
                    self._console.print("[llx.dim]No memories saved yet. Use /remember <text> to save one.[/llx.dim]")
                    return

                self._console.print(f"\n[llx.brand_bright]Saved Memories ({total} total):[/llx.brand_bright]")
                for m in memories:
                    mid = m.get("id", "?")
                    content = m.get("content", "")[:80]
                    source = m.get("source", "?")
                    created = m.get("created_at", "")[:10]
                    self._console.print(
                        f"  [llx.accent]{mid}[/llx.accent]  "
                        f"[llx.dim]{created} ({source})[/llx.dim]  "
                        f"{content}"
                    )
                self._console.print()
            except Exception as e:
                self._console.print(f"[llx.error]Failed to list memories: {e}[/llx.error]")

        elif sub == "search" and len(args) > 1:
            query = " ".join(args[1:])
            try:
                from llx.client import get_client
                client = get_client(server)
                data = client.get("/api/memory", search=query, limit=20)
                result = data.get("data", data)
                memories = result.get("memories", [])

                if not memories:
                    self._console.print(f"[llx.dim]No memories matching '{query}'[/llx.dim]")
                    return

                self._console.print(f"\n[llx.brand_bright]Memories matching '{query}':[/llx.brand_bright]")
                for m in memories:
                    mid = m.get("id", "?")
                    content = m.get("content", "")[:80]
                    self._console.print(f"  [llx.accent]{mid}[/llx.accent]  {content}")
                self._console.print()
            except Exception as e:
                self._console.print(f"[llx.error]Search failed: {e}[/llx.error]")

        elif sub == "delete" and len(args) > 1:
            mem_id = args[1]
            try:
                from llx.client import get_client
                client = get_client(server)
                client.delete(f"/api/memory/{mem_id}")
                self._console.print(f"[llx.success]Deleted memory {mem_id}[/llx.success]")
            except Exception as e:
                self._console.print(f"[llx.error]Delete failed: {e}[/llx.error]")

        elif sub == "clear":
            try:
                from llx.client import get_client
                client = get_client(server)
                client.delete("/api/memory/clear")
                self._console.print("[llx.success]All memories cleared[/llx.success]")
            except Exception as e:
                self._console.print(f"[llx.error]Clear failed: {e}[/llx.error]")

        else:
            self._console.print("[llx.error]Usage: /memory [list|search <query>|delete <id>|clear][/llx.error]")

    def _cmd_quit(self, args: list[str]):
        """Exit the REPL."""
        self._console.print("[llx.dim]Goodbye.[/llx.dim]")
        return False


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
    elif delta < 604800:
        days = int(delta / 86400)
        return f"{days}d ago"
    elif delta < 2592000:
        weeks = int(delta / 604800)
        return f"{weeks}w ago"
    else:
        months = int(delta / 2592000)
        return f"{months}mo ago"
