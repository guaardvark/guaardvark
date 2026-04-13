"""
FastAPI application for the Swarm Orchestrator plugin.

Exposes REST endpoints for launching swarms, checking status,
viewing logs, merging branches, and managing worktrees.
The frontend polls /swarm/status for real-time dashboard updates.
"""

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from service.config import SwarmConfig, load_config, check_internet
from service.models import generate_swarm_id
from service.orchestrator import SwarmOrchestrator
from service.plan_parser import parse_plan, predict_conflicts, auto_serialize_conflicts
from service.worktree_manager import WorktreeManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("swarm.app")

# --- Globals ---
_config: Optional[SwarmConfig] = None
_active_orchestrators: dict[str, SwarmOrchestrator] = {}  # swarm_id -> orchestrator
_lock = threading.Lock()


# --- Pydantic request models ---

class LaunchRequest(BaseModel):
    plan_path: str
    repo_path: Optional[str] = None
    flight_mode: Optional[bool] = None
    max_agents: Optional[int] = None
    auto_merge: Optional[bool] = None
    dry_run: bool = False


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
    global _config
    _config = load_config()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health ---

@app.get("/health")
def health():
    running = sum(1 for o in _active_orchestrators.values() if o.is_running())
    online = check_internet(
        _config.offline_ping_target if _config else "api.anthropic.com",
        _config.offline_ping_timeout if _config else 2,
    )
    return {
        "status": "healthy",
        "active_swarms": running,
        "total_swarms": len(_active_orchestrators),
        "online": online,
        "backends": list(_config.backends.keys()) if _config else [],
    }


# --- Launch ---

@app.post("/swarm/launch")
def launch_swarm(req: LaunchRequest):
    """Launch a swarm from a plan file. Runs async in background."""
    plan_path = Path(req.plan_path)
    if not plan_path.is_absolute():
        # try relative to GUAARDVARK_ROOT
        root = os.environ.get("GUAARDVARK_ROOT", "")
        if root:
            plan_path = Path(root) / plan_path

    if not plan_path.exists():
        raise HTTPException(404, f"Plan file not found: {plan_path}")

    repo_path = Path(req.repo_path) if req.repo_path else _get_default_repo()
    if not repo_path or not (repo_path / ".git").exists():
        raise HTTPException(400, f"Not a git repository: {repo_path}")

    # Pre-flight check: is the repo clean?
    import subprocess
    try:
        status = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5
        )
        if status.stdout.strip():
            logger.warning(f"Launching swarm on dirty repository: {repo_path}")
            # we don't block launch, but we log it. In a stricter setup we might return 400.
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
            auto_merge=req.auto_merge,
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
        result_path = repo_path / (_config.worktree_base if _config else ".swarm-worktrees") / swarm_id / "result.json"
        if result_path.exists():
            with open(result_path) as f:
                data = json.load(f)
            return {"success": True, "data": data}

    raise HTTPException(404, f"Swarm not found: {swarm_id}")


# --- Logs ---

@app.get("/swarm/{swarm_id}/logs/{task_id}")
def task_logs(swarm_id: str, task_id: str, lines: int = 50):
    """Get logs for a specific agent in a swarm."""
    with _lock:
        orch = _active_orchestrators.get(swarm_id)

    if not orch:
        raise HTTPException(404, f"Swarm not found or not active: {swarm_id}")

    logs = orch.get_task_logs(task_id, lines=min(lines, 500))
    return {"success": True, "logs": logs, "task_id": task_id}


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
    template_path = template_dir / filename

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
    
    template_path = template_dir / filename
    
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
        _config.offline_ping_target if _config else "api.anthropic.com",
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

def _get_default_repo() -> Optional[Path]:
    """Get the default repo path from GUAARDVARK_ROOT or cwd."""
    root = os.environ.get("GUAARDVARK_ROOT")
    if root:
        p = Path(root)
        if (p / ".git").exists():
            return p
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return cwd
    return None
