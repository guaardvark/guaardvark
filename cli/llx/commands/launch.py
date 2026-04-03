"""guaardvark launch — start Guaardvark with Ollama integration."""
import os
import subprocess
import sys
import threading
import time

import typer

from llx.launch_config import (
    is_first_launch,
    load_launch_config,
    resolve_guaardvark_root,
    resolve_ollama_url,
    save_launch_config,
)
from llx.theme import make_console


def launch(
    model: str = typer.Option(None, "--model", "-m", help="Model to use"),
    config_only: bool = typer.Option(False, "--config", help="Configure only, don't start"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve all prompts"),
    full: bool = typer.Option(False, "--full", help="Launch with full stack (PostgreSQL, Redis, web UI)"),
    lite: bool = typer.Option(False, "--lite", help="Force lite mode (SQLite, TUI only)"),
    port: int = typer.Option(5000, "--port", "-p", help="Backend server port"),
):
    """Launch Guaardvark with Ollama integration.

    First launch runs an onboarding wizard. Subsequent launches start
    services and open the REPL.

    Lite mode (default): instant start with SQLite, no external dependencies.
    Full mode: PostgreSQL, Redis, Celery, web UI at localhost:5173.
    """
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

    # 4. Launch REPL
    from llx.repl import launch_repl
    launch_repl()


def _start_lite_mode(console, port: int):
    """Start the embedded lite server in a background thread."""
    from llx.lite_server import create_lite_app

    app = create_lite_app()

    def run_server():
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait for server to be ready
    import httpx
    for _ in range(20):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
            if resp.status_code == 200:
                console.print(f"[llx.success]Lite server ready[/llx.success] [llx.dim]port {port}[/llx.dim]")
                return
        except Exception:
            time.sleep(0.25)

    console.print("[llx.warning]Lite server may not be ready yet[/llx.warning]")


def _start_full_stack(console, cfg: dict):
    """Start the full Guaardvark stack via start.sh."""
    root = resolve_guaardvark_root()

    if root is None:
        console.print("[llx.error]Guaardvark installation not found.[/llx.error]")
        console.print("[llx.dim]Set GUAARDVARK_ROOT or run: guaardvark launch --lite[/llx.dim]")
        raise typer.Exit(1)

    start_script = root / "start.sh"
    if not start_script.exists():
        console.print(f"[llx.error]start.sh not found at {root}[/llx.error]")
        raise typer.Exit(1)

    console.print(f"[llx.dim]Starting full stack from {root}...[/llx.dim]")

    result = subprocess.run(
        ["bash", str(start_script)],
        cwd=str(root),
        env={**os.environ, "GUAARDVARK_ROOT": str(root)},
    )

    if result.returncode != 0:
        console.print("[llx.error]Failed to start services.[/llx.error]")
        raise typer.Exit(1)

    cfg["guaardvark_root"] = str(root)
    cfg["mode"] = "full"
    save_launch_config(cfg)
