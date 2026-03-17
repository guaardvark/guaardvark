"""Task management commands — list, create, info, start, delete, download."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
tasks_app = typer.Typer(
    help="Task management (create, run, monitor)", no_args_is_help=True
)


@tasks_app.command("list")
def tasks_list(
    status: str = typer.Option(
        None, "--status", help="Filter by status (queued, running, completed, failed)"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List tasks."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        params = {"limit": limit}
        if status:
            params["status"] = status
        data = api_client.get("/api/tasks", **params)
        tasks = (
            data if isinstance(data, list) else data.get("data", data.get("tasks", []))
        )
        if not isinstance(tasks, list):
            tasks = []

        if json_out or output.is_pipe():
            output.print_json(tasks)
            return

        rows = [
            {
                "id": t.get("id", ""),
                "name": t.get("name", t.get("task_type", "")),
                "type": t.get("task_type", ""),
                "status": t.get("status", ""),
                "progress": f"{t.get('progress', 0)}%",
            }
            for t in tasks
        ]
        output.print_table(
            rows,
            columns=["id", "name", "type", "status", "progress"],
            title=f"Tasks ({len(rows)})",
        )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@tasks_app.command("create")
def tasks_create(
    task_type: str = typer.Argument(
        ..., help="Task type (code_task, csv_generation, content_task, analysis_task)"
    ),
    prompt: str = typer.Argument(..., help="Task prompt/description"),
    name: str = typer.Option(None, "--name", "-n", help="Task name"),
    project_id: int = typer.Option(None, "--project", "-p", help="Project ID"),
    client_id: int = typer.Option(None, "--client", "-c", help="Client ID"),
    auto_start: bool = typer.Option(
        True, "--start/--no-start", help="Start task immediately (default: yes)"
    ),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new task."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {
            "task_type": task_type,
            "prompt": prompt,
            "name": name or f"{task_type}: {prompt[:50]}",
        }
        if project_id:
            body["project_id"] = project_id
        if client_id:
            body["client_id"] = client_id

        data = api_client.post("/api/tasks", json=body)
        result = data.get("data", data) if isinstance(data, dict) else data
        task_id = result.get("id", result.get("task_id", "?"))

        if json_out or output.is_pipe():
            output.print_json(result)
            return

        output.print_success(f"Created task: {body['name']} (id: {task_id})")

        if auto_start and task_id != "?":
            try:
                api_client.post(f"/api/tasks/{task_id}/start")
                console.print(
                    f"  [llx.accent]Task started.[/llx.accent] Watch with: [bold]llx tasks info {task_id}[/bold]"
                )
            except LlxError as start_err:
                console.print(
                    f"  [llx.warning]Auto-start failed: {start_err.message}[/llx.warning]"
                )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@tasks_app.command("info")
def tasks_info(
    task_id: int = typer.Argument(..., help="Task ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show task details and progress."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.get(f"/api/jobs/{task_id}/status")
        result = data if isinstance(data, dict) else {}

        if json_out or output.is_pipe():
            output.print_json(result)
            return

        progress = result.get("progress", {})
        output.print_kv(
            {
                "Task ID": result.get("task_id", task_id),
                "Name": result.get("name", ""),
                "Type": result.get("task_type", ""),
                "Status": result.get("status", ""),
                "Progress": f"{progress.get('percentage', 0)}%",
                "Message": progress.get("message", "—"),
                "Output": result.get("output_file", "—"),
            },
            title="Task Details",
        )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@tasks_app.command("start")
def tasks_start(
    task_id: int = typer.Argument(..., help="Task ID to start"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Start or restart a task."""
    server = server or get_global_server()
    try:
        api_client = get_client(server)
        data = api_client.post(f"/api/tasks/{task_id}/start")
        msg = data.get("message", f"Task {task_id} started.")
        output.print_success(msg)
        console.print(f"  Watch progress: [bold]llx jobs watch {task_id}[/bold]")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@tasks_app.command("download")
def tasks_download(
    task_id: int = typer.Argument(..., help="Task ID"),
    dest: str = typer.Option(".", "--dest", "-d", help="Destination directory"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Download task output file."""
    from pathlib import Path

    server = server or get_global_server()
    try:
        api_client = get_client(server)
        # Get file info first
        info = api_client.get(f"/api/tasks/{task_id}/file-info")
        file_info = info.get("data", info)
        filename = file_info.get("filename", f"task_{task_id}_output")
        dest_path = Path(dest) / filename
        api_client.download(f"/api/tasks/{task_id}/download", dest_path)
        output.print_success(f"Downloaded: {dest_path}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@tasks_app.command("delete")
def tasks_delete(
    task_id: int = typer.Argument(..., help="Task ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a task."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete task {task_id}?", abort=True)
    try:
        api_client = get_client(server)
        api_client.delete(f"/api/tasks/{task_id}")
        output.print_success(f"Deleted task {task_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
