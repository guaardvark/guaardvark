"""
FastAPI application for the Swarm Orchestrator plugin.

Exposes REST endpoints for launching swarms, checking status,
viewing logs, merging branches, and managing worktrees.
The frontend polls /swarm/status for real-time dashboard updates.
"""

import json
import logging
import os
import secrets
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from service.config import SwarmConfig, load_config, check_internet
from service.deps_check import collect_dependency_status
from service.models import generate_swarm_id
from service.orchestrator import SwarmOrchestrator
from service.plan_parser import parse_plan, predict_conflicts, auto_serialize_conflicts
from service.worktree_manager import WorktreeManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("swarm.app")


def _contained(root, *parts) -> Path:
    """Join ``parts`` under ``root``; raise ValueError if the result leaves root.

    Same contract as backend/utils/path_guard.py (sidecars do not import the
    backend): lexical normalisation, then a prefix check that must end on a
    path boundary.
    """
    root_abs = os.path.abspath(os.fspath(root))
    candidate = os.path.abspath(os.path.join(root_abs, *(os.fspath(p) for p in parts)))
    if candidate.startswith(root_abs) and (
        candidate == root_abs
        or root_abs.endswith(os.sep)
        or candidate[len(root_abs)] == os.sep
    ):
        return Path(candidate)
    raise ValueError(f"{candidate!r} is outside {root_abs!r}")

# --- Globals ---
_config: Optional[SwarmConfig] = None
_active_orchestrators: dict[str, SwarmOrchestrator] = {}  # swarm_id -> orchestrator
_lock = threading.Lock()
_internal_secret: Optional[str] = None


def _load_internal_secret() -> str:
    """Resolve the shared internal token.

    Priority: env SWARM_INTERNAL_SECRET, then data/.swarm_internal_secret
    (read if present, generated + persisted otherwise). The Flask proxy reads
    the same file/env so the two sides stay in sync.
    """
    env_secret = os.environ.get("SWARM_INTERNAL_SECRET")
    if env_secret:
        return env_secret.strip()

    root = os.environ.get("GUAARDVARK_ROOT")
    base = Path(root) if root else Path(__file__).resolve().parents[3]
    secret_file = base / "data" / ".swarm_internal_secret"
    try:
        if secret_file.exists():
            existing = secret_file.read_text().strip()
            if existing:
                return existing
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(32)
        secret_file.write_text(generated)
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
        return generated
    except Exception as e:
        logger.warning(f"Could not read/write internal secret file ({e}); generating ephemeral secret")
        return secrets.token_urlsafe(32)


# --- Pydantic request models ---

class LaunchRequest(BaseModel):
    plan_path: str
    repo_path: Optional[str] = None
    flight_mode: Optional[bool] = None
    max_agents: Optional[int] = None
    auto_merge: Optional[bool] = None
    dry_run: bool = False
    self_code: bool = False
    acknowledge_dirty_tree: bool = False


class MergeRequest(BaseModel):
    swarm_id: str
    repo_path: Optional[str] = None


class CleanupRequest(BaseModel):
    swarm_id: Optional[str] = None
    repo_path: Optional[str] = None
    delete_branches: bool = False
    all: bool = False


class CancelRequest(BaseModel):
    swarm_id: str


class SavePlanRequest(BaseModel):
    filename: str
    content: str


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app):
    global _config, _internal_secret
    _config = load_config()
    _internal_secret = _load_internal_secret()
    logger.info(f"Swarm service started — {len(_config.backends)} backends configured")
    yield
    # shutdown: cancel any running swarms
    with _lock:
        for sid, orch in _active_orchestrators.items():
            if orch.is_running():
                logger.info(f"Shutting down running swarm {sid}")
                orch.cancel()
        _active_orchestrators.clear()
    logger.info("Swarm service stopped")


# --- App ---

app = FastAPI(title="Swarm Orchestrator", version="0.1.0", lifespan=lifespan)

