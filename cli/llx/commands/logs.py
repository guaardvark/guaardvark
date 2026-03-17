"""Live log viewer — tail, search, and filter across services."""

import os
import time
import re
import typer
from rich.text import Text

from llx.global_opts import get_global_json
from llx.theme import make_console
from llx import output

console = make_console()
logs_app = typer.Typer(help="Log viewing and analysis", no_args_is_help=True)

# Detect project root
_ROOT = os.environ.get(
    "GUAARDVARK_ROOT",
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)
_LOG_DIR = os.path.join(_ROOT, "logs")

SERVICE_LOGS = {
    "backend": "backend.log",
    "celery": "celery.log",
    "frontend": "frontend.log",
    "startup": "backend_startup.log",
    "setup": "setup.log",
}


def _find_log(service: str) -> str | None:
    filename = SERVICE_LOGS.get(service, f"{service}.log")
    path = os.path.join(_LOG_DIR, filename)
    return path if os.path.isfile(path) else None


def _colorize_line(line: str) -> Text:
    """Apply color coding based on log level."""
    text = Text(line.rstrip())
    lower = line.lower()
    if "error" in lower or "exception" in lower or "traceback" in lower:
        text.stylize("bold red")
    elif "warning" in lower or "warn" in lower:
        text.stylize("yellow")
    elif "info" in lower:
        text.stylize("dim")
    elif "debug" in lower:
        text.stylize("dim italic")
    return text


@logs_app.command("tail")
def logs_tail(
    service: str = typer.Argument(
        "backend", help="Service to tail (backend, celery, frontend, startup, setup)"
    ),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Follow log output (like tail -f)"
    ),
    filter_str: str = typer.Option(
        None, "--filter", help="Show only lines matching this string"
    ),
):
    """Tail a service log with color-coded output."""
    log_path = _find_log(service)
    if not log_path:
        output.print_error(
            f"Log file not found for '{service}'. Available: {', '.join(SERVICE_LOGS.keys())}"
        )
        raise typer.Exit(1)

    console.print(f"[llx.accent]{service}[/llx.accent] [llx.dim]{log_path}[/llx.dim]\n")

    try:
        with open(log_path, "r") as f:
            # Read last N lines
            all_lines = f.readlines()
            tail_lines = all_lines[-lines:]

            for line in tail_lines:
                if filter_str and filter_str.lower() not in line.lower():
                    continue
                console.print(_colorize_line(line))

            if not follow:
                return

            # Follow mode
            console.print(
                f"\n[llx.dim]--- Following {service} log (Ctrl+C to stop) ---[/llx.dim]\n"
            )
            while True:
                line = f.readline()
                if line:
                    if filter_str and filter_str.lower() not in line.lower():
                        continue
                    console.print(_colorize_line(line))
                else:
                    time.sleep(0.3)

    except KeyboardInterrupt:
        console.print("\n[llx.dim]Stopped.[/llx.dim]")
    except FileNotFoundError:
        output.print_error(f"Log file not found: {log_path}")
        raise typer.Exit(1)


@logs_app.command("search")
def logs_search(
    pattern: str = typer.Argument(help="Search pattern (regex supported)"),
    service: str = typer.Option(None, "--service", "-s", help="Limit to one service"),
    lines: int = typer.Option(
        5, "--context", "-C", help="Context lines around matches"
    ),
    max_results: int = typer.Option(20, "--max", "-m", help="Maximum results to show"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Search across all service logs with regex support."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    services = (
        {service: SERVICE_LOGS[service]}
        if service and service in SERVICE_LOGS
        else SERVICE_LOGS
    )
    results = []

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        output.print_error(f"Invalid regex: {e}")
        raise typer.Exit(1)

    for svc, filename in services.items():
        path = os.path.join(_LOG_DIR, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r") as f:
                file_lines = f.readlines()
            for i, line in enumerate(file_lines):
                if regex.search(line):
                    context_start = max(0, i - lines)
                    context_end = min(len(file_lines), i + lines + 1)
                    results.append(
                        {
                            "service": svc,
                            "line_number": i + 1,
                            "match": line.rstrip(),
                            "context": [
                                l.rstrip()
                                for l in file_lines[context_start:context_end]
                            ],
                        }
                    )
                    if len(results) >= max_results:
                        break
        except Exception:
            continue
        if len(results) >= max_results:
            break

    if json_out or output.is_pipe():
        output.print_json(results)
        return

    if not results:
        console.print(f"[llx.dim]No matches for '{pattern}'[/llx.dim]")
        return

    console.print(
        f"[llx.accent]Found {len(results)} match(es) for[/llx.accent] [bold]{pattern}[/bold]\n"
    )
    for r in results:
        console.print(
            f"[llx.brand]{r['service']}[/llx.brand]:[llx.dim]{r['line_number']}[/llx.dim]"
        )
        for ctx_line in r["context"]:
            if regex.search(ctx_line):
                console.print(f"  [bold red]> {ctx_line}[/bold red]")
            else:
                console.print(f"  [llx.dim]  {ctx_line}[/llx.dim]")
        console.print()


@logs_app.command("stats")
def logs_stats(
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show error/warning counts across all service logs."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    stats = {}
    for svc, filename in SERVICE_LOGS.items():
        path = os.path.join(_LOG_DIR, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r") as f:
                content = f.read()
            size = os.path.getsize(path)
            errors = len(re.findall(r"\bERROR\b", content, re.IGNORECASE))
            warnings = len(re.findall(r"\bWARNING\b", content, re.IGNORECASE))
            total_lines = content.count("\n")
            stats[svc] = {
                "lines": total_lines,
                "errors": errors,
                "warnings": warnings,
                "size_mb": round(size / (1024 * 1024), 1),
            }
        except Exception:
            continue

    if json_out or output.is_pipe():
        output.print_json(stats)
        return

    if not stats:
        console.print("[llx.dim]No log files found.[/llx.dim]")
        return

    rows = []
    for svc, s in stats.items():
        err_str = (
            f"[red]{s['errors']}[/red]" if s["errors"] > 0 else "[llx.dim]0[/llx.dim]"
        )
        warn_str = (
            f"[yellow]{s['warnings']}[/yellow]"
            if s["warnings"] > 0
            else "[llx.dim]0[/llx.dim]"
        )
        rows.append(
            {
                "Service": svc,
                "Lines": str(s["lines"]),
                "Errors": err_str,
                "Warnings": warn_str,
                "Size": f"{s['size_mb']} MB",
            }
        )

    from rich.table import Table

    table = Table(title="Log Statistics", border_style="llx.panel.border")
    table.add_column("Service", style="llx.accent")
    table.add_column("Lines", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Warnings", justify="right")
    table.add_column("Size", justify="right")
    for r in rows:
        table.add_row(r["Service"], r["Lines"], r["Errors"], r["Warnings"], r["Size"])
    console.print(table)
