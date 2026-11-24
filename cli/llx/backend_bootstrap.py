"""Ensure this install's Flask backend is running.

Used by bare `guaardvark` and the REPL. When the backend is not answering it runs
`./start.sh --fast --no-voice`, which starts the whole stack: start.sh has no
backend-only mode.
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


def _env_file_has(root: Path, key: str) -> bool:
    try:
        return any(line.strip().startswith(f"{key}=") for line in (root / ".env").read_text().splitlines())
    except OSError:
        return False


def is_docker_checkout(root: Path) -> bool:
    """A checkout that runs Guaardvark with ./start-docker.sh: its .env holds the
    database password start-docker.sh writes, and start.sh has never built
    backend/venv there. Starting start.sh in it would set up a second, native
    install next to the containers."""
    return _env_file_has(root, "GUAARDVARK_POSTGRES_PASSWORD") and not (root / "backend" / "venv").exists()


def resolve_flask_port(root: Path | None = None) -> int:
    """Resolution order: env → repo .env → runtime.json → default 5000.

    runtime.json is one file per user; with ``root`` given, a port it records for
    another install is skipped.
    """
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
            if port and not _runtime_for_other_root(runtime, root):
                return int(port)
        except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError):
            pass

    return DEFAULT_FLASK_PORT


def _runtime_for_other_root(runtime: dict, root: Path | None) -> bool:
    recorded = runtime.get("root")
    if root is None or not recorded:
        return False
    return Path(recorded).resolve() != Path(root).resolve()


def other_install_on_port(root: Path, port: int) -> tuple[int, Path] | None:
    """The listener on ``port`` when it belongs to a Guaardvark install other than
    ``root`` (a worktree nested inside it included), as (pid, that install's root).

    None when the listener is this install's, or when nothing tells whose it is
    (no listener this user can see, such as a container's port proxy).
    """
    from llx.port_owner import other_install_holders

    try:
        held = other_install_holders(root, [port])
    except (OSError, subprocess.SubprocessError):
        return None
    return (held[0]["pid"], Path(held[0]["install"])) if held else None


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
        other = other_install_on_port(root, port)
        if other is not None:
            pid, other_root = other
            raise RuntimeError(
                f"Port {port} is answered by the Guaardvark install at {other_root} (PID {pid}), "
                f"not this one at {root}. Stop that install, or give this one its own "
                f"FLASK_PORT in {root / '.env'}."
            )
        return server_url, False

    start_script = root / "start.sh"
    if not start_script.is_file():
        if os.environ.get("GUAARDVARK_DOCKER") == "1":
            # Inside the backend container: the API is this container's own
            # process and Docker restarts it, so there is nothing to start here.
            raise RuntimeError(
                f"The backend is not answering at {server_url}. It runs in this container; "
                "on the host, `docker compose logs backend` shows why, and ./start-docker.sh starts it again."
            )
        raise RuntimeError(f"start.sh not found at {root}")
    if is_docker_checkout(root):
        raise RuntimeError(
            f"The backend is not answering at {server_url}. {root} runs Guaardvark in Docker: "
            "start it with ./start-docker.sh there, and if its API is published on another "
            "port, set GUAARDVARK_SERVER to it."
        )

    if console and not quiet:
        console.print(f"[llx.dim]Starting Guaardvark (./start.sh --fast) on port {port}...[/llx.dim]")

    env = {
        **os.environ,
        "GUAARDVARK_ROOT": str(root),
        "GUAARDVARK_STARTED_BY": "cli",
        "FLASK_PORT": str(port),
    }
    result = subprocess.run(
        ["bash", str(start_script), "--fast", "--no-voice"],
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
