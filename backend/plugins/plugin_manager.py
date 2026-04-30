"""
Plugin Manager
Manages plugin lifecycle and operations.
"""

import json
import logging
import os
import signal
import subprocess
import threading
import time
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from .plugin_base import PluginStatus, PluginMetadata
from .plugin_registry import PluginRegistry, get_plugin_registry
from .plugin_state_store import PluginStateStore

logger = logging.getLogger(__name__)


# ─── Traffic Light: Plugin Operation Gate ──────────────────────────────────
#
# Prevents the failure mode where rapid back-to-back plugin start/stop calls
# accumulate state corruption (multiprocessing semaphore leaks, GPU contention,
# etc.) and eventually crash the backend. Each plugin gets:
#
#   1. A per-plugin in-progress flag — second click on the same plugin while
#      its first operation is still running gets rejected.
#   2. A per-plugin cooldown after the operation completes — gives the plugin
#      a few seconds to actually settle (process exit, port release, GPU
#      memory free) before another operation can be initiated.
#   3. A global short cooldown — prevents user from machine-gunning multiple
#      plugins in <1s, which is what triggered the resource_tracker death
#      pattern observed on 2026-04-11.
#   4. A GPU exclusivity mutex — only one of {ollama, comfyui} can be
#      mid-operation at a time, regardless of cooldowns.
#
# The frontend reads cooldown_remaining from the plugin list endpoint and
# disables the toggle switch with a countdown tooltip during the cooldown.

PLUGIN_COOLDOWN_S = 3.0           # per-plugin cooldown after release
PLUGIN_COOLDOWN_GPU_S = 8.0       # longer for GPU plugins (more state to settle)
GLOBAL_COOLDOWN_S = 2.0           # global cooldown across all plugin ops
GLOBAL_COOLDOWN_AFTER_GPU_S = 8.0  # global cooldown after a GPU op (CUDA needs to settle)
GPU_EXCLUSIVE_PLUGIN_IDS = {'ollama', 'comfyui'}  # mirror of frontend constant