# Restrict CORS to the local frontend origin. The sidecar is loopback-bound and
# reached via the Flask proxy; the browser never talks to :8210 with credentials.
_cors_origin = os.environ.get("SWARM_CORS_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_cors_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


INTERNAL_TOKEN_HEADER = "X-Swarm-Internal-Token"


@app.middleware("http")
async def _require_internal_token(request: Request, call_next):
    """Shared-secret gate on every route except /health.

    The Flask proxy attaches X-Swarm-Internal-Token on every proxied call. A
    direct hit on :8210 without the matching token is rejected with 403, so the
    sidecar can no longer be driven around the Flask guard.
    """
    # /health stays open for liveness probes (start.sh curls it).
    if request.url.path == "/health":
        return await call_next(request)
    # CORS preflight carries no custom headers — let it through; the actual
    # request still has to present the token.
    if request.method == "OPTIONS":
        return await call_next(request)

    expected = _internal_secret or _load_internal_secret()
    provided = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    if not expected or not secrets.compare_digest(provided, expected):
        return JSONResponse(status_code=403, content={"detail": "Forbidden: invalid internal token"})
    return await call_next(request)


# --- Health ---

@app.get("/health")
def health():
    running = sum(1 for o in _active_orchestrators.values() if o.is_running())
    online = check_internet(
        _config.offline_ping_target if _config else "",
        _config.offline_ping_timeout if _config else 2,
    )

    # Static dependency inspection — tells the UI *why* something is unavailable
    # (missing git / agent CLI / python deps) instead of a blank offline state.
    try:
        deps = collect_dependency_status()
    except Exception as e:  # never let a healthcheck crash the health endpoint
        logger.debug(f"Dependency check failed: {e}")
        deps = {"dependencies": [], "missing": [], "ok": True}

    # degrade if a launch-blocking dependency is missing, but the service itself
    # is up and answering, so it's "degraded", not "unhealthy".
    status = "healthy" if deps.get("ok", True) else "degraded"

    return {
        "status": status,
        "active_swarms": running,
        "total_swarms": len(_active_orchestrators),
        "online": online,
        "backends": list(_config.backends.keys()) if _config else [],
        "dependencies": deps.get("dependencies", []),
        "missing": deps.get("missing", []),
    }


# --- Launch ---

@app.post("/swarm/launch")
def launch_swarm(req: LaunchRequest):
    """Launch a swarm from a plan file. Runs async in background."""
    # Plan files live inside the Guaardvark root; relative paths resolve from it.
    root = os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    try:
        plan_path = _contained(root, req.plan_path)
    except ValueError:
        raise HTTPException(400, f"plan_path must be inside {root}")

    if not plan_path.exists():
        raise HTTPException(404, f"Plan file not found: {plan_path}")

    repo_path = Path(req.repo_path).resolve() if req.repo_path else _get_default_repo()
    if not repo_path or not (repo_path / ".git").exists():
        raise HTTPException(400, f"Not a git repository: {repo_path}")

    # --- Launch guard parity with the Flask proxy (swarm_api.py:~94-112) ---
    # Direct :8210 access must not bypass the self-code guard. Treat a request
    # that targets GUAARDVARK_ROOT (or sets self_code) as a self-code swarm:
    # force auto_merge=False and require a clean tree unless acknowledged.
    guard_root = _guaardvark_root()
    is_targeting_self = req.self_code or (guard_root is not None and repo_path == guard_root)

    auto_merge = req.auto_merge
    if is_targeting_self:
        auto_merge = False  # never auto-merge into the live repo
        try:
            status = subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            dirty = bool(status.stdout.strip())
        except Exception as e:
            logger.debug(f"Git status check failed: {e}")
            dirty = False
        if dirty and not req.acknowledge_dirty_tree:
            raise HTTPException(
                409,
                "Repository has uncommitted changes; acknowledge_dirty_tree is required for self-code swarms",
            )
    else:
        # Non-self-code target: log a dirty tree but don't block.
        try:
            status = subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            if status.stdout.strip():
                logger.warning(f"Launching swarm on dirty repository: {repo_path}")
        except Exception as e:
            logger.debug(f"Git status check failed: {e}")

    # dry run — parse and return without launching
    if req.dry_run:
        tasks = parse_plan(plan_path)
        warnings = predict_conflicts(tasks)
        return {
            "success": True,
            "dry_run": True,
            "tasks": [t.to_dict() for t in tasks],
            "warnings": [w.to_dict() for w in warnings],
            "task_count": len(tasks),
            "warning_count": len(warnings),
        }

    orch = SwarmOrchestrator(repo_path, _config)

    try:
        swarm_id = orch.launch_async(
            plan_path,
            flight_mode=req.flight_mode,
            max_agents=req.max_agents,
            auto_merge=auto_merge,
        )
    except ValueError as e:
        # plan parse failed — don't register a broken swarm
        raise HTTPException(400, str(e))

    with _lock:
        _active_orchestrators[swarm_id] = orch

    logger.info(f"Launched swarm {swarm_id} from {plan_path}")
    return {
        "success": True,
        "swarm_id": swarm_id,
        "message": f"Swarm launched: {swarm_id}",
    }


# --- Status ---

@app.get("/swarm/status")
def all_swarm_status():
    """Status of all tracked swarms — the dashboard polls this."""
    swarms = []
    with _lock:
        for sid, orch in _active_orchestrators.items():
            swarms.append(orch.get_status())

    # also check for completed swarms on disk that we're not tracking
    return {
        "success": True,
        "swarms": swarms,
        "count": len(swarms),
    }


@app.get("/swarm/status/{swarm_id}")
def swarm_status(swarm_id: str):
    """Detailed status for a specific swarm."""
    with _lock:
        orch = _active_orchestrators.get(swarm_id)

    if orch:
        return {"success": True, "data": orch.get_status()}

    # check disk for completed swarm results
    repo_path = _get_default_repo()
    if repo_path:
        worktree_root = repo_path / (_config.worktree_base if _config else ".swarm-worktrees")
        try:
            result_path = _contained(worktree_root, swarm_id, "result.json")
        except ValueError:
            raise HTTPException(400, "invalid swarm_id")
        if result_path.exists():
            with open(result_path) as f:
                data = json.load(f)
            return {"success": True, "data": data}

    raise HTTPException(404, f"Swarm not found: {swarm_id}")


# --- Logs ---

@app.get("/swarm/{swarm_id}/logs/{task_id}")
def task_logs(swarm_id: str, task_id: str, lines: int = 50):
    """Get logs for a specific agent in a swarm.

    While the orchestrator is in memory we read from it directly. Once a
    swarm completes and the orchestrator is evicted, we fall back to tailing
    the on-disk agent log inside the task's worktree (same recovery pattern
    as the diff endpoint).
    """
    lines = min(lines, 500)

    with _lock:
        orch = _active_orchestrators.get(swarm_id)

    if orch:
        logs = orch.get_task_logs(task_id, lines=lines)
        return {"success": True, "logs": logs, "task_id": task_id}

    # No active orchestrator — try the worktree on disk.
    repo_path = _get_default_repo()
    if repo_path:
        worktree_base = _config.worktree_base if _config else ".swarm-worktrees"
        mgr = WorktreeManager.load_existing(repo_path, swarm_id, worktree_base)
        if mgr:
            info = mgr.manifest.worktrees.get(task_id)
            if info and info.worktree_path and os.path.exists(info.worktree_path):
                logs = _read_worktree_log(info.worktree_path, lines)
                return {"success": True, "logs": logs, "task_id": task_id}

    raise HTTPException(404, f"Swarm not found or worktree missing: {swarm_id}")


@app.get("/swarm/{swarm_id}/diff/{task_id}")
def task_diff(swarm_id: str, task_id: str):
    """Get the current git diff for a specific agent in a swarm."""
    with _lock:
        orch = _active_orchestrators.get(swarm_id)

    if not orch:
        # check if it's a finished swarm, we might still have the worktrees
        repo_path = _get_default_repo()
        if repo_path:
            worktree_base = _config.worktree_base if _config else ".swarm-worktrees"
            mgr = WorktreeManager.load_existing(repo_path, swarm_id, worktree_base)
            if mgr:
                info = mgr.manifest.worktrees.get(task_id)
                if info and info.worktree_path and os.path.exists(info.worktree_path):
                    import subprocess
                    try:
                        res = subprocess.run(
                            ["git", "diff", "HEAD"],
                            cwd=info.worktree_path,
                            capture_output=True, text=True, timeout=5
                        )
                        return {"success": True, "diff": res.stdout or "(no changes)", "task_id": task_id}
                    except Exception as e:
                        return {"success": False, "error": str(e)}

        raise HTTPException(404, f"Swarm not found or worktree missing: {swarm_id}")

    diff = orch.get_task_diff(task_id)
    return {"success": True, "diff": diff, "task_id": task_id}


# --- Communication Bus ---

@app.get("/swarm/{swarm_id}/bus/state")
def get_bus_state(swarm_id: str):
    """Get shared state for the swarm."""
    with _lock:
        orch = _active_orchestrators.get(swarm_id)
    if not orch:
        raise HTTPException(404, f"Swarm not found: {swarm_id}")
    return {"success": True, "state": orch.comm_bus.get_state(swarm_id)}


@app.post("/swarm/{swarm_id}/bus/broadcast")
def broadcast_event(swarm_id: str, req: dict):
    """Broadcast an event to sibling agents."""
    with _lock:
        orch = _active_orchestrators.get(swarm_id)
    if not orch:
        raise HTTPException(404, f"Swarm not found: {swarm_id}")
    
    orch.comm_bus.broadcast(
        swarm_id, 
        req.get("sender", "agent"), 
        req.get("event_type", "message"), 
        req.get("data", {})
    )
    return {"success": True}


@app.post("/swarm/{swarm_id}/bus/state")
def update_bus_state(swarm_id: str, req: dict):
    """Update a shared state key."""
    with _lock:
        orch = _active_orchestrators.get(swarm_id)
    if not orch:
        raise HTTPException(404, f"Swarm not found: {swarm_id}")
    
    orch.comm_bus.update_state(swarm_id, req.get("key"), req.get("value"))
    return {"success": True}


# --- Cancel ---

@app.post("/swarm/cancel")
def cancel_swarm(req: CancelRequest):
    """Cancel a running swarm."""
    with _lock:
        orch = _active_orchestrators.get(req.swarm_id)

    if not orch:
        raise HTTPException(404, f"Swarm not found: {req.swarm_id}")

    if not orch.is_running():
        return {"success": True, "message": "Swarm already completed"}

    orch.cancel()
    return {"success": True, "message": f"Swarm {req.swarm_id} cancelled"}


# --- Merge ---

@app.post("/swarm/merge")
def merge_swarm(req: MergeRequest):
    """Trigger merge for a completed swarm's branches."""
    with _lock:
        orch = _active_orchestrators.get(req.swarm_id)

    if not orch:
        raise HTTPException(404, f"Swarm not found: {req.swarm_id}")

    if orch.is_running():
        raise HTTPException(400, "Cannot merge while swarm is still running")

    if not orch.merge_mgr or not orch.result:
        raise HTTPException(400, "Swarm has no merge manager or results")

    from service.merge_manager import MergeManager

    merge_queue = orch.merge_mgr.merge_queue(orch.result.tasks)
    results = {}
    for task in merge_queue:
        mr = orch.merge_mgr.attempt_merge(
            task,
            run_tests=_config.run_tests_before_merge if _config else False,
            test_command=_config.test_command if _config else "python3 -m pytest",
        )
        results[task.id] = mr.to_dict()
        orch.result.merge_results[task.id] = mr

    return {
        "success": True,
        "merged": sum(1 for r in results.values() if r["success"]),
        "conflicts": sum(1 for r in results.values() if not r["success"]),
        "results": results,
    }


# --- Cleanup ---

@app.post("/swarm/cleanup")
def cleanup_swarm(req: CleanupRequest):
    """Remove worktrees and branches for a swarm."""
    repo_path = Path(req.repo_path) if req.repo_path else _get_default_repo()
    if not repo_path:
        raise HTTPException(400, "No repo path available")

    worktree_base = _config.worktree_base if _config else ".swarm-worktrees"

    if req.all:
        import shutil
        import subprocess
        swarm_base = repo_path / worktree_base
        if swarm_base.exists():
            subprocess.run(["git", "-C", str(repo_path), "worktree", "prune"], capture_output=True)
            shutil.rmtree(swarm_base)
            # also remove from active tracking
            with _lock:
                _active_orchestrators.clear()
            return {"success": True, "message": "All swarm worktrees cleaned up"}
        return {"success": True, "message": "Nothing to clean up"}

    if not req.swarm_id:
        raise HTTPException(400, "Specify swarm_id or use all=true")

    mgr = WorktreeManager.load_existing(repo_path, req.swarm_id, worktree_base)
    if not mgr:
        raise HTTPException(404, f"Swarm not found: {req.swarm_id}")

    count = mgr.cleanup_all(delete_branches=req.delete_branches)

    with _lock:
        _active_orchestrators.pop(req.swarm_id, None)

    return {
        "success": True,
        "message": f"Cleaned up {count} worktrees for {req.swarm_id}",
        "worktrees_removed": count,
    }


# --- Templates ---

@app.get("/swarm/templates")
def list_templates():
    """List available swarm plan templates."""
    template_dir = Path(__file__).parent.parent / "templates"
    if not template_dir.exists():
        return {"success": True, "templates": []}

    templates = []
    for f in sorted(template_dir.glob("*.md")):
        lines = f.read_text().split("\n")
        title = lines[0].lstrip("# ").strip() if lines else f.stem
        desc = ""
        for line in lines[1:]:
            line = line.strip()
            if line and not line.startswith("#"):
                desc = line
                break

        # count tasks by counting ## headings
        task_count = sum(1 for line in lines if line.startswith("## ") and not line.startswith("### "))

        templates.append({
            "filename": f.name,
            "path": str(f),
            "title": title,
            "description": desc,
            "task_count": task_count,
        })

    return {"success": True, "templates": templates, "count": len(templates)}


@app.get("/swarm/templates/{filename}")
def get_template(filename: str):
    """Get the raw content of a template."""
    template_dir = Path(__file__).parent.parent / "templates"
    try:
        template_path = _contained(template_dir, filename)
    except ValueError:
        raise HTTPException(404, f"Template not found: {filename}")

    if not template_path.exists() or not template_path.suffix == ".md":
        raise HTTPException(404, f"Template not found: {filename}")

    return {
        "success": True,
        "filename": filename,
        "content": template_path.read_text(),
    }


@app.post("/swarm/templates/save")
def save_template(req: SavePlanRequest):
    """Save a new or existing plan template."""
    template_dir = Path(__file__).parent.parent / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    
    filename = req.filename
    if not filename.endswith(".md"):
        filename += ".md"
        
    # sanitize filename
    import re
    filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    
    try:
        template_path = _contained(template_dir, filename)
    except ValueError:
        raise HTTPException(400, f"invalid template filename: {filename}")
    
    try:
        template_path.write_text(req.content)
        return {"success": True, "message": f"Template saved: {filename}", "filename": filename}
    except Exception as e:
        raise HTTPException(500, f"Failed to save template: {e}")


# --- Connectivity ---

@app.get("/swarm/connectivity")
def check_connectivity():
    """Check internet connectivity — UI shows flight mode toggle based on this."""
    online = check_internet(
        _config.offline_ping_target if _config else "",
        _config.offline_ping_timeout if _config else 2,
    )
    available_backends = []
    if _config:
        for name, bc in _config.backends.items():
            available_backends.append({
                "name": name,
                "available": not bc.requires_internet or online,
                "requires_internet": bc.requires_internet,
                "priority": bc.priority,
            })

    return {
        "success": True,
        "online": online,
        "flight_mode": _config.flight_mode if _config else False,
        "backends": available_backends,
    }


# --- History ---

@app.get("/swarm/history")
def swarm_history(limit: int = 20):
    """List past swarm runs from saved results on disk."""
    repo_path = _get_default_repo()
    if not repo_path:
        return {"success": True, "swarms": [], "count": 0}

    worktree_base = _config.worktree_base if _config else ".swarm-worktrees"
    swarm_base = repo_path / worktree_base

    if not swarm_base.exists():
        return {"success": True, "swarms": [], "count": 0}

    swarms = []
    for d in sorted(swarm_base.iterdir(), key=lambda x: x.name, reverse=True):
        if not d.is_dir():
            continue
        result_file = d / "result.json"
        if result_file.exists():
            with open(result_file) as f:
                data = json.load(f)
            swarms.append({
                "swarm_id": data.get("swarm_id", d.name),
                "plan_path": data.get("plan_path", ""),
                "summary": data.get("summary", ""),
                "flight_mode": data.get("flight_mode", False),
                "total_cost_usd": data.get("total_cost_usd", 0),
                "total_tokens": data.get("total_tokens", 0),
                "started_at": data.get("started_at"),
                "completed_at": data.get("completed_at"),
                "task_count": len(data.get("tasks", [])),
            })
        if len(swarms) >= limit:
            break

    return {"success": True, "swarms": swarms, "count": len(swarms)}


# --- Helpers ---

# Agents write their log here, inside their worktree (see agent_backends/*).
_AGENT_LOG_FILE = ".swarm-agent.log"


def _read_worktree_log(worktree_path: str, lines: int) -> str:
    """Tail the agent log file inside a worktree.

    Returns the last `lines` lines (decode errors replaced), or a clear
    placeholder if the log file doesn't exist yet.
    """
    log_path = Path(worktree_path) / _AGENT_LOG_FILE
    if not log_path.exists():
        return "(no log file)"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(could not read log file: {e})"
    all_lines = text.split("\n")
    return "\n".join(all_lines[-lines:])


def _guaardvark_root() -> Optional[Path]:
    """Resolve the configured Guaardvark repository root, if any."""
    root = os.environ.get("GUAARDVARK_ROOT")
    if root:
        try:
            return Path(root).expanduser().resolve()
        except Exception:
            return None
    return None


def _get_default_repo() -> Optional[Path]:
    """Get the default repo path from GUAARDVARK_ROOT or cwd."""
    root = os.environ.get("GUAARDVARK_ROOT")
    if root:
        p = Path(root)
        if (p / ".git").exists():
            return p.resolve()
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return cwd.resolve()
    return None
