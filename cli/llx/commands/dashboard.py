"""Live system dashboard with auto-refreshing metrics."""

import time
import typer
from rich.live import Live
from rich.panel import Panel

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_server
from llx.theme import make_console, ICON_ONLINE, ICON_OFFLINE

console = make_console()


def dashboard(
    interval: float = typer.Option(
        3.0, "--interval", "-i", help="Refresh interval in seconds"
    ),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Live system dashboard with auto-refreshing metrics. Press Ctrl+C to exit."""
    # When called directly (not via Typer CLI), Options remain as OptionInfo objects
    if not isinstance(interval, (int, float)):
        interval = 3.0
    server = server or get_global_server()

    try:
        client = get_client(server)
        # Quick connection test
        client.get("/api/health")
    except (LlxConnectionError, LlxError) as e:
        console.print(f"[llx.error]Cannot connect: {e}[/llx.error]")
        raise typer.Exit(1)

    console.print("[llx.dim]Dashboard starting... Press Ctrl+C to exit.[/llx.dim]\n")

    try:
        with Live(console=console, refresh_per_second=1, screen=False) as live:
            while True:
                panel = _build_dashboard(client)
                live.update(panel)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[llx.dim]Dashboard stopped.[/llx.dim]")


def _safe_get(client, path: str) -> dict:
    """Fetch endpoint, return empty dict on failure."""
    try:
        return client.get(path)
    except Exception:
        return {}


def _build_dashboard(client) -> Panel:
    """Build the full dashboard panel from API data."""
    health = _safe_get(client, "/api/health")
    model = _safe_get(client, "/api/model/status")
    celery = _safe_get(client, "/api/health/celery")
    metrics = _safe_get(client, "/api/meta/metrics")
    gpu = _safe_get(client, "/api/gpu/status")
    jobs_data = _safe_get(client, "/api/meta/active_jobs")

    lines = []

    # -- Server --
    status = health.get("status", "?")
    version = health.get("version", "?")
    uptime = health.get("uptime_seconds", 0)
    uptime_str = _format_uptime(uptime)
    if status == "ok":
        lines.append(
            f"  [llx.status.online]{ICON_ONLINE} Server[/llx.status.online]   [llx.dim]v{version}  |  up {uptime_str}[/llx.dim]"
        )
    else:
        lines.append(
            f"  [llx.status.offline]{ICON_OFFLINE} Server[/llx.status.offline]   [llx.dim]{status}[/llx.dim]"
        )

    # -- Model --
    model_info = model.get("data", model.get("message", {}))
    if isinstance(model_info, str):
        model_info = {}
    text_model = model_info.get("text_model", "?")
    lines.append(f"  [llx.accent]Model[/llx.accent]      {text_model}")

    # -- Celery --
    celery_status = celery.get("status", "?")
    active_tasks = celery.get("active_tasks", 0)
    celery_icon = ICON_ONLINE if celery_status == "up" else ICON_OFFLINE
    celery_style = (
        "llx.status.online" if celery_status == "up" else "llx.status.offline"
    )
    lines.append(
        f"  [{celery_style}]{celery_icon} Celery[/{celery_style}]    {celery_status}  [llx.dim]|  {active_tasks} active tasks[/llx.dim]"
    )

    # -- GPU --
    gpu_pct = metrics.get("gpu_percent")
    gpu_temp = metrics.get("gpu_temp")
    gpu_mem = metrics.get("gpu_mem")
    gpu_owner = gpu.get("owner", "none")
    if gpu_pct is not None:
        gpu_color = (
            "llx.success"
            if gpu_pct < 50
            else ("llx.warning" if gpu_pct < 80 else "llx.error")
        )
        gpu_line = f"  [llx.accent]GPU[/llx.accent]        [{gpu_color}]{gpu_pct:.0f}% util[/{gpu_color}]"
        if gpu_mem is not None:
            mem_color = (
                "llx.success"
                if gpu_mem < 50
                else ("llx.warning" if gpu_mem < 80 else "llx.error")
            )
            gpu_line += f"  [{mem_color}]{gpu_mem:.0f}% VRAM[/{mem_color}]"
        if gpu_temp is not None:
            gpu_line += f"  [llx.dim]{gpu_temp:.0f}C[/llx.dim]"
        if gpu_owner != "none":
            gpu_line += f"  [llx.warning]lock: {gpu_owner}[/llx.warning]"
        lines.append(gpu_line)
    else:
        lines.append(
            "  [llx.accent]GPU[/llx.accent]        [llx.dim]unavailable[/llx.dim]"
        )

    # -- CPU --
    cpu_pct = metrics.get("cpu_percent")
    cpu_mem = metrics.get("cpu_mem")
    if cpu_pct is not None:
        cpu_color = (
            "llx.success"
            if cpu_pct < 50
            else ("llx.warning" if cpu_pct < 80 else "llx.error")
        )
        cpu_line = f"  [llx.accent]CPU[/llx.accent]        [{cpu_color}]{cpu_pct:.0f}%[/{cpu_color}]"
        if cpu_mem is not None:
            cpu_line += f"  [llx.dim]RAM {cpu_mem:.0f}%[/llx.dim]"
        lines.append(cpu_line)

    # -- Jobs --
    active_jobs = jobs_data.get("active_jobs", [])
    stuck_count = jobs_data.get("stuck_count", 0)
    if active_jobs:
        lines.append("")
        lines.append(
            f"  [llx.brand_bright]Active Jobs ({len(active_jobs)})[/llx.brand_bright]"
        )
        for job in active_jobs[:5]:
            name = job.get("description", job.get("command", job.get("id", "?")))[:30]
            progress = job.get("progress", 0)
            total = job.get("total", 100)
            pct = (progress / total * 100) if total > 0 else 0
            status_str = job.get("status", "?")
            pct_color = "llx.success" if pct >= 100 else "llx.accent"
            lines.append(
                f"    [llx.dim]{name:<30}[/llx.dim] [{pct_color}]{pct:5.1f}%[/{pct_color}]  [llx.dim]{status_str}[/llx.dim]"
            )
        if len(active_jobs) > 5:
            lines.append(f"    [llx.dim]... and {len(active_jobs) - 5} more[/llx.dim]")
    if stuck_count > 0:
        lines.append(f"  [llx.warning]Stuck jobs: {stuck_count}[/llx.warning]")

    # -- Timestamp --
    lines.append("")
    lines.append(f"  [llx.dim]Last refresh: {time.strftime('%H:%M:%S')}[/llx.dim]")

    content = "\n".join(lines)
    return Panel(
        content,
        title="[llx.brand_bright]Guaardvark Dashboard[/llx.brand_bright]",
        border_style="llx.panel.border",
        padding=(1, 1),
    )


def _format_uptime(seconds) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    return f"{hours}h {mins}m"
