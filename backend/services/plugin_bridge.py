"""Route-intent → plugin-lifecycle bridge.

The documented missing wire (CLAUDE.md "plugin auto-orchestration goal"): a
feature that needs a plugin should be able to bring it up on demand. This is the
minimal, focused version of that — NOT a rewrite of gpu_memory_orchestrator
(which is load-bearing and whose preload path is deliberately left alone).

`ensure_plugin_running` reuses the existing, already-gated, already-idempotent
PluginManager primitives (`is_effectively_enabled` / `enable_plugin` /
`start_plugin`). Celery stage tasks and `prepare_plugins_for_route` both call
into the same helpers.

**Full phase map (P1+ per approved design)**: STAGE_PLUGIN_REQUIREMENTS + plugins_for_stage /
ensure_plugins_for_stage provide stage-aware sequencing for pipelines (music-video,
film-crew). E.g. analyze (ollama+video_editor for Director unique per-cut prompts) before
storyboard (comfyui for flux/SDXL keyframes) before generating (comfyui + gpu gate).
Auto paths use persist_user_pref=False to avoid conflicting with manual UI toggles.
Tied to PipelineService stages for resume_all/dispatch safety. ROUTE map kept for nav
(with sub-path support).

CUDA-FORK SAFETY (load-bearing — see plugin_runner.py docstring): this MUST be
called from INSIDE a task/request body at runtime, never at import time. The
underlying start_plugin runs the plugin's start script through the plugin_runner
sidecar precisely so the backend never fork()s after importing torch.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Mirror of PluginManager.GPU_EXCLUSIVE_PLUGIN_IDS — avoid import cycle at module load.
GPU_EXCLUSIVE_PLUGIN_IDS = frozenset({"ollama", "comfyui"})

# Frontend route → sidecar plugins required for that page.
# In-process models (SD pipeline, whisper.cpp) stay in gpu_memory_orchestrator.
ROUTE_PLUGIN_MAP: Dict[str, List[str]] = {
    "/": [],
    "/chat": ["ollama"],
    "/voice-chat": ["ollama"],
    "/documents": ["ollama"],
    "/images": [],
    "/batch-images": [],
    "/video": ["comfyui"],
    "/video-editor": ["video_editor"],
    "/video-text-overlay": ["video_editor"],
    "/audio": ["audio_foundry"],
    "/training": ["lora_trainer"],
    "/film-crew": ["comfyui", "video_editor", "ollama"],
    "/music-video": ["comfyui", "video_editor", "ollama"],
    "/music-video/storyboard": ["comfyui"],  # phase for pre-approval thumbnails (analyze needs ollama+video_editor earlier)
    # Cast Studio: tasks drive ensure (planning→ollama, generate→comfyui). Do not
    # warm both on nav — that fights 16GB VRAM when the operator only opened /cast.
    "/cast": [],
    "/swarm": ["swarm"],
    "/settings": [],
    "/plugins": [],
    "/tools": [],
    "/agents": [],
}

# Full phase map for stage-aware plugin auto-orchestration (per approved design).
# Tied to PipelineService stages (current_stage / STAGE_TO_AGENT) for MV + Film Crew.
# Phases ensure correct sequencing: e.g., analyze (ollama for Director unique prompts)
# before storyboard (comfyui keyframes) before clip_gen (comfyui + i2v with gate).
# Used by ensure_plugins_for_stage (auto paths use persist_user_pref=False).
# ROUTE_PLUGIN_MAP remains for nav/prepare (with sub-path phasing support).
STAGE_PLUGIN_REQUIREMENTS: dict[str, dict[str, list[str]]] = {
    "music-video": {
        "analyzing": ["video_editor", "ollama"],      # Director for per-cut unique prompts
        "storyboard": ["comfyui"],                     # Pre-approval thumbnails (flux/SDXL + LoRA)
        "generating": ["comfyui"],                     # Per-clip keyframe + i2v (with gpu_session)
        "assembling": ["video_editor"],                # Final MLT/melt compose
    },
    "film-crew": {
        "draft": [],  # no plugins needed
        "screenwriting": ["ollama"],
        "casting": ["ollama"],  # advisory, but ensure for consistency
        "cinematography": ["ollama"],
        "storyboard_gen": ["comfyui"],                 # Storyboard artist keyframes
        "awaiting_approval": [],  # user gate
        "rendering": ["comfyui", "video_editor"],      # Editor (i2v + optional compose)
        "complete": [],
    },
    # Cast & LoRA Studio — first-class (was borrowing film-crew/storyboard_gen).
    # planning needs Ollama for bible/shots. generate/regen map lists ComfyUI for
    # FLUX/SDXL routes; character_generation_tasks skips ensure when Z-Image
    # offline path is selected. train is reclaim-only (lora_trainer tool).
    "cast": {
        "planning": ["ollama"],
        "generate_samples": ["comfyui"],
        "regen_sample": ["comfyui"],
        "train": [],
    },
}

PLUGIN_START_MAX_RETRIES = 3
PLUGIN_START_RETRY_PAD_S = 0.5

_state_lock = threading.Lock()
_last_route: Optional[str] = None
_orchestrator_claims: Set[str] = set()
_user_controlled: Set[str] = set()


class PluginUnavailable(RuntimeError):
    """A required plugin could not be enabled/started. Stage tasks turn this into
    a clean fail_stage rather than a crash."""


def auto_orchestrator_enabled() -> bool:
    """Feature flag — instant revert to manual-only plugin toggles."""
    return os.environ.get("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _normalize_route(route: str) -> str:
    """Strip parameterized segments (same rules as GPUMemoryOrchestrator)."""
    parts = route.strip("/").split("/")
    if len(parts) >= 2:
        second = parts[1]
        if any(c.isdigit() for c in second) or len(second) > 20:
            return f"/{parts[0]}"
    return f"/{parts[0]}" if parts and parts[0] else "/"


def plugins_for_route(route: str) -> List[str]:
    """Return plugin ids needed for a frontend route (deduped, order preserved).
    Supports explicit sub-paths (e.g. /music-video/storyboard for phased comfy-only) before falling back to normalized.
    """
    # Try exact first for phased sub-intents (storyboard only etc.), then normalized.
    seen: Set[str] = set()
    out: List[str] = []
    for key in (route, _normalize_route(route)):
        for pid in ROUTE_PLUGIN_MAP.get(key, []):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def plugins_for_stage(context: str, stage: str) -> list[str]:
    """Return plugin ids needed for a pipeline stage (deduped, order preserved).
    Used for full phase map (stage-aware auto-orchestration for agent swarms).
    Falls back to empty list for unknown context/stage (defensive).
    """
    seen: Set[str] = set()
    out: list[str] = []
    for pid in STAGE_PLUGIN_REQUIREMENTS.get(context, {}).get(stage, []):
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def ensure_plugins_for_stage(
    context: str,
    stage: str,
    *,
    job_critical: bool = False,
    **kwargs,
) -> None:
    """Ensure all plugins for a given pipeline stage/context are running.
    Auto-orchestrated paths pass persist_user_pref=False (see ensure_plugin_running).
    Called from PipelineService dispatch/resume and explicit task sites.

    job_critical=True (Cast Character jobs): transiently start even when the
    operator previously set user_enabled=False for VRAM — does NOT persist
    re-enable in the Plugins UI. Music-video / film-crew leave this False.

    P3: also triggers GPU model preparation for the stage (enhances gpu_memory_orchestrator
    with phase support; coordinates plugin + model loading for swarms).
    """
    for pid in plugins_for_stage(context, stage):
        ensure_plugin_running(
            pid,
            persist_user_pref=False,
            job_critical=job_critical,
            **kwargs,
        )
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        get_orchestrator().prepare_for_stage(context, stage)
    except Exception:
        logger.warning("GPU prepare_for_stage failed for %s/%s (non-fatal)", context, stage)


def get_orchestrator_state() -> Dict[str, Any]:
    """Snapshot for APIs / socket subscribers."""
    with _state_lock:
        return {
            "enabled": auto_orchestrator_enabled(),
            "last_route": _last_route,
            "orchestrator_claims": sorted(_orchestrator_claims),
            "user_controlled": sorted(_user_controlled),
        }


def mark_user_controlled(plugin_id: str) -> None:
    """Operator toggled a plugin manually — don't auto-stop or auto-start over it."""
    with _state_lock:
        _user_controlled.add(plugin_id)
        _orchestrator_claims.discard(plugin_id)
    _emit_plugins_status(f"user_control:{plugin_id}")


