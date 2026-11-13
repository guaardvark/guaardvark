"""A plugin's Install step, declared in its plugin.json and run only on request.

    "install": {
      "script": "scripts/setup.sh",              # run with bash from the plugin dir
      "installed_when": ["node_modules/.bin/zg", "cache/models/*"],
      "downloads": "what the script fetches, shown before Install"
    }

The plugin counts as installed when every ``installed_when`` glob matches at
least one file. Nothing here runs at startup; the script runs only from
``start()``, one at a time per plugin, with its output in
``logs/<plugin_id>_install.log``.
"""

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = _REPO_ROOT / "logs"
_lock = threading.Lock()
_runs: Dict[str, dict] = {}


def _plugin_dir(plugin_id: str) -> Optional[Path]:
    # The running manager knows extension plugins too; never build one here,
    # since construction restores and probes every plugin.
    try:
        from backend.plugins import plugin_manager as pm
        d = pm._manager.registry._plugin_dirs.get(plugin_id) if pm._manager is not None else None
        if d:
            return Path(d)
    except Exception:
        pass
    d = _REPO_ROOT / "plugins" / plugin_id
    return d if (d / "plugin.json").is_file() else None


def install_spec(plugin_id: str) -> Optional[dict]:
    """The manifest's ``install`` block, or None when the plugin declares none."""
    d = _plugin_dir(plugin_id)
    if d is None:
        return None
    try:
        spec = json.loads((d / "plugin.json").read_text(encoding="utf-8")).get("install")
    except Exception:
        return None
    if not isinstance(spec, dict) or not spec.get("script"):
        return None
    script = (d / spec["script"]).resolve()
    if not script.is_file() or d.resolve() not in script.parents:
        return None
    return {**spec, "_dir": d, "_script": script}


def is_installed(spec: dict) -> bool:
    d = spec["_dir"]
    globs = spec.get("installed_when") or []
    return bool(globs) and all(any(p.is_file() for p in d.glob(g)) for g in globs)


def _log_path(plugin_id: str) -> Path:
    return _LOG_DIR / f"{plugin_id}_install.log"


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def status(plugin_id: str) -> Optional[dict]:
    """Install state for the UI, or None when the plugin has no Install step."""
    spec = install_spec(plugin_id)
    if spec is None:
        return None
    with _lock:
        run = dict(_runs.get(plugin_id) or {})
    running = run.get("state") == "installing"
    installed = is_installed(spec)
    state = "installing" if running else ("installed" if installed else
                                          ("failed" if run.get("state") == "failed" else "not_installed"))
    return {
        "plugin_id": plugin_id,
        "state": state,
        "installed": installed,
        "downloads": spec.get("downloads", ""),
        "exit_code": run.get("exit_code"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "log_tail": _tail(_log_path(plugin_id)) if run else "",
    }


def start(plugin_id: str) -> tuple[Optional[dict], Optional[str]]:
    """Run the Install script in the background. Returns (status, None) or (None, refusal)."""
    spec = install_spec(plugin_id)
    if spec is None:
        return None, "This plugin has no Install step"
    with _lock:
        if (_runs.get(plugin_id) or {}).get("state") == "installing":
            return None, "Install is already running"
        _runs[plugin_id] = {"state": "installing", "started_at": time.time()}
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    def run():
        code = -1
        try:
            with open(_log_path(plugin_id), "w", encoding="utf-8") as log:
                proc = subprocess.run(["bash", str(spec["_script"])], cwd=str(spec["_dir"]),
                                      stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                code = proc.returncode
        except Exception as e:
            logger.error("Install of %s could not run: %s", plugin_id, e)
        ok = code == 0 and is_installed(spec)
        with _lock:
            _runs[plugin_id].update(state="done" if ok else "failed", exit_code=code, finished_at=time.time())
        logger.info("Install of %s %s (exit %s)", plugin_id, "finished" if ok else "failed", code)

    threading.Thread(target=run, name=f"install-{plugin_id}", daemon=True).start()
    return status(plugin_id), None
