"""Slash-command router for the REPL — maps /command args to handlers."""

import inspect
import os
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Callable

import click
import typer

from llx import output
from llx.command_catalog import COMMAND_META, COMMAND_TREE, suggest_command
from llx.lite_mode import lite_mode_block_message
from llx.theme import make_console, THEMES, set_active_theme, get_active_theme_name, get_theme_names
from llx.typer_utils import build_typer_kwargs, format_command_usage


def _subapp_usage(name: str) -> str:
    subs = COMMAND_TREE.get(name, [])
    if subs:
        return f"Usage: /{name} {'|'.join(subs)}"
    return f"Usage: /{name}"

_HELP_GROUPS: list[tuple[str, list[str]]] = [
    ("Session Commands", ["new", "abort", "history", "export", "clear"]),
    ("System Commands", ["health", "status", "doctor", "start", "stop", "dashboard"]),
    ("Data Commands", ["files", "projects", "rules", "agents", "clients", "websites", "tasks"]),
    ("AI Commands", ["search", "models", "generate", "images", "videos", "index", "rag"]),
    ("Memory Commands", ["remember", "memory"]),
    ("Local Coding (agentic)", ["ls", "cd", "pwd", "read", "grep", "edit", "run", "test", "todo", "diff", "apply", "undo"]),
    ("Backend Tools", ["tools", "tool"]),
    ("Context & State", ["context", "suggest", "analyze", "init", "load", "skills"]),
    ("Multi-Modal Commands", ["imagine", "video", "voice", "ingest", "agent", "web"]),
    ("Admin Commands", ["jobs", "outreach", "logs", "backup", "family", "recipes"]),
    ("Studio Commands", ["plugins", "gpu", "audio", "swarm", "lessons", "mcp"]),
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
            matches = suggest_command(cmd)
            if matches:
                listed = ", ".join(f"/{m}" for m in matches)
                self._console.print(f"[llx.dim]Did you mean {listed}?[/llx.dim]")
            else:
                self._console.print("[llx.dim]Type /help for available commands.[/llx.dim]")
            return True

        try:
            result = handler(args)
            # Only /quit and /exit return False
            if result is False:
                return False
        except (click.ClickException, click.exceptions.Exit) as e:
            code = getattr(e, "exit_code", 1)
            if code not in (0, None):
                msg = str(e).strip()
                if msg:
                    self._console.print(f"[llx.error]{msg}[/llx.error]")
        except SystemExit as e:
            if e.code not in (0, None):
                self._console.print(f"[llx.error]Command failed (exit {e.code})[/llx.error]")
        except Exception as e:
            self._console.print(f"[llx.error]Error in /{cmd}: {e}[/llx.error]")

        return True

    # ── REPL-only command registration ────────────────────────────

    def _register_repl_commands(self):
        """Register REPL-only commands (implemented inline)."""
        self._commands["new"] = self._cmd_new
        self._commands["abort"] = self._cmd_abort
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

        # Local agentic coding commands (new)
        self._commands["ls"] = self._cmd_ls
        self._commands["cd"] = self._cmd_cd
        self._commands["pwd"] = self._cmd_pwd
        self._commands["read"] = self._cmd_read
        self._commands["grep"] = self._cmd_grep
        self._commands["edit"] = self._cmd_edit
        self._commands["run"] = self._cmd_run
        self._commands["test"] = self._cmd_test
        self._commands["todo"] = self._cmd_todo
        self._commands["diff"] = self._cmd_diff
        self._commands["apply"] = self._cmd_apply
        self._commands["undo"] = self._cmd_undo

        # Existing agent tools surface (important: there is a large backend tool registry)
        self._commands["tools"] = self._cmd_tools
        self._commands["tool"] = self._cmd_tool
        self._commands["context"] = self._cmd_context
        self._commands["suggest"] = self._cmd_suggest
        self._commands["analyze"] = self._cmd_analyze
        self._commands["init"] = self._cmd_init
        self._commands["load"] = self._cmd_load
        self._commands["skills"] = self._cmd_skills

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
        from llx.commands.outreach import outreach_app
        from llx.commands.recipes import recipes_app
        from llx.commands.plugins import plugins_app
        from llx.commands.gpu import gpu_app
        from llx.commands.mcp import mcp_app
        from llx.commands.audio import audio_app
        from llx.commands.swarm import swarm_app
        from llx.commands.lessons import lessons_app

        subapps = {
            "files": files_app,
            "projects": projects_app,
            "rules": rules_app,
            "agents": agents_app,
            "generate": generate_app,
            "jobs": jobs_app,
            "outreach": outreach_app,
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
            "recipes": recipes_app,
            "plugins": plugins_app,
            "gpu": gpu_app,
            "mcp": mcp_app,
            "audio": audio_app,
            "swarm": swarm_app,
            "lessons": lessons_app,
        }
        for name, subapp in subapps.items():
            self._register_subapp(name, subapp)

    def _register_simple(self, name: str, func: Callable):
        """Register a simple Typer command (direct function call)."""
        sig = inspect.signature(func)

        def handler(args: list[str]):
            injected = {}
            if "server" in sig.parameters:
                injected["server"] = self._state.get("server")
            if "json_out" in sig.parameters:
                injected["json_out"] = False

            kwargs = build_typer_kwargs(sig, args, injected)
            if kwargs is None:
                self._console.print(f"[llx.error]{format_command_usage(name, func)}[/llx.error]")
                return

            try:
                func(**kwargs)
            except SystemExit as e:
                if e.code not in (0, None):
                    self._console.print(f"[llx.error]Command failed (exit {e.code})[/llx.error]")
            except (click.exceptions.Exit, typer.Exit) as e:
                code = getattr(e, "exit_code", 1)
                if code not in (0, None):
                    self._console.print(f"[llx.error]Command failed (exit {code})[/llx.error]")
            except Exception as e:
                msg = str(e).strip()
                if msg:
                    self._console.print(f"[llx.error]Error: {msg}[/llx.error]")

        self._commands[name] = handler

    def _register_subapp(self, name: str, typer_app):
        """Register a Typer sub-app, invoking the sub-typer directly (not root app)."""
        def handler(args: list[str]):
            from typer.main import get_command

            from llx.global_opts import set_global_opts

            if not args:
                self._console.print(f"[llx.dim]{_subapp_usage(name)}[/llx.dim]")
                return

            blocked = lite_mode_block_message(name)
            if blocked:
                self._console.print(f"[llx.error]{blocked}[/llx.error]")
                return

            server = self._state.get("server")
            set_global_opts(server=server, json_out=False)

            if name == "generate" and args and args[0] == "image" and len(args) > 1:
                self._console.print(
                    "[llx.dim]Hint: use /imagine for image prompts, not /generate image[/llx.dim]"
                )

            try:
                get_command(typer_app)(
                    args=args,
                    standalone_mode=False,
                    prog_name=f"guaardvark {name}",
                )
            except SystemExit as e:
                if e.code not in (0, None):
                    self._console.print(f"[llx.error]Command failed (exit {e.code})[/llx.error]")
            except click.exceptions.Exit as e:
                if e.exit_code not in (0, None):
                    self._console.print(f"[llx.error]Command failed (exit {e.exit_code})[/llx.error]")
            except click.ClickException as e:
                msg = str(e).strip()
                if msg:
                    self._console.print(f"[llx.error]{msg}[/llx.error]")
            except Exception as e:
                msg = str(e).strip()
                if msg:
                    self._console.print(f"[llx.error]Error: {msg}[/llx.error]")

        self._commands[name] = handler

    # ── REPL-only command implementations ─────────────────────────

    def _cmd_new(self, args: list[str]):
        """Start a new chat session."""
        from llx.working_memory import empty_working_memory

        new_id = str(uuid.uuid4())
        self._state["session_id"] = new_id
        self._state["message_count"] = 0
        self._state["context"] = None
        self._state["working_memory"] = empty_working_memory()
        self._console.print(f"[llx.success]New session started.[/llx.success]")
        self._console.print(f"[llx.dim]Session: {new_id[:8]}...[/llx.dim]")

    def _cmd_abort(self, args: list[str]):
        """Hard-abort the in-flight chat for the current session."""
        session_id = self._state.get("session_id")
        if not session_id:
            self._console.print("[llx.error]No active session.[/llx.error]")
            return

        server = self._state.get("server")
        try:
            from llx.client import get_client
            client = get_client(server)
            result = client.abort_session(session_id)
            cleared = result.get("inflight_cleared")
            self._console.print("[llx.success]Abort requested.[/llx.success]")
            if cleared:
                self._console.print(
                    "[llx.dim]In-flight request cleared — you can send another message.[/llx.dim]"
                )
            else:
                self._console.print(
                    "[llx.dim]No in-flight request was registered; abort flag set.[/llx.dim]"
                )
        except Exception as e:
            self._console.print(f"[llx.error]Abort failed: {e}[/llx.error]")
            self._console.print(
                "[llx.dim]Try /new for a fresh session, or restart the backend.[/llx.dim]"
            )

    def _cmd_clear(self, args: list[str]):
        """Clear the console screen."""
        self._console.clear()

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
            from llx.working_memory import normalize_working_memory

            self._state["session_id"] = session["id"]
            self._state["message_count"] = session.get("message_count", 0)
            self._state["context"] = None
            self._state["working_memory"] = normalize_working_memory(session.get("working_memory"))
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
            for name in get_theme_names():
                if name in THEMES:
                    desc = THEMES[name]["description"]
                else:
                    desc = "Follow terminal light/dark (COLORFGBG)"
                marker = " [llx.success]*[/llx.success]" if name == current else ""
                self._console.print(
                    f"  [llx.accent]{name:<12}[/llx.accent] "
                    f"[llx.dim]{desc}[/llx.dim]{marker}"
                )
            self._console.print(f"\n[llx.dim]Usage: /theme <name>[/llx.dim]\n")
            return

        name = args[0].lower()
        if name not in THEMES and name not in get_theme_names():
            self._console.print(f"[llx.error]Unknown theme: {name}[/llx.error]")
            self._console.print(f"[llx.dim]Available: {', '.join(get_theme_names())}[/llx.dim]")
            return

        set_active_theme(name)

        # Persist to config
        from llx.config import set_theme_name
        set_theme_name(name)

        # Refresh consoles
        self._console = make_console()
        output.refresh_theme()

        label = THEMES[name]["label"] if name in THEMES else name
        self._console.print(f"[llx.success]Theme switched to {label}[/llx.success]\n")

    def _cmd_help(self, args: list[str]):
        """Print comprehensive help for all commands. `/help [query]` filters."""
        query = " ".join(args).strip().lower() if args else ""

        if query and query in self._commands:
            meta = COMMAND_META.get(query, "")
            sub = COMMAND_TREE.get(query, [])
            self._console.print(f"[llx.brand_bright]/{query}[/llx.brand_bright]  {meta}")
            if sub:
                self._console.print(f"[llx.dim]Subcommands: {', '.join(sub)}[/llx.dim]")
            self._console.print(f"[llx.dim]Usage: /{query}" + (f" {'|'.join(sub)}" if sub else "") + "[/llx.dim]")
            return

        self._console.print("[llx.brand_bright]Guaardvark REPL[/llx.brand_bright]\n")
        self._console.print("[llx.dim]In chat mode, type a message to chat with the LLM.[/llx.dim]")
        self._console.print("[llx.dim]Use slash commands to manage the system. /help <command> for one command.[/llx.dim]\n")

        shown = 0
        for section_title, commands in _HELP_GROUPS:
            rows = []
            for name in commands:
                if name not in self._commands:
                    continue
                meta = COMMAND_META.get(name, "")
                sub = COMMAND_TREE.get(name, [])
                if query and query not in name and query not in meta.lower() and not any(query in s for s in sub):
                    continue
                suffix = f" ({', '.join(sub)})" if sub else ""
                rows.append((name, f"{meta}{suffix}"))
            if not rows:
                continue
            self._console.print(f"[llx.brand_bright]{section_title}:[/llx.brand_bright]")
            for name, desc in rows:
                self._console.print(
                    f"  [llx.accent]/{name}[/llx.accent]  [llx.dim]{desc}[/llx.dim]"
                )
                shown += 1
            self._console.print()

        if query and shown == 0:
            matches = suggest_command(query)
            self._console.print(f"[llx.dim]No commands matching '{query}'.[/llx.dim]")
            if matches:
                listed = ", ".join(f"/{m}" for m in matches)
                self._console.print(f"[llx.dim]Did you mean {listed}?[/llx.dim]")

    def _cmd_imagine(self, args: list[str]):
        """Generate an image from a text prompt (direct tool — same path as browser /imagine)."""
        if not args:
            self._console.print("[llx.error]Usage: /imagine <prompt>[/llx.error]")
            self._console.print("[llx.dim]Example: /imagine a sunset over mountains[/llx.dim]")
            return

        prompt = " ".join(args)
        server = self._state.get("server")

        try:
            from llx.client import get_client, LlxError, LlxConnectionError
            client = get_client(server)
            data = client.post("/api/chat/unified/direct-tool", json={
                "slash_command": "imagine",
                "slash_args": prompt,
                "params": {"prompt": prompt, "model": "auto"},
                "message": f"/imagine {prompt}",
                "session_id": self._state.get("session_id") or "cli_imagine",
            })
            if data.get("success"):
                self._console.print("[llx.success]Image generated successfully[/llx.success]")
                response = data.get("response") or ""
                if response:
                    self._console.print(f"[llx.dim]{response}[/llx.dim]")
                try:
                    from pathlib import Path as _Path

                    from llx.media_preview import extract_media_path, preview_image

                    media = extract_media_path(data, server or "")
                    if media and not str(media).startswith("http") and _Path(media).is_file():
                        preview_image(media, console=self._console)
                    elif media:
                        self._console.print(f"[link={media}]{media}[/link]")
                except Exception:
                    pass
            else:
                err = data.get("error") or data.get("response") or "unknown error"
                self._console.print(f"[llx.error]Image generation failed: {err}[/llx.error]")
                if data.get("gpu_busy"):
                    self._console.print("[llx.dim]GPU busy — run /imagine again in a moment.[/llx.dim]")
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
            try:
                from pathlib import Path as _Path

                from llx.media_preview import extract_media_path, play_audio

                media = extract_media_path(data, server or "")
                local = filename if _Path(str(filename)).is_file() else None
                if not local and media and not str(media).startswith("http") and _Path(str(media)).is_file():
                    local = media
                if local:
                    player = play_audio(local)
                    if player:
                        self._console.print(f"[llx.dim]Playing with {player}[/llx.dim]")
            except Exception:
                pass
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
        """Toggle agent mode, or capture a screenshot of the agent desktop."""
        sub = (args[0].lower() if args else "")
        if sub in ("shot", "screenshot", "view"):
            self._cmd_agent_shot()
            return
        if sub == "on":
            wanted = True
        elif sub == "off":
            wanted = False
        else:
            wanted = not self._state.get("agent_mode", False)

        self._state["agent_mode"] = wanted
        self._state["agent_screen_active"] = wanted

        if wanted:
            self._console.print("[llx.success]Agent mode ON[/llx.success]")
            self._console.print("[llx.dim]Chat messages will use tool-calling agent. /agent shot for a screenshot.[/llx.dim]")
            try:
                from llx.config import get_frontend_url

                url = get_frontend_url() + "/agent"
                self._console.print(f"[llx.dim]Viewer: [link={url}]{url}[/link][/llx.dim]")
            except Exception:
                pass
        else:
            self._console.print("[llx.dim]Agent mode OFF — back to standard chat.[/llx.dim]")

    def _cmd_agent_shot(self):
        """Fetch the agent framebuffer and preview it in-terminal."""
        server = self._state.get("server")
        try:
            from llx.client import get_client
            from llx.media_preview import preview_image

            client = get_client(server)
            resp = client.http.post("/api/agent-control/capture/raw", json={"quality": 70})
            if resp.status_code >= 400 or (resp.headers.get("content-type", "").startswith("application/json")):
                try:
                    err = resp.json().get("error", resp.text)
                except Exception:
                    err = resp.text
                self._console.print(f"[llx.error]Screenshot failed: {err}[/llx.error]")
                return
            preview_image(resp.content, console=self._console)
            self._console.print("[llx.dim]Agent desktop screenshot.[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]Screenshot failed: {e}[/llx.error]")

    def _cmd_web(self, args: list[str]):
        """Open the Guaardvark web UI in the default browser."""
        import webbrowser

        from llx.config import get_frontend_url

        _WEB_PATHS = {
            "images": "/images",
            "image": "/images",
            "chat": "/",
            "videos": "/video",
            "video": "/video",
            "settings": "/settings",
            "plugins": "/plugins",
            "gpu": "/plugins",
            "files": "/files",
            "docs": "/documents",
            "documents": "/documents",
        }
        base = get_frontend_url()
        path = ""
        if args:
            key = args[0].lower().lstrip("/")
            path = _WEB_PATHS.get(key, f"/{key}" if key else "")
        url = f"{base}{path}"
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
            data = client.post("/api/chat/unified/direct-tool", json={
                "slash_command": "remember",
                "slash_args": content,
                "params": {"content": content},
                "message": f"/remember {content}",
                "session_id": session_id or "cli_remember",
            })
            if data.get("success"):
                self._console.print("[llx.success]Saved to memory[/llx.success]")
                response = data.get("response") or ""
                if response:
                    self._console.print(f"[llx.dim]{response}[/llx.dim]")
            else:
                err = data.get("error") or data.get("response") or "unknown error"
                self._console.print(f"[llx.error]Failed to save: {err}[/llx.error]")
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

    # ── Local coding commands (agentic / like Grok + Claude Code + Cursor) ──

    def _cmd_ls(self, args: list[str]):
        target = " ".join(args).strip() or "."
        server = self._state.get("server")
        used_backend = False
        try:
            from llx.client import get_client
            client = get_client(server)
            res = client.execute_tool("list_files", {"directory": target, "recursive": False})
            if res.get("success"):
                out = res.get("result", {})
                # Backend returns ToolResult style
                files = out.get("output") or out.get("files") or []
                self._console.print(f"[llx.dim]list_files (backend) {target}[/llx.dim]")
                for item in files[:100]:
                    if isinstance(item, dict):
                        name = item.get("name", item)
                        is_dir = item.get("is_dir", False)
                        marker = "/" if is_dir else ""
                        self._console.print(f"  [llx.tree.{'folder' if is_dir else 'file'}]{name}{marker}[/]")
                    else:
                        self._console.print(f"  {item}")
                used_backend = True
        except Exception:
            pass

        if not used_backend:
            from llx.local_tools import list_dir
            from rich.tree import Tree as RichTree
            res = list_dir(target, cwd=self._state.get("cwd"))
            if "error" in res:
                self._console.print(f"[llx.error]{res['error']}[/llx.error]")
                return
            tree = RichTree(f"[bold]{res['path']}[/bold]")
            for f in res.get("folders", []):
                tree.add(f"[llx.tree.folder]{f}[/llx.tree.folder]")
            for fi in res.get("files", []):
                nm = fi.get("name", "?")
                sz = fi.get("size", 0)
                tree.add(f"[llx.tree.file]{nm}[/llx.tree.file]  [llx.tree.meta]{sz}B[/llx.tree.meta]")
            self._console.print(tree)

    def _cmd_cd(self, args: list[str]):
        if not args:
            self._console.print("[llx.error]Usage: /cd <dir>[/llx.error]")
            return
        from pathlib import Path
        newp = Path(" ".join(args)).expanduser()
        base = self._state.get("cwd") or Path.cwd()
        if not newp.is_absolute():
            newp = (base / newp).resolve()
        else:
            newp = newp.resolve()
        if not newp.exists() or not newp.is_dir():
            self._console.print(f"[llx.error]Not a directory: {newp}[/llx.error]")
            return
        self._state["cwd"] = newp
        # update memory + re-populate project context (GUAARDVARK.md, build.py, CSS for analysis)
        from llx.working_memory import normalize_working_memory
        mem = normalize_working_memory(self._state.get("working_memory"))
        mem["cwd"] = str(newp)
        from llx.local_tools import get_git_info
        mem["git"] = get_git_info(newp)
        try:
            from llx.utils import populate_project_context, find_project_root
            proj_root = find_project_root(newp)
            populate_project_context(mem, proj_root)
        except Exception:
            pass
        self._state["working_memory"] = mem
        self._console.print(f"[llx.success]cwd → {newp}[/llx.success]")

    def _cmd_pwd(self, args: list[str]):
        cwd = self._state.get("cwd") or Path.cwd()
        self._console.print(str(cwd))

    def _cmd_read(self, args: list[str]):
        if not args:
            self._console.print("[llx.error]Usage: /read <file> [offset] [limit][/llx.error]")
            return
        from llx.local_tools import read_file
        from rich.syntax import Syntax

        # naive parse: last two numeric tokens optional
        parts = args[:]
        limit = offset = None
        if parts and parts[-1].isdigit():
            limit = int(parts.pop())
        if parts and parts[-1].isdigit():
            offset = int(parts.pop())
        path = " ".join(parts)

        # Prefer backend read_code (existing agent tool) when connected
        server = self._state.get("server")
        used_backend = False
        try:
            from llx.client import get_client
            client = get_client(server)
            bres = client.execute_tool("read_code", {"filepath": path})
            if bres.get("success"):
                out = bres.get("result", {})
                content = out.get("output", str(out))
                self._console.print(f"[llx.dim]{path} (via backend read_code)[/llx.dim]")
                try:
                    self._console.print(Syntax(content[:8000], "python", line_numbers=True))
                except Exception:
                    self._console.print(content[:4000])
                used_backend = True
                from llx.working_memory import record_tool_use
                mem = normalize_working_memory(self._state.get("working_memory"))
                record_tool_use(mem, "read_code", {"filepath": path})
                self._state["working_memory"] = mem
        except Exception:
            pass

        if not used_backend:
            res = read_file(path, offset=offset, limit=limit, cwd=self._state.get("cwd"))
            if "error" in res:
                self._console.print(f"[llx.error]{res['error']}[/llx.error]")
                return
            content = res.get("content", "")
            try:
                syn = Syntax(content, "python", line_numbers=True, word_wrap=True)
                self._console.print(f"[llx.dim]{res['path']} (local read; backend read_code preferred when online)[/llx.dim]")
                self._console.print(syn)
            except Exception:
                self._console.print(content)
            path_for_mem = res.get("path", path)
        else:
            path_for_mem = path

        from llx.working_memory import normalize_working_memory, apply_attachments
        mem = normalize_working_memory(self._state.get("working_memory"))
        apply_attachments(mem, [{"path": path_for_mem, "read_status": "ok", "is_file": True}])
        self._state["working_memory"] = mem

    def _cmd_grep(self, args: list[str]):
        if not args:
            self._console.print("[llx.error]Usage: /grep <pattern> [path][/llx.error]")
            return

        # last token that looks like path?
        path = "."
        pat_parts = []
        for a in args:
            if ("/" in a or a.startswith(".")) and len(pat_parts) > 0:
                path = a
            else:
                pat_parts.append(a)
        pattern = " ".join(pat_parts) or args[0]

        server = self._state.get("server")
        used_backend = False
        try:
            from llx.client import get_client
            client = get_client(server)
            # Prefer grep_search or search_codebase
            for tool in ("grep_search", "search_codebase"):
                try:
                    bres = client.execute_tool(tool, {"pattern": pattern, "path": path})
                    if bres.get("success"):
                        out = bres.get("result", {})
                        matches = out.get("output") or out.get("matches") or []
                        self._console.print(f"[llx.accent]{len(matches)} matches[/llx.accent] via backend {tool} for '{pattern}'")
                        for m in matches[:50]:
                            if isinstance(m, dict):
                                self._console.print(f"  [llx.dim]{m.get('file', '?')}:{m.get('line','?')}[/llx.dim] {m.get('text','')[:120]}")
                            else:
                                self._console.print(f"  {m}")
                        used_backend = True
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if not used_backend:
            from llx.local_tools import grep
            res = grep(pattern, path=path, cwd=self._state.get("cwd"))
            if "error" in res:
                self._console.print(f"[llx.error]{res['error']}[/llx.error]")
                return
            matches = res.get("matches", [])
            self._console.print(f"[llx.accent]{len(matches)} matches[/llx.accent] (local) for '{pattern}' in {res.get('path')}")
            for m in matches[:50]:
                self._console.print(f"  [llx.dim]{m['file']}:{m['line']}[/llx.dim] {m['text']}")

    def _cmd_edit(self, args: list[str]):
        """ /edit <file> <old> <new>   OR /edit <file> "instruction..." 

        Prefers the backend's edit_code tool (from the large existing agent tool set)
        when the server is reachable for consistency with guarded edits, Uncle Claude,
        self-improvement, etc. Falls back to fast local search/replace.
        """
        if len(args) < 2:
            self._console.print("[llx.error]Usage: /edit <file> <old_text> <new_text>   or provide a patch[/llx.error]")
            self._console.print("[llx.dim]Example: /edit foo.py 'print(1)' 'print(\"hi\")'[/llx.dim]")
            return

        path = args[0]
        rest = " ".join(args[1:])

        server = self._state.get("server")
        old_t = new_t = None
        if " -> " in rest:
            old_t, new_t = [x.strip() for x in rest.split(" -> ", 1)]
        elif ">>>" in rest:
            old_t, new_t = [x.strip() for x in rest.split(">>>", 1)]

        if old_t and new_t:
            # Strongly prefer the real backend edit_code (guarded, audited, part of 70+ tool engine)
            try:
                from llx.client import get_client, LlxConnectionError, LlxError
                client = get_client(server)
                res = client.execute_tool("edit_code", {
                    "filepath": path,
                    "old_text": old_t,
                    "new_text": new_t
                })
                if res.get("success"):
                    out = res.get("result", res)
                    self._console.print("[llx.success]Used backend edit_code tool (guarded)[/llx.success]")
                    diff = (out.get("diff") if isinstance(out, dict) else None) or str(out)[:1800]
                    try:
                        from rich.syntax import Syntax
                        self._console.print(Syntax(diff, "diff", line_numbers=False))
                    except Exception:
                        self._console.print(diff)
                    return
            except Exception:
                pass  # fall to local
        elif len(rest) > 20:
            # Instruction-style edit (no explicit old/new) → use intelligent backend path (like frontend codeIntelligenceService)
            try:
                from llx.client import get_client
                client = get_client(server)
                res = client.edit_code_intelligent(
                    original_code="",  # backend will read the file
                    edit_instructions=rest,
                    language="auto",
                    file_path=path
                )
                if res.get("success"):
                    self._console.print("[llx.success]Used backend edit_code_intelligent (smart patch + verify)[/llx.success]")
                    out = res.get("result") or res
                    self._console.print(str(out)[:2000])
                    return
            except Exception:
                pass  # fallthrough

        # Local fast path
        from llx.local_tools import apply_search_replace
        from rich.syntax import Syntax

        if rest.startswith("<<"):
            new_content = rest.split("\n", 1)[-1].strip()
            new_content = re.sub(r"\n?EOF\s*$", "", new_content)
            res = apply_search_replace(path, "", new_content, cwd=self._state.get("cwd"))
        else:
            if not (old_t and new_t):
                self._console.print("[llx.dim]No explicit old -> new. Use 'old -> new' or heredoc.[/llx.dim]")
                return
            res = apply_search_replace(path, old_t, new_t, cwd=self._state.get("cwd"))

        if not res.get("success"):
            self._console.print(f"[llx.error]{res.get('error')}[/llx.error]")
            return

        diff = res.get("diff", "")
        self._console.print(f"[llx.success]Local edit applied. Backup: {res.get('backup')}[/llx.success]  (prefer /tool edit_code when connected)")
        if diff:
            try:
                self._console.print(Syntax(diff, "diff", line_numbers=False))
            except Exception:
                self._console.print(diff[:2000])

        from llx.working_memory import normalize_working_memory, record_last_edit
        mem = normalize_working_memory(self._state.get("working_memory"))
        record_last_edit(mem, res.get("path", path), "slash edit")
        mem["active_file"] = res.get("path", path)
        self._state["working_memory"] = mem

        # Suggest verification like real agent workflows (using existing verify_change tool)
        if "edit" in tool_name.lower() or "edit" in str(locals().get("res", "")):
            self._console.print("[llx.dim]Tip: /tool verify_change filepath={} expected=... (or use after edit to confirm change)[/llx.dim]".format(path))

    def _cmd_run(self, args: list[str]):
        if not args:
            self._console.print("[llx.error]Usage: /run <command>[/llx.error]")
            return
        cmd = " ".join(args)
        cwd = self._state.get("cwd")

        server = self._state.get("server")
        used_backend = False
        # Heuristic: if it looks like python code, try execute_python backend tool
        if cmd.strip().startswith("python") or cmd.strip().startswith("python3") or ".py" in cmd or "import " in cmd or cmd.strip().startswith("exec"):
            try:
                from llx.client import get_client
                client = get_client(server)
                # Extract code if possible
                code = cmd
                if cmd.startswith("python") or cmd.startswith("python3"):
                    # naive: take after -c "
                    if "-c " in cmd:
                        code = cmd.split("-c ", 1)[1].strip().strip('"').strip("'")
                bres = client.execute_tool("execute_python", {"code": code, "timeout": 30})
                if bres.get("success"):
                    out = bres.get("result", {})
                    self._console.print(f"[llx.dim]execute_python (backend) for snippet[/llx.dim]")
                    content = out.get("output") or str(out)
                    self._console.print(content[:3000])
                    used_backend = True
            except Exception:
                pass

        if not used_backend:
            from llx.local_tools import run_command
            from rich.syntax import Syntax
            res = run_command(cmd, cwd=cwd)
            self._console.print(f"[llx.dim]$ {cmd}  (exit {res.get('exit_code')})[/llx.dim]")
            out = res.get("output") or res.get("stdout", "")
            if out.strip():
                try:
                    self._console.print(Syntax(out[-4000:], "bash", word_wrap=False))
                except Exception:
                    self._console.print(out[-2000:])
            if res.get("stderr"):
                self._console.print(f"[llx.error]{res['stderr'][-500:]}[/llx.error]")

            # record
            from llx.working_memory import normalize_working_memory, record_last_run
            mem = normalize_working_memory(self._state.get("working_memory"))
            record_last_run(mem, cmd, res.get("exit_code", -1))
            self._state["working_memory"] = mem

    def _cmd_test(self, args: list[str]):
        # convenience wrapper around run with pytest
        pytest_args = " ".join(args) or "."
        self._cmd_run(["python", "-m", "pytest", pytest_args, "-q", "--tb=line"])

    def _cmd_todo(self, args: list[str]):
        from llx.todo import TodoStore
        store: TodoStore = self._state.get("_todo_store") or TodoStore(self._state.get("session_id"))
        self._state["_todo_store"] = store

        sub = (args[0].lower() if args else "list")
        if sub in ("list", "ls", ""):
            items = store.list()
            if not items:
                self._console.print("[llx.dim]No open todos. Use /todo add <text>[/llx.dim]")
                return
            for t in items:
                self._console.print(f"  [llx.accent]{t['id']}[/llx.accent] {t['text']}")
        elif sub == "add" and len(args) > 1:
            text = " ".join(args[1:])
            item = store.add(text)
            self._console.print(f"[llx.success]Added todo {item['id']}: {text}[/llx.success]")

            # Also persist to the backend's long-term memory system (save_memory tool)
            server = self._state.get("server")
            try:
                from llx.client import get_client
                client = get_client(server)
                mem_res = client.execute_tool("save_memory", {
                    "content": f"[CLI TODO] {text}",
                    "type": "note",
                    "tags": ["cli-todo", "task"],
                    "importance": 0.7
                })
                if mem_res.get("success"):
                    self._console.print("[llx.dim]  (also saved to agent long-term memory via save_memory)[/llx.dim]")
            except Exception:
                pass  # non-fatal
        elif sub in ("done", "complete", "finish") and len(args) > 1:
            tid = args[1]
            if store.done(tid):
                self._console.print(f"[llx.success]Marked {tid} done[/llx.success]")
            else:
                self._console.print("[llx.error]Todo not found or already done[/llx.error]")
        elif sub == "clear":
            n = store.clear()
            self._console.print(f"[llx.dim]Cleared {n} open todos[/llx.dim]")
        else:
            self._console.print("[llx.error]Usage: /todo [list|add <text>|done <id>|clear][/llx.error]")

        # sync to working memory for prompt/context
        from llx.working_memory import normalize_working_memory, apply_todos
        mem = normalize_working_memory(self._state.get("working_memory"))
        apply_todos(mem, store.all())
        self._state["working_memory"] = mem

    def _cmd_diff(self, args: list[str]):
        # Show last edit diff if available, or git diff
        from llx.local_tools import run_command
        target = " ".join(args).strip() or None
        if target:
            res = run_command(f"git diff -- {target}", cwd=self._state.get("cwd"))
            self._console.print(res.get("output", ""))
        else:
            # last recorded edit
            mem = self._state.get("working_memory") or {}
            last = mem.get("last_edit")
            if last and last.get("path"):
                self._console.print(f"[llx.dim]Last edit target was {last['path']}[/llx.dim]")
            res = run_command("git diff --stat", cwd=self._state.get("cwd"))
            self._console.print(res.get("output", "(no git diff)"))

    def _cmd_apply(self, args: list[str]):
        self._console.print("[llx.dim]/apply is currently an alias for confirming the last /edit. Use /edit directly.[/llx.dim]")

    def _cmd_undo(self, args: list[str]):
        # Very simple: restore the most recent .bak we can find next to active or arg
        import glob
        target = " ".join(args).strip()
        from llx.working_memory import normalize_working_memory
        mem = normalize_working_memory(self._state.get("working_memory"))
        p = target or mem.get("active_file") or mem.get("last_edit", {}).get("path")
        if not p:
            self._console.print("[llx.error]No target for undo. Pass a path or have an active edit.[/llx.error]")
            return
        base = Path(p)
        cands = sorted(glob.glob(str(base) + ".bak-*"), reverse=True)
        if not cands:
            self._console.print("[llx.error]No .bak-* backup found next to file.[/llx.error]")
            return
        bak = Path(cands[0])
        try:
            base.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            self._console.print(f"[llx.success]Restored from {bak.name}[/llx.success]")
        except Exception as ex:
            self._console.print(f"[llx.error]Undo failed: {ex}[/llx.error]")

    def _cmd_tools(self, args: list[str]):
        """List tools from the backend agent tool registry (the real powerful ones: read_code, edit_code, search_code, execute_python, save_memory, etc.)."""
        server = self._state.get("server")
        try:
            from llx.client import get_client, LlxConnectionError, LlxError
            client = get_client(server)
            data = client.list_tools()
            tools = data.get("tools", data.get("data", [])) or []
            if not tools:
                self._console.print("[llx.dim]No tools returned (is the backend running with registry?).[/llx.dim]")
                return
            self._console.print(f"[llx.brand_bright]Registered Agent Tools ({len(tools)}):[/llx.brand_bright]")
            for t in tools[:80]:
                name = t.get("name", "?")
                desc = (t.get("description") or "")[:120].replace("\n", " ")
                params = list((t.get("parameters") or {}).keys())
                param_str = f"  params: {', '.join(params)}" if params else ""
                self._console.print(f"  [llx.accent]{name}[/llx.accent]{param_str}")
                if desc:
                    self._console.print(f"    [llx.dim]{desc}[/llx.dim]")
            if len(tools) > 80:
                self._console.print(f"  ... +{len(tools)-80} more")
            self._console.print("\n[llx.dim]Use /tool <name> to see schema, or /tool <name> key=val ... to call.[/llx.dim]")
            # cache for completion, context, and prompt
            names = [t.get("name") for t in tools if t.get("name")]
            self._state["_tool_names"] = names
            self._state["_available_tools"] = names
            mem = self._state.get("working_memory") or {}
            mem["_available_tools"] = names
            self._state["working_memory"] = mem
        except (LlxConnectionError, LlxError) as e:
            self._console.print(f"[llx.error]Could not reach backend tools: {e}[/llx.error]")
            self._console.print("[llx.dim]Local fallbacks (ls/read/grep/edit/run) still available. Use /tools when connected for the full registry.[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]{e}[/llx.error]")

    def _cmd_tool(self, args: list[str]):
        """Directly invoke (or inspect) a backend agent tool.

        Examples:
          /tool read_code filepath=cli/llx/repl.py
          /tool edit_code filepath=foo.py old_text="..." new_text="..."
          /tool <name>          # show full schema + params
          /tool <name> '{ "param": "value", ... }'   # raw JSON params
        """
        if not args:
            self._console.print("[llx.error]Usage: /tool <tool_name> [key=val ... or JSON][/llx.error]")
            self._console.print("[llx.dim]Run /tools first to list. Backend tools include read_code, edit_code, search_code, execute_python, save_memory, etc.[/llx.dim]")
            return

        tool_name = args[0]
        server = self._state.get("server")
        from llx.client import get_client, LlxConnectionError, LlxError
        client = get_client(server)

        rest = args[1:]
        params = {}

        # If single arg looks like JSON, parse it
        if len(rest) == 1 and (rest[0].strip().startswith("{") or rest[0].strip().startswith("[")):
            try:
                import json
                params = json.loads(rest[0])
            except Exception as je:
                self._console.print(f"[llx.error]Failed to parse JSON params: {je}[/llx.error]")
                return
        else:
            # key=value parsing with better support for quoted values
            for a in rest:
                if "=" in a:
                    k, v = a.split("=", 1)
                    v = v.strip()
                    if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                        v = v[1:-1]
                    if v.lower() in ("true", "false"):
                        params[k] = v.lower() == "true"
                    elif v.lstrip("-").isdigit():
                        params[k] = int(v)
                    elif "." in v and v.replace(".", "", 1).lstrip("-").isdigit():
                        try:
                            params[k] = float(v)
                        except:
                            params[k] = v
                    else:
                        params[k] = v
                else:
                    self._console.print(f"[llx.warning]Ignoring non key=val: {a} (use JSON or key=val)[/llx.warning]")

        # If no params provided, just show schema (discovery mode)
        if not params:
            try:
                schema = client.get_tool_schema(tool_name)
                tool = schema.get("tool", schema)
                self._console.print(f"[llx.brand_bright]Tool: {tool.get('name', tool_name)}[/llx.brand_bright]")
                self._console.print(f"[llx.dim]{tool.get('description', '')}[/llx.dim]")
                params_info = tool.get("parameters", {})
                if params_info:
                    self._console.print("\nParameters:")
                    for pn, pv in params_info.items():
                        req = "required" if pv.get("required") else "optional"
                        self._console.print(f"  [llx.accent]{pn}[/llx.accent] ({pv.get('type','?')}, {req}): {pv.get('description','')}")
                self._console.print("\n[llx.dim]Call with params: /tool {} key=val ... or JSON blob[/llx.dim]".format(tool_name))
                return
            except Exception:
                self._console.print(f"[llx.dim]No detailed schema available for {tool_name} (or offline).[/llx.dim]")

        # Execute
        dangerous = any(x in tool_name.lower() for x in ["edit", "delete", "write", "execute", "kill", "run", "modify"])
        if dangerous:
            try:
                import typer
                if not typer.confirm(f"About to run potentially dangerous tool '{tool_name}'. Proceed?", default=False):
                    self._console.print("[llx.dim]Cancelled.[/llx.dim]")
                    return
            except Exception:
                pass  # if no tty, proceed

        try:
            result = client.execute_tool(tool_name, params)
            if result.get("success"):
                out = result.get("result", result)
                self._console.print(f"[llx.success]Tool '{tool_name}' executed successfully[/llx.success]")
                if isinstance(out, dict):
                    if "output" in out or "success" in out:
                        content = out.get("output") or out.get("result") or out
                        self._console.print(str(content)[:2500])
                    else:
                        self._console.print(str(out)[:2500])
                else:
                    self._console.print(str(out)[:2500])

                # record for context
                from llx.working_memory import record_tool_use
                mem = normalize_working_memory(self._state.get("working_memory"))
                record_tool_use(mem, tool_name, params, str(out)[:100] if 'out' in locals() else None)
                self._state["working_memory"] = mem
            else:
                err = result.get("error") or result
                self._console.print(f"[llx.error]Tool failed: {err}[/llx.error]")
        except (LlxConnectionError, LlxError) as e:
            self._console.print(f"[llx.error]Backend unreachable for tool call: {e}[/llx.error]")
        except Exception as e:
            # last resort direct-tool
            try:
                res2 = client.direct_tool(tool_name=tool_name, params=params)
                self._console.print(f"[llx.dim]Fell back to direct-tool: {str(res2)[:1200]}[/llx.dim]")
            except Exception as e2:
                self._console.print(f"[llx.error]Tool execution failed: {e} / {e2}[/llx.error]")

    def _cmd_context(self, args: list[str]):
        """Show current CLI working context (files, todos, git, tools summary). Like a status for agentic work."""
        from llx.working_memory import normalize_working_memory
        mem = normalize_working_memory(self._state.get("working_memory"))
        cwd = self._state.get("cwd") or mem.get("cwd") or "."
        git = mem.get("git") or {}
        todos = [t for t in mem.get("todos", []) if not t.get("done")]
        active = mem.get("active_file")
        recent = mem.get("recent_files", [])[:3]

        self._console.print("[llx.brand_bright]Current CLI Context[/llx.brand_bright]")
        self._console.print(f"  CWD: {cwd}")
        if git.get("branch"):
            self._console.print(f"  Git: {git['branch']}{' *dirty*' if git.get('dirty') else ''}")
        if active:
            self._console.print(f"  Active file: {active}")
        if recent:
            self._console.print(f"  Recent: {', '.join(recent)}")
        if todos:
            self._console.print(f"  Open todos ({len(todos)}):")
            for t in todos[:5]:
                self._console.print(f"    - {t.get('id')}: {t.get('text')[:70]}")
        else:
            self._console.print("  Todos: none open (use /todo add)")

        # Recent tool activity
        recent_tools = mem.get("recent_tools") or []
        if recent_tools:
            self._console.print("  Recent tools:")
            for rt in recent_tools[-3:]:
                self._console.print(f"    - {rt.get('tool')}: {rt.get('result', '')[:50]}")

        # Tools count if cached
        tool_names = self._state.get("_available_tools") or self._state.get("_tool_names", [])
        if tool_names:
            self._console.print(f"  Backend tools available: {len(tool_names)} (use /tools or /tool <name>)")
        else:
            self._console.print("  Backend tools: run /tools to discover (read_code, edit_code, save_memory, verify_change, get_repository_map...)")

        self._console.print("\n[llx.dim]This context is injected into chat messages.[/llx.dim]")

    def _cmd_suggest(self, args: list[str]):
        """Suggest relevant backend tools based on current context (active file, todos, recent actions). Like Cursor/Claude agent hints."""
        from llx.working_memory import normalize_working_memory
        mem = normalize_working_memory(self._state.get("working_memory"))
        active = mem.get("active_file") or ""
        todos = mem.get("todos") or []
        recent = mem.get("recent_tools") or []

        suggestions = []
        avail = self._state.get("_available_tools") or self._state.get("_tool_names") or []

        if any(active.endswith(ext) for ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".java")):
            suggestions.extend(["read_code", "search_code", "edit_code", "verify_change", "get_repository_map", "read_ast_node"])
        if any("test" in (t.get("text", "") or "").lower() for t in todos):
            suggestions.append("execute_python")
        if recent and any(x in str(recent[-1]).lower() for x in ["edit", "write"]):
            suggestions.append("verify_change")
        if todos or active:
            suggestions.append("save_memory")

        # keep only real ones if we know them
        if avail:
            suggestions = [s for s in suggestions if s in avail] or [s for s in ["read_code", "edit_code", "search_code"] if s in avail]

        if not suggestions:
            suggestions = ["read_code", "edit_code", "search_code", "list_files", "save_memory"]

        self._console.print("[llx.brand_bright]Suggested tools for current context:[/llx.brand_bright]")
        for s in list(dict.fromkeys(suggestions))[:6]:
            self._console.print(f"  /tool {s} ...")
        self._console.print("\n[llx.dim]Use /context for state, /tools for full list.[/llx.dim]")

    def _cmd_analyze(self, args: list[str]):
        """Analyze a project/site (especially for CSS/styling improvements). Takes initiative: explores structure, reviews build.py, finds CSS, loads GUAARDVARK.md, creates todos."""
        from llx.utils import populate_project_context, find_project_root
        from llx.local_tools import read_file
        from llx.working_memory import normalize_working_memory, apply_todos
        from llx.todo import TodoStore

        target = " ".join(args).strip() or "."
        root = find_project_root(target)
        mem = normalize_working_memory(self._state.get("working_memory"))
        populate_project_context(mem, root)

        # Auto-create GUAARDVARK.md if missing (agent initiative)
        if not mem.get("guaardvark_instructions"):
            try:
                from llx.utils import generate_and_write_guaardvark_md
                gen_res = generate_and_write_guaardvark_md(root, force=False)
                if gen_res.get("status") == "created":
                    self._console.print(f"[llx.success]Auto-created GUAARDVARK.md at {gen_res.get('path')}[/llx.success]")
                    populate_project_context(mem, root)  # reload
            except Exception as e:
                self._console.print(f"[llx.dim]Could not auto-create GUAARDVARK.md: {e}[/llx.dim]")

        # Take initiative: review build.py if present (prefer backend code tools for shared architecture)
        build_path = root / "build.py"
        build_content = ""
        server = self._state.get("server")
        used_backend_tools = False
        try:
            from llx.client import get_client
            client = get_client(server)
            if build_path.exists():
                bres = client.execute_tool("read_code", {"filepath": str(build_path)})
                if bres.get("success"):
                    out = bres.get("result", {})
                    build_content = out.get("output", str(out))[:3000]
                    mem["active_file"] = str(build_path)
                    mem.setdefault("recent_files", []).insert(0, str(build_path))
                    used_backend_tools = True
            if mem.get("css_files"):
                for cssf in mem["css_files"][:3]:
                    bres = client.execute_tool("read_code", {"filepath": str(root / cssf)})
                    if bres.get("success"):
                        out = bres.get("result", {})
                        mem.setdefault("key_files", {})[cssf] = out.get("output", str(out))[:2000]
                        used_backend_tools = True
        except Exception:
            pass

        if not used_backend_tools:
            # Local fast path fallback (lite/offline)
            if build_path.exists():
                r = read_file(str(build_path), limit=150, cwd=root)
                if r.get("read_status") == "ok":
                    build_content = r["content"]
                    mem["active_file"] = str(build_path)
                    mem.setdefault("recent_files", []).insert(0, str(build_path))
            if mem.get("css_files"):
                for cssf in mem["css_files"][:3]:
                    r = read_file(str(root / cssf), limit=50, cwd=root)
                    if r.get("read_status") == "ok":
                        mem.setdefault("key_files", {})[cssf] = r["content"][:2000]

        # Create initiative todos
        store = self._state.get("_todo_store") or TodoStore(self._state.get("session_id"))
        self._state["_todo_store"] = store
        store.add(f"Review build.py for styling/build pipeline (in {root.name})")
        if mem.get("css_files"):
            store.add("Analyze CSS files for improvements (modern practices, variables, responsive)")
        store.add("Suggest concrete CSS/styling improvements based on project")
        apply_todos(mem, store.all())

        self._state["working_memory"] = mem
        self._state["cwd"] = root

        self._console.print(f"[llx.success]Analyzed project at {root}[/llx.success]")
        if mem.get("project_summary"):
            self._console.print(mem["project_summary"])
        if build_content:
            self._console.print("[llx.dim]build.py snippet (first 800 chars):[/llx.dim]")
            self._console.print(build_content[:800])
        if mem.get("css_files"):
            self._console.print(f"CSS files: {', '.join(mem['css_files'][:5])}")
        backend_note = " (used backend code tools)" if used_backend_tools else " (local fast path)"
        self._console.print(f"\n[llx.dim]Todos created for analysis{backend_note}. Use chat to ask for suggestions, or /context /suggest /tool edit_code etc. GUAARDVARK.md loaded if present.[/llx.dim]")

    def _cmd_init(self, args: list[str]):
        """
        /init — Recursively scan the current CLI project root and auto-create (or update) GUAARDVARK.md.
        The agent explores structure, key files (build.py, package.json, CSS, README), detects type,
        and writes a rich project instruction file so future analysis/suggestions follow project rules.
        Use this instead of (or in addition to) dragging a folder.
        """
        from llx.utils import find_project_root, generate_and_write_guaardvark_md
        from llx.working_memory import normalize_working_memory

        force = "--force" in " ".join(args).lower() or "force" in args
        root = find_project_root(self._state.get("cwd") or ".")
        mem = normalize_working_memory(self._state.get("working_memory"))

        self._console.print(f"[llx.brand_bright]Initializing GUAARDVARK.md for {root}...[/llx.brand_bright]")
        result = generate_and_write_guaardvark_md(root, force=force)

        if result.get("status") == "exists":
            self._console.print("[llx.dim]GUAARDVARK.md already exists. Use /init --force to overwrite.[/llx.dim]")
        else:
            self._console.print(f"[llx.success]Created {result.get('path')} via {result.get('via')}[/llx.success]")
            self._console.print(f"Detected type: {result.get('detected_type')}, build.py: {result.get('has_build_py')}, CSS files: {result.get('css_count')}")

        # Re-populate context so the new md is loaded immediately
        from llx.utils import populate_project_context
        populate_project_context(mem, root)
        self._state["working_memory"] = mem
        self._state["cwd"] = root

        self._console.print("[llx.dim]Project context refreshed. You can now /analyze or chat about the project with full instructions.[/llx.dim]")

    def _cmd_load(self, args: list[str]):
        """Load a skill / instructions file (.md) and inject into project context for this session.
        Usage: /load path/to/skill.md   or bare NL: "load skill foo.md"
        The content is added to working memory and will be included in chat context (like GUAARDVARK.md).
        """
        from llx.working_memory import normalize_working_memory
        from pathlib import Path

        if not args:
            self._console.print("[llx.error]Usage: /load <path-to-.md-skill-file>[/llx.error]")
            return

        skill_path = " ".join(args).strip().strip('"\'')
        p = Path(skill_path)
        if not p.exists():
            # try relative to cwd or project_root
            cwd = Path(self._state.get("cwd") or ".")
            p = (cwd / skill_path).resolve()
            if not p.exists():
                self._console.print(f"[llx.error]Skill file not found: {skill_path}[/llx.error]")
                return

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            mem = normalize_working_memory(self._state.get("working_memory"))
            skill_name = p.stem
            mem[f"loaded_skill_{skill_name}"] = f"[Loaded skill: {p.name}]\n{content}\n---\n"
            mem.setdefault("loaded_skills", []).append(str(p))
            # also put top level for easy context injection
            mem["skill_instructions"] = (mem.get("skill_instructions", "") + f"\n\n### {skill_name}\n{content}").strip()

            self._state["working_memory"] = mem
            self._console.print(f"[llx.success]Loaded skill: {p.name} ({len(content)} chars)[/llx.success]")
            self._console.print("[llx.dim]Skill instructions injected. Future chat /analyze will use it. Use /context to view.[/llx.dim]")
        except Exception as e:
            self._console.print(f"[llx.error]Failed to load skill: {e}[/llx.error]")

    def _cmd_skills(self, args: list[str]):
        """List SKILL.md files under the project and ~/.guaardvark/skills."""
        from pathlib import Path

        roots = []
        cwd = Path(self._state.get("cwd") or Path.cwd())
        roots.append(cwd)
        home_skills = Path.home() / ".guaardvark" / "skills"
        if home_skills.exists():
            roots.append(home_skills)
        found: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            try:
                for p in root.rglob("SKILL.md"):
                    key = str(p.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(p)
            except OSError:
                continue
        if not found:
            self._console.print("[llx.dim]No SKILL.md files found. Drop one in the project or ~/.guaardvark/skills.[/llx.dim]")
            return
        self._console.print("[llx.brand_bright]Skills:[/llx.brand_bright]")
        for p in found[:50]:
            self._console.print(f"  [llx.accent]{p}[/llx.accent]")
        self._console.print("\n[llx.dim]Load with: /load <path>[/llx.dim]")

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
