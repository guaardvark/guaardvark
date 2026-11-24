"""guaardvark launch — start Guaardvark with Ollama integration."""
import os
import subprocess
import sys
import threading
import time

from pathlib import Path

import typer

from llx import output
from llx.global_opts import get_global_json
from llx.launch_config import (
    _config_path,
    is_first_launch,
    load_launch_config,
    resolve_guaardvark_root,
    resolve_ollama_url,
    save_launch_config,
)
from llx.theme import make_console


def launch(
    project: str | None = typer.Argument(None, help="Optional project folder to cd into and analyze (drag & drop support)"),
    model: str = typer.Option(None, "--model", "-m", help="Model to use"),
    config_only: bool = typer.Option(False, "--config", help="Configure only, don't start"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve all prompts"),
    full: bool = typer.Option(False, "--full", help="Launch with full stack (PostgreSQL, Redis, web UI)"),
    lite: bool = typer.Option(False, "--lite", help="Force lite mode (SQLite, TUI only)"),
    port: int = typer.Option(5002, "--port", "-p", help="Backend server port"),
    json_out: bool = typer.Option(False, "--json", "-j",
                                  help="No REPL: report the configuration (--config) or the started full stack (--full) as JSON"),
):
    """Launch Guaardvark with Ollama integration.

    Supports project folders (drag & drop): guaardvark /path/to/website "analyze and suggest CSS improvements"

    First launch runs an onboarding wizard. Subsequent launches start
    services and open the REPL (auto-loads GUAARDVARK.md, explores build.py/CSS, etc.).

    Lite mode (default): instant start with SQLite, no external dependencies.
    Full mode: PostgreSQL, Redis, Celery, web UI at localhost:5173.
    """
    if json_out or get_global_json():
        output.set_json_mode(True)
        _launch_json(project, model, config_only, yes, full, lite)
        return

    console = make_console()

    # 1. Onboarding on first launch
    if is_first_launch():
        from llx.onboarding import run_onboarding
        cfg = run_onboarding(auto_yes=yes, model=model)
        if config_only:
            return
        model = cfg.get("model")
    elif config_only:
        from llx.onboarding import run_onboarding
        run_onboarding(auto_yes=yes, model=model)
        return

    # 2. Determine mode
    cfg = load_launch_config()
    if full:
        mode = "full"
    elif lite:
        mode = "lite"
    else:
        mode = cfg.get("mode", "lite")

    # Apply model override if provided
    if model:
        cfg["model"] = model
        save_launch_config(cfg)

    # 3. Start services
    if mode == "full":
        _start_full_stack(console, cfg)
    else:
        _start_lite_mode(console, port)

    # 4. Handle project path (drag folder support)
    if project:
        from pathlib import Path
        p = Path(project).expanduser().resolve()
        if p.exists() and p.is_dir():
            import os
            os.chdir(p)
            console.print(f"[llx.success]Switched to project: {p}[/llx.success]")
        else:
            console.print(f"[llx.warning]Project path not found or not dir: {project}[/llx.warning]")

    # 5. Launch REPL (will auto-detect GUAARDVARK.md + populate context)
    from llx.repl import launch_repl
    launch_repl()


def _json_error(code: str, message: str, hint: str | None = None, data: dict | None = None):
    error = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    payload = {"status": "error", "error": error}
    if data is not None:
        payload = {"status": "error", "data": data, "error": error}
    output.print_json(payload)
    raise typer.Exit(1)


def _launch_json(project, model, config_only: bool, yes: bool, full: bool, lite: bool) -> None:
    """launch for scripts: never prompts and never opens the REPL.

    The lite server lives inside the REPL's process, so only --config (save the
    launch settings) and the full stack (start.sh, which outlives this command)
    can be reported."""
    onboarded = False
    if is_first_launch() or config_only:
        if not yes:
            _json_error("CONFIRMATION_REQUIRED",
                        "Onboarding asks for confirmation; nothing was saved.",
                        hint="Pass --yes to accept the onboarding notice and save the defaults.")
        from llx.onboarding import run_onboarding
        cfg = run_onboarding(auto_yes=True, model=model, quiet=True)
        onboarded = True
        model = cfg.get("model")
        if config_only:
            output.print_json({"status": "success", "data": {
                "onboarded": True, "started": None, **_launch_summary(cfg)}})
            return

    cfg = load_launch_config()
    mode = "full" if full else "lite" if lite else cfg.get("mode", "lite")
    if mode != "full":
        _json_error("LITE_NEEDS_REPL",
                    "The lite server runs inside the interactive session, so it cannot be started for JSON output.",
                    hint="Use --full to start the full stack, or --config to save settings only.",
                    data={"onboarded": onboarded, "started": None, **_launch_summary(cfg)})

    if model:
        cfg["model"] = model
        save_launch_config(cfg)
    started = _start_full_stack(None, cfg, quiet=True)
    data = {"onboarded": onboarded, "started": started, **_launch_summary(cfg)}
    if project:
        p = Path(project).expanduser().resolve()
        data["project"] = {"path": str(p), "found": p.is_dir()}
    output.print_json({"status": "success", "data": data})


def _launch_summary(cfg: dict) -> dict:
    return {"mode": cfg.get("mode"), "model": cfg.get("model"),
            "ollama_base_url": cfg.get("ollama_base_url"), "config_file": str(_config_path())}


def _wait_for_health(server_url: str, tries: int, interval: float, timeout: float,
                     started_after: float | None = None) -> bool:
    """Poll /api/health until it answers 200. Every other outcome, an error
    status included, waits ``interval`` before the next try. With
    ``started_after`` (an epoch time), a backend whose uptime says it was
    already running then is the old one, which start.sh is about to stop."""
    import httpx

    for attempt in range(tries):
        try:
            resp = httpx.get(f"{server_url}/api/health", timeout=timeout)
            if resp.status_code == 200 and not _started_before(resp, started_after):
                return True
        except Exception:
            pass
        if attempt < tries - 1:
            time.sleep(interval)
    return False


def _started_before(resp, moment: float | None) -> bool:
    """True when the answering backend's uptime places its start before ``moment``.
    A reply without uptime_seconds cannot be dated and counts as new."""
    if moment is None:
        return False
    try:
        uptime = float(resp.json().get("uptime_seconds"))
    except Exception:
        return False
    # One second of slack for the rounding in uptime_seconds.
    return time.time() - uptime < moment - 1


def _start_lite_mode(console, port: int):
    """Start the embedded lite server in a background thread."""
    from llx.lite_server import create_lite_app
    from llx.config import load_config, save_config
    from llx.launch_config import load_launch_config, save_launch_config

    app = create_lite_app()

    def run_server():
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    server_url = f"http://127.0.0.1:{port}"
    if _wait_for_health(server_url, tries=20, interval=0.25, timeout=1):
        console.print(f"[llx.success]Lite server ready[/llx.success] [llx.dim]port {port}[/llx.dim]")
        cfg = load_launch_config()
        cfg["server_url"] = server_url
        cfg["mode"] = "lite"
        save_launch_config(cfg)
        llx_cfg = load_config()
        llx_cfg["server"] = server_url
        save_config(llx_cfg)
        return

    console.print("[llx.warning]Lite server may not be ready yet[/llx.warning]")


def _refuse_if_other_install(console, root) -> None:
    """Exit before start.sh when another Guaardvark install holds this one's ports.

    start.sh refuses too, but launch runs it in the background with its output
    discarded and would then take the other install's backend for this one.
    """
    from llx import output
    from llx.port_owner import other_install_holders, start_ports

    held = other_install_holders(root, start_ports(root))
    if not held:
        return
    first = held[0]
    message = (f"Port {first['port']} is held by another Guaardvark install at "
               f"{first['install']} (PID {first['pid']}); not starting this one over it.")
    hint = (f"Stop that install with {first['install']}/stop.sh, or give this one other "
            f"ports (FLASK_PORT and VITE_PORT in {root}/.env).")
    if output.is_json_mode():
        output.print_json({"status": "error", "data": {"root": str(root), "held": held},
                           "error": {"code": "PORT_HELD_BY_OTHER_INSTALL",
                                     "message": message, "hint": hint}})
    else:
        console.print(f"[llx.error]{message}[/llx.error]")
        console.print(f"[llx.dim]{hint}[/llx.dim]")
    raise typer.Exit(1)


def _start_full_stack(console, cfg: dict, quiet: bool = False) -> dict:
    """Start the full Guaardvark stack via start.sh. ``quiet`` reports failures
    as JSON and returns {root, server_url, ready} without printing."""
    root = resolve_guaardvark_root()

    if root is None:
        env_root = os.environ.get("GUAARDVARK_ROOT")
        message = (f"GUAARDVARK_ROOT is {env_root}, which is not a folder." if env_root
                   else "Guaardvark installation not found.")
        if quiet:
            _json_error("INSTALL_NOT_FOUND", message,
                        hint="Set GUAARDVARK_ROOT to the folder that holds start.sh.")
        console.print(f"[llx.error]{message}[/llx.error]")
        console.print("[llx.dim]Set GUAARDVARK_ROOT or run: guaardvark launch --lite[/llx.dim]")
        raise typer.Exit(1)

    start_script = root / "start.sh"
    if not start_script.exists():
        if quiet:
            _json_error("START_SCRIPT_NOT_FOUND", f"start.sh not found at {root}")
        console.print(f"[llx.error]start.sh not found at {root}[/llx.error]")
        raise typer.Exit(1)

    _refuse_if_other_install(console, root)

    if not quiet:
        console.print(f"[llx.dim]Starting full stack from {root}...[/llx.dim]")

    # start.sh runs in the foreground — launch it in a background process
    spawned_at = time.time()
    subprocess.Popen(
        ["bash", str(start_script)],
        cwd=str(root),
        env={**os.environ, "GUAARDVARK_ROOT": str(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    from llx.backend_bootstrap import resolve_flask_port
    server_url = cfg.get("server_url", f"http://localhost:{resolve_flask_port(root)}")
    ready = _wait_for_health(server_url, tries=60, interval=1, timeout=2, started_after=spawned_at)
    if not quiet:
        if ready:
            console.print(f"[llx.success]Full stack ready[/llx.success]")
        else:
            console.print("[llx.warning]Backend may still be starting...[/llx.warning]")

    cfg["guaardvark_root"] = str(root)
    cfg["mode"] = "full"
    save_launch_config(cfg)
    return {"root": str(root), "server_url": server_url, "ready": ready}
