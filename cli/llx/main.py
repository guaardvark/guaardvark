import os
import typer

from llx import __version__
from llx import output
from llx.global_opts import set_global_opts
from llx.commands.system import health, status, doctor, start, stop, init as setup_wizard, models_app
from llx.commands.chat import chat
from llx.commands.search import search
from llx.commands.dashboard import dashboard
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
from llx.commands.launch import launch
from llx.commands.quality import quality_app
from llx.commands.outreach import outreach_app
from llx.commands.recipes import recipes_app

app = typer.Typer(
    name="guaardvark",
    help="[#a29bfe]Guaardvark CLI[/#a29bfe] — chat, search, manage files, and more from the terminal.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

# Top-level analyze and init for headless / natural use (e.g. guaardvark analyze /path )
# These delegate to the REPL logic but run non-interactively when --non-interactive or no REPL needed.
def _run_analyze(project: str = None, **opts):
    from llx.slash import SlashRouter
    from llx.working_memory import empty_working_memory, normalize_working_memory
    state = {"server": None, "session_id": "headless", "cwd": None, "working_memory": empty_working_memory()}
    router = SlashRouter(state)
    args = [project] if project else []
    router.dispatch("analyze " + " ".join(args) if args else "analyze")
    # For headless, the cmd prints; if more output needed, enhance.

app.command("analyze")(_run_analyze)

def _run_init(**opts):
    from llx.slash import SlashRouter
    from llx.working_memory import empty_working_memory
    state = {"server": None, "session_id": "headless", "cwd": None, "working_memory": empty_working_memory()}
    router = SlashRouter(state)
    router.dispatch("init")

app.command("init")(_run_init)


def _maybe_bootstrap_backend() -> None:
    """Start the real backend when offline (skip in explicit lite launch mode)."""
    try:
        from llx.launch_config import _config_path, load_launch_config

        if _config_path().exists() and load_launch_config().get("mode") == "lite":
            return
    except Exception:
        pass

    try:
        from llx.backend_bootstrap import ensure_backend_running, is_backend_healthy
        from llx.config import get_server_url
        from llx.theme import make_console

        server = get_server_url()
        if is_backend_healthy(server):
            return
        ensure_backend_running(make_console())
    except Exception as e:
        console = None
        try:
            from llx.theme import make_console
            console = make_console()
        except Exception:
            pass
        if console:
            console.print(f"[llx.warning]Could not auto-start backend: {e}[/llx.warning]")
            console.print("[llx.dim]Try: guaardvark start --backend-only or ./start.sh[/llx.dim]")


app.command("health")(health)
app.command("status")(status)
# app.command("init")(init)  # overridden by project init below for GUAARDVARK/agentic use
app.command("doctor")(doctor)
app.command("start")(start)
app.command("stop")(stop)
app.command("chat")(chat)
app.command("search")(search)
app.command("dashboard")(dashboard)
app.command("launch")(launch)


def ask(
    message: str = typer.Argument(..., help="Natural language message to send to chat"),
    server: str | None = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
    no_rag: bool = typer.Option(False, "--no-rag"),
    stream: bool = typer.Option(False, "--stream"),
):
    """Send a one-shot chat message (replaces bare guaardvark \"query\" syntax)."""
    from llx.global_opts import get_global_json as _gj, get_global_server as _gs

    chat(
        message=message,
        server=server or _gs(),
        json_out=json_out or _gj(),
        no_rag=no_rag,
        stream=stream,
        resume=False,
        session=None,
        list_sessions=False,
        export=False,
        output_file=None,
        project=None,
    )


app.command("ask")(ask)
app.command("setup")(setup_wizard)

app.add_typer(models_app, name="models")
app.add_typer(files_app, name="files")
app.add_typer(projects_app, name="projects")
app.add_typer(rules_app, name="rules")
app.add_typer(agents_app, name="agents")
app.add_typer(generate_app, name="generate")
app.add_typer(jobs_app, name="jobs")
app.add_typer(settings_app, name="settings")
app.add_typer(index_app, name="index")
app.add_typer(backup_app, name="backup")
app.add_typer(family_app, name="family")
app.add_typer(logs_app, name="logs")
app.add_typer(rag_app, name="rag")
app.add_typer(clients_app, name="clients")
app.add_typer(websites_app, name="websites")
app.add_typer(tasks_app, name="tasks")
app.add_typer(images_app, name="images")
app.add_typer(videos_app, name="videos")
app.add_typer(quality_app, name="quality")
app.add_typer(outreach_app, name="outreach")
app.add_typer(recipes_app, name="recipes")


def version_callback(value: bool):
    if value:
        print(f"guaardvark {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=version_callback, is_eager=True),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON (for scripting)"),
    server: str | None = typer.Option(None, "--server", "-s", help="Override server URL"),
    timeout: float | None = typer.Option(None, "--timeout", "-t", help="Request timeout in seconds"),
    theme: str | None = typer.Option(None, "--theme", help="Color theme (default, teal, musk, hacker, vader, guaardvark)"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Verbose output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-essential output"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not start REPL when no command is provided",
    ),
):
    set_global_opts(server=server, json_out=json_out, timeout=timeout, verbose=verbose, quiet=quiet)
    if theme:
        from llx.theme import set_active_theme, THEMES
        if theme in THEMES:
            set_active_theme(theme)

    if ctx.invoked_subcommand is None:
        if non_interactive:
            if json_out:
                output.print_json(
                    {
                        "status": "error",
                        "error": {
                            "code": "NO_COMMAND_PROVIDED",
                            "message": "No command provided in non-interactive mode.",
                            "hint": "Pass a command or remove --non-interactive.",
                        },
                    }
                )
            else:
                typer.echo("No command provided in non-interactive mode. Use --help for usage.", err=True)
            raise typer.Exit(2)

        _maybe_bootstrap_backend()
        from llx.repl import launch_repl
        launch_repl()


def run():
    import sys as _sys
    if _sys.argv and os.path.basename(_sys.argv[0]) == "llx":
        print("Warning: 'llx' is deprecated. Use 'guaardvark' instead.", file=_sys.stderr)
    app()


if __name__ == "__main__":
    run()
