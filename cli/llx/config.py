"""LLX CLI configuration — loads/saves ~/.llx/config.json."""

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
            return {**DEFAULT_CONFIG, **saved}
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(config: dict):
    """Save config to ~/.llx/config.json."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


RUNTIME_FILE = Path.home() / ".guaardvark" / "runtime.json"


def _discover_runtime_server() -> str | None:
    """Auto-discover the running Guaardvark backend from runtime state file."""
    if not RUNTIME_FILE.exists():
        return None
    try:
        with open(RUNTIME_FILE) as f:
            runtime = json.load(f)
        port = runtime.get("backend_port")
        pid = runtime.get("backend_pid", 0)
        if not port:
            return None
        # Verify the process is actually running
        if pid and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return None
            except PermissionError:
                pass  # Process exists but we can't signal it — that's fine
        return f"http://localhost:{port}"
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def get_server_url() -> str:
    """Get the server URL. Resolution order:
    1. GUAARDVARK_SERVER / LLX_SERVER env var
    2. Auto-discovery from ~/.guaardvark/runtime.json (written by start.sh)
    3. ~/.llx/config.json user config
    """
    url = os.environ.get(ENV_SERVER) or os.environ.get(ENV_SERVER_ALT)
    if url:
        return url.rstrip("/")
    discovered = _discover_runtime_server()
    if discovered:
        return discovered
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


def load_sessions() -> list[dict]:
    """Load chat session history."""
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_session(session_id: str, preview: str, message_count: int = 1):
    """Save a chat session to history."""
    ensure_config_dir()
    sessions = load_sessions()
    # Find existing entry to preserve the higher message_count
    existing = next((s for s in sessions if s["id"] == session_id), None)
    if existing:
        prev_count = existing.get("message_count", 0)
        message_count = max(message_count, prev_count)
    sessions = [s for s in sessions if s["id"] != session_id]
    sessions.insert(
        0,
        {
            "id": session_id,
            "preview": preview[:80],
            "timestamp": time.time(),
            "message_count": message_count,
        },
    )
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
