# CLI Enhancements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 5 high-impact features to the Guaardvark CLI: REPL history/line editing, agent execution, live dashboard, upload progress + retries, and backup/restore.

**Architecture:** Each feature is self-contained in its own task. The retry logic (Task 4) modifies the shared HTTP client. All other tasks add new files/commands without touching existing working code.

**Tech Stack:** prompt_toolkit (REPL), Rich Live/Layout (dashboard), httpx + tenacity (retries), Rich Progress (uploads)

---

### Task 1: REPL Line Editing & History

**Files:**
- Modify: `cli/requirements.txt`
- Modify: `cli/setup.py`
- Modify: `cli/llx/repl.py`

**Step 1: Add prompt_toolkit dependency**

In `cli/requirements.txt`, add:
```
prompt_toolkit>=3.0.0
```

In `cli/setup.py`, add `"prompt_toolkit>=3.0.0"` to the `install_requires` list.

**Step 2: Install the new dependency**

Run: `cd /home/llamax1/LLAMAX7/cli && pip install -e .`

**Step 3: Replace console.input with PromptSession in repl.py**

In `cli/llx/repl.py`, add imports at top:
```python
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import ANSI
```

Replace the REPL input loop. The key change is in `launch_repl()` — replace the `console.input()` call with a `PromptSession`:

```python
def launch_repl():
    # ... existing banner code stays the same up to the while loop ...

    # Create prompt session with history and completion
    repl_commands = ['chat', 'c', 'search', 's', 'health', 'status',
                     'files', 'fl', 'projects', 'p', 'rules', 'r',
                     'models', 'm', 'theme', 't', 'help', 'exit', 'quit']
    completer = WordCompleter(repl_commands, ignore_case=True)
    history_file = Path.home() / ".llx" / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
    )

    while True:
        try:
            # Use Rich to render the styled prompt to ANSI, then pass to prompt_toolkit
            prompt_text = console.export_text("[llx.prompt]guaardvark>[/llx.prompt] ", soft_wrap=True) if False else "guaardvark> "
            line = session.prompt("guaardvark> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[llx.dim]Goodbye.[/llx.dim]")
            break
        # ... rest of dispatch stays identical ...
```

Note: We use a plain `"guaardvark> "` prompt string for prompt_toolkit (it handles its own terminal control). The Rich styled output remains for everything else.

Add `from pathlib import Path` to imports if not already present.

**Step 4: Verify**

Run: `cd /home/llamax1/LLAMAX7/cli && python3 -c "from llx.repl import launch_repl; print('import ok')"`

Then manually test the REPL:
- Launch `llx` (no args) to enter REPL
- Type `he` then press Tab — should complete to `health`
- Type `health`, press Enter, then press Up arrow — should recall `health`
- Exit and relaunch — history should persist (check `~/.llx/history`)

**Step 5: Commit**

```
feat(cli): add REPL line editing, tab completion, and persistent history

Uses prompt_toolkit for readline-style editing. History saved to
~/.llx/history. Tab completion for all REPL commands.
```

---

### Task 2: Agent Execution Command

**Files:**
- Modify: `cli/llx/commands/agents.py`

**Step 1: Add `run` and `update` subcommands to agents.py**

Add these commands after the existing `info` command:

