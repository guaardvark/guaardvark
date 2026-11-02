"""
``python -m backend.mcp`` entrypoint.

Default subcommand is ``stdio`` (run the MCP server on stdin/stdout) so
that bare ``python -m backend.mcp`` works as the ``command`` line in
Claude Desktop / Claude Code / Cursor / Zed config files — no extra
args required.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from contextlib import contextmanager

# Tell shared services this is the MCP stdio process, not the API server
# (e.g. BatchVideoGenerator must not resume on-disk batches here).
os.environ.setdefault("GUAARDVARK_MCP_PROCESS", "1")

# The MCP client starts this process, not start.sh, so it sets start.sh's
# quiet-library settings itself, before anything imports huggingface_hub
# (which reads them once, at import).
for _key, _value in (
    ("HF_HUB_DISABLE_TELEMETRY", "1"),
    ("DO_NOT_TRACK", "1"),
    ("ORT_DISABLE_TELEMETRY", "1"),
    ("UNSLOTH_DISABLE_STATISTICS", "1"),
    ("PIP_DISABLE_PIP_VERSION_CHECK", "1"),
):
    os.environ.setdefault(_key, _value)


def _configure_stderr_logging(level: int = logging.INFO) -> None:
    # Log to STDERR only — stdout is the JSON-RPC pipe.
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


@contextmanager
def _stdout_to_stderr():
    """
    Redirect ``sys.stdout`` *and* the underlying fd to stderr while backend
    modules run their noisy import-time ``print()`` calls (CUDA banner,
    PyNVML warnings, pool-init logs, …). Without this, a single stray
    ``print`` during boot corrupts the JSON-RPC pipe for Claude Desktop.

    Restored before the SDK touches stdout for real traffic.
    """
    saved_stdout = sys.stdout
    saved_stdout_fd = os.dup(1)
    try:
        os.dup2(2, 1)  # fd 1 → fd 2 (stderr)
        sys.stdout = sys.stderr
        yield
    finally:
        sys.stdout = saved_stdout
        os.dup2(saved_stdout_fd, 1)
        os.close(saved_stdout_fd)


def _configure_logging(cmd: str | None, verbose: bool = False) -> None:
    """Keep server diagnostics intact and one-shot commands quiet by default."""
    if cmd in (None, "stdio", "http"):
        _configure_stderr_logging(logging.DEBUG if verbose else logging.INFO)
    else:
        level = logging.INFO if verbose else logging.WARNING
        _configure_stderr_logging(level)
        # Filter records from dependencies that explicitly set their own level.
        for handler in logging.getLogger().handlers:
            handler.setLevel(level)


def _build_parser() -> argparse.ArgumentParser:
    from backend.mcp.installer import CLIENTS

    parser = argparse.ArgumentParser(prog="python -m backend.mcp")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("stdio", help="Run MCP server on stdin/stdout (default)")

    http_cmd = sub.add_parser("http", help="Run MCP server over streamable HTTP")
    http_cmd.add_argument("--host", default="127.0.0.1",
                          help="Bind address (default: 127.0.0.1 — no auth, keep it local)")
    http_cmd.add_argument("--port", type=int, default=8788, help="Port (default: 8788)")

    config_cmd = sub.add_parser("config", help="Print client install snippet")
    config_cmd.add_argument(
        "--client",
        required=True,
        choices=("claude-desktop", "claude-code", "cursor", "zed"),
    )

    install_cmd = sub.add_parser(
        "install",
        help="Write the guaardvark entry into agent client configs (Cursor, Claude, Codex, Grok, ...)",
    )
    install_cmd.add_argument(
        "--client",
        action="append",
        dest="clients",
        choices=CLIENTS,
        help="Client to configure (repeatable). Default: every client detected on this machine.",
    )
    install_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be written/run without touching anything",
    )
    install_cmd.add_argument(
        "--skills", action="store_true",
        help="Also link the agent skills in .agents/skills into ~/.claude/skills "
             "(and into this checkout's .claude/skills) so Claude Code loads them",
    )

    doctor_cmd = sub.add_parser(
        "doctor",
        help="Diagnose the MCP setup: server self-test + scan of agent client configs",
    )
    doctor_cmd.add_argument(
        "--call", action="store_true",
        help="Also make real read-only tool calls, one per tool family",
    )

    list_tools_cmd = sub.add_parser(
        "list-tools",
        help="Print exposed tools and exit (no transport, useful for smoke tests)",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="INFO logging for one-shot commands; DEBUG logging for servers (stderr)",
    )
    for command in (config_cmd, install_cmd, doctor_cmd, list_tools_cmd):
        command.add_argument(
            "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
            help="Show INFO-level logging to stderr",
        )
    return parser


def _refuse_to_start(reason: Exception) -> int:
    """Say on stderr why no server was started. stdout stays empty: it is the
    JSON-RPC pipe, and a client shows the server's stderr in its MCP log."""
    print(f"guaardvark mcp: {reason}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.cmd, args.verbose)

    cmd = args.cmd or "stdio"

    if cmd == "stdio":
        # Import + tool registry boot are loud — quarantine them from stdout
        # so the JSON-RPC pipe stays clean when Claude Desktop pipes us in.
        with _stdout_to_stderr():
            from backend.mcp.server import MCPServerDisabled, build_server, run_stdio
            try:
                prebuilt = build_server()
            except MCPServerDisabled as exc:
                return _refuse_to_start(exc)
        try:
            asyncio.run(run_stdio(prebuilt=prebuilt))
        except KeyboardInterrupt:
            return 0
        return 0

    if cmd == "http":
        with _stdout_to_stderr():
            from backend.mcp.server import MCPServerDisabled, build_server, run_http
            try:
                prebuilt = build_server()
            except MCPServerDisabled as exc:
                return _refuse_to_start(exc)
        try:
            run_http(host=args.host, port=args.port, prebuilt=prebuilt)
        except KeyboardInterrupt:
            return 0
        return 0

    if cmd == "config":
        from backend.mcp.cli import print_snippet
        return print_snippet(args.client)

    if cmd == "install":
        from backend.mcp.installer import run_install
        return run_install(args.clients, dry_run=args.dry_run, skills=args.skills)

    if cmd == "doctor":
        from backend.mcp.doctor import run_doctor
        return run_doctor(call=args.call)

    if cmd == "list-tools":
        with _stdout_to_stderr():
            from backend.mcp.config import load_config
            from backend.mcp.server import _ensure_tools_initialized
            from backend.mcp.tools_adapter import collect_exposed_tools
            _ensure_tools_initialized()
            exposed = collect_exposed_tools(load_config())
        for _base, mcp_tool in exposed:
            print(f"{mcp_tool.name}\t{(mcp_tool.description or '').splitlines()[0][:80]}")
        print(f"\n({len(exposed)} tools exposed)", file=sys.stderr)
        return 0

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
