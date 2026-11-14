"""What kind of process built the app."""

import os
import sys
from typing import List, Optional

# Options of the `flask` command itself that take a value.
_FLASK_VALUE_OPTIONS = ("-A", "--app", "-e", "--env-file")


def flask_cli_command(argv: Optional[List[str]] = None) -> Optional[str]:
    """The command a `flask ...` invocation runs ("run", "db", "shell", ...), else None.

    None for anything not started through the flask command line: Celery,
    scripts, tests, and children of the server, which inherit its
    FLASK_RUN_FROM_CLI.
    """
    argv = list(sys.argv if argv is None else argv)
    if os.environ.get("FLASK_RUN_FROM_CLI") != "true" or not argv:
        return None
    program = argv[0].replace("\\", "/")
    if os.path.basename(program) != "flask" and not program.endswith("flask/__main__.py"):
        return None
    args = iter(argv[1:])
    for arg in args:
        if arg in _FLASK_VALUE_OPTIONS:
            next(args, None)
        elif not arg.startswith("-"):
            return arg
    return None


def one_shot_command(argv: Optional[List[str]] = None) -> bool:
    """True in a flask command that builds the app, does one thing and exits
    (`flask db upgrade`, `flask load-rules`), as opposed to the server.

    Such a process must not start loading the chat model: it exits mid-load,
    and Ollama aborts the load when the connection closes. `flask db upgrade`
    did that at every Docker start.
    """
    command = flask_cli_command(argv)
    return command is not None and command != "run"
