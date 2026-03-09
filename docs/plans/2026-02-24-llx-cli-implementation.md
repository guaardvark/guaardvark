# LLX CLI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a full-featured interactive CLI (`llx`) for the Guaardvark platform with streaming chat, file management, RAG search, content generation, and system administration.

**Architecture:** Typer-based CLI with command groups, a shared HTTP client (`client.py`), Socket.IO streaming for chat/jobs, Rich-formatted output with `--json` fallback, and an interactive REPL mode. All commands talk to the existing Flask backend at `localhost:5000`.

**Tech Stack:** Python 3.12+, typer[all], rich, python-socketio, httpx

**Design doc:** `docs/plans/2026-02-24-llx-cli-design.md`

---

### Task 1: Project Scaffolding

**Files:**
- Create: `cli/setup.py`
- Create: `cli/requirements.txt`
- Create: `cli/llx/__init__.py`
- Create: `cli/llx/main.py`
- Create: `cli/llx/commands/__init__.py`

**Step 1: Create directory structure**

```bash
mkdir -p cli/llx/commands
```

**Step 2: Write setup.py**

```python
# cli/setup.py
from setuptools import setup, find_packages

setup(
    name="llx",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "typer[all]>=0.9.0",
        "rich>=13.0.0",
        "python-socketio>=5.10.0",
        "httpx>=0.25.0",
    ],
    entry_points={
        "console_scripts": [
            "llx=llx.main:run",
        ],
    },
    python_requires=">=3.12",
)
```

**Step 3: Write requirements.txt**

```
typer[all]>=0.9.0
rich>=13.0.0
python-socketio>=5.10.0
httpx>=0.25.0
```

**Step 4: Write llx/__init__.py**

```python
__version__ = "1.0.0"
```

**Step 5: Write llx/main.py (skeleton)**

```python
"""LLX — Guaardvark CLI."""

import typer

app = typer.Typer(
    name="llx",
    help="Guaardvark CLI — chat, search, manage files, and more from the terminal.",
    no_args_is_help=False,  # We'll handle no-args to launch REPL
    rich_markup_mode="rich",
)


def run():
    app()


if __name__ == "__main__":
    run()
```

**Step 6: Write llx/commands/__init__.py**

```python
# Command group registration
```

**Step 7: Install in dev mode and verify**

```bash
cd cli && pip install -e . && llx --help
```

Expected: Help text showing "Guaardvark CLI" with no commands yet.

**Step 8: Commit**

```bash
git add cli/
git commit -m "feat(cli): scaffold llx CLI package with typer entry point"
```

---

### Task 2: Configuration System

**Files:**
- Create: `cli/llx/config.py`

**Step 1: Write config.py**

```python
"""LLX CLI configuration — loads/saves ~/.llx/config.json."""

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = {
    "server": "http://localhost:5000",
    "api_key": None,
    "default_output": "table",
    "chat_session_history": 50,
}

CONFIG_DIR = Path.home() / ".llx"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_FILE = CONFIG_DIR / "sessions.json"


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config from ~/.llx/config.json, falling back to defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            # Merge with defaults so new keys are always present
            return {**DEFAULT_CONFIG, **saved}
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(config: dict):
    """Save config to ~/.llx/config.json."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_server_url() -> str:
    """Get the server URL from config."""
    return load_config()["server"]


def get_api_key() -> str | None:
    """Get the API key from config."""
    return load_config().get("api_key")


# --- Session persistence ---

def load_sessions() -> list[dict]:
    """Load chat session history."""
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_session(session_id: str, preview: str):
    """Save a chat session to history."""
    ensure_config_dir()
    sessions = load_sessions()
    # Remove existing entry for this session
    sessions = [s for s in sessions if s["id"] != session_id]
    # Add to front
    sessions.insert(0, {"id": session_id, "preview": preview[:80]})
    # Trim to max history
    config = load_config()
    max_history = config.get("chat_session_history", 50)
    sessions = sessions[:max_history]
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)


def get_last_session_id() -> str | None:
    """Get the most recent session ID."""
    sessions = load_sessions()
    return sessions[0]["id"] if sessions else None
```

**Step 2: Verify import**

```bash
cd /home/llamax1/LLAMAX7 && python -c "from llx.config import load_config; print(load_config())"
```

Expected: Default config dict printed.

**Step 3: Commit**

```bash
git add cli/llx/config.py
git commit -m "feat(cli): add configuration system with session persistence"
```

---

### Task 3: HTTP Client

**Files:**
- Create: `cli/llx/client.py`

**Step 1: Write client.py**

```python
"""LLX HTTP client — single abstraction for all API calls."""

import httpx
from pathlib import Path
from typing import Any

from llx.config import get_server_url, get_api_key


class LlxError(Exception):
    """API error with status code and server message."""
    def __init__(self, message: str, status_code: int = 0):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class LlxConnectionError(LlxError):
    """Cannot connect to the server."""
    pass


class LlxClient:
    """HTTP client for the Guaardvark backend API."""

    def __init__(self, server_url: str | None = None, api_key: str | None = None):
        self.server_url = server_url or get_server_url()
        api_key = api_key or get_api_key()
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self.http = httpx.Client(
            base_url=self.server_url,
            timeout=60.0,
            headers=headers,
        )

    def _handle_response(self, resp: httpx.Response) -> dict:
        """Parse response, raise LlxError on failure."""
        try:
            data = resp.json()
        except Exception:
            if resp.status_code >= 400:
                raise LlxError(f"Server returned {resp.status_code}", resp.status_code)
            return {"raw": resp.text}

        if resp.status_code >= 400:
            msg = data.get("error") or data.get("message") or f"HTTP {resp.status_code}"
            raise LlxError(msg, resp.status_code)

        return data

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an HTTP request with connection error handling."""
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
                f"Request timed out. The server may be busy processing a long operation.",
                408,
            )

    def get(self, path: str, **params) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict | None = None, **kwargs) -> dict:
        return self._request("POST", path, json=json, **kwargs)

    def put(self, path: str, json: dict | None = None) -> dict:
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    def upload(self, path: str, file_path: Path, **extra_fields) -> dict:
        """Upload a file via multipart form."""
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            data = {k: str(v) for k, v in extra_fields.items() if v is not None}
            try:
                resp = self.http.post(path, files=files, data=data)
                return self._handle_response(resp)
            except httpx.ConnectError:
                raise LlxConnectionError(
                    f"Cannot connect to Guaardvark at {self.server_url}. "
                    "Is the server running? Try: ./start.sh"
                )

    def download(self, path: str, dest: Path) -> Path:
        """Download a file to disk."""
        try:
            resp = self.http.get(path)
            if resp.status_code >= 400:
                raise LlxError(f"Download failed: HTTP {resp.status_code}", resp.status_code)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
        except httpx.ConnectError:
            raise LlxConnectionError(
                f"Cannot connect to Guaardvark at {self.server_url}. "
                "Is the server running? Try: ./start.sh"
            )


def get_client(server: str | None = None) -> LlxClient:
    """Factory — creates a client using config or override."""
    return LlxClient(server_url=server)
```

