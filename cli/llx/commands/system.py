import os
import subprocess
import typer

from llx.client import get_client, LlxError, LlxConnectionError
from llx.config import load_config, save_config, CONFIG_FILE
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console, make_panel, ICON_ONLINE, ICON_OFFLINE
from llx import output

console = make_console()

system_app = typer.Typer(help="System and model commands")
models_app = typer.Typer(help="LLM model management", no_args_is_help=True)


def _find_project_root(path: str) -> str:
    """Find project root (directory containing start.sh and scripts/system-manager)."""
    resolved = os.path.abspath(path)
    for _ in range(10):
        if os.path.isfile(os.path.join(resolved, "start.sh")) and os.path.isdir(
            os.path.join(resolved, "scripts", "system-manager")
        ):
            return resolved
        parent = os.path.dirname(resolved)
        if parent == resolved:
            break
        resolved = parent
    return os.path.abspath(path)


def start(
    path: str | None = typer.Option(None, "--path", "-p", help="Project path (default: GUAARDVARK_ROOT or cwd)"),
):
    """Start Guaardvark services (Flask, Celery, Vite)."""
    target = path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    target = _find_project_root(target)
    start_script = os.path.join(target, "start.sh")
    if not os.path.isfile(start_script):
        output.print_error(f"start.sh not found at {start_script}")
        raise typer.Exit(1)
    result = subprocess.run([start_script], cwd=target)
    raise typer.Exit(result.returncode)


def stop(
    path: str | None = typer.Option(None, "--path", "-p", help="Project path (default: GUAARDVARK_ROOT or cwd)"),
):
    """Stop Guaardvark services."""
    target = path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    target = _find_project_root(target)
    stop_script = os.path.join(target, "stop.sh")
    if not os.path.isfile(stop_script):
        output.print_error(f"stop.sh not found at {stop_script}")
        raise typer.Exit(1)
    result = subprocess.run([stop_script], cwd=target)
    raise typer.Exit(result.returncode)


def doctor(
    path: str | None = typer.Option(None, "--path", "-p", help="Project path (default: GUAARDVARK_ROOT or cwd)"),
    repair: bool = typer.Option(False, "--repair", "-r", help="Run repair instead of check"),
):
    """Run environment health check (or repair) via system-manager."""
    target = path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    target = _find_project_root(target)
    manager_script = os.path.join(target, "scripts", "system-manager", "system-manager")
    if not os.path.isfile(manager_script):
        output.print_error(f"System manager not found at {manager_script}")
        raise typer.Exit(1)
    cmd = [manager_script, "repair" if repair else "check", target]
    result = subprocess.run(cmd)
    raise typer.Exit(result.returncode)


def health(
    server: str = typer.Option(None, "--server", "-s", help="Server URL override"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/health")
        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            status = data.get("status", "unknown")
            icon = ICON_ONLINE if status == "ok" else ICON_OFFLINE
            style = "llx.status.online" if status == "ok" else "llx.status.offline"
            version = data.get("version", "?")
            uptime = int(data.get("uptime_seconds", 0))
            h, m = divmod(uptime // 60, 60)
            console.print(f"[{style}]{icon} {status}[/{style}]  [llx.dim]|[/llx.dim]  Version: {version}  [llx.dim]|[/llx.dim]  Uptime: {h}h {m}m")
    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)


def status(
    server: str = typer.Option(None, "--server", "-s", help="Server URL override"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        health_data = client.get("/api/health")
        model_data = client.get("/api/model/status")
        celery_data = client.get("/api/health/celery")

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

        server_url = client.server_url
        status_ok = health_data.get("status") == "ok"
        s_icon = ICON_ONLINE if status_ok else ICON_OFFLINE
        s_style = "llx.status.online" if status_ok else "llx.status.offline"
        server_line = f"[llx.kv.key]Server:[/llx.kv.key]  {server_url}  [{s_style}]{s_icon} {'Online' if status_ok else 'Offline'}[/{s_style}]"

        model_info = model_data.get("message", model_data.get("data", {}))
        if isinstance(model_info, str):
            model_info = {}
        text_model = model_info.get("text_model", "none")
        model_line = f"[llx.kv.key]Model:[/llx.kv.key]   [llx.accent]{text_model}[/llx.accent]"

        celery_status = celery_data.get("status", "unknown")
        workers = celery_data.get("workers", [])
        c_icon = ICON_ONLINE if celery_status == "up" else ICON_OFFLINE
        c_style = "llx.status.online" if celery_status == "up" else "llx.status.offline"
        celery_line = f"[llx.kv.key]Celery:[/llx.kv.key]  {len(workers)} workers  [{c_style}]{c_icon} {celery_status}[/{c_style}]"

        metrics = metrics_data.get("data", metrics_data) if metrics_data else {}
        gpu_mem = metrics.get("gpu_mem")
        cpu_pct = metrics.get("cpu_percent")
        gpu_line = f"[llx.kv.key]GPU:[/llx.kv.key]     {gpu_mem:.0f}% memory" if gpu_mem is not None else "[llx.kv.key]GPU:[/llx.kv.key]     [llx.dim]N/A[/llx.dim]"
        cpu_line = f"[llx.kv.key]CPU:[/llx.kv.key]     {cpu_pct:.0f}% util" if cpu_pct is not None else "[llx.kv.key]CPU:[/llx.kv.key]     [llx.dim]N/A[/llx.dim]"

        version = health_data.get("version", "?")
        ver_line = f"[llx.kv.key]Version:[/llx.kv.key] {version}"

        content = "\n".join([server_line, model_line, celery_line, gpu_line, cpu_line, ver_line])
        console.print(make_panel(content, title="System Status"))

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


def init():
    console.print("[llx.brand]LLX Setup[/llx.brand]\n")

    config = load_config()
    server = typer.prompt("Server URL", default=config["server"])

    console.print(f"\nTesting connection to [bold]{server}[/bold]...")
    try:
        client = get_client(server)
        data = client.get("/api/health")
        console.print(f"  [llx.success]{ICON_ONLINE} Connected![/llx.success] Server version: {data.get('version', '?')}")
    except (LlxConnectionError, LlxError) as e:
        console.print(f"  [llx.error]{ICON_OFFLINE} Failed:[/llx.error] {e}")
        if not typer.confirm("Save config anyway?", default=False):
            raise typer.Exit(1)

    try:
        model_data = client.get("/api/model/status")
        model_info = model_data.get("message", model_data.get("data", {}))
        if isinstance(model_info, str):
            model_info = {}
        model_name = model_info.get("text_model", "none")
        console.print(f"  Active model: [llx.accent]{model_name}[/llx.accent]")
    except LlxError:
        pass

    api_key = typer.prompt("API key (leave blank for none)", default="", show_default=False)

    config["server"] = server
    config["api_key"] = api_key if api_key else None
    save_config(config)

    console.print(f"\n[llx.success]Config saved to {CONFIG_FILE}[/llx.success]")
    console.print("Run [bold]llx --install-completion[/bold] for tab completions.")


@models_app.command("list")
def models_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass model cache"),
):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/model/list", refresh=str(refresh).lower())
        msg = data.get("message", data.get("data", {}))
        if isinstance(msg, str):
            msg = {}
        models = msg.get("models", [])

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
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/model/status")
        info = data.get("message", data.get("data", {}))
        if isinstance(info, str):
            info = {}

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
    server = server or get_global_server()
    try:
        client = get_client(server)
        data = client.post("/api/model/set", json={"model": model})
        output.print_success(f"Switching to {model}... (this may take a moment)")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
