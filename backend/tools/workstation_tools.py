#!/usr/bin/env python3
"""Chat-callable workstation tools — the same services the pages use.

These are not sketches. Each execute() calls the live mapper / GPU / log /
swarm / self-improvement modules. If a sidecar is down or the codebase lock
is on, the tool returns that fact instead of a canned "I would…".
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import BackendError, is_mcp_transport, request_json

logger = logging.getLogger(__name__)

_SWARM_OFFLINE_ERROR = (
    "Swarm plugin is not running (port 8210). Start it from /plugins or say so — "
    "do not pretend a swarm launched."
)
# The sidecar is up but its status could not be read (timeout, a 5xx, a body
# that is not JSON, a rejected internal token). Not the same as offline:
# starting the plugin again is not the fix.
_SWARM_FAULT_ERROR = "The swarm service is running but did not return its status: {detail}"

# Swarm ids look like "swarm-20260930-120000-a1b2c3" (generate_swarm_id in
# plugins/swarm/service/models.py). The id becomes a URL path segment, so
# anything else, such as "../../gpu/status", is refused rather than sent.
_SWARM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The log files read_logs serves, each with the file that writes it (checked
# by backend/tests/test_read_logs.py, which also scans the start scripts for
# names missing here). A log a new script or plugin writes is added here or to
# _LOG_NOT_SERVED; a name nothing writes does not belong in either.
_LOG_WRITERS: Dict[str, str] = {
    # Core services.
    "backend.log": "backend/app.py",
    "backend_startup.log": "start.sh",
    "frontend.log": "start.sh",
    "setup.log": "start.sh",
    "preflight.log": "start.sh",
    "dep_reconciler.log": "start.sh",
    "celery.log": "start.sh",
    "celery_main.log": "start_celery.sh",
    "celery_training.log": "start_celery.sh",
    "celery_research.log": "backend/services/research_worker.py",
    "celery_beat.log": "start_celery.sh",
    "ollama_bootstrap.log": "start.sh",
    "whisper_server.log": "start.sh",
    "reboot.log": "backend/api/reboot_api.py",
    "heal_backend_venv.log": "scripts/heal_backend_venv.sh",
    "video_generation.log": "backend/services/batch_video_generator.py",
    "lora_trainer_daemon.log": "plugins/lora_trainer/real_trainer.py",
    # Agent display.
    "xfce_agent.log": "scripts/start_agent_display.sh",
    "x11vnc_agent.log": "scripts/start_agent_display.sh",
    # Plugins.
    "ollama_serve.log": "plugins/ollama/scripts/start.sh",
    "comfyui.log": "plugins/comfyui/scripts/start.sh",
    "audio_foundry.log": "plugins/audio_foundry/scripts/start.sh",
    "swarm.log": "plugins/swarm/scripts/start.sh",
    "video_editor.log": "plugins/video_editor/scripts/start.sh",
    "upscaling.log": "plugins/upscaling/scripts/start.sh",
    "vision_pipeline.log": "plugins/vision_pipeline/scripts/start.sh",
    "gpu_embedding_service.log": "plugins/gpu_embedding/scripts/start.sh",
    "discord_bot.log": "plugins/discord/scripts/start.sh",
}
_LOG_ALLOWLIST = frozenset(_LOG_WRITERS)

# Written under logs/ but never served: these hold what people typed and what
# models answered, or belong to a password-protected service, rather than
# service diagnostics. Redaction masks credentials, not conversations.
_LOG_NOT_SERVED: Dict[str, str] = {
    "llm_debug.log": "prompts and model replies (backend/app.py)",
    "memory_audit.log": "saved memories (backend/utils/memory_audit_log.py)",
    "mcp_audit.log": "arguments of MCP calls (backend/services/mcp_client_service.py)",
    "terminal.log": "the web terminal's server log (scripts/terminal_server.sh)",
}

# With a query, only this many of the newest matching lines are examined. Each
# one is redacted before it is matched again (a line that matches only inside
# a masked secret is not a match), and redaction costs about 60 microseconds a
# line (5,000 lines in 0.3 s, measured 2026-09-30).
_LOG_QUERY_CANDIDATES = 5000


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
    candidate = Path(root_arg).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()
    try:
        rel = candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"root must be inside the Guaardvark tree ({base})") from exc
    # The mapper skips these folders at any depth below the checkout, so a root
    # inside one (data/uploads, logs, a venv) is refused rather than mapped.
    from backend.services.system_mapper.core import DEFAULT_EXCLUDE_DIRS
    skipped = next((part for part in rel.parts if part in DEFAULT_EXCLUDE_DIRS), None)
    if skipped:
        raise ValueError(
            f"root is inside '{skipped}', a folder the System Mapper always skips. "
            "For an uploaded Code Repository use get_repository_map or get_dependency_graph."
        )
    if not candidate.is_dir():
        raise ValueError(f"Not a directory: {candidate}")
    return candidate


def _load_snapshot(root: Path, refresh: bool = False) -> dict:
    """The stored map of root, for looking a finding up by id. Raises when
    there is none yet."""
    from backend.services.system_mapper import store

    payload, _age = store.read_snapshot(root)
    if refresh or payload is None:
        store.start(root)
    if payload is None:
        raise RuntimeError(f"No map of {_root_label(root)} yet; call map_codebase first")
    return payload


def _root_label(root: Path) -> str:
    """root as answers name it: relative to the checkout, never absolute."""
    try:
        rel = Path(root).resolve().relative_to(_repo_root()).as_posix()
    except ValueError:
        return Path(root).name
    return "Guaardvark's checkout" if rel in ("", ".") else f"{rel}/ in Guaardvark's checkout"


# How long a map tool waits for a map being built before answering with what
# it has. Measured 2026-10-26 on one workstation: the whole checkout (2,608
# Python and JavaScript files) maps in about 7 s in a fresh process and 1.5 s
# when its files were read before; nothing is imported or run. MCP clients are
# cut off at 120 s by default. 60 s is many times the build and half that cutoff.
_MAP_WAIT_SECONDS = 60.0


def _map_wait_seconds(over_mcp: bool) -> float:
    """_MAP_WAIT_SECONDS, or half the MCP call timeout when that is shorter,
    so the answer always reaches an MCP client before it gives up."""
    if not over_mcp:
        return _MAP_WAIT_SECONDS
    try:
        from backend.mcp.config import load_config
        return min(_MAP_WAIT_SECONDS, max(1.0, load_config().timeout_seconds / 2))
    except Exception:
        return _MAP_WAIT_SECONDS


def _age_text(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def _map_snapshot(root: Path, refresh: bool, wait_seconds: float) -> Tuple[Optional[dict], Optional[str]]:
    """(snapshot, note), never blocking longer than wait_seconds.

    A map that still describes the files on disk is returned as is. Otherwise
    the one background build for root is started, or joined: an older map is
    returned at once with a note, and when there is none, or refresh was
    asked, the call first waits up to wait_seconds for the new one.
    (None, note) means there is nothing to show yet.
    """
    from backend.services.system_mapper import store

    payload, info = store.state(root)
    if payload is not None and not info["stale"] and not refresh:
        payload["_cache"] = info
        return payload, None

    job = store.running_job(root) or store.start(root)
    if payload is None or refresh:
        job.done.wait(wait_seconds)

    if job.done.is_set() and job.error is None and job.payload is not None:
        return job.payload, None

    if job.done.is_set():
        if payload is None:
            raise RuntimeError(f"system map failed for {_root_label(root)}: {job.error}")
        payload["_cache"] = {**info, "refresh_error": job.error}
        return payload, f"Building a new map failed ({job.error}). This is the last map, {_age_text(info['age_seconds'] or 0)} old."

    running = int(time.time() - job.started)
    if payload is not None:
        payload["_cache"] = {**info, "refreshing": True, "refresh_running_seconds": running}
        return payload, (
            f"The source changed since this map was built ({_age_text(info['age_seconds'] or 0)} ago). "
            f"A new one has been building for {running} s; ask again shortly, or pass refresh=true "
            "to wait for it. Asking again never starts a second build."
        )
    return None, (
        f"No map of this folder exists yet. It has been building in the background for {running} s "
        "(seconds on this checkout). Ask again in a moment; asking again does not start a second build."
    )


def _num(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "?"


def _map_text(root: Path, snapshot: dict, findings: List[dict], limit: int, note: Optional[str],
              over_mcp: bool) -> str:
    """map_codebase's answer: the findings first, most severe first, then the
    numbers. Paths are relative to the checkout."""
    st = snapshot.get("stats") or {}
    dep, reach, tool = st.get("dependency") or {}, st.get("reachability") or {}, st.get("tool") or {}
    subs = st.get("subsystems") or {}
    cache = snapshot.get("_cache") or {}
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.get("severity")] = counts.get(f.get("severity"), 0) + 1
    lines = [
        f"System map of {_root_label(root)}: {_num(snapshot.get('file_count'))} source files "
        f"({_num(dep.get('python_modules'))} Python, {_num(dep.get('js_modules'))} JavaScript)"
        + (", rebuilding because the source changed" if cache.get("stale") else ""),
    ]
    if note:
        lines.append(note)
    sev = ", ".join(f"{k} {counts[k]}" for k in ("high", "medium", "low", "info") if counts.get(k))
    shown = findings[:limit]
    lines.append(f"Findings: {len(findings)} open ({sev or 'none'}); the {len(shown)} most severe:")
    for f in shown:
        paths = f.get("paths") or []
        where = f" [{', '.join(paths[:3])}{' …' if len(paths) > 3 else ''}]" if paths else ""
        mark = " (dispatchable)" if f.get("dispatchable") else ""
        lines.append(f"  {f.get('severity')} {f.get('kind')} {f.get('id')}: {f.get('summary')}{where}{mark}")
    lines.append(
        f"Stats: {_num(dep.get('internal_edges'))} load-time import edges and {_num(dep.get('lazy_edges'))} inside "
        f"functions; {_num(dep.get('cycles'))} import cycles; {_num(reach.get('backend_routes'))} routes and "
        f"{_num(reach.get('frontend_callers'))} frontend API calls ({_num(reach.get('ghost_callers'))} with no route); "
        f"{_num(tool.get('registered_count'))} tools ({_num(tool.get('unwired_count'))} unreachable); "
        f"{_num(len(snapshot.get('celery_tasks') or []))} Celery tasks; {_num(subs.get('subsystems'))} subsystems "
        f"({_num(subs.get('unclaimed'))} files in none)."
    )
    lines.append(
        "To hand a dispatchable finding to self-improvement, open the System Map page (/system-map) in Guaardvark."
        if over_mcp else
        "dispatch_map_finding with a finding id hands a dispatchable finding to self-improvement (PendingFix)."
    )
    return "\n".join(lines)


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
    read_only = True
    plan_research = True
    observation_chars = 5000
    chat_summary = (
        "Health report of Guaardvark's own code: the most severe System Mapper findings (unreachable "
        "tools, frontend calls with no route, dormant modules, import cycles) and the numbers."
    )
    description = (
        "Run the System Mapper over Guaardvark's own source checkout, or a folder inside it, and "
        "return a short text report: the top findings first, most severe first (each with severity, "
        "kind, id, summary, up to three paths and whether it is dispatchable; dismissed findings are "
        "left out), then the numbers (files, import edges, cycles, routes, frontend calls, tools, "
        "Celery tasks, subsystems). Static analysis of the files only: nothing is imported or run, "
        "and no backend, network or GPU is needed. Never blocks for long: a map that still describes "
        "the files on disk comes back as is; when the source has changed the last map comes back at "
        "once with a note while one background build replaces it. When no map exists yet, or "
        "refresh=true, it waits up to 60 s for the new map. For an uploaded Code Repository folder "
        "use get_repository_map or get_dependency_graph; to find code by meaning, search_codebase."
    )
    parameters = {
        "refresh": ToolParameter(
            name="refresh", type="bool", required=False, default=False,
            description="true: build a new map now, rereading changed files, and wait up to 60 s for it. false (default): return the stored map; one that no longer matches the files is rebuilt in the background.",
        ),
        "root": ToolParameter(
            name="root", type="string", required=False, default="",
            description="Folder inside the Guaardvark checkout to map instead of all of it, absolute or relative to the checkout (e.g. 'backend/api'); paths outside are refused. Folders named data, logs, backups, outputs, build, dist, env, venv, node_modules, migrations, plans, audit, voice or ComfyUI, among others, are always skipped, and a root inside one is refused. In a git checkout only files git tracks, or has not been told to ignore, are mapped, so a git-ignored folder maps as empty.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=15, minimum=1, maximum=40,
            description="How many findings to list, most severe first, 1-40 (default 15). The report still gives the total.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        try:
            root = _safe_root(kwargs.get("root") or None)
            refresh = bool(kwargs.get("refresh", False))
            limit = max(1, min(int(kwargs.get("limit") or 15), 40))
            over_mcp = is_mcp_transport(self)
            snapshot, note = _map_snapshot(root, refresh, _map_wait_seconds(over_mcp))
            if snapshot is None:
                return ToolResult(
                    success=True,
                    output=f"Status: computing. {note}",
                    metadata={"root": _root_label(root), "computing": True},
                )
            from backend.services.system_mapper.actions import ranked_findings

            findings = ranked_findings(snapshot, root)
            text = _map_text(root, snapshot, findings, limit, note, over_mcp)
            meta = {
                "root": _root_label(root),
                "file_count": snapshot.get("file_count"),
                "finding_count": len(findings),
                "findings": [{
                    "id": f.get("id"), "kind": f.get("kind"), "severity": f.get("severity"),
                    "summary": f.get("summary"), "paths": (f.get("paths") or [])[:6],
                    "dispatchable": bool(f.get("dispatchable")),
                } for f in findings[:limit]],
                "cache": snapshot.get("_cache"),
                "note": note,
            }
            return ToolResult(success=True, output=text, metadata=meta)
        except Exception as e:
            logger.exception("map_codebase failed")
            return ToolResult(success=False, error=str(e))


class DispatchMapFindingTool(BaseTool):
    name = "dispatch_map_finding"
    read_only = False
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
            snapshot = _load_snapshot(root)
            from backend.services.system_mapper.actions import (
                DISPATCHABLE_KINDS,
                dispatch_finding,
                find_finding,
            )
            from backend.services.self_improvement_service import get_self_improvement_service

            finding = find_finding(snapshot, finding_id)
            if not finding:
                return ToolResult(success=False, error=f"No finding {finding_id} in the stored map. Run map_codebase first.")
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
            result = dispatch_finding(finding, priority=str(kwargs.get("priority") or "medium"), root=root)
            return ToolResult(success=bool(result.get("success")), output=result, error=result.get("reason"))
        except Exception as e:
            logger.exception("dispatch_map_finding failed")
            return ToolResult(success=False, error=str(e))


class InspectGpuTool(BaseTool):
    name = "inspect_gpu"
    read_only = True
    idempotent = True
    description = (
        "Inspect live GPU state: nvidia-smi, the exclusive lock (Ollama vs video), "
        "orchestrator model slots, and which plugins are running. Use when the user "
        "says 'debug GPU issues', 'what's using VRAM', 'GPU status', or 'OOM'."
    )
    parameters: Dict[str, ToolParameter] = {}

    @staticmethod
    def _plugin_rows(plugins) -> List[Dict[str, Any]]:
        return [
            {
                "id": p.get("id"),
                "running": p.get("running"),
                "status": p.get("status"),
                "port": p.get("port"),
                "vram_estimate_mb": p.get("vram_estimate_mb"),
            }
            for p in (plugins or [])
        ]

    def execute(self, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            return self._inspect_via_backend()
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
            payload["plugins"] = self._plugin_rows(get_plugin_manager().list_plugins())
        except Exception as e:
            payload["plugins"] = {"error": str(e)}
        return ToolResult(success=True, output=payload)

    def _inspect_via_backend(self) -> ToolResult:
        """Read the same state over HTTP instead of importing the orchestrator.

        Building the orchestrator pulls torch into this process, and a CUDA
        context here is VRAM a render on the same card cannot use — the reason
        backend.app refuses to be imported under GUAARDVARK_MCP_PROCESS at all.
        nvidia-smi stays local: it is a subprocess, not a CUDA client.
        """
        payload: Dict[str, Any] = {"nvidia": _nvidia_smi()}
        for key, path in (
            ("lock", "/api/gpu/status"),
            ("orchestrator", "/api/gpu/memory/status"),
            ("plugins", "/api/plugins/"),
        ):
            try:
                data = request_json("GET", path).data
            except BackendError as e:
                payload[key] = {"error": str(e)}
                continue
            if key == "plugins":
                # GET /api/plugins/ answers {"count": N, "plugins": [...]}
                data = data.get("plugins") if isinstance(data, dict) else data
                payload[key] = self._plugin_rows(data)
            else:
                payload[key] = data
        return ToolResult(success=True, output=payload)


class ReadLogsTool(BaseTool):
    name = "read_logs"
    read_only = True
    description = (
        "Tail one of Guaardvark's log files under logs/: the backend, Celery workers and "
        "beat, the frontend, startup and setup, and each plugin's log. Use when the user "
        "says 'review the logs', 'check backend.log', 'celery errors', or 'what did the "
        "last crash say'. Credentials in the text (passwords in URLs, tokens, API keys, "
        "Authorization headers) are replaced with ***. A log that was never written on "
        "this machine (a plugin that has not run) is reported as not found."
    )
    parameters = {
        "name": ToolParameter(
            name="name", type="string", required=False, default="backend.log",
            description="Log filename (not a path). Default backend.log.",
            enum=sorted(_LOG_ALLOWLIST),
        ),
        "lines": ToolParameter(
            name="lines", type="int", required=False, default=80,
            description="How many trailing lines (10-400).",
        ),
        "query": ToolParameter(
            name="query", type="string", required=False, default="",
            description=(
                "Optional case-insensitive substring filter, matched against the text as "
                "it is returned (after credentials are masked)."
            ),
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        raw_name = os.path.basename(str(kwargs.get("name") or "backend.log").strip() or "backend.log")
        if raw_name in _LOG_NOT_SERVED:
            return ToolResult(
                success=False,
                error=f"'{raw_name}' is not served by read_logs: it holds {_LOG_NOT_SERVED[raw_name]}.",
            )
        if raw_name not in _LOG_ALLOWLIST:
            return ToolResult(
                success=False,
                error=f"Unknown log '{raw_name}'. Allowed: {sorted(_LOG_ALLOWLIST)}",
            )
        try:
            from backend.utils.display_paths import display_path, display_text
            from backend.utils.secret_redaction import redact_secrets

            path = _log_dir() / raw_name
            if not path.is_file():
                return ToolResult(
                    success=False,
                    error=(
                        f"Log file not found: {display_path(path)}. Its writer "
                        f"({_LOG_WRITERS[raw_name]}) has not run on this machine, or the log was rotated."
                    ),
                )
            n = max(10, min(int(kwargs.get("lines") or 80), 400))
            text = path.read_text(encoding="utf-8", errors="replace")
            rows = text.splitlines()
            query = (kwargs.get("query") or "").strip().lower()
            capped = False
            if query:
                candidates = [ln for ln in rows if query in ln.lower()]
                capped = len(candidates) > _LOG_QUERY_CANDIDATES
                # Matched again after masking, so a query cannot be used to
                # test what a masked value contains.
                rows = [
                    ln for ln in (redact_secrets(c) for c in candidates[-_LOG_QUERY_CANDIDATES:])
                    if query in ln.lower()
                ]
            tail = rows[-n:]
            # Redacted as one block so a multi-line secret (a PEM key) is
            # caught; path shortening comes last so it cannot split a match.
            shown = display_text(redact_secrets("\n".join(tail)))
            output = {
                # Relative to the checkout: this result crosses the MCP boundary.
                "path": display_path(path),
                "matched_lines": len(rows),
                "returned_lines": len(tail),
                "query": query or None,
                # Tracebacks and file logs name the checkout and the home
                # directory on nearly every line; those leave with the text.
                "text": shown,
            }
            if capped:
                output["note"] = (
                    f"Only the newest {_LOG_QUERY_CANDIDATES} matching lines were examined; "
                    "matched_lines counts those."
                )
            return ToolResult(success=True, output=output)
        except Exception as e:
            logger.exception("read_logs failed")
            return ToolResult(success=False, error=str(e))


class SwarmStatusTool(BaseTool):
    name = "swarm_status"
    read_only = True
    idempotent = True
    description = (
        "Report what Guaardvark's Swarm Orchestrator is doing: the coding swarms that run several agents "
        "at once, each task in its own git worktree. Without swarm_id it returns every swarm the service "
        "is tracking (swarms, with their count); each swarm has its status (not_started, running, completed or "
        "failed), its tasks (title, status, branch, worktree, agent backend, elapsed time, tokens, "
        "estimated cost, error), task counts by status, how many are running, total cost and tokens, "
        "elapsed seconds and worktree disk use. With swarm_id it returns that swarm alone, a finished "
        "one too when its result is saved on disk; an unknown id is 'Swarm not found'. Reads only: swarms "
        "are started from Guaardvark's chat, its Swarm page or POST /api/swarm/launch. When the swarm "
        "plugin is not running the call fails and says so; do not report a swarm as running then."
    )
    parameters = {
        "swarm_id": ToolParameter(
            name="swarm_id", type="string", required=False, default="",
            description="One swarm's id, as its launch returned it, e.g. 'swarm-20260930-120000-a1b2c3' (letters, digits, '-' and '_' only). Omit to list every tracked swarm.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        swarm_id = (kwargs.get("swarm_id") or "").strip()
        if swarm_id and not _SWARM_ID_RE.match(swarm_id):
            return ToolResult(
                success=False,
                error=(
                    "swarm_id must be a swarm id such as 'swarm-20260930-120000-a1b2c3': "
                    "letters, digits, '-' and '_', at most 64 characters. Leave it empty "
                    "to list every swarm."
                ),
            )
        swarm_id = quote(swarm_id, safe="")
        if is_mcp_transport(self):
            return self._status_via_backend(swarm_id)

        from backend.api import swarm_api

        path = f"/swarm/status/{swarm_id}" if swarm_id else "/swarm/status"
        data, status = swarm_api._proxy_get(path)
        if status == 503:
            return ToolResult(
                success=False,
                error=_SWARM_OFFLINE_ERROR,
                metadata={"http_status": 503, "data": data},
            )
        if status == 404:
            return ToolResult(success=False, error="Swarm not found", metadata={"http_status": 404})
        if status >= 400:
            detail = swarm_api._extract_error(data, "swarm status failed") if isinstance(data, dict) else "swarm status failed"
            return ToolResult(
                success=False,
                error=_SWARM_FAULT_ERROR.format(detail=detail),
                metadata={"http_status": status},
            )
        return ToolResult(success=True, output=data)

    def _status_via_backend(self, swarm_id: str) -> ToolResult:
        path = f"/api/swarm/status/{swarm_id}" if swarm_id else "/api/swarm/status"
        try:
            resp = request_json("GET", path)
        except BackendError as e:
            if e.kind == "plugin_offline":
                return ToolResult(success=False, error=_SWARM_OFFLINE_ERROR, metadata={"http_status": 503})
            if e.status in (502, 504):
                return ToolResult(
                    success=False,
                    error=_SWARM_FAULT_ERROR.format(detail=e),
                    metadata={"http_status": e.status},
                )
            return ToolResult(success=False, error=str(e), metadata={"http_status": e.status})
        # GET /api/swarm/status answers 200 with an empty list when the sidecar
        # is down, so the offline case is only visible in its message.
        if isinstance(resp.body, dict) and resp.body.get("message") == "Swarm service offline":
            return ToolResult(success=False, error=_SWARM_OFFLINE_ERROR, metadata={"http_status": 503})
        # A status is never just an error message. A backend that wraps the
        # sidecar's failure in a success envelope must not be read as one.
        data = resp.data
        if isinstance(data, dict) and data and set(data) <= {"error", "detail", "message"}:
            detail = data.get("error") or data.get("detail") or data.get("message")
            return ToolResult(
                success=False,
                error=_SWARM_FAULT_ERROR.format(detail=detail),
                metadata={"http_status": resp.status},
            )
        return ToolResult(success=True, output=data)


class LaunchSwarmTool(BaseTool):
    name = "launch_swarm"
    read_only = False
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

        # Same path as the Swarm page: the goal becomes a one-task plan file,
        # which is what the sidecar reads (it refuses a body with no plan_path).
        body, refusal = swarm_api.prepare_launch({
            "goal": goal,
            "self_code": True,
            "auto_merge": False,
            "acknowledge_dirty_tree": bool(kwargs.get("acknowledge_dirty_tree")),
            "repo_path": str(default_repo_root()),
        })
        if refusal:
            message, http_status, code = refusal
            return ToolResult(success=False, error=message,
                              metadata={"http_status": http_status, "code": code})
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
    read_only = True
    description = (
        "Report whether self-improvement can run (codebase lock, enabled flag, "
        "already running) plus recent runs and PendingFix rows. Use when the user "
        "asks if SI is on, why a fix didn't apply, or what pending fixes exist. "
        "Over MCP every part comes from the running Guaardvark backend; when it is "
        "not answering, the call fails and says so."
    )
    parameters: Dict[str, ToolParameter] = {}

    def execute(self, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            return self._status_via_backend()
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

    def _status_via_backend(self) -> ToolResult:
        """The whole answer from the backend.

        The precheck reads the database and the running service's state, and
        this process has neither: computed here it could only ever say
        "disabled". A backend that is not answering is reported as that.
        """
        try:
            pre = request_json("GET", "/api/self-improvement/precheck").data
        except BackendError as e:
            if e.kind in ("unreachable", "timeout", "auth"):
                return ToolResult(success=False, error=str(e), metadata={"backend_error": e.kind})
            pre = {
                "ok": None,
                "reason": f"The backend did not report whether self-improvement can run: {e}",
            }
        if not isinstance(pre, dict) or "ok" not in pre:
            pre = {"ok": None, "reason": "The backend's precheck answer had no 'ok' field."}
        payload: Dict[str, Any] = {"precheck": pre, "runs": [], "pending_fixes": []}
        payload.update(self._history_via_backend())
        return ToolResult(success=True, output=payload)

    @staticmethod
    def _history_via_backend() -> Dict[str, Any]:
        """Recent runs and fixes in the same shape as the in-process query."""
        try:
            runs = (request_json("GET", "/api/self-improvement/runs", params={"limit": 5}).data or {}).get("runs") or []
            fixes = request_json("GET", "/api/self-improvement/pending-fixes", params={"limit": 8}).data or []
        except BackendError as e:
            return {"db_error": str(e)}
        return {
            "runs": [
                {"id": r.get("id"), "trigger": r.get("trigger"), "status": r.get("status"), "created_at": r.get("timestamp")}
                for r in runs
            ],
            "pending_fixes": [
                {
                    "id": f.get("id"),
                    "status": f.get("status"),
                    "file_path": f.get("file_path"),
                    "summary": (f.get("fix_description") or "")[:240],
                }
                for f in fixes
            ],
        }


class SubmitImprovementTool(BaseTool):
    name = "submit_improvement"
    read_only = False
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