**Step 2: Verify**

```bash
python -c "from llx.client import get_client; c = get_client(); print(c.server_url)"
```

Expected: `http://localhost:5000`

**Step 3: Commit**

```bash
git add cli/llx/client.py
git commit -m "feat(cli): add HTTP client with error handling and file upload/download"
```

---

### Task 4: Output Formatting

**Files:**
- Create: `cli/llx/output.py`

**Step 1: Write output.py**

```python
"""Rich output formatting — tables, panels, markdown, JSON, pipe detection."""

import json
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax

# Global state for --json flag
_json_mode = False
_console = Console(stderr=True)  # errors to stderr
_out = Console()  # normal output to stdout


def set_json_mode(enabled: bool):
    global _json_mode
    _json_mode = enabled


def is_json_mode() -> bool:
    return _json_mode


def is_pipe() -> bool:
    """True if stdout is piped (not a terminal)."""
    return not sys.stdout.isatty()


def print_json(data: Any):
    """Pretty-print JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def print_table(rows: list[dict], columns: list[str] | None = None, title: str | None = None):
    """Render a Rich table, or JSON in json/pipe mode."""
    if _json_mode or is_pipe():
        print_json(rows)
        return

    if not rows:
        _out.print("[dim]No results.[/dim]")
        return

    cols = columns or list(rows[0].keys())
    table = Table(title=title, show_header=True, header_style="bold cyan", show_lines=False)
    for col in cols:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in cols])
    _out.print(table)


def print_panel(title: str, content: str, style: str = "cyan"):
    """Render a Rich panel, or JSON in json/pipe mode."""
    if _json_mode or is_pipe():
        print_json({"title": title, "content": content})
        return
    _out.print(Panel(content, title=title, border_style=style))


def print_markdown(text: str):
    """Render markdown with syntax highlighting."""
    if _json_mode or is_pipe():
        print(text)
        return
    _out.print(Markdown(text))


def print_success(message: str):
    if _json_mode or is_pipe():
        print_json({"status": "success", "message": message})
        return
    _out.print(f"[green]{message}[/green]")


def print_error(message: str):
    """Print error to stderr."""
    _console.print(f"[bold red]Error:[/bold red] {message}")


def print_warning(message: str):
    _console.print(f"[yellow]Warning:[/yellow] {message}")


def print_kv(pairs: dict, title: str | None = None):
    """Print key-value pairs as a neat panel."""
    if _json_mode or is_pipe():
        print_json(pairs)
        return
    lines = []
    for k, v in pairs.items():
        lines.append(f"[bold]{k}:[/bold] {v}")
    content = "\n".join(lines)
    if title:
        _out.print(Panel(content, title=title, border_style="cyan"))
    else:
        _out.print(content)
```

**Step 2: Commit**

```bash
git add cli/llx/output.py
git commit -m "feat(cli): add Rich output formatting with JSON/pipe fallback"
```

---

### Task 5: System Commands (health, status, models, init)

**Files:**
- Create: `cli/llx/commands/system.py`
- Modify: `cli/llx/main.py` — register commands

**Step 1: Write commands/system.py**

