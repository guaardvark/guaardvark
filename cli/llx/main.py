import typer

from llx import __version__
from llx.global_opts import set_global_opts
from llx.commands.system import health, status, init, doctor, start, stop, models_app
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

app = typer.Typer(
    name="guaardvark",
    help="[#a29bfe]Guaardvark CLI[/#a29bfe] — chat, search, manage files, and more from the terminal.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

app.command("health")(health)
app.command("status")(status)
app.command("init")(init)
app.command("doctor")(doctor)
app.command("start")(start)
app.command("stop")(stop)
app.command("chat")(chat)
app.command("search")(search)
app.command("dashboard")(dashboard)

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
):
    set_global_opts(server=server, json_out=json_out, timeout=timeout, verbose=verbose, quiet=quiet)
    if theme:
        from llx.theme import set_active_theme, THEMES
        if theme in THEMES:
            set_active_theme(theme)
    if ctx.invoked_subcommand is None:
        from llx.repl import launch_repl
        launch_repl()


def run():
    app()


if __name__ == "__main__":
    run()
