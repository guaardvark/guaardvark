"""Interactive REPL mode — llx with no arguments."""

import shlex
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter

from llx import __version__
from llx.config import load_config, get_last_session_id
from llx.client import get_client, LlxError, LlxConnectionError
from llx.theme import make_console, get_banner, ICON_ONLINE, ICON_OFFLINE, THEMES, set_active_theme, get_active_theme_name

console = make_console()


def launch_repl():
    """Start the interactive REPL."""
    config = load_config()
    server = config["server"]

    # Connection check
    try:
        client = get_client(server)
        health = client.get("/api/health")
        model_data = client.get("/api/model/status")
        model_info = model_data.get("message", model_data.get("data", {}))
        if isinstance(model_info, str):
            model_info = {}
        model = model_info.get("text_model", "?")
        status_line = f"[llx.status.online]{ICON_ONLINE} Connected[/llx.status.online]  {server}"
    except (LlxConnectionError, LlxError):
        model = "?"
        status_line = f"[llx.status.offline]{ICON_OFFLINE} Offline[/llx.status.offline]  {server}"

    model_line = f"[llx.accent]{model}[/llx.accent]"
    console.print(get_banner(__version__, status_line, model_line))
    console.print("[llx.dim]Type a command (chat, search, files, ...) or 'help'. Ctrl+D to exit.[/llx.dim]\n")

    # Keep a persistent chat session for the REPL
    chat_session_id = get_last_session_id() or str(uuid.uuid4())

    # Create prompt session with history and completion
    repl_commands = ['chat', 'c', 'search', 's', 'health', 'status',
                     'files', 'fl', 'projects', 'p', 'rules', 'r',
                     'models', 'm', 'theme', 't', 'dashboard', 'dash', 'd',
                     'help', 'exit', 'quit']
    completer = WordCompleter(repl_commands, ignore_case=True)
    history_file = Path.home() / ".llx" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
    )

    while True:
        try:
            line = session.prompt("guaardvark> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[llx.dim]Goodbye.[/llx.dim]")
            break

        if not line:
            continue

        if line in ("exit", "quit"):
            console.print("[llx.dim]Goodbye.[/llx.dim]")
            break

        if line == "help":
            _print_repl_help()
            continue

        # Parse and dispatch
        try:
            parts = shlex.split(line)
        except ValueError as e:
            console.print(f"[llx.error]Parse error: {e}[/llx.error]")
            continue

        cmd = parts[0]
        args = parts[1:]

        if cmd == "chat" or cmd == "c":
            if args:
                message = " ".join(args)
            else:
                console.print("[llx.dim]Usage: chat <message>[/llx.dim]")
                continue
            _repl_chat(server, chat_session_id, message)

        elif cmd == "search" or cmd == "s":
            if args:
                _repl_search(server, " ".join(args))
            else:
                console.print("[llx.dim]Usage: search <query>[/llx.dim]")

        elif cmd == "health":
            _repl_health(server)

        elif cmd == "status":
            from llx.commands.system import status as status_cmd
            try:
                status_cmd(server=server, json_out=False)
            except SystemExit:
                pass

        elif cmd in ("files", "fl"):
            _repl_run(server, ["files", "list"])

        elif cmd in ("projects", "p"):
            _repl_run(server, ["projects", "list"])

        elif cmd in ("rules", "r"):
            _repl_run(server, ["rules", "list"])

        elif cmd in ("models", "m"):
            _repl_run(server, ["models", "active"])

        elif cmd in ("theme", "t"):
            _repl_theme(args)

        elif cmd in ("dashboard", "dash", "d"):
            from llx.commands.dashboard import dashboard as dash_cmd
            try:
                dash_cmd(server=server)
            except SystemExit:
                pass

        else:
            # Try running as a full llx command
            import sys
            from llx.main import app
            try:
                sys.argv = ["guaardvark"] + parts
                app(standalone_mode=False)
            except SystemExit:
                pass
            except Exception as e:
                console.print(f"[llx.error]Error: {e}[/llx.error]")


def _repl_chat(server: str, session_id: str, message: str):
    """Quick chat within REPL — synchronous request with spinner."""
    import time
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.spinner import Spinner

    try:
        client = get_client(server)
        start = time.time()

        with Live(Spinner("dots", text="[llx.dim]Thinking...[/llx.dim]"), console=console, transient=True):
            data = client.post("/api/enhanced-chat", json={
                "session_id": session_id,
                "message": message,
                "use_rag": True,
            })

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

        elapsed = time.time() - start
        console.print(Markdown(response_text))
        console.print(f"[llx.dim]{elapsed:.1f}s[/llx.dim]\n")

        from llx.config import save_session
        save_session(session_id, message[:80])

    except (LlxConnectionError, LlxError) as e:
        console.print(f"[llx.error]Error: {e}[/llx.error]")


