"""Job management commands — list, status, watch, cancel."""

import typer
from rich.live import Live
from rich.style import Style
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.streaming import LlxStreamer
from llx.theme import make_console, BRAND, SUCCESS
from llx import output

console = make_console()
jobs_app = typer.Typer(help="Background job management")


@jobs_app.command("list")
def jobs_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List recent jobs."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/meta/active_jobs")
        jobs = data.get("active_jobs", [])
        if not isinstance(jobs, list):
            jobs = [jobs] if jobs else []

        if json_out or output.is_pipe():
            output.print_json(jobs)
            return

        rows = [{
            "id": j.get("task_id", j.get("id", "")),
            "name": j.get("name", ""),
            "type": j.get("type", ""),
            "status": j.get("status", ""),
        } for j in jobs]
        output.print_table(rows, columns=["id", "name", "type", "status"], title="Jobs")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@jobs_app.command("status")
def jobs_status(
    task_id: int = typer.Argument(..., help="Task/job ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check status of a specific job."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/jobs/{task_id}/status")

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        progress = data.get("progress", {})
        output.print_kv({
            "Task ID": data.get("task_id", ""),
            "Job ID": data.get("job_id", ""),
            "Name": data.get("name", ""),
            "Status": data.get("status", ""),
            "Progress": f"{progress.get('percentage', 0)}%",
            "Message": progress.get("message", "—"),
        }, title="Job Status")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@jobs_app.command("watch")
def jobs_watch(
    job_id: str = typer.Argument(..., help="Job ID to watch"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Live-watch job progress."""
    server = server or get_global_server()
    try:
        client = get_client(server)
        streamer = LlxStreamer(server_url=client.server_url)

        with Progress(
            TextColumn("[llx.brand]{task.description}"),
            BarColumn(complete_style=Style(color=BRAND), finished_style=Style(color=SUCCESS)),
            TextColumn("[llx.dim]{task.percentage:>3.0f}%[/llx.dim]"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Job {job_id}", total=100)

            def on_progress(data):
                pct = data.get("percentage", data.get("progress", 0))
                msg = data.get("message", data.get("status", ""))
                progress.update(task, completed=pct, description=msg or f"Job {job_id}")

            def on_complete(data):
                progress.update(task, completed=100, description="[llx.success]Complete[/llx.success]")

            def on_error(msg):
                progress.update(task, description=f"[llx.error]{msg}[/llx.error]")

            streamer.watch_job(job_id, on_progress=on_progress, on_complete=on_complete, on_error=on_error)
            streamer.wait(timeout=600)
            streamer.disconnect()

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@jobs_app.command("cancel")
def jobs_cancel(
    job_id: str = typer.Argument(..., help="Job ID to cancel"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Cancel a running job."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post(f"/api/meta/cancel_job/{job_id}")

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            msg = data.get("message", f"Job {job_id} cancelled.")
            output.print_success(msg)

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