```python
@agents_app.command("run")
def run_agent(
    prompt: str = typer.Argument(..., help="Message/prompt to send to the agent"),
    agent_id: str = typer.Option(None, "--agent", "-a", help="Agent ID (auto-matched if omitted)"),
    max_iterations: int = typer.Option(10, "--max-iter", "-n", help="Max agent iterations"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Execute an agent with a prompt. Auto-selects best agent if --agent is omitted."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    try:
        client = get_client(server)

        if not json_out and not output.is_pipe():
            from rich.live import Live
            from rich.spinner import Spinner
            with Live(Spinner("dots", text="[llx.dim]Agent working...[/llx.dim]"), console=console, transient=True):
                data = client.post("/api/agents/execute", json={
                    "agent_id": agent_id,
                    "message": prompt,
                    "context": {"max_iterations": max_iterations},
                })
        else:
            data = client.post("/api/agents/execute", json={
                "agent_id": agent_id,
                "message": prompt,
                "context": {"max_iterations": max_iterations},
            })

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        result = data.get("result", data)
        agent_used = data.get("agent_used", agent_id or "auto")
        answer = result.get("final_answer", "") if isinstance(result, dict) else str(result)
        iterations = result.get("iterations", "?") if isinstance(result, dict) else "?"
        steps = result.get("steps", []) if isinstance(result, dict) else []

        from rich.markdown import Markdown
        console.print(f"\n[llx.accent]Agent:[/llx.accent] {agent_used}  [llx.dim]|[/llx.dim]  [llx.accent]Iterations:[/llx.accent] {iterations}")
        if steps:
            tool_calls = sum(len(s.get("tool_calls", [])) for s in steps)
            console.print(f"[llx.accent]Tool calls:[/llx.accent] {tool_calls}")
        console.print()
        console.print(Markdown(answer))
        console.print()

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@agents_app.command("update")
def update_agent(
    agent_id: str = typer.Argument(..., help="Agent ID to update"),
    enabled: bool = typer.Option(None, "--enabled/--disabled", help="Enable or disable agent"),
    max_iterations: int = typer.Option(None, "--max-iter", "-n", help="Max iterations"),
    prompt_file: Path = typer.Option(None, "--prompt-file", "-f", help="System prompt from file"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Update an agent's configuration."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    payload = {}
    if enabled is not None:
        payload["enabled"] = enabled
    if max_iterations is not None:
        payload["max_iterations"] = max_iterations
    if prompt_file:
        payload["system_prompt"] = prompt_file.read_text(encoding="utf-8")

    if not payload:
        output.print_error("No updates specified. Use --enabled, --max-iter, or --prompt-file.")
        raise typer.Exit(1)

    try:
        client = get_client(server)
        data = client._request("PATCH", f"/api/agents/{agent_id}", json=payload)

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            output.print_success(f"Agent '{agent_id}' updated")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
```

Add necessary imports at top of agents.py:
```python
from pathlib import Path
```

**Step 2: Verify**

Run: `cd /home/llamax1/LLAMAX7/cli && python3 -m llx agents run --help`
Expected: Shows help with `--agent`, `--max-iter`, prompt argument

Run: `python3 -m llx agents update --help`
Expected: Shows help with `--enabled/--disabled`, `--max-iter`, `--prompt-file`

**Step 3: Commit**

```
feat(cli): add `agents run` and `agents update` commands

`agents run` executes an agent with a prompt, auto-selects best agent
if --agent is omitted. `agents update` modifies agent config (enabled,
max_iterations, system_prompt).
```

---

### Task 3: Live Dashboard

**Files:**
- Create: `cli/llx/commands/dashboard.py`
- Modify: `cli/llx/main.py`

**Step 1: Create dashboard.py**

Create `cli/llx/commands/dashboard.py`:

