#!/usr/bin/env python3
"""Chat-callable workstation tools — the same services the pages use.

These are not sketches. Each execute() calls the live mapper / GPU / log /
swarm / self-improvement modules. If a sidecar is down or the codebase lock
is on, the tool returns that fact instead of a canned "I would…".
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

_LOG_ALLOWLIST = frozenset({
    "backend.log",
    "celery_main.log",
    "celery_training.log",
    "frontend.log",
    "setup.log",
    "xfce_agent.log",
    "x11vnc_agent.log",
    "ollama.log",
    "comfyui.log",
    "audio_foundry.log",
    "swarm.log",
    "video_editor.log",
    "upscaling.log",
    "vision_pipeline.log",
    "discord.log",
})


def _repo_root() -> Path:
    from backend.config import GUAARDVARK_ROOT
    return Path(GUAARDVARK_ROOT).resolve()


def _log_dir() -> Path:
    from backend.config import LOG_DIR
    return Path(LOG_DIR)


def _safe_root(root_arg: Optional[str]) -> Path:
    """Default to GUAARDVARK_ROOT. Other paths must stay inside that tree."""
    base = _repo_root()
    if not root_arg:
        return base
    candidate = Path(root_arg).expanduser().resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"root must be inside the Guaardvark tree ({base})") from exc
    if not candidate.is_dir():
        raise ValueError(f"Not a directory: {candidate}")
    return candidate


def _load_snapshot(root: Path, refresh: bool) -> dict:
    from backend.api.system_map_api import _load_or_compute

    payload, err = _load_or_compute(root, refresh)
    if err:
        # err is (jsonify_response, status) — unwrap for the tool
        raise RuntimeError(f"system map failed for {root}")
    return payload


def _nvidia_smi() -> Dict[str, Any]:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi not on PATH"}
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if out.returncode != 0:
        return {"available": False, "error": (out.stderr or out.stdout or "nvidia-smi failed")[:300]}
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        gpus.append({
            "index": parts[0],
            "name": parts[1],
            "memory_used_mb": parts[2],
            "memory_total_mb": parts[3],
            "utilization_pct": parts[4],
            "temp_c": parts[5],
        })
    return {"available": True, "gpus": gpus}


class MapCodebaseTool(BaseTool):
    name = "map_codebase"
    description = (
        "Run the System Mapper (same snapshot as /system-map) and return stats plus "
        "ranked findings. Use when the user says 'use the system mapper', 'map the "
        "codebase', 'what's wrong with this repo', or 'constellation findings'."
    )
    parameters = {
        "refresh": ToolParameter(
            name="refresh", type="bool", required=False, default=False,
            description="Ignore the 5-minute disk cache and recompute.",
        ),
        "root": ToolParameter(
            name="root", type="string", required=False, default="",
            description="Code root. Defaults to GUAARDVARK_ROOT. Must stay inside that tree.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=15,
            description="Max findings to return (default 15).",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        try:
            root = _safe_root(kwargs.get("root") or None)
            refresh = bool(kwargs.get("refresh", False))
            limit = int(kwargs.get("limit") or 15)
            snapshot = _load_snapshot(root, refresh)
            from backend.services.system_mapper.actions import ranked_findings

            findings = ranked_findings(snapshot, root)
            slim = []
            for f in findings[: max(1, min(limit, 40))]:
                slim.append({
                    "id": f.get("id"),
                    "kind": f.get("kind"),
                    "severity": f.get("severity"),
                    "summary": f.get("summary"),
                    "paths": (f.get("paths") or [])[:6],
                    "dispatchable": bool(f.get("dispatchable")),
                })
            payload = {
                "root": str(root),
                "file_count": snapshot.get("file_count"),
                "languages": snapshot.get("languages"),
                "stats": snapshot.get("stats"),
                "finding_count": len(findings),
                "findings": slim,
                "cache": snapshot.get("_cache"),
                "hint": (
                    "Call dispatch_map_finding with a finding id to hand a "
                    "dispatchable finding to self-improvement (PendingFix)."
                ),
            }
            return ToolResult(success=True, output=payload, metadata={"root": str(root)})
        except Exception as e:
            logger.exception("map_codebase failed")
            return ToolResult(success=False, error=str(e))


class DispatchMapFindingTool(BaseTool):
    name = "dispatch_map_finding"
    description = (
        "Hand a System Mapper finding to the self-improvement engine. Creates a "
        "real directed run / PendingFix — same path as POST /api/system-map/findings/<id>/dispatch. "
        "Use after map_codebase when the user wants a finding actually fixed."
    )
    requires_approval = True
    parameters = {
        "finding_id": ToolParameter(
            name="finding_id", type="string", required=True,
            description="Finding id from map_codebase (fingerprint).",
        ),
        "root": ToolParameter(
            name="root", type="string", required=False, default="",
            description="Same root used for map_codebase. Defaults to GUAARDVARK_ROOT.",
        ),
        "priority": ToolParameter(
            name="priority", type="string", required=False, default="medium",
            description="low | medium | high",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        finding_id = (kwargs.get("finding_id") or "").strip()
        if not finding_id:
            return ToolResult(success=False, error="finding_id is required")
        try:
            root = _safe_root(kwargs.get("root") or None)
            snapshot = _load_snapshot(root, refresh=False)
            from backend.services.system_mapper.actions import (
                DISPATCHABLE_KINDS,
                dispatch_finding,
                find_finding,
            )
            from backend.services.self_improvement_service import get_self_improvement_service

            finding = find_finding(snapshot, finding_id)
            if not finding:
                return ToolResult(success=False, error=f"No finding {finding_id} in the cached map. Run map_codebase first.")
            if finding.get("kind") not in DISPATCHABLE_KINDS:
                return ToolResult(
                    success=False,
                    error=(
                        f"Finding {finding_id} kind={finding.get('kind')} is not dispatchable "
                        f"(advisory only). Dispatchable kinds: {sorted(DISPATCHABLE_KINDS)}"
                    ),
                    metadata={"finding": finding},
                )
            pre = get_self_improvement_service().dispatch_precheck()
            if not pre.get("ok"):
                return ToolResult(success=False, error=pre.get("reason") or "self-improvement cannot run", metadata=pre)
            result = dispatch_finding(finding, priority=str(kwargs.get("priority") or "medium"))
            return ToolResult(success=bool(result.get("success")), output=result, error=result.get("reason"))
        except Exception as e:
            logger.exception("dispatch_map_finding failed")
            return ToolResult(success=False, error=str(e))


class InspectGpuTool(BaseTool):
    name = "inspect_gpu"
    description = (
        "Inspect live GPU state: nvidia-smi, the exclusive lock (Ollama vs video), "
        "orchestrator model slots, and which plugins are running. Use when the user "
        "says 'debug GPU issues', 'what's using VRAM', 'GPU status', or 'OOM'."
    )
    parameters: Dict[str, ToolParameter] = {}

    def execute(self, **kwargs) -> ToolResult:
        payload: Dict[str, Any] = {"nvidia": _nvidia_smi()}
        try:
            from backend.services.gpu_resource_coordinator import get_gpu_coordinator
            payload["lock"] = get_gpu_coordinator().get_gpu_status()
        except Exception as e:
            payload["lock"] = {"error": str(e)}
        try:
            from backend.services.gpu_memory_orchestrator import get_orchestrator
            payload["orchestrator"] = get_orchestrator().get_registry_snapshot()
        except Exception as e:
            payload["orchestrator"] = {"error": str(e)}
        try:
            from backend.plugins.plugin_manager import get_plugin_manager
            plugins = get_plugin_manager().list_plugins()
            payload["plugins"] = [
                {
                    "id": p.get("id"),
                    "running": p.get("running"),
                    "status": p.get("status"),
                    "port": p.get("port"),
                    "vram_estimate_mb": p.get("vram_estimate_mb"),
                }
                for p in plugins
            ]
        except Exception as e:
            payload["plugins"] = {"error": str(e)}
        return ToolResult(success=True, output=payload)


class ReadLogsTool(BaseTool):
    name = "read_logs"
    description = (
        "Tail a Guaardvark log file under logs/. Use when the user says 'review the "
        "logs', 'check backend.log', 'celery errors', or 'what did the last crash say'."
    )
    parameters = {
        "name": ToolParameter(
            name="name", type="string", required=False, default="backend.log",
            description="Log filename (not a path). Default backend.log.",
        ),
        "lines": ToolParameter(
            name="lines", type="int", required=False, default=80,
            description="How many trailing lines (10-400).",
        ),
        "query": ToolParameter(
            name="query", type="string", required=False, default="",
            description="Optional case-insensitive substring filter.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        raw_name = os.path.basename(str(kwargs.get("name") or "backend.log").strip() or "backend.log")
        if raw_name not in _LOG_ALLOWLIST:
            return ToolResult(
                success=False,
                error=f"Unknown log '{raw_name}'. Allowed: {sorted(_LOG_ALLOWLIST)}",
            )
        try:
            path = _log_dir() / raw_name
            if not path.is_file():
                return ToolResult(success=False, error=f"Log file not found: {path}")
            n = max(10, min(int(kwargs.get("lines") or 80), 400))
            text = path.read_text(encoding="utf-8", errors="replace")
            rows = text.splitlines()
            query = (kwargs.get("query") or "").strip().lower()
            if query:
                rows = [ln for ln in rows if query in ln.lower()]
            tail = rows[-n:]
            return ToolResult(
                success=True,
                output={
                    "path": str(path),
                    "matched_lines": len(rows),
                    "returned_lines": len(tail),
                    "query": query or None,
                    "text": "\n".join(tail),
                },
            )
        except Exception as e:
            logger.exception("read_logs failed")
            return ToolResult(success=False, error=str(e))


class SwarmStatusTool(BaseTool):
    name = "swarm_status"
    description = (
        "Get Swarm Orchestrator status (same as GET /api/swarm/status). "
        "Use when the user asks about the coding swarm, worktrees, or running swarm tasks. "
        "Returns an honest offline error if the swarm plugin is not running."
    )
    parameters = {
        "swarm_id": ToolParameter(
            name="swarm_id", type="string", required=False, default="",
            description="Optional swarm id for a single swarm.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        from backend.api import swarm_api

        swarm_id = (kwargs.get("swarm_id") or "").strip()
        path = f"/swarm/status/{swarm_id}" if swarm_id else "/swarm/status"
        data, status = swarm_api._proxy_get(path)
        if status == 503:
            return ToolResult(
                success=False,
                error="Swarm plugin is not running (port 8210). Start it from /plugins or say so — do not pretend a swarm launched.",
                metadata={"http_status": 503, "data": data},
            )
        if status >= 400:
            return ToolResult(success=False, error=swarm_api._extract_error(data, "swarm status failed"), metadata={"http_status": status})
        return ToolResult(success=True, output=data)


class LaunchSwarmTool(BaseTool):
    name = "launch_swarm"
    description = (
        "Launch a Swarm Orchestrator run (same as POST /api/swarm/launch). "
        "Self-code swarms target this repo, never auto-merge, and require "
        "acknowledge_dirty_tree if the tree is dirty. Use when the user asks to "
        "launch a coding swarm / parallel worktree agents."
    )
    requires_approval = True
    parameters = {
        "goal": ToolParameter(
            name="goal", type="string", required=True,
            description="What the swarm should implement or investigate.",
        ),
        "acknowledge_dirty_tree": ToolParameter(
            name="acknowledge_dirty_tree", type="bool", required=False, default=False,
            description="Required if launching a self-code swarm on a dirty git tree.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        goal = (kwargs.get("goal") or "").strip()
        if not goal:
            return ToolResult(success=False, error="goal is required")
        from backend.api import swarm_api
        from backend.services.guarded_code_service import default_repo_root

        body = {
            "goal": goal,
            "self_code": True,
            "auto_merge": False,
            "acknowledge_dirty_tree": bool(kwargs.get("acknowledge_dirty_tree")),
            "repo_path": str(default_repo_root()),
        }
        data, status = swarm_api._proxy_post("/swarm/launch", body, timeout=30)
        if status == 503:
            return ToolResult(
                success=False,
                error="Swarm plugin is not running. Start the swarm plugin first.",
                metadata={"http_status": 503, "data": data},
            )
        if status >= 400:
            return ToolResult(
                success=False,
                error=swarm_api._extract_error(data, "launch failed"),
                metadata={"http_status": status, "data": data},
            )
        return ToolResult(success=True, output=data)


class SelfImprovementStatusTool(BaseTool):
    name = "self_improvement_status"
    description = (
        "Report whether self-improvement can run (codebase lock, enabled flag, "
        "already running) plus recent runs and PendingFix rows. Use when the user "
        "asks if SI is on, why a fix didn't apply, or what pending fixes exist."
    )
    parameters: Dict[str, ToolParameter] = {}

    def execute(self, **kwargs) -> ToolResult:
        try:
            from backend.services.self_improvement_service import get_self_improvement_service
            svc = get_self_improvement_service()
            pre = svc.dispatch_precheck()
            payload: Dict[str, Any] = {"precheck": pre, "runs": [], "pending_fixes": []}
            try:
                from backend.models import PendingFix, SelfImprovementRun, db
                runs = (
                    db.session.query(SelfImprovementRun)
                    .order_by(SelfImprovementRun.id.desc())
                    .limit(5)
                    .all()
                )
                payload["runs"] = [
                    {
                        "id": r.id,
                        "trigger": r.trigger,
                        "status": r.status,
                        "created_at": r.timestamp.isoformat() if getattr(r, "timestamp", None) else None,
                    }
                    for r in runs
                ]
                fixes = (
                    db.session.query(PendingFix)
                    .order_by(PendingFix.id.desc())
                    .limit(8)
                    .all()
                )
                payload["pending_fixes"] = [
                    {
                        "id": f.id,
                        "status": getattr(f, "status", None),
                        "file_path": getattr(f, "file_path", None),
                        "summary": (getattr(f, "fix_description", None) or "")[:240],
                    }
                    for f in fixes
                ]
            except Exception as db_err:
                payload["db_error"] = str(db_err)
            return ToolResult(success=True, output=payload)
        except Exception as e:
            logger.exception("self_improvement_status failed")
            return ToolResult(success=False, error=str(e))


class SubmitImprovementTool(BaseTool):
    name = "submit_improvement"
    description = (
        "Submit a directed self-improvement task (same as the directed SI path). "
        "The engine proposes a real fix staged for review — it will refuse with "
        "the real lock/disabled reason instead of pretending. Use when the user "
        "says 'fix this in the codebase', 'self-improve X', or 'open a pending fix for…'."
    )
    requires_approval = True
    parameters = {
        "description": ToolParameter(
            name="description", type="string", required=True,
            description="What to investigate and fix.",
        ),
        "target_files": ToolParameter(
            name="target_files", type="list", required=False, default=[],
            description="Optional file paths to focus on.",
        ),
        "priority": ToolParameter(
            name="priority", type="string", required=False, default="medium",
            description="low | medium | high",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        description = (kwargs.get("description") or "").strip()
        if not description:
            return ToolResult(success=False, error="description is required")
        try:
            from backend.services.self_improvement_service import get_self_improvement_service
            svc = get_self_improvement_service()
            pre = svc.dispatch_precheck()
            if not pre.get("ok"):
                return ToolResult(success=False, error=pre.get("reason") or "self-improvement cannot run", metadata=pre)
            files = kwargs.get("target_files") or []
            if isinstance(files, str):
                files = [p.strip() for p in files.split(",") if p.strip()]
            result = svc.submit_directed_task(
                description=description,
                target_files=list(files) if files else None,
                priority=str(kwargs.get("priority") or "medium"),
            )
            return ToolResult(success=bool(result.get("success")), output=result, error=result.get("reason"))
        except Exception as e:
            logger.exception("submit_improvement failed")
            return ToolResult(success=False, error=str(e))


WORKSTATION_TOOLS: List[BaseTool] = [
    MapCodebaseTool(),
    DispatchMapFindingTool(),
    InspectGpuTool(),
    ReadLogsTool(),
    SwarmStatusTool(),
    LaunchSwarmTool(),
    SelfImprovementStatusTool(),
    SubmitImprovementTool(),
]