class PluginOperationGate:
    """Traffic light for plugin start/stop operations.

    Thread-safe. Each plugin operation must call try_acquire() first; if it
    returns acquired=True, the caller proceeds with the operation and MUST
    call release() in a finally block. If acquired=False, the caller returns
    a 409-style response with the cooldown_remaining seconds so the frontend
    can disable the toggle and show a countdown.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_progress: Dict[str, bool] = {}
        self._last_finished_at: Dict[str, float] = {}
        self._global_last_finished_at: float = 0.0
        self._last_op_was_gpu: bool = False  # if True, longer global cooldown applies
        self._gpu_holder: Optional[str] = None  # plugin_id currently doing GPU op

    def _cooldown_for(self, plugin_id: str) -> float:
        return PLUGIN_COOLDOWN_GPU_S if plugin_id in GPU_EXCLUSIVE_PLUGIN_IDS else PLUGIN_COOLDOWN_S

    def _global_cooldown_active(self) -> float:
        """Return remaining global cooldown seconds (0 if expired). Picks the longer
        cooldown if the last op was a GPU op."""
        cooldown_s = GLOBAL_COOLDOWN_AFTER_GPU_S if self._last_op_was_gpu else GLOBAL_COOLDOWN_S
        elapsed = time.monotonic() - self._global_last_finished_at
        return max(0.0, cooldown_s - elapsed)

    def try_acquire(self, plugin_id: str) -> Tuple[bool, float, str]:
        """Attempt to start a plugin operation.

        Returns (acquired, cooldown_remaining, reason).
        - acquired=True: caller may proceed; cooldown_remaining=0
        - acquired=False: caller must reject; cooldown_remaining is the wait time
        """
        is_gpu = plugin_id in GPU_EXCLUSIVE_PLUGIN_IDS
        cooldown_s = self._cooldown_for(plugin_id)

        with self._lock:
            # 1. Already in progress?
            if self._in_progress.get(plugin_id, False):
                return False, cooldown_s, f"Plugin '{plugin_id}' operation already in progress"

            # 2. Per-plugin cooldown?
            last = self._last_finished_at.get(plugin_id, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < cooldown_s:
                return False, cooldown_s - elapsed, f"Plugin '{plugin_id}' cooling down — let it settle"

            # 3. Global cooldown? (Longer if the last op was GPU — CUDA needs to settle.)
            global_remaining = self._global_cooldown_active()
            if global_remaining > 0:
                msg = (
                    "GPU is settling — wait a moment"
                    if self._last_op_was_gpu
                    else "Plugin system cooling down"
                )
                return False, global_remaining, msg

            # 4. GPU exclusivity? Only one GPU plugin operation in flight at a time.
            if is_gpu and self._gpu_holder is not None and self._gpu_holder != plugin_id:
                return False, cooldown_s, f"GPU is busy with '{self._gpu_holder}'"

            # All checks passed — acquire
            self._in_progress[plugin_id] = True
            if is_gpu:
                self._gpu_holder = plugin_id

        return True, 0.0, ""

    def release(self, plugin_id: str) -> None:
        """Mark the plugin operation as finished and start the cooldown clock."""
        is_gpu = plugin_id in GPU_EXCLUSIVE_PLUGIN_IDS
        now = time.monotonic()
        with self._lock:
            self._in_progress[plugin_id] = False
            self._last_finished_at[plugin_id] = now
            self._global_last_finished_at = now
            self._last_op_was_gpu = is_gpu
            if self._gpu_holder == plugin_id:
                self._gpu_holder = None

    def cooldown_remaining(self, plugin_id: str) -> float:
        """Get the seconds of cooldown remaining for a plugin (0 if available).

        If the plugin is currently in progress, returns the per-plugin cooldown
        as a sentinel (the operation hasn't even started cooling down yet).
        """
        cooldown_s = self._cooldown_for(plugin_id)
        with self._lock:
            if self._in_progress.get(plugin_id, False):
                return cooldown_s

            now = time.monotonic()
            last = self._last_finished_at.get(plugin_id, 0.0)
            per_plugin = max(0.0, cooldown_s - (now - last))
            global_remaining = self._global_cooldown_active()

            return max(per_plugin, global_remaining)


def _run_plugin_script(argv: list, cwd: str, timeout: int) -> dict:
    """Run a plugin script via the sidecar runner if available, else fall back
    to direct subprocess.run.

    Returns a normalized dict: {ok: bool, rc: int, stdout: str, stderr: str, error: str?}

    The sidecar path is preferred because the main backend has CUDA loaded,
    and fork() with CUDA loaded corrupts the parent's CUDA state. The sidecar
    runs in a process tree with no CUDA, so its forks are safe.
    """
    try:
        from backend.services.plugin_runner import PluginRunnerClient
        client = PluginRunnerClient.get()
        if client.is_alive():
            return client.run(argv=argv, cwd=cwd, timeout=timeout)
    except Exception as e:
        logger.warning(f"Plugin sidecar unavailable, falling back to direct subprocess: {e}")

    # Fallback: direct subprocess.run (risks CUDA corruption — only used if sidecar is dead)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return {
            "ok": True,
            "rc": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "rc": -1,
            "stdout": (e.stdout or "") if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "") if isinstance(e.stderr, str) else "",
            "error": f"timeout after {e.timeout}s",
        }
    except Exception as e:
        return {"ok": False, "rc": -1, "stdout": "", "stderr": "", "error": str(e)}

# Where state lives — derived from the registry's plugins_dir at construction
# time. plugin_state.json is per-machine runtime state; see PluginStateStore.

class PluginManager:
    """
    Manages plugin lifecycle operations.
    
    Handles starting, stopping, and monitoring plugins.
    Works with the PluginRegistry for plugin discovery.
    """
    
    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        state_store: Optional[PluginStateStore] = None,
    ):
        """
        Initialize plugin manager.

        Args:
            registry: Plugin registry instance. Uses global if not provided.
            state_store: Where to read/write per-machine runtime state.
                Defaults to a store at <plugins_dir>.parent/data/plugin_state.json,
                so tests that pass a temp plugins_dir get isolated state for free.
        """
        self.registry = registry or get_plugin_registry()
        if state_store is None:
            state_store = PluginStateStore(
                self.registry.plugins_dir.parent / "data" / "plugin_state.json"
            )
        self.state_store = state_store
        self._plugin_status: Dict[str, PluginStatus] = {}
        self._plugin_pids: Dict[str, int] = {}
        self._gate = PluginOperationGate()  # Traffic light for rapid clicks

        # Initialize status for all plugins
        self._init_plugin_status()
    
    def _init_plugin_status(self):
        """Initialize plugin status and restore previously running plugins."""
        # Seed the in-memory `metadata.config.enabled` from the user_enabled
        # overlay before anything else looks at it. After this point, that
        # field reflects the EFFECTIVE state (user pref ∨ manifest default)
        # for the rest of this process's lifetime. enable_plugin/disable_plugin
        # keep it in sync on subsequent toggles. plugin.json on disk stays
        # untouched — it's the canonical default for fresh installs.
        prefs = self.state_store.get_user_enabled()
        if prefs:
            for plugin_id, metadata in self.registry.get_all_plugins().items():
                if plugin_id in prefs:
                    metadata.config.enabled = bool(prefs[plugin_id])

        # First pass: detect what's already running and kill orphans
        for plugin_id, metadata in self.registry.get_all_plugins().items():
            if metadata.config.enabled:
                if self._check_service_running(metadata):
                    self._plugin_status[plugin_id] = PluginStatus.RUNNING
                else:
                    self._plugin_status[plugin_id] = PluginStatus.STOPPED
            else:
                # Plugin is disabled — kill orphans, but never touch core services
                # or services that other enabled plugins depend on
                if metadata.type == 'service' and self._check_service_running(metadata):
                    if metadata.core:
                        logger.info(f"Core plugin '{plugin_id}' is disabled but running on port {metadata.port} — leaving it alone")
                    elif self._has_enabled_dependents(plugin_id):
                        dependents = self._get_enabled_dependents(plugin_id)
                        logger.info(f"Disabled plugin '{plugin_id}' running on port {metadata.port} — keeping it (needed by {dependents})")
                    elif metadata.port == self._backend_port():
                        logger.error(
                            f"Disabled plugin '{plugin_id}' declares port {metadata.port}, which is the "
                            f"main backend's port. Refusing to kill — fix the plugin.json port collision. "
                            f"(_kill_by_port has a self-PID guard as a second line of defense.)"
                        )
                    else:
                        logger.warning(f"Disabled plugin '{plugin_id}' has orphan process on port {metadata.port} — killing it")
                        self._kill_by_port(metadata.port)
                self._plugin_status[plugin_id] = PluginStatus.DISABLED

        # Second pass: start plugins that were running last time
        for plugin_id in self.state_store.get_running():
            if self._plugin_status.get(plugin_id) == PluginStatus.STOPPED:
                logger.info(f"Restoring plugin: {plugin_id} (was running before shutdown)")
                try:
                    result = self.start_plugin(plugin_id)
                    if result.get('success'):
                        logger.info(f"Restored plugin: {plugin_id}")
                    else:
                        logger.warning(f"Failed to restore {plugin_id}: {result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Error restoring {plugin_id}: {e}")
            elif self._plugin_status.get(plugin_id) == PluginStatus.DISABLED:
                logger.info(f"Skipping restore of '{plugin_id}' — plugin was disabled since last run")

        # Clean up: sync state file to match reality (removes stale entries
        # from plugins that were disabled or stopped between reboots)
        self._save_running()

    def _save_running(self) -> None:
        """Persist the current running set via the state store."""
        running = [pid for pid, status in self._plugin_status.items()
                   if status == PluginStatus.RUNNING]
        self.state_store.set_running(running)

    # ─── User-toggle persistence (overlay on plugin.json defaults) ─────────────
    #
    # The toggle in /plugins UI updates `user_enabled[plugin_id]` via the state
    # store, NOT the `enabled` field in plugin.json. plugin.json stays the
    # canonical default for fresh installs / new clones. is_effectively_enabled
    # joins the two with the rule: explicit user pref wins, fall back to
    # manifest default.

    def is_effectively_enabled(self, plugin_id: str) -> bool:
        """User pref wins, falling back to the manifest's default_enabled."""
        prefs = self.state_store.get_user_enabled()
        if plugin_id in prefs:
            return bool(prefs[plugin_id])
        metadata = self.registry.get_plugin(plugin_id)
        return bool(metadata.config.enabled) if metadata else False
    
    @staticmethod
    def _backend_port() -> Optional[int]:
        """Return the main Flask backend's port (FLASK_PORT env, default 5002).
        Used to short-circuit orphan-kill logic when a plugin manifest's port
        collides with the backend's own port — see the gpu_embedding case."""
        try:
            return int(os.environ.get("FLASK_PORT", "5002"))
        except (TypeError, ValueError):
            return 5002

    def _check_service_running(self, metadata: PluginMetadata) -> bool:
        """Check if a service plugin is running by hitting its health endpoint"""
        if metadata.type != 'service':
            return False
        
        health_endpoint = metadata.endpoints.get('health', '/health')
        service_url = metadata.config.service_url
        
        if not service_url:
            if metadata.port:
                service_url = f"http://localhost:{metadata.port}"
            else:
                return False
        
        try:
            url = f"{service_url.rstrip('/')}{health_endpoint}"
            response = requests.get(url, timeout=2)
            return response.status_code == 200
        except Exception:
            return False
    
    def _kill_by_port(self, port: int):
        """Kill any process listening on the given port (orphan cleanup).

        SAFETY: never kill our own PID, our parent's PID, or anything in our
        own process group. This protects against pre-existing manifest bugs
        where a disabled plugin claims the same port as the main Flask
        backend (e.g. gpu_embedding's port=5002 collision with FLASK_PORT).
        Without this guard, init-time orphan cleanup would SIGTERM the
        backend that just spawned us — exactly the pattern that took the
        system down on 2026-04-28.
        """
        if not port:
            return

        # Build a set of PIDs we must NEVER kill, no matter what.
        protected: set[int] = {os.getpid()}
        try:
            protected.add(os.getppid())
        except Exception:
            pass
        try:
            protected.add(os.getpgrp())  # our process group leader
        except Exception:
            pass
        try:
            # Anything sharing our process group is also protected — covers
            # Celery workers / sidecar runners that the backend spawns.
            for line in subprocess.check_output(
                ['ps', '-eo', 'pid,pgid', '--no-headers'], text=True, timeout=3
            ).splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) == os.getpgrp():
                    protected.add(int(parts[0]))
        except Exception:
            pass

        try:
            result = subprocess.run(
                ['lsof', '-ti', f':{port}'],
                capture_output=True, text=True, timeout=5
            )
            pids = result.stdout.strip().split('\n')
            for pid_str in pids:
                pid_str = pid_str.strip()
                if not (pid_str and pid_str.isdigit()):
                    continue
                pid = int(pid_str)
                if pid in protected:
                    logger.error(
                        f"Refusing to kill PID {pid} on port {port} — it is the backend "
                        f"process (or a child of it). Plugin manifest likely declares a "
                        f"port that collides with FLASK_PORT. Fix the plugin.json port."
                    )
                    continue
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.info(f"Killed orphan process on port {port} (PID {pid})")
                except ProcessLookupError:
                    pass
                except PermissionError:
                    logger.warning(f"No permission to kill PID {pid} on port {port}")
        except Exception as e:
            logger.debug(f"Port kill failed for port {port}: {e}")

    def _has_enabled_dependents(self, plugin_id: str) -> bool:
        """Check if any enabled plugin depends on this one."""
        for pid, meta in self.registry.get_all_plugins().items():
            if plugin_id in meta.dependencies and meta.config.enabled:
                return True
        return False

    def _get_enabled_dependents(self, plugin_id: str) -> list:
        """Get list of enabled plugin IDs that depend on this one."""
        return [
            pid for pid, meta in self.registry.get_all_plugins().items()
            if plugin_id in meta.dependencies and meta.config.enabled
        ]

    def get_status(self, plugin_id: str) -> PluginStatus:
        """Get current status of a plugin"""
        if plugin_id not in self._plugin_status:
            return PluginStatus.UNKNOWN
        return self._plugin_status[plugin_id]
    
    def get_all_status(self) -> Dict[str, str]:
        """Get status of all plugins"""
        # Refresh status before returning
        self._refresh_status()
        return {pid: status.value for pid, status in self._plugin_status.items()}
    
    def _refresh_status(self):
        """Refresh status of all plugins"""
        for plugin_id, metadata in self.registry.get_all_plugins().items():
            if not metadata.config.enabled:
                self._plugin_status[plugin_id] = PluginStatus.DISABLED
            elif metadata.type == 'service':
                if self._check_service_running(metadata):
                    self._plugin_status[plugin_id] = PluginStatus.RUNNING
                else:
                    self._plugin_status[plugin_id] = PluginStatus.STOPPED
    
    def start_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """
        Start a plugin.

        Args:
            plugin_id: Plugin ID to start

        Returns:
            Result dictionary with status and message. If the operation gate
            rejects the request (cooldown or in-progress), the dict includes
            'cooldown_remaining' so the frontend can show a countdown.
        """
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}

        if not metadata.config.enabled:
            return {'success': False, 'error': 'Plugin is disabled. Enable it first.'}

        if self._plugin_status.get(plugin_id) == PluginStatus.RUNNING:
            return {'success': True, 'message': 'Plugin already running'}

        # ── Traffic light: rate-limit rapid clicks and enforce GPU exclusivity ──
        acquired, cooldown, reason = self._gate.try_acquire(plugin_id)
        if not acquired:
            logger.info(f"Plugin start rejected by gate: {plugin_id} — {reason} ({cooldown:.1f}s)")
            return {
                'success': False,
                'error': reason,
                'cooldown_remaining': cooldown,
                'gated': True,
            }

        try:
            # Check dependencies are running
            for dep_id in getattr(metadata, 'dependencies', []):
                if not self.registry.is_registered(dep_id):
                    return {
                        'success': False,
                        'error': f"Required dependency '{dep_id}' is not installed"
                    }
                dep_status = self.get_status(dep_id)
                if dep_status != PluginStatus.RUNNING:
                    return {
                        'success': False,
                        'error': f"Required dependency '{dep_id}' is not running (status: {dep_status.value})"
                    }

            plugin_dir = self.registry.get_plugin_dir(plugin_id)
            if not plugin_dir:
                return {'success': False, 'error': 'Plugin directory not found'}

            # Set status to starting
            self._plugin_status[plugin_id] = PluginStatus.STARTING

            # Try to find and run start script
            start_script = plugin_dir / 'scripts' / 'start.sh'
            if not start_script.exists():
                self._plugin_status[plugin_id] = PluginStatus.ERROR
                return {'success': False, 'error': 'No start script found'}

            try:
                plugin_timeout = getattr(getattr(metadata, 'config', None), 'timeout', 30) + 30
                result = _run_plugin_script(
                    argv=['bash', str(start_script)],
                    cwd=str(plugin_dir),
                    timeout=plugin_timeout,
                )

                if not result.get('ok'):
                    self._plugin_status[plugin_id] = PluginStatus.ERROR
                    err = result.get('error', 'unknown error')
                    logger.error(f"Failed to start plugin {plugin_id}: {err}")
                    return {'success': False, 'error': f'Start script failed: {err}'}

                if result.get('rc', -1) != 0:
                    self._plugin_status[plugin_id] = PluginStatus.ERROR
                    stderr = result.get('stderr', '')
                    logger.error(f"Failed to start plugin {plugin_id}: {stderr}")
                    return {'success': False, 'error': f'Start script failed: {stderr}'}

                # Wait for service to become healthy (retry loop)
                max_retries = 20
                for i in range(max_retries):
                    if self._check_service_running(metadata):
                        self._plugin_status[plugin_id] = PluginStatus.RUNNING
                        self._save_running()
                        logger.info(f"Plugin started: {plugin_id}")
                        return {
                            'success': True,
                            'message': 'Plugin started successfully',
                            'output': result.get('stdout', '')
                        }
                    time.sleep(0.5)

                # Check if process is still running
                # (Simple check: if we can't connect after retries, assume failure)
                self._plugin_status[plugin_id] = PluginStatus.ERROR
                return {
                    'success': False,
                    'error': 'Plugin started but health check failed (timeout)'
                }

            except Exception as e:
                self._plugin_status[plugin_id] = PluginStatus.ERROR
                logger.error(f"Error starting plugin {plugin_id}: {e}")
                return {'success': False, 'error': str(e)}
        finally:
            # Always release the gate, even on error — start the cooldown clock.
            self._gate.release(plugin_id)
    
    def stop_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """
        Stop a plugin.

        Args:
            plugin_id: Plugin ID to stop

        Returns:
            Result dictionary with status and message. If the operation gate
            rejects the request, the dict includes 'cooldown_remaining'.
        """
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}

        current_status = self._plugin_status.get(plugin_id)
        if current_status == PluginStatus.STOPPED:
            return {'success': True, 'message': 'Plugin already stopped'}

        if current_status == PluginStatus.DISABLED:
            return {'success': True, 'message': 'Plugin is disabled'}

        # ── Traffic light: rate-limit rapid clicks and enforce GPU exclusivity ──
        acquired, cooldown, reason = self._gate.try_acquire(plugin_id)
        if not acquired:
            logger.info(f"Plugin stop rejected by gate: {plugin_id} — {reason} ({cooldown:.1f}s)")
            return {
                'success': False,
                'error': reason,
                'cooldown_remaining': cooldown,
                'gated': True,
            }

        try:
            plugin_dir = self.registry.get_plugin_dir(plugin_id)
            if not plugin_dir:
                return {'success': False, 'error': 'Plugin directory not found'}

            # Set status to stopping
            self._plugin_status[plugin_id] = PluginStatus.STOPPING

            # Try to find and run stop script
            stop_script = plugin_dir / 'scripts' / 'stop.sh'
            if stop_script.exists():
                try:
                    result = _run_plugin_script(
                        argv=['bash', str(stop_script)],
                        cwd=str(plugin_dir),
                        timeout=30,
                    )

                    # Even if stop script has issues, check if service is actually stopped
                    time.sleep(1)

                    if not self._check_service_running(metadata):
                        self._plugin_status[plugin_id] = PluginStatus.STOPPED
                        self._save_running()
                        logger.info(f"Plugin stopped: {plugin_id}")
                        return {
                            'success': True,
                            'message': 'Plugin stopped successfully',
                            'output': result.get('stdout', '')
                        }

                    if not result.get('ok'):
                        logger.warning(f"Stop script failed for {plugin_id}: {result.get('error', 'unknown')}, trying port kill")
                except Exception as e:
                    logger.warning(f"Stop script failed for {plugin_id}: {e}, trying port kill")

            # Fallback: kill by port if stop script failed or doesn't exist
            if self._check_service_running(metadata) and metadata.port:
                logger.info(f"Killing {plugin_id} by port {metadata.port}")
                self._kill_by_port(metadata.port)
                time.sleep(1)

            if not self._check_service_running(metadata):
                self._plugin_status[plugin_id] = PluginStatus.STOPPED
                self._save_running()
                logger.info(f"Plugin stopped: {plugin_id}")
                return {'success': True, 'message': 'Plugin stopped successfully'}

            self._plugin_status[plugin_id] = PluginStatus.RUNNING
            return {'success': False, 'error': 'Failed to stop plugin — process still running'}
        finally:
            # Always release the gate, even on error — start the cooldown clock.
            self._gate.release(plugin_id)
    
    def restart_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Restart a plugin by stopping and starting it"""
        stop_result = self.stop_plugin(plugin_id)
        if not stop_result.get('success') and 'already stopped' not in stop_result.get('message', ''):
            return stop_result
        
        time.sleep(1)
        return self.start_plugin(plugin_id)
    
    def enable_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Enable a plugin via the user_enabled overlay (does NOT mutate plugin.json)."""
        if not self.registry.is_registered(plugin_id):
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}

        try:
            self.state_store.set_user_enabled(plugin_id, True)
        except Exception as e:
            logger.error(f"Failed to persist user_enabled for {plugin_id}: {e}")
            return {'success': False, 'error': 'Failed to enable plugin'}

        # Keep the in-memory metadata in sync so any code reading
        # metadata.config.enabled (until those paths are migrated to
        # is_effectively_enabled) sees the new value within this process.
        metadata = self.registry.get_plugin(plugin_id)
        if metadata is not None:
            metadata.config.enabled = True

        self._plugin_status[plugin_id] = PluginStatus.STOPPED
        logger.info(f"Plugin enabled (user pref): {plugin_id}")
        return {'success': True, 'message': 'Plugin enabled'}

    def disable_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Disable a plugin via the user_enabled overlay (stops it first if running)."""
        if not self.registry.is_registered(plugin_id):
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}

        # Stop if running — same behavior as before; intuitive UX.
        if self._plugin_status.get(plugin_id) == PluginStatus.RUNNING:
            self.stop_plugin(plugin_id)

        # Special case: Ollama is a system-level service that the plugin
        # manager doesn't physically start/stop, but it can hold gigabytes of
        # VRAM via loaded models. When the user disables the Ollama plugin we
        # unload everything from Ollama's memory so the toggle actually frees
        # GPU. The Ollama daemon itself stays running (system service) — only
        # the loaded models go.
        if plugin_id == "ollama":
            self._unload_all_ollama_models()

        try:
            self.state_store.set_user_enabled(plugin_id, False)
        except Exception as e:
            logger.error(f"Failed to persist user_enabled for {plugin_id}: {e}")
            return {'success': False, 'error': 'Failed to disable plugin'}

        metadata = self.registry.get_plugin(plugin_id)
        if metadata is not None:
            metadata.config.enabled = False

        self._plugin_status[plugin_id] = PluginStatus.DISABLED
        logger.info(f"Plugin disabled (user pref): {plugin_id}")
        return {'success': True, 'message': 'Plugin disabled'}

    def _unload_all_ollama_models(self) -> None:
        """Unload every model currently loaded in Ollama memory.

        Used when the user disables the Ollama plugin from /plugins. Best-
        effort: any model that fails to unload is logged and skipped. Imports
        live inside the method so `plugin_manager` doesn't pull `model_api`
        at module import time (avoids circular-import risk during boot).
        """
        try:
            from backend.api.model_api import get_loaded_models, unload_model_from_ollama
        except Exception as e:
            logger.warning(f"Could not import Ollama unload helpers: {e}")
            return

        loaded = get_loaded_models() or []
        if not loaded:
            logger.info("Ollama disable: no models currently loaded; nothing to unload")
            return

        for m in loaded:
            name = m.get("name") or m.get("model")
            if not name:
                continue
            try:
                unloaded = unload_model_from_ollama(name)
                if unloaded:
                    logger.info(f"Ollama disable: unloaded {name}")
                else:
                    logger.warning(f"Ollama disable: unload of {name} reported failure")
            except Exception as e:
                logger.warning(f"Ollama disable: error unloading {name}: {e}")
    
    def health_check(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get health status of a plugin.
        
        Args:
            plugin_id: Plugin ID
            
        Returns:
            Health status dictionary
        """
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'status': 'unknown', 'error': 'Plugin not found'}
        
        if not metadata.config.enabled:
            return {'status': 'disabled', 'enabled': False}
        
        if metadata.type == 'service':
            health_endpoint = metadata.endpoints.get('health', '/health')
            service_url = metadata.config.service_url
            
            if not service_url and metadata.port:
                service_url = f"http://localhost:{metadata.port}"
            
            if not service_url:
                return {'status': 'error', 'error': 'No service URL configured'}
            
            try:
                url = f"{service_url.rstrip('/')}{health_endpoint}"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    data['plugin_id'] = plugin_id
                    return data
                else:
                    return {
                        'status': 'unhealthy',
                        'http_status': response.status_code,
                        'plugin_id': plugin_id
                    }
            except requests.exceptions.ConnectionError:
                return {'status': 'stopped', 'error': 'Service not running'}
            except Exception as e:
                return {'status': 'error', 'error': str(e)}
        
        return {'status': 'unknown', 'type': metadata.type}
    
    def get_plugin_info(self, plugin_id: str) -> Dict[str, Any]:
        """Get comprehensive plugin information"""
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'error': f'Plugin not found: {plugin_id}'}
        
        plugin_dir = self.registry.get_plugin_dir(plugin_id)
        status = self._plugin_status.get(plugin_id, PluginStatus.UNKNOWN)
        
        info = metadata.to_dict()
        info['status'] = status.value
        info['running'] = status == PluginStatus.RUNNING
        info['plugin_dir'] = str(plugin_dir) if plugin_dir else None
        
        # Add health info if running
        if status == PluginStatus.RUNNING:
            health = self.health_check(plugin_id)
            info['health'] = health
        
        return info
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all plugins with their current status and gate cooldown."""
        self._refresh_status()

        result = []
        for plugin_info in self.registry.list_plugins():
            plugin_id = plugin_info['id']
            status = self._plugin_status.get(plugin_id, PluginStatus.UNKNOWN)
            plugin_info['status'] = status.value
            plugin_info['running'] = status == PluginStatus.RUNNING
            # Round to 1 decimal so the frontend doesn't get noisy fractional updates
            plugin_info['cooldown_remaining'] = round(self._gate.cooldown_remaining(plugin_id), 1)
            result.append(plugin_info)

        return result


# Global manager instance
_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the global plugin manager instance"""
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