```python
"""Live system dashboard with auto-refreshing metrics."""

import time
import typer
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_server
from llx.theme import make_console, ICON_ONLINE, ICON_OFFLINE

console = make_console()


def dashboard(
    interval: float = typer.Option(3.0, "--interval", "-i", help="Refresh interval in seconds"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Live system dashboard with auto-refreshing metrics. Press Ctrl+C to exit."""
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

    # ── Server ──
    status = health.get("status", "?")
    version = health.get("version", "?")
    uptime = health.get("uptime_seconds", 0)
    uptime_str = _format_uptime(uptime)
    if status == "ok":
        lines.append(f"  [llx.status.online]{ICON_ONLINE} Server[/llx.status.online]   [llx.dim]v{version}  |  up {uptime_str}[/llx.dim]")
    else:
        lines.append(f"  [llx.status.offline]{ICON_OFFLINE} Server[/llx.status.offline]   [llx.dim]{status}[/llx.dim]")

    # ── Model ──
    model_info = model.get("data", model.get("message", {}))
    if isinstance(model_info, str):
        model_info = {}
    text_model = model_info.get("text_model", "?")
    lines.append(f"  [llx.accent]Model[/llx.accent]      {text_model}")

    # ── Celery ──
    celery_status = celery.get("status", "?")
    active_tasks = celery.get("active_tasks", 0)
    celery_icon = ICON_ONLINE if celery_status == "up" else ICON_OFFLINE
    celery_style = "llx.status.online" if celery_status == "up" else "llx.status.offline"
    lines.append(f"  [{celery_style}]{celery_icon} Celery[/{celery_style}]    {celery_status}  [llx.dim]|  {active_tasks} active tasks[/llx.dim]")

    # ── GPU ──
    gpu_pct = metrics.get("gpu_percent")
    gpu_temp = metrics.get("gpu_temp")
    gpu_mem = metrics.get("gpu_mem")
    gpu_owner = gpu.get("owner", "none")
    if gpu_pct is not None:
        gpu_color = "llx.success" if gpu_pct < 50 else ("llx.warning" if gpu_pct < 80 else "llx.error")
        gpu_line = f"  [llx.accent]GPU[/llx.accent]        [{gpu_color}]{gpu_pct:.0f}% util[/{gpu_color}]"
        if gpu_mem is not None:
            mem_color = "llx.success" if gpu_mem < 50 else ("llx.warning" if gpu_mem < 80 else "llx.error")
            gpu_line += f"  [{mem_color}]{gpu_mem:.0f}% VRAM[/{mem_color}]"
        if gpu_temp is not None:
            gpu_line += f"  [llx.dim]{gpu_temp:.0f}C[/llx.dim]"
        if gpu_owner != "none":
            gpu_line += f"  [llx.warning]lock: {gpu_owner}[/llx.warning]"
        lines.append(gpu_line)
    else:
        lines.append("  [llx.accent]GPU[/llx.accent]        [llx.dim]unavailable[/llx.dim]")

    # ── CPU ──
    cpu_pct = metrics.get("cpu_percent")
    cpu_mem = metrics.get("cpu_mem")
    if cpu_pct is not None:
        cpu_color = "llx.success" if cpu_pct < 50 else ("llx.warning" if cpu_pct < 80 else "llx.error")
        cpu_line = f"  [llx.accent]CPU[/llx.accent]        [{cpu_color}]{cpu_pct:.0f}%[/{cpu_color}]"
        if cpu_mem is not None:
            cpu_line += f"  [llx.dim]RAM {cpu_mem:.0f}%[/llx.dim]"
        lines.append(cpu_line)

    # ── Jobs ──
    active_jobs = jobs_data.get("active_jobs", [])
    stuck_count = jobs_data.get("stuck_count", 0)
    if active_jobs:
        lines.append("")
        lines.append(f"  [llx.brand_bright]Active Jobs ({len(active_jobs)})[/llx.brand_bright]")
        for job in active_jobs[:5]:
            name = job.get("description", job.get("command", job.get("id", "?")))[:30]
            progress = job.get("progress", 0)
            total = job.get("total", 100)
            pct = (progress / total * 100) if total > 0 else 0
            status_str = job.get("status", "?")
            pct_color = "llx.success" if pct >= 100 else "llx.accent"
            lines.append(f"    [llx.dim]{name:<30}[/llx.dim] [{pct_color}]{pct:5.1f}%[/{pct_color}]  [llx.dim]{status_str}[/llx.dim]")
        if len(active_jobs) > 5:
            lines.append(f"    [llx.dim]... and {len(active_jobs) - 5} more[/llx.dim]")
    if stuck_count > 0:
        lines.append(f"  [llx.warning]Stuck jobs: {stuck_count}[/llx.warning]")

    # ── Timestamp ──
    lines.append("")
    lines.append(f"  [llx.dim]Last refresh: {time.strftime('%H:%M:%S')}[/llx.dim]")

    content = "\n".join(lines)
    return Panel(content, title="[llx.brand_bright]Guaardvark Dashboard[/llx.brand_bright]",
                 border_style="llx.panel.border", padding=(1, 1))


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
```

