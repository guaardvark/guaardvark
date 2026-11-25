import os
import re
import subprocess
import sys
from contextlib import contextmanager

import typer
from rich.markup import escape

from llx.client import get_client, LlxError, LlxConnectionError
from llx.config import load_config, save_config, CONFIG_FILE
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console, make_panel, ICON_ONLINE, ICON_OFFLINE
from llx import output

console = make_console()

system_app = typer.Typer(help="System and model commands")
models_app = typer.Typer(help="LLM model management", no_args_is_help=True)


def _payload(resp: dict) -> dict:
    """The body of a backend reply: `data`, or an older route's dict `message`.
    A string `message` ("Model status retrieved") is a status line, not the body."""
    for key in ("data", "message"):
        if isinstance(resp.get(key), dict):
            return resp[key]
    return {}


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


BACKEND_ONLY_UNAVAILABLE = (
    "--backend-only is not available: start.sh has no backend-only mode. It starts "
    "the whole stack: Redis, PostgreSQL, Ollama, the backend, Celery, the frontend "
    "and the plugins set to start automatically. Run `guaardvark start` for that."
)


def start(
    path: str | None = typer.Option(None, "--path", "-p", help="Project path (default: GUAARDVARK_ROOT or cwd)"),
    backend_only: bool = typer.Option(False, "--backend-only", hidden=True),
    json_out: bool = typer.Option(False, "--json", "-j",
                                  help="Send start.sh's output to stderr and report what was started as JSON"),
):
    """Start Guaardvark services."""
    if json_out or get_global_json():
        output.set_json_mode(True)
        _start_json(path, backend_only)
        return
    if backend_only:
        output.print_error(BACKEND_ONLY_UNAVAILABLE)
        raise typer.Exit(2)

    target = path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    target = _find_project_root(target)
    start_script = os.path.join(target, "start.sh")
    if not os.path.isfile(start_script):
        output.print_error(f"start.sh not found at {start_script}")
        raise typer.Exit(1)
    result = subprocess.run([start_script], cwd=target)
    raise typer.Exit(result.returncode)


@contextmanager
def _stdout_to_stderr():
    """Point fd 1 at stderr so child scripts cannot write into the JSON document."""
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


def _start_json(path: str | None, backend_only: bool) -> None:
    if backend_only:
        output.print_json({"status": "error", "data": {"action": "start", "backend_only": True},
                           "error": {"code": "BACKEND_ONLY_UNAVAILABLE",
                                     "message": BACKEND_ONLY_UNAVAILABLE}})
        raise typer.Exit(2)
    _run_script_json("start", path)


def _run_script_json(action: str, path: str | None) -> None:
    """Run start.sh or stop.sh with its output on stderr, then report the exit
    code and whether the backend answers afterwards."""
    from pathlib import Path

    from llx.backend_bootstrap import is_backend_healthy, resolve_flask_port, server_url_for_port

    target = _find_project_root(path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd())
    script = os.path.join(target, f"{action}.sh")
    if not os.path.isfile(script):
        output.print_json({"status": "error", "error": {
            "code": "SCRIPT_NOT_FOUND", "message": f"{action}.sh not found at {script}"}})
        raise typer.Exit(1)
    sys.stdout.flush()
    result = subprocess.run([script], cwd=target, stdout=2)
    server_url = server_url_for_port(resolve_flask_port(Path(target)))
    data = {"action": action, "root": target, "script": script, "returncode": result.returncode,
            "server_url": server_url, "backend_responding": is_backend_healthy(server_url)}
    if result.returncode:
        output.print_json({"status": "error", "data": data, "error": {
            "code": f"{action.upper()}_FAILED", "message": f"{action}.sh exited {result.returncode}"}})
        raise typer.Exit(result.returncode)
    output.print_json({"status": "success", "data": data})


