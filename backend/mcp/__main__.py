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

    config_cmd = sub.add_parser("config", help="Print client install snippet")
    config_cmd.add_argument(
        "--client",
        required=True,
        choices=("claude-desktop", "claude-code", "cursor", "zed"),
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

    if cmd == "config":
        from backend.mcp.cli import print_snippet
        return print_snippet(args.client)

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
