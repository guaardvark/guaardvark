"""CLI configuration — loads/saves ~/.guaardvark/cli.json.

The previous home was ~/.llx/config.json (from the deprecated `llx` command).
Reads fall back to that path; writes always go to the new path. The launch
contract (~/.guaardvark/config.json) is a different file and is not touched.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

# Environment variables override config file (for scripting/CI)
ENV_SERVER = "GUAARDVARK_SERVER"
ENV_SERVER_ALT = "LLX_SERVER"
ENV_API_KEY = "GUAARDVARK_API_KEY"
ENV_API_KEY_ALT = "LLX_API_KEY"

DEFAULT_CONFIG = {
    "server": "http://localhost:5000",
    "api_key": None,
    "default_output": "table",
    "chat_session_history": 50,
    "timeout": 60,
    "theme": "default",
    "banner": "auto",  # auto | compact | full
    "statusline": ["model", "gpu", "jobs", "git", "cwd"],
}

GUAARDVARK_DIR = Path.home() / ".guaardvark"
CONFIG_DIR = GUAARDVARK_DIR
CONFIG_FILE = GUAARDVARK_DIR / "cli.json"
SESSIONS_FILE = GUAARDVARK_DIR / "sessions.json"
HISTORY_FILE = GUAARDVARK_DIR / "history"

LEGACY_CONFIG_DIR = Path.home() / ".llx"
LEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "config.json"
LEGACY_SESSIONS_FILE = LEGACY_CONFIG_DIR / "sessions.json"
LEGACY_HISTORY_FILE = LEGACY_CONFIG_DIR / "history"


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def load_config() -> dict:
    """Load CLI config. Prefer ~/.guaardvark/cli.json, else ~/.llx/config.json."""
    saved = _read_json(CONFIG_FILE)
    if saved is None:
        saved = _read_json(LEGACY_CONFIG_FILE)
    if saved is None:
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **saved}


def save_config(config: dict):
    """Save config to ~/.guaardvark/cli.json (never writes the legacy path)."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


RUNTIME_FILE = Path.home() / ".guaardvark" / "runtime.json"


def _health_check_port(port: int, timeout: float = 1.5) -> bool:
    """Return True if /api/health responds with status ok on the given port."""
    try:
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=timeout)
        if resp.status_code != 200:
            return False
        return resp.json().get("status") == "ok"
    except Exception:
        return False


def _discover_runtime_server() -> str | None:
    """Auto-discover the running Guaardvark backend from runtime state file."""
    if not RUNTIME_FILE.exists():
        return None
    try:
        with open(RUNTIME_FILE) as f:
            runtime = json.load(f)
        port = runtime.get("backend_port")
        if not port:
            return None
        port = int(port)
        # Prefer a live health probe — stale PIDs are common after restarts.
        if _health_check_port(port):
            return f"http://localhost:{port}"
        pid = runtime.get("backend_pid", 0)
        if pid and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return None
            except PermissionError:
                pass
            return f"http://localhost:{port}"
        return None
    except (json.JSONDecodeError, OSError, KeyError, ValueError, TypeError):
        return None


def get_server_url() -> str:
    """Get the server URL. Resolution order:
    1. GUAARDVARK_SERVER / LLX_SERVER env var
    2. Auto-discovery from ~/.guaardvark/runtime.json (written by start.sh)
    3. FLASK_PORT from repo .env via backend_bootstrap
    4. ~/.guaardvark/cli.json (or legacy ~/.llx/config.json) user config
    """
    url = os.environ.get(ENV_SERVER) or os.environ.get(ENV_SERVER_ALT)
    if url:
        return url.rstrip("/")
    discovered = _discover_runtime_server()
    if discovered:
        return discovered
    try:
        from llx.backend_bootstrap import read_flask_port_from_env, resolve_guaardvark_root

        root = resolve_guaardvark_root()
        env_port = read_flask_port_from_env(root)
        if env_port is not None and _health_check_port(env_port):
            return f"http://localhost:{env_port}"
    except Exception:
        pass
    return load_config()["server"]


def get_timeout() -> float:
    """Get request timeout in seconds from env or config."""
    env_val = os.environ.get("GUAARDVARK_TIMEOUT") or os.environ.get("LLX_TIMEOUT")
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            pass
    return float(load_config().get("timeout", 60))


