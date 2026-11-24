"""Wrap `python -m backend.mcp` — config snippets, install, doctor."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import typer

from llx import output
from llx.commands.system import _find_project_root
from llx.global_opts import get_global_json
from llx.theme import make_console

console = make_console()
mcp_app = typer.Typer(help="MCP server install / doctor for agent clients", no_args_is_help=True)


def _find_checkout() -> Path | None:
    env_root = os.environ.get("GUAARDVARK_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "start.sh").is_file():
            return candidate
        return None

    # Another project's start.sh is not a checkout, in the current folder or above it.
    candidate = Path(_find_project_root(os.getcwd()))
    if (candidate / "start.sh").is_file() and (candidate / "backend" / "mcp" / "__main__.py").is_file():
        return candidate

    curr = Path.cwd().resolve()
    for p in [curr, *curr.parents]:
        if (p / "start.sh").is_file() and (p / "backend" / "mcp" / "__main__.py").is_file():
            return p
    return _installed_checkout()


def _installed_checkout() -> Path | None:
    """MCP clients launch `guaardvark mcp serve` from their own folder, so fall
    back to the checkout this CLI is installed from, then to the install that
    last ran start.sh."""
    own = Path(__file__).resolve().parents[3]
    if (own / "start.sh").is_file() and (own / "backend").is_dir():
        return own
    try:
        import json

        recorded = json.loads((Path.home() / ".guaardvark" / "runtime.json").read_text()).get("root")
        if recorded and (Path(recorded) / "start.sh").is_file():
            return Path(recorded).resolve()
    except (OSError, ValueError):
        pass
    # stop.sh removes runtime.json; `python -m backend.mcp install` leaves this.
    try:
        saved = (Path.home() / ".config" / "guaardvark" / "root").read_text(encoding="utf-8").strip()
        if saved and (Path(saved) / "backend" / "mcp" / "__main__.py").is_file():
            return Path(saved).resolve()
    except OSError:
        pass
    return None


_CHECKOUT_NOT_FOUND_MSG = (
    "Guaardvark checkout not found (looked for start.sh via GUAARDVARK_ROOT, the current folder, "
    "this CLI's own checkout, ~/.guaardvark/runtime.json and ~/.config/guaardvark/root); "
    "a checkout is required (git clone https://github.com/guaardvark/guaardvark + ./start.sh).\n"
)


def _python_and_root() -> tuple[str, Path | None]:
    root = _find_checkout()
    if root is None:
        return sys.executable, None
    venv_py = root / "backend" / "venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    return py, root


def _run_mcp(args: list[str]) -> int:
    py, root = _python_and_root()
    if root is None:
        sys.stderr.write(_CHECKOUT_NOT_FOUND_MSG)
        return 1
    cmd = [py, "-m", "backend.mcp", *args]
    result = subprocess.run(cmd, cwd=str(root))
    return result.returncode


def _run_mcp_json(args: list[str]) -> int:
    """Run a `backend.mcp` subcommand with --json and relay its {status, data}
    report. A missing checkout, or a run that ends without a report, is a JSON
    error as well."""
    from llx.commands.system import _ANSI_ESCAPE

    py, root = _python_and_root()
    if root is None:
        output.print_json({"status": "error", "error": {
            "code": "CHECKOUT_NOT_FOUND", "message": _CHECKOUT_NOT_FOUND_MSG.strip()}})
        return 1
    result = subprocess.run([py, "-m", "backend.mcp", *args, "--json"], cwd=str(root),
                            capture_output=True, text=True)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    if not isinstance(report, dict) or "status" not in report:
        output.print_json({"status": "error", "data": {
            "returncode": result.returncode,
            "output": _ANSI_ESCAPE.sub("", result.stdout),
            "errors": _ANSI_ESCAPE.sub("", result.stderr)[-2000:]}, "error": {
            "code": "MCP_ERROR", "message": f"mcp {args[0]} ended (exit {result.returncode}) without a report"}})
        return result.returncode or 1
    output.print_json(report)
    return result.returncode


# The names live in backend/mcp/config.py (TOOLSETS); the server lists them when given a wrong one.
_TOOLSET_HELP = (
    "Serve one named toolset of at most 15 tools, such as media, knowledge or code, or all "
    "(default: GUAARDVARK_MCP_TOOLSET, then data/config/mcp.json, then all)"
)


@mcp_app.command("serve")
def mcp_serve(
    http: bool = typer.Option(False, "--http", help="Run MCP server over HTTP instead of stdio"),
    toolset: str = typer.Option(None, "--toolset", "-t", help=_TOOLSET_HELP),
):
    """Run the MCP server (stdio by default, or HTTP with --http)."""
    py, root = _python_and_root()
    if root is None:
        sys.stderr.write(_CHECKOUT_NOT_FOUND_MSG)
        raise typer.Exit(1)

    cmd = [py, "-m", "backend.mcp", "http"] if http else [py, "-m", "backend.mcp"]
    if toolset:
        cmd += ["--toolset", toolset]
    os.chdir(str(root))
    os.execv(py, cmd)


@mcp_app.command("config")
def mcp_config(
    client: str = typer.Option(
        ...,
        "--client",
        "-c",
        help="claude-desktop | claude-code | cursor | zed",
    ),
    json_out: bool = typer.Option(False, "--json", "-j",
                                  help="Report the entry, where it goes and any command to run as JSON"),
):
    """Print the JSON snippet to paste into an MCP client config."""
    if json_out or get_global_json():
        output.set_json_mode(True)
        raise typer.Exit(_run_mcp_json(["config", "--client", client]))
    code = _run_mcp(["config", "--client", client])
    raise typer.Exit(code)


@mcp_app.command("install")
def mcp_install(
    client: list[str] = typer.Option(
        None,
        "--client",
        "-c",
        help="Client to configure (repeatable): cursor, claude-code, codex, grok, antigravity, "
             "opencode, claude-desktop, zed, gemini. Default: every detected client.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change, change nothing"),
    skills: bool = typer.Option(False, "--skills", help="Also link Guaardvark's agent skills for Claude Code"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Report what was written, run or skipped as JSON"),
):
    """Write the Guaardvark MCP entry into agent client configs."""
    args = ["install"]
    if dry_run:
        args.append("--dry-run")
    if skills:
        args.append("--skills")
    for c in client or []:
        args.extend(["--client", c])
    if json_out or get_global_json():
        output.set_json_mode(True)
        raise typer.Exit(_run_mcp_json(args))
    raise typer.Exit(_run_mcp(args))


@mcp_app.command("doctor")
def mcp_doctor(
    call: bool = typer.Option(False, "--call", help="Also make a few read-only tool calls"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output every check as JSON"),
):
    """Diagnose MCP server + client config."""
    args = ["doctor", "--call"] if call else ["doctor"]
    if json_out or get_global_json():
        output.set_json_mode(True)
        raise typer.Exit(_run_mcp_json(args))
    raise typer.Exit(_run_mcp(args))


@mcp_app.command("list-tools")
def mcp_list_tools(
    toolset: str = typer.Option(None, "--toolset", "-t", help=_TOOLSET_HELP),
    json_out: bool = typer.Option(False, "--json", "-j",
                                  help="Report the tools, their toolsets and the toolset served as JSON"),
):
    """Print tools the MCP server exposes."""
    args = ["list-tools", "--toolset", toolset] if toolset else ["list-tools"]
    if json_out or get_global_json() or output.is_json_mode():
        output.set_json_mode(True)
        raise typer.Exit(_list_tools_json(args))
    raise typer.Exit(_run_mcp(args))


_SERVED_TOOLSET = re.compile(r"\((\d+) tools exposed, toolset (\S+)\)")


def _list_tools_json(args: list[str]) -> int:
    """`backend.mcp list-tools --json` prints a bare array; wrap it in the
    {status, data} document the other commands print."""
    from llx.commands.system import _ANSI_ESCAPE

    py, root = _python_and_root()
    if root is None:
        output.print_json({"status": "error", "error": {
            "code": "CHECKOUT_NOT_FOUND", "message": _CHECKOUT_NOT_FOUND_MSG.strip()}})
        return 1
    result = subprocess.run([py, "-m", "backend.mcp", *args, "--json"], cwd=str(root),
                            capture_output=True, text=True)
    errors = _ANSI_ESCAPE.sub("", result.stderr)
    try:
        tools = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        tools = None
    if not isinstance(tools, list):
        reason = next((line for line in reversed(errors.splitlines()) if line.strip()), "")
        output.print_json({"status": "error", "data": {
            "returncode": result.returncode, "errors": errors[-2000:]}, "error": {
            "code": "MCP_ERROR",
            "message": reason or f"mcp list-tools ended (exit {result.returncode}) without a tool list"}})
        return result.returncode or 1
    served = _SERVED_TOOLSET.search(errors)
    output.print_json({"status": "success", "data": {
        "toolset": served.group(2) if served else None, "count": len(tools), "tools": tools}})
    return 0


# `guaardvark mcp client ...`: the external MCP servers the agent itself uses.
from llx.commands.mcp_client import mcp_client_app  # noqa: E402

mcp_app.add_typer(mcp_client_app, name="client")