**Step 2: Register in main.py**

In `cli/llx/main.py`, add import and registration:

```python
from llx.commands.dashboard import dashboard
```

Add after the existing command registrations:
```python
app.command("dashboard")(dashboard)
```

Also add `dashboard` as a REPL command in `repl.py` — add to the dispatch:
```python
elif cmd in ("dashboard", "dash", "d"):
    from llx.commands.dashboard import dashboard as dash_cmd
    try:
        dash_cmd(server=server)
    except SystemExit:
        pass
```

And add to REPL help text.

**Step 3: Verify**

Run: `cd /home/llamax1/LLAMAX7/cli && python3 -m llx dashboard --help`
Expected: Shows help with `--interval` and `--server` options.

Run: `python3 -m llx dashboard --interval 5`
Expected: Live-updating panel showing server status, model, GPU, jobs. Ctrl+C exits cleanly.

**Step 4: Commit**

```
feat(cli): add live system dashboard command

`llx dashboard` shows a live-updating panel with server health, model,
GPU/CPU metrics, Celery status, and active jobs. Refreshes every 3s
by default (configurable with --interval). Also available as `dash`
or `d` in the REPL.
```

---

### Task 4: Upload Progress Bars & HTTP Retries

**Files:**
- Modify: `cli/requirements.txt`
- Modify: `cli/setup.py`
- Modify: `cli/llx/client.py`
- Modify: `cli/llx/commands/files.py`

**Step 1: Add tenacity dependency**

In `cli/requirements.txt`, add:
```
tenacity>=8.0.0
```

In `cli/setup.py`, add `"tenacity>=8.0.0"` to `install_requires`.

Run: `cd /home/llamax1/LLAMAX7/cli && pip install -e .`

**Step 2: Add retry logic to LlxClient._request**

In `cli/llx/client.py`, add import and wrap `_request`:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
```

Replace the `_request` method:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
    reraise=True,
)
def _request(self, method: str, path: str, **kwargs) -> dict:
    try:
        resp = self.http.request(method, path, **kwargs)
        return self._handle_response(resp)
    except httpx.ConnectError:
        raise LlxConnectionError(
            f"Cannot connect to Guaardvark at {self.server_url}. "
            "Is the server running? Try: ./start.sh"
        )
    except httpx.TimeoutException:
        raise LlxError(
            "Request timed out. The server may be busy processing a long operation.",
            408,
        )
```

Note: `LlxConnectionError` extends `LlxError` which extends `Exception`, but the retry decorator retries on the raw httpx exceptions BEFORE they're caught and wrapped. We need to restructure slightly — retry on httpx errors, but the except blocks wrap them:

```python
def _request(self, method: str, path: str, **kwargs) -> dict:
    try:
        resp = self._request_with_retry(method, path, **kwargs)
        return self._handle_response(resp)
    except httpx.ConnectError:
        raise LlxConnectionError(
            f"Cannot connect to Guaardvark at {self.server_url}. "
            "Is the server running? Try: ./start.sh"
        )
    except httpx.TimeoutException:
        raise LlxError(
            "Request timed out. The server may be busy processing a long operation.",
            408,
        )

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
    reraise=True,
)
def _request_with_retry(self, method: str, path: str, **kwargs):
    return self.http.request(method, path, **kwargs)
```

**Step 3: Add upload_with_progress method to LlxClient**

In `cli/llx/client.py`, add after the existing `upload` method:

```python
def upload_with_progress(self, path: str, file_path: Path, console=None, **extra_fields) -> dict:
    """Upload a file with a Rich progress bar."""
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TransferSpeedColumn

    file_size = file_path.stat().st_size
    data = {k: str(v) for k, v in extra_fields.items() if v is not None}

    with Progress(
        SpinnerColumn(),
        TextColumn("[llx.brand]{task.description}"),
        BarColumn(),
        TextColumn("[llx.dim]{task.completed}/{task.total} bytes[/llx.dim]"),
        TransferSpeedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Uploading {file_path.name}", total=file_size)

        class ProgressReader:
            def __init__(self, fp, callback):
                self.fp = fp
                self.callback = callback
            def read(self, size=-1):
                chunk = self.fp.read(size)
                if chunk:
                    self.callback(len(chunk))
                return chunk

        with open(file_path, "rb") as f:
            reader = ProgressReader(f, lambda n: progress.update(task, advance=n))
            files = {"file": (file_path.name, reader)}
            try:
                resp = self.http.post(path, files=files, data=data)
                return self._handle_response(resp)
            except httpx.ConnectError:
                raise LlxConnectionError(
                    f"Cannot connect to Guaardvark at {self.server_url}. "
                    "Is the server running? Try: ./start.sh"
                )
```

**Step 4: Use upload_with_progress in files.py upload command**

In `cli/llx/commands/files.py`, find the upload command's `client.upload(...)` call and replace it with `client.upload_with_progress(...)` for interactive mode (non-JSON, non-pipe):

```python
if json_out or output.is_pipe():
    data = client.upload("/api/files/upload", file, folder=folder, tags=tags, auto_index=str(index).lower())
else:
    data = client.upload_with_progress("/api/files/upload", file, console=console, folder=folder, tags=tags, auto_index=str(index).lower())
```

**Step 5: Verify**

Run: `cd /home/llamax1/LLAMAX7/cli && python3 -c "from llx.client import LlxClient; print('retry + progress imported ok')"`

Test retry: temporarily stop server, run `llx health` — should retry 3 times before failing.

Test upload progress: `llx files upload some_file.pdf` — should show progress bar.

**Step 6: Commit**

```
feat(cli): add HTTP retry with backoff and upload progress bars

Retries transient connection/timeout errors 3 times with exponential
backoff (1s, 2s, 4s). File uploads show a Rich progress bar with
transfer speed. Uses tenacity for retry logic.
```

---

### Task 5: Backup & Restore Commands

**Files:**
- Create: `cli/llx/commands/backup.py`
- Modify: `cli/llx/main.py`

**Step 1: Create backup.py**

Create `cli/llx/commands/backup.py`:

