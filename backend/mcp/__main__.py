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


def main(argv: list[str] | None = None) -> int:
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
        help="Write the guaardvark entry into agent client configs (Cursor, Claude, Grok, ...)",
    )
    install_cmd.add_argument(
        "--client",
        action="append",
        dest="clients",
        choices=("cursor", "claude-code", "grok", "claude-desktop", "zed", "gemini"),
        help="Client to configure (repeatable). Default: every client detected on this machine.",
    )
    install_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be written/run without touching anything",
    )

    sub.add_parser(
        "doctor",
        help="Diagnose the MCP setup: server self-test + scan of agent client configs",
    )

    sub.add_parser(
        "list-tools",
        help="Print exposed tools and exit (no transport, useful for smoke tests)",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Debug-level logging to stderr",
    )

    args = parser.parse_args(argv)
    _configure_stderr_logging(logging.DEBUG if args.verbose else logging.INFO)

    cmd = args.cmd or "stdio"

    if cmd == "stdio":
        # Import + tool registry boot are loud — quarantine them from stdout
        # so the JSON-RPC pipe stays clean when Claude Desktop pipes us in.
        with _stdout_to_stderr():
            from backend.mcp.server import build_server, run_stdio
            prebuilt = build_server()
        try:
            asyncio.run(run_stdio(prebuilt=prebuilt))
        except KeyboardInterrupt:
            return 0
        return 0

    if cmd == "http":
        with _stdout_to_stderr():
            from backend.mcp.server import build_server, run_http
            prebuilt = build_server()
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
        return run_install(args.clients, dry_run=args.dry_run)

    if cmd == "doctor":
        from backend.mcp.doctor import run_doctor
        return run_doctor()

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