def mark_user_released(plugin_id: str) -> None:
    """Operator stopped/disabled a plugin — orchestrator may manage it again."""
    with _state_lock:
        _user_controlled.discard(plugin_id)
        _orchestrator_claims.discard(plugin_id)
    _emit_plugins_status(f"user_release:{plugin_id}")


def _emit_plugins_status(reason: str = "") -> None:
    try:
        from backend.services.plugin_status_emitter import emit_plugins_snapshot
        emit_plugins_snapshot(reason)
    except Exception:
        pass


def _plugin_manager():
    from backend.plugins.plugin_manager import get_plugin_manager
    return get_plugin_manager()


def _plugin_status(plugin_id: str):
    from backend.plugins.plugin_base import PluginStatus
    return _plugin_manager().get_status(plugin_id)


def _is_running(plugin_id: str) -> bool:
    """True if this process thinks the plugin is RUNNING, or the service health
    endpoint responds (another process may have started it)."""
    from backend.plugins.plugin_base import PluginStatus
    if _plugin_status(plugin_id) == PluginStatus.RUNNING:
        return True
    try:
        pm = _plugin_manager()
        meta = pm.registry.get_plugin(plugin_id)
        if meta and getattr(meta, "type", None) == "service":
            if pm._check_service_running(meta):
                # Align local status so subsequent checks stay consistent.
                try:
                    pm._plugin_status[plugin_id] = PluginStatus.RUNNING
                    if not getattr(meta.config, "enabled", False) and pm.is_effectively_enabled(plugin_id):
                        meta.config.enabled = True
                except Exception:
                    pass
                return True
    except Exception:
        pass
    return False


