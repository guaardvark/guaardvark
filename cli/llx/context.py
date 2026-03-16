"""Runtime context snapshot for LLM injection.

Gathers and caches system state from the Guaardvark backend with a 30-second
TTL so every chat message can include a [System Context] block describing
server health, active model, GPU status, running jobs, projects, and agents.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from llx.client import get_client, LlxError, LlxConnectionError

_TTL_SECONDS = 30

# The seven context sources and their endpoints.
SOURCES: dict[str, str] = {
    "health":   "/api/health",
    "model":    "/api/model/status",
    "celery":   "/api/health/celery",
    "gpu":      "/api/gpu/status",
    "jobs":     "/api/meta/active_jobs",
    "projects": "/api/projects",
    "agents":   "/api/agents",
}


class ContextSnapshot:
    """Cached, TTL-gated view of Guaardvark runtime state."""

    def __init__(self, server: str | None = None) -> None:
        self._server = server
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _is_fresh(self, key: str) -> bool:
        ts = self._timestamps.get(key)
        if ts is None:
            return False
        return (time.monotonic() - ts) < _TTL_SECONDS

    def _fetch(self, key: str, endpoint: str) -> Any:
        """Fetch *endpoint*, cache the result under *key*, and return it.

        On any error, return the stale cached value if one exists, otherwise
        return ``None``.
        """
        try:
            data = get_client(self._server).get(endpoint)
            with self._lock:
                self._cache[key] = data
                self._timestamps[key] = time.monotonic()
            return data
        except (LlxError, LlxConnectionError, Exception):
            # Return stale cache on failure.
            with self._lock:
                return self._cache.get(key)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

    # ------------------------------------------------------------------
    # Async / sync accessors
    # ------------------------------------------------------------------

    def refresh_async(self) -> None:
        """Submit background fetches for every source that is not fresh."""
        for key, endpoint in SOURCES.items():
            if not self._is_fresh(key):
                self._pool.submit(self._fetch, key, endpoint)

    def get_cached(self, key: str) -> Any:
        with self._lock:
            return self._cache.get(key)

    def get_or_fetch(self, key: str, endpoint: str) -> Any:
        if self._is_fresh(key):
            with self._lock:
                return self._cache.get(key)
        return self._fetch(key, endpoint)

    # ------------------------------------------------------------------
    # Convenience readers
    # ------------------------------------------------------------------

    def is_online(self) -> bool:
        health = self.get_or_fetch("health", SOURCES["health"])
        if not health:
            return False
        return _dig(health, "status") == "ok"

    def get_model_name(self) -> str:
        model = self.get_or_fetch("model", SOURCES["model"])
        if not model:
            return "unknown"
        text_model = (
            _dig(model, "message", "text_model")
            or _dig(model, "data", "text_model")
            or _dig(model, "text_model")
            or "unknown"
        )
        return text_model.removesuffix(":latest")

    def get_active_jobs_count(self) -> int:
        jobs = self.get_or_fetch("jobs", SOURCES["jobs"])
        if not jobs:
            return 0
        active = (
            _dig(jobs, "active_jobs")
            or _dig(jobs, "data", "active_jobs")
            or []
        )
        if isinstance(active, list):
            return len(active)
        return 0

    # ------------------------------------------------------------------
    # Context block for LLM injection
    # ------------------------------------------------------------------

    def format_context_block(self) -> str:
        """Build a multi-line ``[System Context]`` string from cached data.

        Each line is best-effort: if a source has no cached data the line is
        simply omitted.
        """
        lines: list[str] = ["[System Context]"]

        # --- Server ---
        health = self.get_cached("health")
        if health:
            status = _dig(health, "status") or "unknown"
            version = _dig(health, "version") or ""
            uptime_s = _dig(health, "uptime_seconds")
            parts = [f"Server: {status}"]
            meta: list[str] = []
            if version and version != "N/A":
                meta.append(f"v{version}")
            if uptime_s is not None:
                meta.append(f"uptime {_format_uptime(uptime_s)}")
            if meta:
                parts[0] += f" ({', '.join(meta)})"
            if self._server:
                parts[0] += f" @ {self._server}"
            lines.append(parts[0])

        # --- Model ---
        model = self.get_cached("model")
        if model:
            msg = _dig(model, "message") if isinstance(_dig(model, "message"), dict) else model
            text = _dig(msg, "text_model") or _dig(model, "data", "text_model") or ""
            vision = _dig(msg, "vision_model") or _dig(model, "data", "vision_model") or ""
            image_gen = _dig(msg, "image_gen_model") or _dig(model, "data", "image_gen_model") or ""
            segments: list[str] = []
            if text:
                segments.append(f"Model: {text}")
            if vision:
                segments.append(f"Vision: {vision}")
            if image_gen:
                segments.append(f"Image Gen: {image_gen.upper() if len(image_gen) <= 6 else image_gen}")
            if segments:
                lines.append(" | ".join(segments))

        # --- Celery ---
        celery = self.get_cached("celery")
        if celery:
            status = _dig(celery, "status") or _dig(celery, "result") or "unknown"
            active = _dig(celery, "active_tasks")
            if active is not None:
                lines.append(f"Celery: {status}, {active} active task(s)")
            else:
                lines.append(f"Celery: {status}")

        # --- GPU ---
        gpu = self.get_cached("gpu")
        if gpu:
            gpu_data = _dig(gpu, "data") if isinstance(_dig(gpu, "data"), dict) else gpu
            available = _dig(gpu_data, "available")
            owner = _dig(gpu_data, "owner")
            gpu_name = _dig(gpu_data, "gpu_name") or _dig(gpu_data, "name") or ""
            vram_used = _dig(gpu_data, "vram_used") or _dig(gpu_data, "memory_used")
            vram_total = _dig(gpu_data, "vram_total") or _dig(gpu_data, "memory_total")
            parts_gpu: list[str] = []
            if gpu_name:
                parts_gpu.append(gpu_name)
            if vram_used is not None and vram_total is not None and vram_total > 0:
                pct = int(vram_used / vram_total * 100)
                parts_gpu.append(f"{pct}% VRAM used")
            if available is not None:
                parts_gpu.append("available" if available else "busy")
            if owner and owner != "none":
                parts_gpu.append(f"owner: {owner}")
            if parts_gpu:
                lines.append(f"GPU: {', '.join(parts_gpu)}")

        # --- Jobs ---
        jobs = self.get_cached("jobs")
        if jobs:
            active_jobs = _dig(jobs, "active_jobs") or _dig(jobs, "data", "active_jobs") or []
            if isinstance(active_jobs, list):
                if active_jobs:
                    descriptions: list[str] = []
                    for j in active_jobs[:5]:
                        jtype = _dig(j, "job_type") or _dig(j, "type") or "job"
                        jid = _dig(j, "id") or ""
                        progress = _dig(j, "progress")
                        desc = f"{jtype} #{jid}" if jid else jtype
                        if progress is not None:
                            desc += f" — {progress}%"
                        descriptions.append(desc)
                    lines.append(f"Jobs: {len(active_jobs)} running ({', '.join(descriptions)})")
                else:
                    lines.append("Jobs: none active")

        # --- Projects ---
        projects = self.get_cached("projects")
        if projects:
            proj_list = projects if isinstance(projects, list) else (_dig(projects, "data") or _dig(projects, "projects") or [])
            if isinstance(proj_list, list) and proj_list:
                names = [f"{p.get('name', '?')} (id:{p.get('id', '?')})" for p in proj_list[:8]]
                lines.append(f"Projects: {', '.join(names)}")

        # --- Agents ---
        agents = self.get_cached("agents")
        if agents:
            agent_list = _dig(agents, "agents") or (_dig(agents, "data") if isinstance(_dig(agents, "data"), list) else None) or (agents if isinstance(agents, list) else [])
            if isinstance(agent_list, list) and agent_list:
                names = [a.get("name", a.get("id", "?")) for a in agent_list[:10]]
                lines.append(f"Agents: {', '.join(names)}")

        if len(lines) == 1:
            return "[System Context]\nServer: offline or unreachable"

        return "\n".join(lines)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _dig(obj: Any, *keys: str) -> Any:
    """Safely traverse nested dicts. Returns ``None`` on any miss."""
    cur = obj
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    return cur


def _format_uptime(seconds: float | int) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_m = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining_m}m"
    days = hours // 24
    remaining_h = hours % 24
    return f"{days}d {remaining_h}h"
