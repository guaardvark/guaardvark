"""Ensure the real Flask backend is running (Postgres + Redis + backend.app).

Used by bare `guaardvark`, the REPL, and `guaardvark start --backend-only`.
Prefers `./start.sh --backend-only --fast` when the install root is found.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

from llx.launch_config import resolve_guaardvark_root

DEFAULT_FLASK_PORT = 5000
RUNTIME_FILE = Path.home() / ".guaardvark" / "runtime.json"


def read_flask_port_from_env(root: Path | None = None) -> int | None:
    """Parse FLASK_PORT from repo .env if present."""
    if root is None:
        root = resolve_guaardvark_root()
    if root is None:
        return None
    env_path = root / ".env"
    if not env_path.is_file():
        return None
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "FLASK_PORT" and val.strip():
                return int(val.strip().strip('"').strip("'"))
    except (OSError, ValueError):
        pass
    return None


def resolve_flask_port(root: Path | None = None) -> int:
    """Resolution order: env → repo .env → runtime.json → default 5000."""
    env_port = os.environ.get("FLASK_PORT") or os.environ.get("FLASK_RUN_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass

    from_env = read_flask_port_from_env(root)
    if from_env is not None:
        return from_env

    if RUNTIME_FILE.exists():
        try:
            runtime = json.loads(RUNTIME_FILE.read_text())
            port = runtime.get("backend_port")
            if port:
                return int(port)
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    return DEFAULT_FLASK_PORT


def server_url_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_backend_healthy(server_url: str | None = None, port: int | None = None, timeout: float = 2) -> bool:
    """Return True when /api/health responds with status ok."""
    if server_url is None:
        if port is None:
            port = resolve_flask_port()
        server_url = server_url_for_port(port)
    try:
        resp = httpx.get(f"{server_url.rstrip('/')}/api/health", timeout=timeout)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return data.get("status") == "ok"
    except Exception:
        return False


def _sync_server_config(server_url: str) -> None:
    """Keep ~/.guaardvark/cli.json server URL aligned after a successful bootstrap."""
    try:
        from llx.config import load_config, save_config

        cfg = load_config()
        if cfg.get("server") != server_url:
            cfg["server"] = server_url
            save_config(cfg)
    except Exception:
        pass


def write_runtime_state(root: Path, port: int, pid: int, started_by: str = "cli") -> None:
    """Write ~/.guaardvark/runtime.json for CLI / frontend discovery."""
    runtime_dir = RUNTIME_FILE.parent
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "root": str(root),
        "backend_port": port,
        "frontend_port": int(os.environ.get("VITE_PORT", "5173")),
        "backend_pid": pid,
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "started_by": started_by,
    }
    RUNTIME_FILE.write_text(json.dumps(payload, indent=2) + "\n")


def _read_backend_pid(root: Path) -> int:
    pid_file = root / "pids" / "backend.pid"
    if not pid_file.is_file():
        return 0
    try:
        return int(pid_file.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def ensure_backend_running(console=None, quiet: bool = False) -> tuple[str, bool]:
    """Start backend if offline. Returns (server_url, was_started)."""
    root = resolve_guaardvark_root()
    if root is None:
        raise RuntimeError(
            "Guaardvark installation not found. Set GUAARDVARK_ROOT or run from the repo."
        )

    port = resolve_flask_port(root)
    server_url = server_url_for_port(port)

    if is_backend_healthy(server_url):
        return server_url, False

    start_script = root / "start.sh"
    if not start_script.is_file():
        raise RuntimeError(f"start.sh not found at {root}")

    if console and not quiet:
        console.print(f"[llx.dim]Starting backend (Postgres, Redis, Flask) on port {port}...[/llx.dim]")

    env = {
        **os.environ,
        "GUAARDVARK_ROOT": str(root),
        "GUAARDVARK_STARTED_BY": "cli",
        "FLASK_PORT": str(port),
    }
    result = subprocess.run(
        ["bash", str(start_script), "--backend-only", "--fast", "--no-voice"],
        cwd=str(root),
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Backend bootstrap failed (exit {result.returncode}). "
            f"See {root / 'logs' / 'backend_startup.log'} for details."
        )

    if not is_backend_healthy(server_url, timeout=5):
        for _ in range(24):
            if is_backend_healthy(server_url, timeout=2):
                break
            import time

            time.sleep(1)
        else:
            raise RuntimeError(
                f"Backend did not respond on {server_url} after bootstrap. "
                f"Check {root / 'logs' / 'backend_startup.log'}."
            )

    pid = _read_backend_pid(root)
    write_runtime_state(root, port, pid, started_by="cli")
    _sync_server_config(server_url)

    if console and not quiet:
        console.print(f"[llx.success]Backend ready[/llx.success] [llx.dim]{server_url}[/llx.dim]")

    return server_url, True