```python
"""Backup and restore commands."""

import typer
from pathlib import Path

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_server, get_global_json
from llx.theme import make_console
from llx import output

console = make_console()
backup_app = typer.Typer(help="Backup and restore system data.")


@backup_app.command("create")
def create_backup(
    backup_type: str = typer.Option("full", "--type", "-t", help="Backup type: full, data, code_release"),
    name: str = typer.Option(None, "--name", "-n", help="Custom backup name"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a system backup."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    try:
        client = get_client(server)

        from rich.live import Live
        from rich.spinner import Spinner

        payload = {"type": backup_type}
        if name:
            payload["name"] = name

        if not json_out and not output.is_pipe():
            with Live(Spinner("dots", text="[llx.dim]Creating backup...[/llx.dim]"), console=console, transient=True):
                data = client.post("/api/backups/create", json=payload)
        else:
            data = client.post("/api/backups/create", json=payload)

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            filename = data.get("file", "?")
            output.print_success(f"Backup created: {filename}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("list")
def list_backups(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List available backups on the server."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    try:
        client = get_client(server)
        data = client.get("/api/backups")
        backups = data.get("backups", [])

        if json_out or output.is_pipe():
            output.print_json(backups)
        elif not backups:
            console.print("[llx.dim]No backups found.[/llx.dim]")
        else:
            from llx.theme import make_table
            table = make_table(title="Backups")
            table.add_column("Filename")
            for b in backups:
                table.add_row(b)
            console.print(table)

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("download")
def download_backup(
    filename: str = typer.Argument(..., help="Backup filename to download"),
    dest: Path = typer.Option(Path("."), "--dest", "-d", help="Destination directory"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Download a backup file from the server."""
    server = server or get_global_server()

    try:
        client = get_client(server)
        dest_file = dest / filename if dest.is_dir() else dest
        client.download(f"/api/backups/{filename}/download", dest_file)
        output.print_success(f"Downloaded to {dest_file}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("restore")
def restore_backup(
    file: Path = typer.Argument(..., help="Local backup ZIP file to restore"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Restore system from a backup file. This overwrites existing data."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    if not file.exists():
        output.print_error(f"File not found: {file}")
        raise typer.Exit(1)

    if not str(file).endswith(".zip"):
        output.print_error("Only .zip backup files are supported.")
        raise typer.Exit(1)

    if not force and not json_out:
        confirm = typer.confirm(f"Restore from {file.name}? This will overwrite existing data")
        if not confirm:
            console.print("[llx.dim]Cancelled.[/llx.dim]")
            raise typer.Exit(0)

    try:
        client = get_client(server)

        from rich.live import Live
        from rich.spinner import Spinner

        if not json_out and not output.is_pipe():
            with Live(Spinner("dots", text="[llx.dim]Restoring backup...[/llx.dim]"), console=console, transient=True):
                data = client.upload("/api/backups/restore", file)
        else:
            data = client.upload("/api/backups/restore", file)

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            output.print_success("Backup restored successfully")
            # Show summary of restored items
            summary = {k: v for k, v in data.items() if isinstance(v, int) and v > 0}
            if summary:
                output.print_kv(summary, title="Restored Items")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("delete")
def delete_backup(
    filename: str = typer.Argument(..., help="Backup filename to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a backup from the server."""
    server = server or get_global_server()

    if not force:
        confirm = typer.confirm(f"Delete backup {filename}?")
        if not confirm:
            console.print("[llx.dim]Cancelled.[/llx.dim]")
            raise typer.Exit(0)

    try:
        client = get_client(server)
        client.delete(f"/api/backups/{filename}")
        output.print_success(f"Deleted {filename}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
```

**Step 2: Register in main.py**

In `cli/llx/main.py`, add import and registration:

```python
from llx.commands.backup import backup_app
```

Add:
```python
app.add_typer(backup_app, name="backup")
```

**Step 3: Verify**

Run: `cd /home/llamax1/LLAMAX7/cli && python3 -m llx backup --help`
Expected: Shows subcommands: create, list, download, restore, delete

Run: `python3 -m llx backup create --help`
Expected: Shows `--type` and `--name` options

Run: `python3 -m llx backup list`
Expected: Lists backups from server (or "No backups found")

**Step 4: Commit**

```
feat(cli): add backup and restore commands

Full backup lifecycle: create (full/data/code), list, download,
restore (with confirmation), and delete. Supports --json output
for scripting.
```

---

## Verification Checklist

After all tasks are complete, verify end-to-end:

1. **REPL history**: `llx` → type commands → exit → `llx` → press Up arrow → history works
2. **Tab completion**: `llx` → type `he` → Tab → completes to `health`
3. **Agent run**: `llx agents run "What tools do you have available?"`
4. **Agent update**: `llx agents list` → pick an ID → `llx agents update <id> --max-iter 15`
5. **Dashboard**: `llx dashboard` → see live metrics → Ctrl+C exits
6. **Retry**: Stop server → `llx health` → see 3 retry attempts → start server
7. **Upload progress**: `llx files upload <some_file>` → see progress bar
8. **Backup create**: `llx backup create --type data --name test_backup`
9. **Backup list**: `llx backup list` → see the backup
10. **Backup download**: `llx backup download <filename> --dest /tmp`
11. **Backup restore**: `llx backup restore /tmp/<filename>` → confirm → see summary
12. **Backup delete**: `llx backup delete <filename>`