def get_api_key() -> str | None:
    """Get the API key from env vars or config."""
    key = os.environ.get(ENV_API_KEY) or os.environ.get(ENV_API_KEY_ALT)
    if key:
        return key
    return load_config().get("api_key")


# --- Session persistence ---

def get_frontend_url() -> str:
    """Resolve the web UI origin.

    Order: GUAARDVARK_FRONTEND env, ~/.guaardvark/runtime.json frontend_port,
    VITE_PORT env, then http://localhost:5173.
    """
    explicit = os.environ.get("GUAARDVARK_FRONTEND")
    if explicit:
        return explicit.rstrip("/")
    if RUNTIME_FILE.exists():
        try:
            with open(RUNTIME_FILE) as f:
                runtime = json.load(f)
            port = runtime.get("frontend_port")
            if port:
                return f"http://localhost:{int(port)}"
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    vite = os.environ.get("VITE_PORT")
    if vite:
        try:
            return f"http://localhost:{int(vite)}"
        except ValueError:
            if vite.startswith("http"):
                return vite.rstrip("/")
    return "http://localhost:5173"


def ensure_history_file() -> Path:
    """Return the REPL history path, copying ~/.llx/history once if needed."""
    ensure_config_dir()
    if not HISTORY_FILE.exists() and LEGACY_HISTORY_FILE.exists():
        try:
            HISTORY_FILE.write_text(LEGACY_HISTORY_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    return HISTORY_FILE


def load_sessions() -> list[dict]:
    """Load chat session history. Prefer the new path, else the legacy file."""
    for path in (SESSIONS_FILE, LEGACY_SESSIONS_FILE):
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
    return []


def save_session(session_id: str, preview: str, message_count: int = 1, working_memory: dict | None = None):
    """Save a chat session to history."""
    ensure_config_dir()
    sessions = load_sessions()
    # Find existing entry to preserve the higher message_count
    existing = next((s for s in sessions if s["id"] == session_id), None)
    if existing:
        prev_count = existing.get("message_count", 0)
        message_count = max(message_count, prev_count)
    entry = {
        "id": session_id,
        "preview": preview[:80],
        "timestamp": time.time(),
        "message_count": message_count,
    }
    if working_memory is not None:
        entry["working_memory"] = working_memory
    elif existing and existing.get("working_memory"):
        entry["working_memory"] = existing["working_memory"]

    sessions = [s for s in sessions if s["id"] != session_id]
    sessions.insert(0, entry)
    config = load_config()
    max_history = config.get("chat_session_history", 50)
    sessions = sessions[:max_history]
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)


def get_last_session_id() -> str | None:
    """Get the most recent session ID."""
    sessions = load_sessions()
    return sessions[0]["id"] if sessions else None


def get_recent_session(max_age_seconds: float = 3600.0) -> dict | None:
    """Return the most recent session if its timestamp is within max_age_seconds.

    Returns None if no sessions exist or the most recent is too old.
    """
    sessions = load_sessions()
    if not sessions:
        return None
    latest = sessions[0]
    ts = latest.get("timestamp")
    if ts is None:
        return None
    if (time.time() - ts) > max_age_seconds:
        return None
    return latest


# --- Project scope persistence ---

def get_project_scope() -> dict | None:
    """Read the current project scope from config.

    Returns a dict with 'id' and optional 'name', or None if unset.
    """
    config = load_config()
    scope = config.get("project_scope")
    if not scope or scope.get("id") is None:
        return None
    return scope


def set_project_scope(project_id: int | None, project_name: str | None = None):
    """Set or clear the active project scope in config.

    Pass project_id=None to clear the scope.
    """
    config = load_config()
    if project_id is None:
        config.pop("project_scope", None)
    else:
        config["project_scope"] = {"id": project_id, "name": project_name}
    save_config(config)


# --- Theme persistence ---

def get_theme_name() -> str:
    """Get the saved theme name from config."""
    return load_config().get("theme", "default")


def set_theme_name(name: str):
    """Save the theme name to config."""
    config = load_config()
    config["theme"] = name
    save_config(config)