def _count_active_video_render_jobs(max_age_s: float = 1800.0) -> int:
    """Non-terminal video_render progress jobs updated within max_age_s."""
    try:
        from backend.config import config

        progress_dir = Path(config.OUTPUT_DIR) / ".progress_jobs"
        if not progress_dir.is_dir():
            return 0
        now = datetime.now(timezone.utc)
        terminal = frozenset({"complete", "error", "cancelled", "end"})
        active = 0
        for meta_path in progress_dir.glob("*/metadata.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("process_type") != "video_render":
                continue
            if data.get("status") in terminal or data.get("is_complete"):
                continue
            last_raw = data.get("last_update_utc") or data.get("last_update")
            if last_raw:
                try:
                    last_dt = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    age_s = (now - last_dt).total_seconds()
                    if age_s > max_age_s:
                        continue
                except ValueError:
                    pass
            active += 1
        return active
    except Exception as e:
        logger.debug("active video_render scan failed: %s", e)
        return 0


def _stop_blocked_reason(plugin_id: str) -> Optional[str]:
    """Return a human reason if an active job blocks stopping this plugin."""
    if plugin_id == "comfyui":
        n = _count_active_video_render_jobs()
        if n > 0:
            return f"{n} active video render job(s) in progress"

    try:
        from backend.services.job_operation_gate import get_gate
        from backend.services.job_types import JobKind

        snap = get_gate().snapshot()
        holder = snap.get("gpu_holder")
        if not holder:
            return None

        kind = holder.get("kind")
        # ComfyUI / video_editor back VIDEO_RENDER and production pipelines.
        if plugin_id in ("comfyui", "video_editor") and kind in (
            JobKind.VIDEO_RENDER.value,
            JobKind.PRODUCTION.value,
            JobKind.LORA_TRAIN.value,
        ):
            return f"job {kind}:{holder.get('native_id')} holds GPU"
        if plugin_id == "lora_trainer" and kind in (
            JobKind.LORA_TRAIN.value,
            JobKind.TRAINING.value,
        ):
            return f"job {kind}:{holder.get('native_id')} holds GPU"
        if plugin_id == "audio_foundry" and kind == JobKind.TRAINING.value:
            return f"job {kind}:{holder.get('native_id')} in progress"
    except Exception as e:
        logger.debug("job gate probe failed for %s: %s", plugin_id, e)
    return None


def _stop_plugin(plugin_id: str, *, reason: str) -> Dict[str, Any]:
    pm = _plugin_manager()
    if not _is_running(plugin_id):
        return {"success": True, "message": "already stopped"}

    blocked = _stop_blocked_reason(plugin_id)
    if blocked:
        logger.info("plugin_bridge: skip stop %s — %s", plugin_id, blocked)
        return {"success": False, "error": blocked, "blocked_by_job": True}

    res = pm.stop_plugin(plugin_id)
    if res.get("success"):
        logger.info("plugin_bridge: stopped %s (%s)", plugin_id, reason)
    else:
        logger.warning("plugin_bridge: stop %s failed: %s", plugin_id, res.get("error"))
    return res


def _resolve_gpu_conflict(needed: Set[str]) -> List[Dict[str, Any]]:
    """Stop GPU-exclusive plugins not required by the target route."""
    actions: List[Dict[str, Any]] = []
    pm = _plugin_manager()
    for pid in GPU_EXCLUSIVE_PLUGIN_IDS:
        if pid in needed or not _is_running(pid):
            continue
        with _state_lock:
            if pid in _user_controlled:
                continue
            if pid not in _orchestrator_claims:
                continue
        res = _stop_plugin(pid, reason=f"GPU swap for route needing {sorted(needed)}")
        if res.get("success"):
            with _state_lock:
                _orchestrator_claims.discard(pid)
            actions.append({"action": "gpu_swap_stop", "plugin_id": pid})
        elif res.get("blocked_by_job"):
            actions.append({"action": "gpu_swap_blocked", "plugin_id": pid, "error": res.get("error")})
    return actions


def ensure_plugin_running(
    plugin_id: str,
    *,
    enable_if_disabled: bool = True,
    persist_user_pref: bool = True,
    job_critical: bool = False,
) -> None:
    """Make sure ``plugin_id`` is enabled and running. Raises PluginUnavailable otherwise.

    DEPRECATED for stage-driven flows (P3): prefer `ensure_plugins_for_stage(context, stage)`
    which uses the full STAGE_PLUGIN_REQUIREMENTS map for proper sequencing in MV/FilmCrew
    pipelines. Direct calls are still supported for ad-hoc/one-off needs but will be
    migrated away.

    persist_user_pref=False (for auto-orchestrated paths like music-video / film-crew stages):
      - Skips calling enable_plugin (which mutates the persisted user_enabled overlay).
      - Allows transient auto-start without "sticking" a user preference.
      - Still honors user_enabled=False veto unless job_critical=True.

    job_critical=True (Cast Character jobs): bypass user-disable veto for a transient
    start only — never persists user_enabled=True.
    """
    import warnings
    warnings.warn(
        "ensure_plugin_running is deprecated for pipeline stage contexts in favor of "
        "ensure_plugins_for_stage (see STAGE_PLUGIN_REQUIREMENTS).",
        DeprecationWarning,
        stacklevel=2,
    )
    ok, detail = _try_start_plugin(
        plugin_id,
        enable_if_disabled=enable_if_disabled,
        persist_user_pref=persist_user_pref,
        job_critical=job_critical,
    )
    if not ok:
        raise PluginUnavailable(detail or f"could not start '{plugin_id}'")
    logger.info("plugin_bridge: '%s' running", plugin_id)


def _try_start_plugin(
    plugin_id: str,
    *,
    enable_if_disabled: bool = True,
    persist_user_pref: bool = True,
    job_critical: bool = False,
) -> tuple[bool, Optional[str]]:
    pm = _plugin_manager()

    # Live process (health) counts as running even if this worker's memory is stale
    # or user_enabled flipped after another process started the service.
    if _is_running(plugin_id):
        return True, "already running"

    # Explicit-disable veto: if the operator has *explicitly* turned this plugin
    # off (a persisted user_enabled=False, as opposed to merely "never toggled"),
    # the auto-orchestrator must never enable or start it on nav/stage paths —
    # unless job_critical=True (Cast Character), which may transiently start
    # without persisting re-enable.
    try:
        _user_prefs = pm.state_store.get_user_enabled()
        if _user_prefs.get(plugin_id) is False:
            if not job_critical:
                return False, f"plugin '{plugin_id}' is user-disabled"
            logger.info(
                "plugin_bridge: job_critical start of %s "
                "(user_enabled=False, not persisted)",
                plugin_id,
            )
    except Exception:
        pass

    # Always re-sync in-memory enabled from disk before start decisions.
    try:
        if pm.is_effectively_enabled(plugin_id):
            meta = pm.registry.get_plugin(plugin_id)
            if meta is not None:
                meta.config.enabled = True
    except Exception:
        pass

    if not pm.is_effectively_enabled(plugin_id):
        if not enable_if_disabled and not job_critical:
            return False, f"plugin '{plugin_id}' is disabled"
        if persist_user_pref and not job_critical:
            res = pm.enable_plugin(plugin_id)
            if not res.get("success"):
                return False, f"could not enable '{plugin_id}': {res.get('error')}"
            logger.info("plugin_bridge: enabled '%s' on demand", plugin_id)
        else:
            # Auto / job_critical path: do not mutate persistent user_enabled.
            # Temporarily flip in-memory config so the start guard inside manager passes.
            try:
                meta = pm.registry.get_plugin(plugin_id)
                if meta and not getattr(meta.config, "enabled", False):
                    meta.config.enabled = True  # transient for this start attempt only
            except Exception:
                pass
            logger.info(
                "plugin_bridge: auto-orchestrating start for '%s' (no persistent enable%s)",
                plugin_id,
                ", job_critical" if job_critical else "",
            )

    last_detail = "unknown error"
    for attempt in range(PLUGIN_START_MAX_RETRIES):
        res = pm.start_plugin(plugin_id)
        if res.get("success"):
            return True, res.get("message", "started")

        last_detail = res.get("error") or last_detail
        cooldown = float(res.get("cooldown_remaining") or 0)
        if res.get("gated") and cooldown > 0 and attempt < PLUGIN_START_MAX_RETRIES - 1:
            wait_s = cooldown + PLUGIN_START_RETRY_PAD_S
            logger.info(
                "plugin_bridge: %s gated (%s), retry %d/%d in %.1fs",
                plugin_id, last_detail, attempt + 1, PLUGIN_START_MAX_RETRIES, wait_s,
            )
            time.sleep(wait_s)
            continue
        break

    if res.get("cooldown_remaining"):
        last_detail = f"{last_detail} (retry in ~{res['cooldown_remaining']:.0f}s)"
    return False, f"could not start '{plugin_id}': {last_detail}"


def prepare_plugins_for_route(route: str) -> Dict[str, Any]:
    """Start/stop sidecar plugins for a frontend navigation intent.
    For music-video/film-crew sub-paths or known stages, uses persist_user_pref=False
    for auto-orchestrated behavior (per full phase map design).
    """
    if not auto_orchestrator_enabled():
        return {"route": route, "skipped": True, "reason": "auto_orchestrator_disabled"}

    global _last_route
    needed_list = plugins_for_route(route)
    needed = set(needed_list)
    actions: List[Dict[str, Any]] = []

    with _state_lock:
        prev_route = _last_route
        _last_route = route

    # Release orchestrator-owned plugins the new route no longer needs.
    if prev_route is not None:
        prev_needed = set(plugins_for_route(prev_route))
        with _state_lock:
            to_release = [
                pid for pid in _orchestrator_claims
                if pid not in needed and pid not in _user_controlled
            ]
        for pid in to_release:
            if pid in prev_needed or pid not in needed:
                res = _stop_plugin(pid, reason=f"left route {prev_route}")
                if res.get("success"):
                    with _state_lock:
                        _orchestrator_claims.discard(pid)
                    actions.append({"action": "stop", "plugin_id": pid, "reason": "route_change"})
                elif res.get("blocked_by_job"):
                    actions.append({
                        "action": "stop_blocked",
                        "plugin_id": pid,
                        "error": res.get("error"),
                    })

    actions.extend(_resolve_gpu_conflict(needed))

    for plugin_id in needed_list:
        with _state_lock:
            if plugin_id in _user_controlled:
                if _is_running(plugin_id):
                    actions.append({"action": "user_controlled_running", "plugin_id": plugin_id})
                else:
                    actions.append({"action": "user_controlled_stopped", "plugin_id": plugin_id})
                continue

        if _is_running(plugin_id):
            with _state_lock:
                _orchestrator_claims.add(plugin_id)
            actions.append({"action": "already_running", "plugin_id": plugin_id})
            continue

        # Re-check GPU conflict before each start (stop may need cooldown settle).
        actions.extend(_resolve_gpu_conflict(needed))

        # Auto paths (music-video/film-crew/cast stages or subpaths) use non-persisting
        # ensure so manual toggles in PluginsPage are not overridden.
        persist = not (
            route.startswith("/music-video")
            or route.startswith("/film-crew")
            or route.startswith("/cast")
        )
        ok, detail = _try_start_plugin(plugin_id, persist_user_pref=persist)
        if ok and _is_running(plugin_id):
            with _state_lock:
                _orchestrator_claims.add(plugin_id)
            actions.append({"action": "start", "plugin_id": plugin_id, "detail": detail})
        elif ok:
            actions.append({
                "action": "start_unconfirmed",
                "plugin_id": plugin_id,
                "error": detail or "start ack but plugin not running",
            })
            logger.warning(
                "plugin_bridge: route %s start ack for %s but plugin not running",
                route, plugin_id,
            )
        else:
            disabled = bool(detail and "user-disabled" in str(detail))
            action = "user_disabled" if disabled else "start_failed"
            actions.append({"action": action, "plugin_id": plugin_id, "error": detail})
            log_fn = logger.info if disabled else logger.warning
            log_fn("plugin_bridge: route %s could not start %s: %s", route, plugin_id, detail)

    result = {
        "route": route,
        "normalized": _normalize_route(route),
        "needed": needed_list,
        "actions": actions,
        **get_orchestrator_state(),
    }
    logger.info(
        "plugin_bridge: route %s → %d actions, claims=%s",
        route, len(actions), result.get("orchestrator_claims"),
    )
    # P1 phase map integration: ensure stage-critical plugins (with retry/backoff
    # inside _try_start_plugin) beyond the coarse ROUTE_PLUGIN_MAP list.
    norm_route = _normalize_route(route)
    try:
        if "/music-video/storyboard" in route or route == "/music-video/storyboard":
            ensure_plugins_for_stage("music-video", "storyboard")
        elif norm_route.startswith("/music-video"):
            ensure_plugins_for_stage("music-video", "generating")
        elif norm_route.startswith("/film-crew"):
            ensure_plugins_for_stage("film-crew", "rendering")
        elif norm_route == "/video":
            ensure_plugins_for_stage("music-video", "generating")
    except Exception as exc:
        logger.warning("plugin_bridge: stage ensure failed for %s: %s", route, exc)
    _emit_plugins_status(f"route:{route}")
    return result