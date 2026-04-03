"""Launch configuration for ~/.guaardvark/config.json.

This config file is the contract between Ollama's Go Editor and the
Guaardvark CLI. Ollama writes `model` and `ollama_base_url`.
Guaardvark reads them on launch.
"""
import json
import os
from pathlib import Path

_DEFAULTS = {
    "onboarded": False,
    "mode": "lite",
    "model": None,
    "ollama_base_url": "http://127.0.0.1:11434",
    "server_url": "http://localhost:5000",
    "auto_start_services": True,
    "guaardvark_root": None,
}


def _config_dir() -> Path:
    return Path.home() / ".guaardvark"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def load_launch_config() -> dict:
    """Load config from ~/.guaardvark/config.json, merged with defaults."""
    path = _config_path()
    base = dict(_DEFAULTS)
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            base.update(raw)
        except (json.JSONDecodeError, OSError):
            pass
    return base


def save_launch_config(cfg: dict) -> None:
    """Save config to ~/.guaardvark/config.json, preserving unknown keys."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing.update(cfg)
    path.write_text(json.dumps(existing, indent=2) + "\n")


def is_first_launch() -> bool:
    """True if no config exists or onboarded is False."""
    cfg = load_launch_config()
    return not cfg.get("onboarded", False)


def _normalize_url(url: str) -> str:
    """Ensure URL has a scheme (Ollama's OLLAMA_HOST may omit it)."""
    if url and not url.startswith("http"):
        return f"http://{url}"
    return url


def resolve_ollama_url() -> str:
    """Resolve Ollama URL: OLLAMA_HOST env > config > default."""
    env = os.environ.get("OLLAMA_HOST")
    if env:
        return _normalize_url(env)
    cfg = load_launch_config()
    return _normalize_url(cfg.get("ollama_base_url", _DEFAULTS["ollama_base_url"]))


def resolve_guaardvark_root() -> Path | None:
    """Find the Guaardvark installation root directory."""
    env = os.environ.get("GUAARDVARK_ROOT")
    if env and Path(env).is_dir():
        return Path(env)

    cfg = load_launch_config()
    root = cfg.get("guaardvark_root")
    if root and Path(root).is_dir():
        return Path(root)

    for candidate in [
        Path.home() / "guaardvark",
        Path.home() / "LLAMAX8",
        Path("/opt/guaardvark"),
    ]:
        if (candidate / "start.sh").exists():
            return candidate

    return None
