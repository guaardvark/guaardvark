"""
Static dependency healthcheck for the Swarm Orchestrator plugin.

When the sidecar is down the UI otherwise shows a blank "Service Offline"
with no explanation. This module lets the backend report *why* — e.g.
"git not installed" or "no agent CLI (claude/cline) on PATH" — without the
sidecar process even running.

Everything here is PURE STATIC inspection:
  - NO network calls
  - NO GPU / model loads
  - just shutil.which() + import probes + file checks

Runnable as a module so the backend can shell out to it safely:

    python -m plugins.swarm.service.deps_check

It prints a JSON document to stdout.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

# config.yaml lives at plugins/swarm/config.yaml — one level up from service/
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
# The built-in code agent the guaardvark backend runs, in this checkout.
_BUILTIN_AGENT = Path(__file__).resolve().parents[3] / "backend" / "services" / "worktree_agent.py"


def _builtin_agent_present() -> bool:
    return _BUILTIN_AGENT.is_file()


def _which(cmd: str) -> bool:
    """True if `cmd` resolves on PATH."""
    return shutil.which(cmd) is not None


def _importable(module: str) -> bool:
    """True if `module` can be imported in the current interpreter."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _config_readable() -> tuple[bool, str]:
    """Is the plugin's config.yaml present and readable?"""
    try:
        if not _CONFIG_PATH.exists():
            return False, f"config.yaml not found at {_CONFIG_PATH}"
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            f.read(1)
        return True, str(_CONFIG_PATH)
    except OSError as e:
        return False, f"config.yaml unreadable: {e}"


def collect_dependency_status() -> dict[str, Any]:
    """
    Inspect the environment and report what the swarm sidecar needs.

    Returns a dict:
        {
          "dependencies": [ {name, kind, required_for, installed, detail}, ... ],
          "missing": [ <name>, ... ],   # required_for=="launch" deps that are absent
          "ok": bool,                    # True if nothing required-for-launch is missing
        }

    "kind" is "binary", "python", "file", or "builtin".
    """
    checks: list[dict[str, Any]] = []

    # Guaardvark's own code agent: no CLI to install. Whether a local Ollama
    # model is there for it is a live question the running service answers
    # (/swarm/connectivity); this check stays static.
    builtin_ok = _builtin_agent_present()
    checks.append({
        "name": "guaardvark",
        "kind": "builtin",
        "required_for": "launch",
        "installed": builtin_ok,
        "detail": ("built-in code agent (runs on a local Ollama model)" if builtin_ok
                   else "built-in code agent not found in this checkout"),
    })

    # --- binaries ---
    git_ok = _which("git")
    checks.append({
        "name": "git",
        "kind": "binary",
        "required_for": "core",
        "installed": git_ok,
        "detail": "found on PATH" if git_ok else "git not installed — worktrees impossible",
    })

    claude_ok = _which("claude")
    checks.append({
        "name": "claude",
        "kind": "binary",
        "required_for": "launch",
        "installed": claude_ok,
        "detail": "Claude Code CLI found" if claude_ok else "claude CLI not on PATH (online backend)",
    })

    # cline ships under a few names — any of them counts.
    cline_name = next((c for c in ("cline", "openclaw", "cline-cli") if _which(c)), None)
    cline_ok = cline_name is not None
    checks.append({
        "name": "cline",
        "kind": "binary",
        "required_for": "launch",
        "installed": cline_ok,
        "detail": (f"found as '{cline_name}'" if cline_ok
                   else "no cline/openclaw CLI on PATH (offline backend)"),
    })

    # --- python imports (current interpreter) ---
    for module in ("fastapi", "uvicorn", "yaml"):
        ok = _importable(module)
        checks.append({
            "name": module,
            "kind": "python",
            "required_for": "core",
            "installed": ok,
            "detail": f"importable ({module})" if ok else f"{module} not importable in this interpreter",
        })

    # --- config file ---
    cfg_ok, cfg_detail = _config_readable()
    checks.append({
        "name": "config.yaml",
        "kind": "file",
        "required_for": "core",
        "installed": cfg_ok,
        "detail": cfg_detail,
    })

    # An agent is "required for launch", but any one of them suffices: the
    # built-in agent, claude or cline. Track the individual entries above for
    # detail, and compute launch-readiness from the set so a working
    # offline-only setup is not flagged.
    missing: list[str] = []
    for c in checks:
        if c["installed"]:
            continue
        if c["required_for"] == "core":
            missing.append(c["name"])
        # launch deps handled below as a pair

    # at least one agent backend must exist to launch a swarm
    if not builtin_ok and not claude_ok and not cline_ok:
        missing.append("agent-cli")  # no built-in agent, claude or cline

    return {
        "dependencies": checks,
        "missing": missing,
        "ok": len(missing) == 0,
    }


if __name__ == "__main__":
    print(json.dumps(collect_dependency_status(), indent=2))
