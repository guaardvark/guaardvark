"""Job management commands — list, status, watch, cancel."""

import time

import typer
from rich.style import Style
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from llx.client import get_client, LlxError
from llx.global_opts import get_global_json, get_global_server
from llx.job_status import TERMINAL, read_job
from llx.theme import make_console, BRAND, SUCCESS
from llx import output

console = make_console()
jobs_app = typer.Typer(help="Background job management", no_args_is_help=True)


@jobs_app.command("list")
def jobs_list(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List active jobs, by the ids `jobs status` reads."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        # The unified job registry: every id it lists resolves at /api/jobs/<id>,
        # which `jobs status` reads (the progress list's ids did not).
        data = client.get("/api/jobs/active")
        jobs = (data.get("jobs") if isinstance(data, dict) else data) or []
        if not isinstance(jobs, list):
            jobs = [jobs]

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"jobs": jobs}})
            return

        rows = [{
            "id": j.get("id", ""),
            "name": j.get("label", ""),
            "type": j.get("kind", ""),
            "status": j.get("status", ""),
            "progress": f"{j['progress']:.0f}%" if isinstance(j.get("progress"), (int, float)) else "",
        } for j in jobs]
        output.print_table(rows, columns=["id", "name", "type", "status", "progress"], title="Jobs")

    except LlxError as e:
        raise output.fail(e)


@jobs_app.command("status")
def jobs_status(
    job_id: str = typer.Argument(..., help="Job ID as a command printed it (ImageBatch_…, VideoBatch_…, a task number, …)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Check status of a specific job."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        info = read_job(get_client(server), job_id)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {k: v for k, v in info.items() if k != "raw"}})
            return

        pct = info.get("percent")
        rows = {
            "Job ID": info["id"],
            "Kind": info["kind"],
            "Status": info["status"],
            "Progress": f"{pct:.0f}%" if isinstance(pct, (int, float)) else "—",
            "Message": info.get("message") or "—",
        }
        if info.get("error"):
            rows["Error"] = info["error"]
        for n, f in enumerate(info.get("files") or [], 1):
            rows[f"File {n}"] = f
        output.print_kv(rows, title="Job Status")

    except LlxError as e:
        raise output.fail(e)


def watch_job(client, job_id: str, interval: float = 1.5, timeout: float = 0, quiet: bool = False) -> dict:
    """Poll a job until it ends or `timeout` seconds pass; return its last state.

    quiet draws nothing, for JSON output and pipes.
    """
    info = read_job(client, job_id)
    started = time.monotonic()

    def _expired() -> bool:
        return bool(timeout) and time.monotonic() - started > timeout

    if quiet:
        while info["status"] not in TERMINAL and not _expired():
            time.sleep(max(0.2, interval))
            info = read_job(client, job_id)
        return info

    with Progress(
        TextColumn("[llx.brand]{task.description}"),
        BarColumn(complete_style=Style(color=BRAND), finished_style=Style(color=SUCCESS)),
        TextColumn("[llx.dim]{task.percentage:>3.0f}%[/llx.dim]"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"{info['kind']} {job_id}", total=100)
        while True:
            pct = info.get("percent")
            label = info.get("message") or info["kind"]
            if isinstance(pct, (int, float)):
                progress.update(task, completed=pct, description=label)
            else:
                progress.update(task, description=label)
            if info["status"] in TERMINAL or _expired():
                break
            time.sleep(max(0.2, interval))
            info = read_job(client, job_id)

        if info["status"] == "completed":
            progress.update(task, completed=100, description="[llx.success]Complete[/llx.success]")
        elif info["status"] in ("failed", "cancelled"):
            progress.update(task, description=f"[llx.error]{info['status'].capitalize()}[/llx.error]")
    return info


def job_failure_json(info: dict) -> dict:
    return {"status": "error", "data": info, "error": {
        "code": "JOB_" + info["status"].upper(), "message": str(info.get("error") or info["status"])}}


@jobs_app.command("watch")
def jobs_watch(
    job_id: str = typer.Argument(..., help="Job ID to watch"),
    interval: float = typer.Option(1.5, "--interval", "-i", help="Seconds between checks"),
    timeout: float = typer.Option(0, "--timeout", help="Give up after this many seconds (0 = wait until it ends)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="No progress bar; print the final job state as JSON"),
):
    """Live-watch job progress until it finishes."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        info = watch_job(client, job_id, interval=interval, timeout=timeout, quiet=json_out)

        if json_out:
            if info["status"] in ("failed", "cancelled"):
                output.print_json(job_failure_json(info))
                raise typer.Exit(output.EXIT_JOB_FAILED)
            output.print_json({"status": "success", "data": info})
            return

        if info["status"] == "completed":
            for f in info.get("files") or []:
                console.print(f"  [llx.accent]{f}[/llx.accent]")
            try:
                from llx.notify import notify

                notify("Guaardvark", f"Job {job_id} complete")
            except Exception:
                pass
        elif info["status"] in ("failed", "cancelled"):
            if info.get("error"):
                output.print_error(str(info["error"]), code="JOB_FAILED")
            raise typer.Exit(output.EXIT_JOB_FAILED)
        else:
            console.print(f"[llx.dim]Still {info['status']}; stopped watching after {timeout:.0f} s.[/llx.dim]")

    except LlxError as e:
        raise output.fail(e)


@jobs_app.command("cancel")
def jobs_cancel(
    job_id: str = typer.Argument(..., help="Job ID to cancel"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Cancel a running job."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        if ":" in job_id:
            # Ids as `jobs list` prints them (kind:native id). This route says
            # whether anything was cancelled; the legacy one answers 200 either way.
            data = client.post(f"/api/jobs/{job_id}/cancel")
            if not data.get("cancelled"):
                output.print_error(data.get("reason") or f"Job {job_id} was not cancelled", code="CANCEL_FAILED")
                raise typer.Exit(1)
        else:
            data = client.post(f"/api/meta/cancel_job/{job_id}")

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
        else:
            msg = data.get("message", f"Job {job_id} cancelled.")
            output.print_success(msg)

    except LlxError as e:
        raise output.fail(e)