def _repl_search(server: str, query: str):
    """Quick search within REPL."""
    try:
        client = get_client(server)
        data = client.post("/api/search/semantic", json={"query": query})
        answer = data.get("answer", "")
        if answer:
            from rich.markdown import Markdown
            console.print(Markdown(answer))
        sources = data.get("sources", [])[:3]
        if sources:
            console.print(f"[llx.dim]Sources: {', '.join(s.get('source_document', '?') for s in sources)}[/llx.dim]")
        console.print()
    except (LlxConnectionError, LlxError) as e:
        console.print(f"[llx.error]Error: {e}[/llx.error]")


def _repl_run(server: str, parts: list[str]):
    """Run a full llx command within REPL."""
    import sys
    from llx.global_opts import set_global_opts
    from llx.main import app
    try:
        set_global_opts(server=server, json_out=False)
        sys.argv = ["guaardvark"] + parts
        app(standalone_mode=False)
    except SystemExit:
        pass
    except Exception as e:
        console.print(f"[llx.error]Error: {e}[/llx.error]")


def _repl_theme(args: list[str]):
    """Switch or list CLI themes."""
    global console

    if not args:
        # List available themes
        current = get_active_theme_name()
        console.print("\n[llx.brand_bright]Available themes:[/llx.brand_bright]")
        for name, data in THEMES.items():
            marker = " [llx.success]*[/llx.success]" if name == current else ""
            console.print(f"  [llx.accent]{name:<12}[/llx.accent] [llx.dim]{data['description']}[/llx.dim]{marker}")
        console.print(f"\n[llx.dim]Usage: theme <name>[/llx.dim]\n")
        return

    name = args[0].lower()
    if name not in THEMES:
        console.print(f"[llx.error]Unknown theme: {name}[/llx.error]")
        console.print(f"[llx.dim]Available: {', '.join(THEMES.keys())}[/llx.dim]")
        return

    set_active_theme(name)

    # Persist to config
    from llx.config import set_theme_name
    set_theme_name(name)

    # Refresh consoles
    console = make_console()
    from llx import output
    output.refresh_theme()

    label = THEMES[name]["label"]
    console.print(f"[llx.success]Theme switched to {label}[/llx.success]\n")


def _repl_health(server: str):
    """Quick health check within REPL."""
    try:
        client = get_client(server)
        data = client.get("/api/health")
        status = data.get("status", "?")
        icon = ICON_ONLINE if status == "ok" else ICON_OFFLINE
        style = "llx.status.online" if status == "ok" else "llx.status.offline"
        console.print(f"[{style}]{icon} {status}[/{style}]")
    except (LlxConnectionError, LlxError) as e:
        console.print(f"[llx.error]{e}[/llx.error]")


def _print_repl_help():
    console.print("""
[llx.brand_bright]Commands:[/llx.brand_bright]
  [llx.accent]chat[/llx.accent] <message>    Chat with the LLM
  [llx.accent]c[/llx.accent] <message>       Shortcut for chat
  [llx.accent]search[/llx.accent] <query>    Semantic search
  [llx.accent]s[/llx.accent] <query>         Shortcut for search
  [llx.accent]health[/llx.accent]            Quick health check
  [llx.accent]status[/llx.accent]            System dashboard
  [llx.accent]files[/llx.accent] / [llx.accent]fl[/llx.accent]       List files
  [llx.accent]projects[/llx.accent] / [llx.accent]p[/llx.accent]   List projects
  [llx.accent]rules[/llx.accent] / [llx.accent]r[/llx.accent]      List rules
  [llx.accent]models[/llx.accent] / [llx.accent]m[/llx.accent]     Show active model
  [llx.accent]theme[/llx.accent] / [llx.accent]t[/llx.accent]      List or switch themes (e.g. theme hacker)
  [llx.accent]dashboard[/llx.accent] / [llx.accent]d[/llx.accent]   Live system dashboard
  [llx.accent]help[/llx.accent]              Show this help
  [llx.accent]exit[/llx.accent] / [llx.accent]quit[/llx.accent]       Exit REPL

[llx.dim]Any other input is tried as a full llx command (e.g. 'files list', 'index status').[/llx.dim]
""")
