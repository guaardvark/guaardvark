"""Wrap `python -m backend.mcp` — config snippets, install, doctor."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from llx import output
from llx.commands.system import _find_project_root
from llx.theme import make_console

console = make_console()
mcp_app = typer.Typer(help="MCP server install / doctor for agent clients", no_args_is_help=True)


def _python_and_root() -> tuple[str, Path]:
    root = Path(_find_project_root(os.environ.get("GUAARDVARK_ROOT") or os.getcwd()))
    venv_py = root / "backend" / "venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    return py, root


def _run_mcp(args: list[str]) -> int:
    py, root = _python_and_root()
    cmd = [py, "-m", "backend.mcp", *args]
    result = subprocess.run(cmd, cwd=str(root))
    return result.returncode


@mcp_app.command("config")
def mcp_config(
    client: str = typer.Option(
        ...,
        "--client",
        "-c",
        help="claude-desktop | claude-code | cursor | zed",
    ),
):
    """Print the JSON snippet to paste into an MCP client config."""
    code = _run_mcp(["config", "--client", client])
    raise typer.Exit(code)


@mcp_app.command("install")
def mcp_install(
    client: list[str] = typer.Option(
        None,
        "--client",
        "-c",
        help="Client to configure (repeatable). Default: every detected client.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Write the Guaardvark MCP entry into agent client configs."""
    args = ["install"]
    if dry_run:
        args.append("--dry-run")
    for c in client or []:
        args.extend(["--client", c])
    raise typer.Exit(_run_mcp(args))


@mcp_app.command("doctor")
def mcp_doctor():
    """Diagnose MCP server + client config."""
    raise typer.Exit(_run_mcp(["doctor"]))


@mcp_app.command("list-tools")
def mcp_list_tools():
    """Print tools the MCP server exposes."""
    raise typer.Exit(_run_mcp(["list-tools"]))