```python
"""System commands — health, status, models, init."""

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from llx.client import get_client, LlxError, LlxConnectionError
from llx.config import load_config, save_config, CONFIG_FILE
from llx import output

console = Console()

system_app = typer.Typer(help="System and model commands")
models_app = typer.Typer(help="LLM model management")


def health(
    server: str = typer.Option(None, "--server", "-s", help="Server URL override"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Quick health check of the Guaardvark server."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/health")
        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            status = data.get("status", "unknown")
            color = "green" if status == "ok" else "red"
            version = data.get("version", "?")
            uptime = int(data.get("uptime_seconds", 0))
            h, m = divmod(uptime // 60, 60)
            console.print(f"[{color}]Server: {status}[/{color}]  |  Version: {version}  |  Uptime: {h}h {m}m")
    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)


def status(
    server: str = typer.Option(None, "--server", "-s", help="Server URL override"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """System dashboard — server, model, GPU, workers, jobs."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        health_data = client.get("/api/health")
        model_data = client.get("/api/model/status")
        celery_data = client.get("/api/health/celery")

        # Try to get metrics (may fail if not available)
        try:
            metrics_data = client.get("/api/system/metrics")
        except LlxError:
            metrics_data = {}

        if json_out or output.is_pipe():
            output.print_json({
                "health": health_data,
                "model": model_data,
                "celery": celery_data,
                "metrics": metrics_data,
            })
            return

        # Build status panel
        server_url = client.server_url
        status_ok = health_data.get("status") == "ok"
        server_line = f"Server:  {server_url}  [green]Online[/green]" if status_ok else f"Server:  {server_url}  [red]Offline[/red]"

        model_info = model_data.get("data", {})
        text_model = model_info.get("text_model", "none")
        model_line = f"Model:   {text_model}"

        celery_status = celery_data.get("status", "unknown")
        workers = celery_data.get("workers", [])
        celery_color = "green" if celery_status == "up" else "red"
        celery_line = f"Celery:  {len(workers)} workers  [{celery_color}]{celery_status}[/{celery_color}]"

        metrics = metrics_data.get("data", metrics_data) if metrics_data else {}
        gpu_mem = metrics.get("gpu_mem")
        cpu_pct = metrics.get("cpu_percent")
        gpu_line = f"GPU:     {gpu_mem:.0f}% memory" if gpu_mem is not None else "GPU:     N/A"
        cpu_line = f"CPU:     {cpu_pct:.0f}% util" if cpu_pct is not None else "CPU:     N/A"

        version = health_data.get("version", "?")
        content = "\n".join([server_line, model_line, celery_line, gpu_line, cpu_line, f"Version: {version}"])
        console.print(Panel(content, title="Guaardvark System Status", border_style="cyan"))

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


def init():
    """First-run setup wizard — configure server URL and test connection."""
    console.print("[bold cyan]LLX Setup[/bold cyan]\n")

    config = load_config()
    server = typer.prompt("Server URL", default=config["server"])

    console.print(f"\nTesting connection to [bold]{server}[/bold]...")
    try:
        client = get_client(server)
        data = client.get("/api/health")
        console.print(f"  [green]Connected![/green] Server version: {data.get('version', '?')}")
    except (LlxConnectionError, LlxError) as e:
        console.print(f"  [red]Failed:[/red] {e}")
        if not typer.confirm("Save config anyway?", default=False):
            raise typer.Exit(1)

    # Try to show active model
    try:
        model_data = client.get("/api/model")
        model_name = model_data.get("data", {}).get("model", "none")
        console.print(f"  Active model: [bold]{model_name}[/bold]")
    except LlxError:
        pass

    api_key = typer.prompt("API key (leave blank for none)", default="", show_default=False)

    config["server"] = server
    config["api_key"] = api_key if api_key else None
    save_config(config)

    console.print(f"\n[green]Config saved to {CONFIG_FILE}[/green]")
    console.print("Run [bold]llx --install-completion[/bold] for tab completions.")


# --- Models subcommands ---

@models_app.command("list")
def models_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass model cache"),
):
    """List available LLM models."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/model/list", refresh=str(refresh).lower())
        models = data.get("data", {}).get("models", [])

        if json_out or output.is_pipe():
            output.print_json(models)
            return

        rows = [{"name": m.get("name", "?"), "id": m.get("id", m.get("full_name", "?"))} for m in models]
        output.print_table(rows, columns=["name", "id"], title=f"Available Models ({len(rows)})")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@models_app.command("active")
def models_active(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show the currently active model."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/model/status")
        info = data.get("data", {})

        if json_out or output.is_pipe():
            output.print_json(info)
            return

        output.print_kv({
            "Text model": info.get("text_model", "none"),
            "Vision model": info.get("vision_model", "none"),
            "Vision loaded": str(info.get("vision_loaded", False)),
            "Image gen model": info.get("image_gen_model", "none"),
        }, title="Active Models")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@models_app.command("set")
def models_set(
    model: str = typer.Argument(help="Model name to switch to"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Switch the active LLM model."""
    try:
        client = get_client(server)
        data = client.post("/api/model/set", json={"model": model})
        output.print_success(f"Switching to {model}... (this may take a moment)")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register commands in main.py**

Update `cli/llx/main.py` to import and register these commands:

```python
"""LLX — Guaardvark CLI."""

import typer

from llx import __version__
from llx.commands.system import health, status, init, models_app