def _doctor_cli_check(json_out: bool = False) -> None:
    """Validate CLI-side config and backend reachability."""
    from pathlib import Path

    from llx.backend_bootstrap import is_backend_healthy
    from llx.config import CONFIG_FILE, LEGACY_CONFIG_FILE, get_server_url
    from llx.launch_config import _config_path as launch_cfg_path, load_launch_config
    from llx.lite_mode import is_lite_mode

    checks: list[tuple[str, bool, str]] = []

    if CONFIG_FILE.is_file():
        checks.append(("~/.guaardvark/cli.json", True, str(CONFIG_FILE)))
    elif LEGACY_CONFIG_FILE.is_file():
        checks.append(("~/.llx/config.json (legacy)", True, str(LEGACY_CONFIG_FILE)))
    else:
        checks.append(("CLI config", True, f"{CONFIG_FILE} (not created yet)"))

    # The launch contract is optional: only `guaardvark launch` writes it, and
    # without it the mode is full. Reported, never a failure.
    launch_path = launch_cfg_path()
    launch_exists = launch_path.is_file()
    checks.append(("~/.guaardvark/config.json", True,
                   str(launch_path) if launch_exists
                   else f"{launch_path} (not created; optional, `guaardvark launch` writes it)"))

    mode = "full"
    if launch_exists:
        mode = load_launch_config().get("mode", "lite")
    checks.append(("launch mode", True, mode))

    server = get_server_url()
    healthy = is_backend_healthy(server)
    checks.append(("backend health", healthy, server))

    root = os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    root_ok = Path(root, "start.sh").is_file()
    checks.append(("install root (start.sh)", root_ok, root))

    if is_lite_mode():
        checks.append(
            (
                "lite/full note",
                True,
                "lite mode — full-stack slash commands (files, jobs, rag, …) need launch --full",
            )
        )

    try:
        from llx.termcaps import detect, tmux_hints

        caps = detect()
        gfx = caps.graphics
        checks.append(("terminal", True, f"{caps.name}  colors={caps.color_count}  graphics={gfx}"))
        if caps.tmux:
            checks.append(("tmux", True, "passthrough needed for inline images: " + "; ".join(tmux_hints()[:2])))
    except Exception:
        pass

    failed = [label for label, ok, _ in checks
              if label not in ("lite/full note", "launch mode", "terminal", "tmux") and not ok]
    if json_out:
        data = {"checks": [{"name": label, "ok": ok, "detail": detail} for label, ok, detail in checks],
                "failed": failed}
        if failed:
            output.print_json({"status": "error", "data": data, "error": {
                "code": "CHECKS_FAILED", "message": f"Failed: {', '.join(failed)}"}})
            raise typer.Exit(1)
        output.print_json({"status": "success", "data": data})
        return

    console.print("[llx.brand]CLI Doctor[/llx.brand]\n")
    for label, ok, detail in checks:
        icon = ICON_ONLINE if ok else ICON_OFFLINE
        style = "llx.status.online" if ok else "llx.status.offline"
        console.print(f"  [{style}]{icon}[/{style}] [llx.kv.key]{label}:[/llx.kv.key] {detail}")

    if failed:
        hints = {"backend health": "guaardvark start",
                 "install root (start.sh)": "run from the install folder or set GUAARDVARK_ROOT"}
        tries = [hints[label] for label in failed if label in hints]
        console.print(f"\n[llx.warning]Failed: {', '.join(failed)}."
                      + (f" Try: {'; '.join(tries)}" if tries else "") + "[/llx.warning]")
        raise typer.Exit(1)
    console.print("\n[llx.success]CLI checks passed.[/llx.success]")