app = typer.Typer(
    name="llx",
    help="Guaardvark CLI — chat, search, manage files, and more from the terminal.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

# Top-level commands
app.command("health")(health)
app.command("status")(status)
app.command("init")(init)

# Subcommand groups
app.add_typer(models_app, name="models")


def version_callback(value: bool):
    if value:
        print(f"llx {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=version_callback, is_eager=True),
):
    """Guaardvark CLI — chat, search, manage files, and more."""
    if ctx.invoked_subcommand is None:
        # No subcommand = launch REPL (will implement later)
        # For now, show help
        print(ctx.get_help())


def run():
    app()


if __name__ == "__main__":
    run()
```

**Step 3: Test**

```bash
llx --version
llx health
llx status
llx models list
llx models active
```

**Step 4: Commit**

```bash
git add cli/llx/commands/system.py cli/llx/main.py
git commit -m "feat(cli): add health, status, init, and models commands"
```

---

### Task 6: Socket.IO Streaming Client

**Files:**
- Create: `cli/llx/streaming.py`

**Step 1: Write streaming.py**

```python
"""Socket.IO streaming client for chat and job progress."""

import socketio
import threading
import time
from typing import Callable

from llx.config import get_server_url


class LlxStreamer:
    """Handles Socket.IO connections for streaming chat and job progress."""

    def __init__(self, server_url: str | None = None):
        self.server_url = server_url or get_server_url()
        self.sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        self._connected = False
        self._done = threading.Event()

    def stream_chat(
        self,
        session_id: str,
        on_token: Callable[[str], None],
        on_thinking: Callable[[dict], None] | None = None,
        on_tool_call: Callable[[dict], None] | None = None,
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """
        Connect to Socket.IO, join session, and listen for chat events.
        Call this BEFORE posting the chat message via HTTP.
        Returns when chat:complete or chat:error fires.
        """
        self._done.clear()

        @self.sio.on("chat:token")
        def handle_token(data):
            content = data.get("content", "")
            if content:
                on_token(content)

        @self.sio.on("chat:thinking")
        def handle_thinking(data):
            if on_thinking:
                on_thinking(data)

        @self.sio.on("chat:tool_call")
        def handle_tool_call(data):
            if on_tool_call:
                on_tool_call(data)

        @self.sio.on("chat:complete")
        def handle_complete(data):
            if on_complete:
                on_complete(data)
            self._done.set()

        @self.sio.on("chat:error")
        def handle_error(data):
            if on_error:
                on_error(data.get("error", "Unknown error"))
            self._done.set()

        @self.sio.on("chat:aborted")
        def handle_aborted(data):
            if on_error:
                on_error("Chat aborted")
            self._done.set()

        try:
            self.sio.connect(self.server_url, transports=["websocket"])
            self._connected = True
            self.sio.emit("chat:join", {"session_id": session_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect for streaming: {e}")
            self._done.set()
            return

    def wait(self, timeout: float = 300.0) -> bool:
        """Block until streaming is done. Returns True if completed, False on timeout."""
        return self._done.wait(timeout=timeout)

    def abort(self, session_id: str):
        """Send abort signal for current chat."""
        if self._connected:
            try:
                self.sio.emit("chat:abort", {"session_id": session_id})
            except Exception:
                pass

    def disconnect(self):
        """Disconnect from Socket.IO."""
        if self._connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
            self._connected = False

    def watch_job(
        self,
        job_id: str,
        on_progress: Callable[[dict], None],
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """Subscribe to job progress updates via Socket.IO."""
        self._done.clear()

        @self.sio.on("progress")
        def handle_progress(data):
            on_progress(data)
            # Check if job completed
            status = data.get("status", "")
            if status in ("completed", "done", "failed", "error"):
                if status in ("failed", "error") and on_error:
                    on_error(data.get("message", "Job failed"))
                elif on_complete:
                    on_complete(data)
                self._done.set()

        try:
            self.sio.connect(self.server_url, transports=["websocket"])
            self._connected = True
            self.sio.emit("subscribe", {"job_id": job_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect: {e}")
            self._done.set()
```

**Step 2: Commit**

```bash
git add cli/llx/streaming.py
git commit -m "feat(cli): add Socket.IO streaming client for chat and job progress"
```

---

### Task 7: Chat Command

**Files:**
- Create: `cli/llx/commands/chat.py`
- Modify: `cli/llx/main.py` — register chat

**Step 1: Write commands/chat.py**

```python
"""Chat command — streaming conversation with the LLM."""

import sys
import uuid
import signal
import time
import threading

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner

from llx.client import get_client, LlxError, LlxConnectionError
from llx.streaming import LlxStreamer
from llx.config import save_session, get_last_session_id, load_sessions
from llx import output

console = Console()


def chat(
    message: str = typer.Argument(None, help="Message to send"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Continue last conversation"),
    session: str = typer.Option(None, "--session", help="Resume a specific session ID"),
    list_sessions: bool = typer.Option(False, "--list", "-l", help="List recent chat sessions"),
    export: bool = typer.Option(False, "--export", help="Export conversation (requires --session)"),
    no_rag: bool = typer.Option(False, "--no-rag", help="Disable RAG context"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Chat with the LLM. Supports streaming, piped input, and session management."""
    output.set_json_mode(json_out)

    # List sessions
    if list_sessions:
        sessions = load_sessions()
        if not sessions:
            output.print_warning("No chat sessions found.")
            return
        rows = [{"id": s["id"][:8] + "...", "full_id": s["id"], "preview": s["preview"]} for s in sessions]
        output.print_table(rows, columns=["id", "preview"], title="Recent Sessions")
        return

    # Determine session ID
    if session:
        session_id = session
    elif resume:
        session_id = get_last_session_id()
        if not session_id:
            output.print_error("No previous session to resume.")
            raise typer.Exit(1)
    else:
        session_id = str(uuid.uuid4())

    # Read piped input
    piped_input = ""
    if not sys.stdin.isatty():
        piped_input = sys.stdin.read()

    # Build final message
    if piped_input and message:
        full_message = f"{message}\n\n---\n{piped_input}"
    elif piped_input:
        full_message = piped_input
    elif message:
        full_message = message
    else:
        output.print_error("No message provided. Usage: llx chat \"your message\"")
        raise typer.Exit(1)

    try:
        client = get_client(server)
        streamer = LlxStreamer(server_url=client.server_url)

        # Collect streamed response
        response_parts = []
        start_time = time.time()
        complete_data = {}

        def on_token(content):
            response_parts.append(content)

        def on_complete(data):
            nonlocal complete_data
            complete_data = data

        def on_error(msg):
            response_parts.append(f"\n[ERROR] {msg}")

        # Connect streaming first
        streamer.stream_chat(
            session_id=session_id,
            on_token=on_token,
            on_complete=on_complete,
            on_error=on_error,
        )

        # Handle Ctrl+C
        original_sigint = signal.getsignal(signal.SIGINT)

        def sigint_handler(sig, frame):
            streamer.abort(session_id)
            console.print("\n[yellow]Aborted.[/yellow]")
            streamer.disconnect()
            signal.signal(signal.SIGINT, original_sigint)
            raise typer.Exit(0)

        signal.signal(signal.SIGINT, sigint_handler)

        # Post the message
        client.post("/api/enhanced-chat", json={
            "session_id": session_id,
            "message": full_message,
            "use_rag": not no_rag,
        })

        # Stream output
        if json_out or output.is_pipe():
            # Non-interactive: wait for completion, print JSON/plain
            streamer.wait(timeout=300)
            full_response = "".join(response_parts)
            if json_out:
                output.print_json({
                    "session_id": session_id,
                    "response": full_response,
                    "elapsed": round(time.time() - start_time, 2),
                })
            else:
                print(full_response)
        else:
            # Interactive: live stream tokens
            with Live("", console=console, refresh_per_second=15, transient=False) as live:
                while not streamer._done.is_set():
                    current = "".join(response_parts)
                    if current:
                        live.update(Markdown(current))
                    streamer._done.wait(timeout=0.07)
                # Final render
                current = "".join(response_parts)
                if current:
                    live.update(Markdown(current))

            elapsed = time.time() - start_time
            console.print(f"\n[dim]Session: {session_id[:8]}  |  {elapsed:.1f}s[/dim]")

        # Restore signal handler
        signal.signal(signal.SIGINT, original_sigint)

        # Save session
        save_session(session_id, full_message[:80])

        # Cleanup
        streamer.disconnect()

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
```

**Step 2: Register in main.py**

Add to `cli/llx/main.py` imports and registration:

```python
from llx.commands.chat import chat
# ...
app.command("chat")(chat)
```

**Step 3: Test (requires running server)**

```bash
llx chat "Hello, who are you?"
echo "What is 2+2?" | llx chat
llx chat --list
```

**Step 4: Commit**

```bash
git add cli/llx/commands/chat.py cli/llx/main.py
git commit -m "feat(cli): add chat command with streaming, piping, and session management"
```

---

### Task 8: Files Command

**Files:**
- Create: `cli/llx/commands/files.py`
- Modify: `cli/llx/main.py` — register

**Step 1: Write commands/files.py**

```python
"""File management commands — list, upload, download, delete, mkdir."""

from pathlib import Path
import typer
from rich.console import Console
from rich.tree import Tree

from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

console = Console()
files_app = typer.Typer(help="File and folder management")


@files_app.command("list")
def files_list(
    path: str = typer.Option("/", "--path", "-p", help="Folder path to browse"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List files and folders."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/files/browse", path=path)
        result = data.get("data", data)
        folders = result.get("folders", [])
        documents = result.get("documents", [])

        if json_out or output.is_pipe():
            output.print_json({"folders": folders, "documents": documents})
            return

        if not folders and not documents:
            output.print_warning(f"Empty directory: {path}")
            return

        tree = Tree(f"[bold]{path}[/bold]")
        for f in folders:
            name = f.get("name", f) if isinstance(f, dict) else str(f)
            tree.add(f"[bold cyan]{name}/[/bold cyan]")
        for d in documents:
            name = d.get("filename", d.get("name", str(d)))
            doc_id = d.get("id", "")
            size = d.get("size", 0)
            size_str = _format_size(size) if size else ""
            tree.add(f"{name}  [dim]{size_str}  id:{doc_id}[/dim]")
        console.print(tree)

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@files_app.command("upload")
def files_upload(
    file_path: Path = typer.Argument(..., help="File to upload", exists=True),
    folder: str = typer.Option(None, "--folder", "-f", help="Target folder path"),
    tags: str = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Upload a file."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.upload("/api/files/upload", file_path, folder_id=folder, tags=tags)
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
            return

        output.print_success(f"Uploaded: {file_path.name} (id: {result.get('id', '?')})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@files_app.command("download")
def files_download(
    doc_id: int = typer.Argument(..., help="Document ID to download"),
    dest: Path = typer.Option(Path("."), "--dest", "-d", help="Destination directory"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Download a file by ID."""
    try:
        client = get_client(server)
        # Get filename first
        info = client.get(f"/api/files/document/{doc_id}")
        doc_data = info.get("data", info)
        filename = doc_data.get("filename", f"document_{doc_id}")
        dest_file = dest / filename
        client.download(f"/api/files/document/{doc_id}/download", dest_file)
        output.print_success(f"Downloaded: {dest_file}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@files_app.command("delete")
def files_delete(
    doc_id: int = typer.Argument(..., help="Document ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a file by ID."""
    if not force:
        typer.confirm(f"Delete document {doc_id}?", abort=True)
    try:
        client = get_client(server)
        client.delete(f"/api/files/document/{doc_id}")
        output.print_success(f"Deleted document {doc_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@files_app.command("mkdir")
def files_mkdir(
    name: str = typer.Argument(..., help="Folder name"),
    parent: int = typer.Option(None, "--parent", "-p", help="Parent folder ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a folder."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post("/api/files/folder", json={"name": name, "parent_id": parent})
        result = data.get("data", data)
        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Created folder: {name}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.0f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"
```

**Step 2: Register in main.py**

```python
from llx.commands.files import files_app
app.add_typer(files_app, name="files")
```

**Step 3: Commit**

```bash
git add cli/llx/commands/files.py cli/llx/main.py
git commit -m "feat(cli): add files command (list, upload, download, delete, mkdir)"
```

---

### Task 9: Search Command

**Files:**
- Create: `cli/llx/commands/search.py`
- Modify: `cli/llx/main.py` — register

**Step 1: Write commands/search.py**

```python
"""Semantic search command."""

import typer
from rich.console import Console

from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

console = Console()


def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Semantic search over indexed documents."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.post("/api/search/semantic", json={"query": query})

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        answer = data.get("answer", "")
        sources = data.get("sources", [])[:limit]

        if answer:
            output.print_markdown(answer)

        if sources:
            console.print(f"\n[dim]Sources ({len(sources)}):[/dim]")
            rows = [{"source": s.get("source_document", "?"), "score": f"{s.get('score', 0):.3f}"} for s in sources]
            output.print_table(rows, columns=["source", "score"])

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py**

```python
from llx.commands.search import search
app.command("search")(search)
```

**Step 3: Commit**

```bash
git add cli/llx/commands/search.py cli/llx/main.py
git commit -m "feat(cli): add semantic search command"
```

---

### Task 10: Projects Command

**Files:**
- Create: `cli/llx/commands/projects.py`
- Modify: `cli/llx/main.py`

**Step 1: Write commands/projects.py**

```python
"""Project management commands."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

projects_app = typer.Typer(help="Project management")


@projects_app.command("list")
def projects_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all projects."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/projects")
        projects = data if isinstance(data, list) else data.get("data", [])

        if json_out or output.is_pipe():
            output.print_json(projects)
            return

        rows = [{
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "client": (p.get("client") or {}).get("name", "—"),
            "docs": p.get("document_count", 0),
            "tasks": p.get("task_count", 0),
        } for p in projects]
        output.print_table(rows, columns=["id", "name", "client", "docs", "tasks"], title=f"Projects ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("create")
def projects_create(
    name: str = typer.Argument(..., help="Project name"),
    client_id: int = typer.Option(None, "--client", "-c", help="Client ID"),
    description: str = typer.Option("", "--desc", "-d", help="Description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new project."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        body = {"name": name, "description": description}
        if client_id:
            body["client_id"] = client_id
        data = client.post("/api/projects", json=body)
        result = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Created project: {name} (id: {result.get('id', '?')})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("info")
def projects_info(
    project_id: int = typer.Argument(..., help="Project ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show project details."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/projects/{project_id}")
        project = data.get("data", data) if isinstance(data, dict) else data

        if json_out or output.is_pipe():
            output.print_json(project)
        else:
            output.print_kv({
                "ID": project.get("id", ""),
                "Name": project.get("name", ""),
                "Client": (project.get("client") or {}).get("name", "—"),
                "Description": project.get("description", "—"),
                "Documents": project.get("document_count", 0),
                "Tasks": project.get("task_count", 0),
            }, title="Project Details")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@projects_app.command("delete")
def projects_delete(
    project_id: int = typer.Argument(..., help="Project ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a project."""
    if not force:
        typer.confirm(f"Delete project {project_id}?", abort=True)
    try:
        client = get_client(server)
        client.delete(f"/api/projects/{project_id}")
        output.print_success(f"Deleted project {project_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py**

```python
from llx.commands.projects import projects_app
app.add_typer(projects_app, name="projects")
```

**Step 3: Commit**

```bash
git add cli/llx/commands/projects.py cli/llx/main.py
git commit -m "feat(cli): add projects command (list, create, info, delete)"
```

---

### Task 11: Rules Command

**Files:**
- Create: `cli/llx/commands/rules.py`
- Modify: `cli/llx/main.py`

**Step 1: Write commands/rules.py**

Rules follow same CRUD pattern as projects, plus export/import.

```python
"""Rules/prompts management commands."""

import json
from pathlib import Path
import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

rules_app = typer.Typer(help="Rules and system prompts management")


@rules_app.command("list")
def rules_list(
    project_id: int = typer.Option(None, "--project", "-p", help="Filter by project ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all rules."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        params = {}
        if project_id:
            params["project_id"] = project_id
        data = client.get("/api/rules", **params)
        rules = data if isinstance(data, list) else data.get("data", [])

        if json_out or output.is_pipe():
            output.print_json(rules)
            return

        rows = [{
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "level": r.get("level", ""),
            "type": r.get("type", ""),
            "active": "Yes" if r.get("is_active") else "No",
        } for r in rules]
        output.print_table(rows, columns=["id", "name", "level", "type", "active"], title=f"Rules ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("create")
def rules_create(
    name: str = typer.Argument(..., help="Rule name"),
    content: str = typer.Option(None, "--content", "-c", help="Rule text"),
    file: Path = typer.Option(None, "--file", "-f", help="Read rule text from file", exists=True),
    level: str = typer.Option("USER_GLOBAL", "--level", "-l", help="Rule level"),
    rule_type: str = typer.Option("SYSTEM", "--type", "-t", help="Rule type"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new rule/prompt."""
    output.set_json_mode(json_out)

    if file:
        rule_text = file.read_text()
    elif content:
        rule_text = content
    else:
        output.print_error("Provide --content or --file")
        raise typer.Exit(1)

    try:
        client = get_client(server)
        data = client.post("/api/rules", json={
            "name": name,
            "rule_text": rule_text,
            "level": level,
            "type": rule_type,
        })
        result = data.get("data", data)
        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Created rule: {name} (id: {result.get('id', '?')})")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("delete")
def rules_delete(
    rule_id: int = typer.Argument(..., help="Rule ID"),
    force: bool = typer.Option(False, "--force", "-f"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a rule."""
    if not force:
        typer.confirm(f"Delete rule {rule_id}?", abort=True)
    try:
        client = get_client(server)
        client.delete(f"/api/rules/{rule_id}")
        output.print_success(f"Deleted rule {rule_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("export")
def rules_export(
    file: Path = typer.Option("rules.json", "--file", "-f", help="Output file"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Export all rules to a JSON file."""
    try:
        client = get_client(server)
        data = client.get("/api/meta/rules/export")
        rules = data.get("rules", data)
        file.write_text(json.dumps(rules, indent=2))
        output.print_success(f"Exported {len(rules)} rules to {file}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("import")
def rules_import(
    file: Path = typer.Argument(..., help="JSON file to import", exists=True),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Import rules from a JSON file."""
    try:
        rules = json.loads(file.read_text())
        client = get_client(server)
        data = client.post("/api/meta/rules/import", json={"rules": rules})
        created = data.get("created", 0)
        updated = data.get("updated", 0)
        skipped = data.get("skipped", 0)
        output.print_success(f"Import complete: {created} created, {updated} updated, {skipped} skipped")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py, commit**

```bash
git add cli/llx/commands/rules.py cli/llx/main.py
git commit -m "feat(cli): add rules command (list, create, delete, export, import)"
```

---

### Task 12: Agents Command

**Files:**
- Create: `cli/llx/commands/agents.py`
- Modify: `cli/llx/main.py`

**Step 1: Write commands/agents.py**

```python
"""Agent management commands."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

agents_app = typer.Typer(help="Agent configuration and info")


@agents_app.command("list")
def agents_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all configured agents."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/agents")
        agents = data.get("agents", [])

        if json_out or output.is_pipe():
            output.print_json(agents)
            return

        rows = [{
            "id": a.get("id", ""),
            "name": a.get("name", ""),
            "enabled": "Yes" if a.get("enabled") else "No",
            "tools": str(len(a.get("tools", []))),
        } for a in agents]
        output.print_table(rows, columns=["id", "name", "enabled", "tools"], title=f"Agents ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@agents_app.command("info")
def agents_info(
    agent_id: str = typer.Argument(..., help="Agent ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show agent details and available tools."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/agents/{agent_id}")
        agent = data.get("agent", data)
        tools = data.get("tools_detail", [])

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        output.print_kv({
            "ID": agent.get("id", ""),
            "Name": agent.get("name", ""),
            "Enabled": str(agent.get("enabled", False)),
            "Description": agent.get("description", "—"),
            "Max iterations": str(agent.get("max_iterations", "—")),
        }, title="Agent")

        if tools:
            rows = [{"name": t.get("name", ""), "description": t.get("description", "")} for t in tools]
            output.print_table(rows, columns=["name", "description"], title="Tools")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py, commit**

```bash
git add cli/llx/commands/agents.py cli/llx/main.py
git commit -m "feat(cli): add agents command (list, info)"
```

---

### Task 13: Generate Command

**Files:**
- Create: `cli/llx/commands/generate.py`
- Modify: `cli/llx/main.py`

**Step 1: Write commands/generate.py**

```python
"""Content generation commands — CSV, image."""

import typer
from rich.console import Console

from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

console = Console()
generate_app = typer.Typer(help="Content generation")


@generate_app.command("csv")
def generate_csv(
    prompt: str = typer.Argument(..., help="Generation prompt"),
    output_file: str = typer.Option("output.csv", "--output", "-o", help="Output filename"),
    client_name: str = typer.Option(None, "--client", "-c", help="Client name"),
    project_name: str = typer.Option(None, "--project", "-p", help="Project name"),
    word_count: int = typer.Option(500, "--words", "-w", help="Target word count"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate CSV content from a prompt."""
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        body = {
            "type": "single",
            "output_filename": output_file,
            "prompt": prompt,
            "target_word_count": word_count,
        }
        if client_name:
            body["client"] = client_name
        if project_name:
            body["project"] = project_name

        data = api_client.post("/api/generate/csv", json=body)
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Generated: {result.get('output_file', output_file)}")
            stats = result.get("statistics", {})
            if stats:
                console.print(f"  [dim]Items: {stats.get('generated_items', '?')} | Time: {stats.get('generation_time', '?'):.1f}s[/dim]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@generate_app.command("image")
def generate_image(
    prompt: str = typer.Argument(..., help="Image description"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate an image from a prompt."""
    output.set_json_mode(json_out)
    try:
        api_client = get_client(server)
        data = api_client.post("/api/batch-image-generation", json={
            "prompts": [prompt],
            "batch_size": 1,
        })
        result = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            job_id = result.get("job_id", "")
            output.print_success(f"Image generation started (job: {job_id})")
            console.print(f"  Track with: [bold]llx jobs watch {job_id}[/bold]")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py, commit**

```bash
git add cli/llx/commands/generate.py cli/llx/main.py
git commit -m "feat(cli): add generate command (csv, image)"
```

---

### Task 14: Jobs Command

**Files:**
- Create: `cli/llx/commands/jobs.py`
- Modify: `cli/llx/main.py`

**Step 1: Write commands/jobs.py**

```python
"""Job management commands — list, status, watch, cancel."""

import typer
from rich.console import Console
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from llx.client import get_client, LlxError, LlxConnectionError
from llx.streaming import LlxStreamer
from llx import output

console = Console()
jobs_app = typer.Typer(help="Background job management")


@jobs_app.command("list")
def jobs_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List recent jobs."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/unified-jobs")
        jobs = data.get("data", data) if isinstance(data, dict) else data
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
    try:
        client = get_client(server)
        streamer = LlxStreamer(server_url=client.server_url)

        with Progress(
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Job {job_id}", total=100)

            def on_progress(data):
                pct = data.get("percentage", data.get("progress", 0))
                msg = data.get("message", data.get("status", ""))
                progress.update(task, completed=pct, description=msg or f"Job {job_id}")

            def on_complete(data):
                progress.update(task, completed=100, description="[green]Complete[/green]")

            def on_error(msg):
                progress.update(task, description=f"[red]{msg}[/red]")

            streamer.watch_job(job_id, on_progress=on_progress, on_complete=on_complete, on_error=on_error)
            streamer.wait(timeout=600)
            streamer.disconnect()

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py, commit**

```bash
git add cli/llx/commands/jobs.py cli/llx/main.py
git commit -m "feat(cli): add jobs command (list, status, watch)"
```

---

### Task 15: Settings Command

**Files:**
- Create: `cli/llx/commands/settings.py`
- Modify: `cli/llx/main.py`

**Step 1: Write commands/settings.py**

```python
"""Settings commands — get, set, list."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx import output

settings_app = typer.Typer(help="Application settings")


@settings_app.command("list")
def settings_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show all settings."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        # Gather known settings
        settings = {}
        for key in ["web_access", "advanced_debug"]:
            try:
                data = client.get(f"/api/settings/{key}")
                settings[key] = data.get("data", data)
            except LlxError:
                settings[key] = "unavailable"

        if json_out or output.is_pipe():
            output.print_json(settings)
        else:
            output.print_kv(
                {k: str(v) for k, v in settings.items()},
                title="Settings",
            )
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@settings_app.command("get")
def settings_get(
    key: str = typer.Argument(..., help="Setting key"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Get a setting value."""
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/settings/{key}")
        result = data.get("data", data)
        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_kv({key: str(result)})
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@settings_app.command("set")
def settings_set(
    key: str = typer.Argument(..., help="Setting key"),
    value: str = typer.Argument(..., help="Setting value"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Set a setting value."""
    try:
        # Try to parse as bool/int
        parsed: str | bool | int = value
        if value.lower() in ("true", "false"):
            parsed = value.lower() == "true"
        elif value.isdigit():
            parsed = int(value)

        client = get_client(server)
        client.post(f"/api/settings/{key}", json={key: parsed})
        output.print_success(f"Set {key} = {parsed}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
```

**Step 2: Register in main.py, commit**

```bash
git add cli/llx/commands/settings.py cli/llx/main.py
git commit -m "feat(cli): add settings command (list, get, set)"
```

---

### Task 16: Interactive REPL

**Files:**
- Create: `cli/llx/repl.py`
- Modify: `cli/llx/main.py` — launch REPL on no-args

**Step 1: Write repl.py**

```python
"""Interactive REPL mode — llx with no arguments."""

import shlex
import uuid

from rich.console import Console
from rich.panel import Panel

from llx import __version__
from llx.config import load_config, get_last_session_id
from llx.client import get_client, LlxError, LlxConnectionError

console = Console()


def launch_repl():
    """Start the interactive REPL."""
    config = load_config()
    server = config["server"]

    # Connection check
    try:
        client = get_client(server)
        health = client.get("/api/health")
        model_data = client.get("/api/model/status")
        model = model_data.get("data", {}).get("text_model", "?")
        status_str = f"[green]Connected[/green] to {server}"
    except (LlxConnectionError, LlxError):
        model = "?"
        status_str = f"[red]Offline[/red] — {server}"

    console.print(Panel(
        f"{status_str}\nModel: [bold]{model}[/bold]",
        title=f"LLX CLI v{__version__}",
        border_style="cyan",
    ))
    console.print("[dim]Type a command (chat, search, files, ...) or 'help'. Ctrl+D to exit.[/dim]\n")

    # Keep a persistent chat session for the REPL
    chat_session_id = get_last_session_id() or str(uuid.uuid4())

    while True:
        try:
            line = console.input("[bold cyan]llx>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not line:
            continue

        if line in ("exit", "quit"):
            console.print("[dim]Goodbye.[/dim]")
            break

        if line == "help":
            _print_repl_help()
            continue

        # Parse and dispatch
        try:
            parts = shlex.split(line)
        except ValueError as e:
            console.print(f"[red]Parse error: {e}[/red]")
            continue

        cmd = parts[0]
        args = parts[1:]

        if cmd == "chat" or cmd == "c":
            # In REPL, bare text after 'chat' is the message
            if args:
                message = " ".join(args)
            else:
                console.print("[dim]Usage: chat <message>[/dim]")
                continue
            _repl_chat(server, chat_session_id, message)

        elif cmd == "search" or cmd == "s":
            if args:
                _repl_search(server, " ".join(args))
            else:
                console.print("[dim]Usage: search <query>[/dim]")

        elif cmd == "health":
            _repl_health(server)

        elif cmd == "status":
            # Import and call status command directly
            from llx.commands.system import status as status_cmd
            try:
                status_cmd(server=server, json_out=False)
            except SystemExit:
                pass

        else:
            # Try running as a full llx command
            import sys
            from llx.main import app
            try:
                sys.argv = ["llx"] + parts
                app(standalone_mode=False)
            except SystemExit:
                pass
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")


def _repl_chat(server: str, session_id: str, message: str):
    """Quick chat within REPL — streams response."""
    import time
    from rich.live import Live
    from rich.markdown import Markdown
    from llx.streaming import LlxStreamer

    try:
        client = get_client(server)
        streamer = LlxStreamer(server_url=server)
        parts = []
        start = time.time()

        streamer.stream_chat(
            session_id=session_id,
            on_token=lambda t: parts.append(t),
            on_complete=lambda d: None,
            on_error=lambda e: parts.append(f"\n[ERROR] {e}"),
        )

        client.post("/api/enhanced-chat", json={
            "session_id": session_id,
            "message": message,
            "use_rag": True,
        })

        with Live("", console=console, refresh_per_second=15, transient=False) as live:
            while not streamer._done.is_set():
                current = "".join(parts)
                if current:
                    live.update(Markdown(current))
                streamer._done.wait(timeout=0.07)
            current = "".join(parts)
            if current:
                live.update(Markdown(current))

        elapsed = time.time() - start
        console.print(f"[dim]{elapsed:.1f}s[/dim]\n")
        streamer.disconnect()

        from llx.config import save_session
        save_session(session_id, message[:80])

    except (LlxConnectionError, LlxError) as e:
        console.print(f"[red]Error: {e}[/red]")


def _repl_search(server: str, query: str):
    """Quick search within REPL."""
    try:
        client = get_client(server)
        data = client.post("/api/search/semantic", json={"query": query})
        answer = data.get("answer", "")
        if answer:
            from rich.markdown import Markdown
            console.print(Markdown(answer))
        sources = data.get("sources", [])[:3]
        if sources:
            console.print(f"[dim]Sources: {', '.join(s.get('source_document', '?') for s in sources)}[/dim]")
        console.print()
    except (LlxConnectionError, LlxError) as e:
        console.print(f"[red]Error: {e}[/red]")


def _repl_health(server: str):
    """Quick health check within REPL."""
    try:
        client = get_client(server)
        data = client.get("/api/health")
        status = data.get("status", "?")
        color = "green" if status == "ok" else "red"
        console.print(f"[{color}]{status}[/{color}]")
    except (LlxConnectionError, LlxError) as e:
        console.print(f"[red]{e}[/red]")


def _print_repl_help():
    console.print("""
[bold]REPL Commands:[/bold]
  chat <message>    Chat with the LLM (streaming)
  c <message>       Shortcut for chat
  search <query>    Semantic search
  s <query>         Shortcut for search
  health            Quick health check
  status            System dashboard
  help              Show this help
  exit / quit       Exit REPL

[dim]Any other input is tried as a full llx command (e.g. 'files list', 'models active').[/dim]
""")
```

**Step 2: Update main.py callback to launch REPL**

In `cli/llx/main.py`, update the `main` callback:

```python
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=version_callback, is_eager=True),
):
    """Guaardvark CLI — chat, search, manage files, and more."""
    if ctx.invoked_subcommand is None:
        from llx.repl import launch_repl
        launch_repl()
```

**Step 3: Test**

```bash
llx  # Should launch REPL
# Type: help
# Type: health
# Type: exit
```

**Step 4: Commit**

```bash
git add cli/llx/repl.py cli/llx/main.py
git commit -m "feat(cli): add interactive REPL mode with chat, search, health"
```

---

### Task 17: Final main.py Assembly

**Files:**
- Modify: `cli/llx/main.py` — register ALL command groups

**Step 1: Write the complete main.py with all imports and registrations**

```python
"""LLX — Guaardvark CLI."""

import typer

from llx import __version__
from llx.commands.system import health, status, init, models_app
from llx.commands.chat import chat
from llx.commands.search import search
from llx.commands.files import files_app
from llx.commands.projects import projects_app
from llx.commands.rules import rules_app
from llx.commands.agents import agents_app
from llx.commands.generate import generate_app
from llx.commands.jobs import jobs_app
from llx.commands.settings import settings_app

app = typer.Typer(
    name="llx",
    help="Guaardvark CLI — chat, search, manage files, and more from the terminal.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

# Top-level commands
app.command("health")(health)
app.command("status")(status)
app.command("init")(init)
app.command("chat")(chat)
app.command("search")(search)

# Subcommand groups
app.add_typer(models_app, name="models")
app.add_typer(files_app, name="files")
app.add_typer(projects_app, name="projects")
app.add_typer(rules_app, name="rules")
app.add_typer(agents_app, name="agents")
app.add_typer(generate_app, name="generate")
app.add_typer(jobs_app, name="jobs")
app.add_typer(settings_app, name="settings")


def version_callback(value: bool):
    if value:
        print(f"llx {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=version_callback, is_eager=True),
):
    """Guaardvark CLI — chat, search, manage files, and more."""
    if ctx.invoked_subcommand is None:
        from llx.repl import launch_repl
        launch_repl()


def run():
    app()


if __name__ == "__main__":
    run()
```

**Step 2: Reinstall and test all commands**

```bash
cd /home/llamax1/LLAMAX7/cli && pip install -e .
llx --help
llx --version
llx health
llx status
llx models list
llx chat "hello"
llx search "test query"
llx files list
llx projects list
llx rules list
llx agents list
llx jobs list
llx settings list
llx  # REPL mode
```

**Step 3: Commit**

```bash
git add cli/llx/main.py
git commit -m "feat(cli): wire up all command groups in main app"
```

---

### Task 18: Integration Testing & Polish

**Step 1: Test all commands against running server**

Start the Guaardvark server if not running:
```bash
cd /home/llamax1/LLAMAX7 && ./start.sh --fast
```

Run through each command:
```bash
llx health
llx status
llx models list
llx models active
llx chat "What is your name?"
llx chat --list
echo "Summarize this: hello world" | llx chat
llx search "test"
llx files list
llx projects list
llx rules list
llx agents list
llx jobs list
llx settings list
llx --json health
llx health | cat  # pipe detection test
```

**Step 2: Fix any issues found**

Address connection errors, missing fields, incorrect API paths, etc.

**Step 3: Install shell completions**

```bash
llx --install-completion
```

**Step 4: Final commit**

```bash
git add -A cli/
git commit -m "feat(cli): complete llx CLI with all commands, REPL, and streaming chat"
```