def stop(
    path: str | None = typer.Option(None, "--path", "-p", help="Project path (default: GUAARDVARK_ROOT or cwd)"),
    json_out: bool = typer.Option(False, "--json", "-j",
                                  help="Send stop.sh's output to stderr and report what was stopped as JSON"),
):
    """Stop Guaardvark services."""
    if json_out or get_global_json():
        output.set_json_mode(True)
        _run_script_json("stop", path)
        return
    target = path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    target = _find_project_root(target)
    stop_script = os.path.join(target, "stop.sh")
    if not os.path.isfile(stop_script):
        output.print_error(f"stop.sh not found at {stop_script}")
        raise typer.Exit(1)
    result = subprocess.run([stop_script], cwd=target)
    raise typer.Exit(result.returncode)


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def doctor(
    path: str | None = typer.Option(None, "--path", "-p", help="Project path (default: GUAARDVARK_ROOT or cwd)"),
    repair: bool = typer.Option(False, "--repair", "-r", help="Run repair instead of check"),
    cli_check: bool = typer.Option(False, "--cli", help="Validate CLI config, ports, and mode (no system-manager)"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Run environment health check (or repair) via system-manager."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    if cli_check:
        _doctor_cli_check(json_out)
        raise typer.Exit(0)

    target = path or os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    target = _find_project_root(target)
    manager_script = os.path.join(target, "scripts", "system-manager", "system-manager")
    if not os.path.isfile(manager_script):
        output.print_error(f"System manager not found at {manager_script}")
        raise typer.Exit(1)
    cmd = [manager_script, "repair" if repair else "check", target]
    if json_out:
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = {"command": "repair" if repair else "check", "returncode": result.returncode,
                "output": _ANSI_ESCAPE.sub("", result.stdout), "errors": _ANSI_ESCAPE.sub("", result.stderr)}
        if result.returncode:
            output.print_json({"status": "error", "data": data, "error": {
                "code": "CHECK_FAILED", "message": f"system-manager exited {result.returncode}"}})
        else:
            output.print_json({"status": "success", "data": data})
        raise typer.Exit(result.returncode)
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        result = subprocess.run(cmd)
    else:
        # system-manager colours its output unconditionally; strip that for
        # pipes, files and NO_COLOR.
        result = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(_ANSI_ESCAPE.sub("", result.stdout))
        sys.stderr.write(_ANSI_ESCAPE.sub("", result.stderr))
    raise typer.Exit(result.returncode)


def health(
    server: str = typer.Option(None, "--server", "-s", help="Server URL override"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Is the backend up? One line: status, version, uptime."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/health")
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
        else:
            status = data.get("status", "unknown")
            icon = ICON_ONLINE if status == "ok" else ICON_OFFLINE
            style = "llx.status.online" if status == "ok" else "llx.status.offline"
            version = data.get("version", "?")
            uptime = int(data.get("uptime_seconds", 0))
            h, m = divmod(uptime // 60, 60)
            console.print(f"[{style}]{icon} {status}[/{style}]  [llx.dim]|[/llx.dim]  Version: {version}  [llx.dim]|[/llx.dim]  Uptime: {h}h {m}m")
    except LlxConnectionError as e:
        raise output.fail(e)


def _sub_check(client, path: str) -> dict:
    """One row of `status` beyond the server itself. A check the backend
    answers with an error, or does not answer in time, comes back as
    `{"status": "unknown", "error": ...}` so the other rows still print."""
    try:
        return client.get(path)
    except LlxConnectionError:
        raise
    except LlxError as e:
        return {"status": "unknown", "error": e.message}


def status(
    server: str = typer.Option(None, "--server", "-s", help="Server URL override"),
    json_out: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Everything at a glance: server, chat model, workers, GPU, MCP, version."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        health_data = client.get("/api/health")
        model_data = _sub_check(client, "/api/model/status")
        celery_data = _sub_check(client, "/api/health/celery")

        try:
            metrics_data = client.get("/api/meta/metrics")
        except LlxError:
            metrics_data = {}
        try:
            mcp_data = client.get("/api/automation/mcp/status")
        except LlxError:
            mcp_data = {}

        if json_out or output.is_pipe():
            output.print_json(
                {
                    "status": "success",
                    "data": {
                        "health": health_data,
                        "model": model_data,
                        "celery": celery_data,
                        "metrics": metrics_data,
                        "mcp": mcp_data,
                    },
                }
            )
            return

        server_url = client.server_url
        status_ok = health_data.get("status") == "ok"
        s_icon = ICON_ONLINE if status_ok else ICON_OFFLINE
        s_style = "llx.status.online" if status_ok else "llx.status.offline"
        server_line = f"[llx.kv.key]Server:[/llx.kv.key]  {server_url}  [{s_style}]{s_icon} {'Online' if status_ok else 'Offline'}[/{s_style}]"

        model_info = _payload(model_data)
        if model_data.get("status") == "unknown":
            model_line = ("[llx.kv.key]Model:[/llx.kv.key]   [llx.dim]unknown "
                          f"({escape(str(model_data.get('error', '')))})[/llx.dim]")
        else:
            text_model = model_info.get("text_model", "none")
            model_line = f"[llx.kv.key]Model:[/llx.kv.key]   [llx.accent]{text_model}[/llx.accent]"

        celery_status = celery_data.get("status", "unknown")
        workers = celery_data.get("workers", [])
        c_icon = ICON_ONLINE if celery_status == "up" else ICON_OFFLINE
        c_style = "llx.status.online" if celery_status == "up" else "llx.status.offline"
        if celery_status == "unknown":
            celery_line = (f"[llx.kv.key]Celery:[/llx.kv.key]  [{c_style}]{c_icon} unknown[/{c_style}]  "
                           f"[llx.dim]{escape(str(celery_data.get('error', '')))}[/llx.dim]")
        else:
            celery_line = f"[llx.kv.key]Celery:[/llx.kv.key]  {len(workers)} workers  [{c_style}]{c_icon} {celery_status}[/{c_style}]"

        metrics = metrics_data.get("data", metrics_data) if metrics_data else {}
        gpu_mem = metrics.get("gpu_mem")
        cpu_pct = metrics.get("cpu_percent")
        gpu_line = f"[llx.kv.key]GPU:[/llx.kv.key]     {gpu_mem:.0f}% memory" if gpu_mem is not None else "[llx.kv.key]GPU:[/llx.kv.key]     [llx.dim]N/A[/llx.dim]"
        cpu_line = f"[llx.kv.key]CPU:[/llx.kv.key]     {cpu_pct:.0f}% util" if cpu_pct is not None else "[llx.kv.key]CPU:[/llx.kv.key]     [llx.dim]N/A[/llx.dim]"

        version = health_data.get("version", "?")
        ver_line = f"[llx.kv.key]Version:[/llx.kv.key] {version}"

        if mcp_data.get("mcp_enabled"):
            mcp_ok = not mcp_data.get("errors") and not mcp_data.get("config_errors")
            m_style = "llx.status.online" if mcp_ok else "llx.status.offline"
            mcp_line = (f"[llx.kv.key]MCP:[/llx.kv.key]     {mcp_data.get('servers_connected', 0)}/"
                        f"{mcp_data.get('servers_configured', 0)} servers, "
                        f"{mcp_data.get('total_tools_available', 0)} tools"
                        + ("" if mcp_ok else f"  [{m_style}]{ICON_OFFLINE} see: guaardvark mcp client status[/{m_style}]"))
        else:
            mcp_line = "[llx.kv.key]MCP:[/llx.kv.key]     [llx.dim]disabled or unavailable[/llx.dim]"

        content = "\n".join([server_line, model_line, celery_line, gpu_line, cpu_line, mcp_line, ver_line])
        console.print(make_panel(content, title="System Status"))

    except LlxError as e:
        raise output.fail(e)


def init(
    server: str = typer.Option(None, "--server", "-s", help="Server URL (default: the saved one)"),
    api_key: str = typer.Option(None, "--api-key", help="API key to save; an empty value clears it"),
    json_out: bool = typer.Option(False, "--json", "-j",
                                  help="No prompts: test the server, save it, and report as JSON"),
):
    """Point the CLI at a Guaardvark server and save it (asks for the URL and API key)."""
    server = server or get_global_server()
    if json_out or get_global_json():
        output.set_json_mode(True)
        _setup_json(server, api_key)
        return

    console.print("[llx.brand]Guaardvark Setup[/llx.brand]\n")

    config = load_config()
    server = typer.prompt("Server URL", default=server or config["server"])

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
        model_info = _payload(model_data)
        model_name = model_info.get("text_model", "none")
        console.print(f"  Active model: [llx.accent]{model_name}[/llx.accent]")
    except LlxError:
        pass

    if api_key is None:
        api_key = typer.prompt("API key (leave blank for none)", default="", show_default=False)

    config["server"] = server
    config["api_key"] = api_key if api_key else None
    save_config(config)

    console.print(f"\n[llx.success]Config saved to {CONFIG_FILE}[/llx.success]")
    console.print("Run [bold]guaardvark --install-completion[/bold] for tab completions.")


def _setup_json(server: str | None, api_key: str | None) -> None:
    """setup without prompts. The server is saved only when it answers; the API
    key changes only when --api-key is given. The key itself is never printed."""
    config = load_config()
    server = server or config["server"]
    data = {"server": server, "saved": False, "config_file": str(CONFIG_FILE)}
    client = get_client(server)
    try:
        health = client.get("/api/health")
    except (LlxConnectionError, LlxError) as e:
        output.print_json({"status": "error", "data": data, "error": {
            "code": "CONNECTION_ERROR", "message": e.message,
            "hint": "Start the server (guaardvark start) and run setup again; nothing was saved."}})
        raise typer.Exit(output.EXIT_UNREACHABLE)
    data["version"] = health.get("version")
    try:
        data["active_model"] = _payload(client.get("/api/model/status")).get("text_model")
    except LlxError:
        data["active_model"] = None

    config["server"] = server
    if api_key is not None:
        config["api_key"] = api_key or None
    save_config(config)
    data["saved"] = True
    data["api_key"] = "set" if config.get("api_key") else "none"
    data["api_key_changed"] = api_key is not None
    output.print_json({"status": "success", "data": data})


@models_app.command("list")
def models_list(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass model cache"),
):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/model/list", refresh=str(refresh).lower())
        msg = _payload(data)
        models = msg.get("models", [])

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"models": models}})
            return

        rows = [{"name": m.get("name", "?"), "id": m.get("id", m.get("full_name", "?"))} for m in models]
        output.print_table(rows, columns=["name", "id"], title=f"Available Models ({len(rows)})")

    except LlxError as e:
        raise output.fail(e)


@models_app.command("active")
def models_active(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/model/status")
        info = _payload(data)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": info})
            return

        output.print_kv({
            "Text model": info.get("text_model", "none"),
            "Vision model": info.get("vision_model", "none"),
            "Vision loaded": str(info.get("vision_loaded", False)),
            "Image gen model": info.get("image_gen_model", "none"),
        }, title="Active Models")

    except LlxError as e:
        raise output.fail(e)


@models_app.command("set")
def models_set(
    model: str = typer.Argument(help="Model name to switch to"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        client.post("/api/model/set", json={"model": model})
        if json_out or output.is_pipe():
            output.print_json(
                {
                    "status": "success",
                    "data": {
                        "message": f"Switching to {model}... (this may take a moment)",
                        "model": model,
                    },
                }
            )
        else:
            output.print_success(f"Switching to {model}... (this may take a moment)")
    except LlxError as e:
        raise output.fail(e)
