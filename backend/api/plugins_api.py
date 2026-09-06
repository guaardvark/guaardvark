# backend/api/plugins_api.py
"""Plugin management API endpoints."""

import collections
import logging
import subprocess
from pathlib import Path

import requests as http_requests
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response
from backend.utils.plugin_secrets import (
    apply_secret_updates,
    enrich_with_secrets,
    secret_field_names,
)
from ..plugins import get_plugin_manager
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path

logger = logging.getLogger(__name__)

plugins_bp = Blueprint("plugins", __name__, url_prefix="/api/plugins")

# Log basenames under LOG_DIR. Order matters — first existing file wins.
# Keep in sync with plugins/*/scripts/start.sh LOG_FILE assignments.
PLUGIN_LOG_CANDIDATES: dict[str, list[str]] = {
    "comfyui": ["comfyui.log"],
    "ollama": ["ollama_serve.log", "ollama.log"],
    "video_editor": ["video_editor.log"],
    "gpu_embedding": ["gpu_embedding_service.log"],
    "discord": ["discord_bot.log"],
    "swarm": ["swarm.log"],
    "vision_pipeline": ["vision_pipeline.log"],
    "upscaling": ["upscaling.log"],
    "audio_foundry": ["audio_foundry.log"],
}


def _tail_text_file(path: Path, lines: int) -> tuple[str, int]:
    with open(path, "r", errors="replace") as f:
        tail = collections.deque(f, maxlen=lines)
    log_text = "".join(tail)
    return log_text, len(tail)


def _read_plugin_log_text(plugin_id: str, lines: int) -> tuple[str, int, str]:
    """Return (log_text, line_count, source_label)."""
    from backend.config import LOG_DIR

    candidates = PLUGIN_LOG_CANDIDATES.get(plugin_id, [f"{plugin_id}.log"])
    log_dir = Path(LOG_DIR)
    for name in candidates:
        try:
            path = contained(log_dir, name)
        except PathEscapesRoot:
            continue
        if path.is_file():
            text, count = _tail_text_file(path, lines)
            return text, count, str(path)

    if plugin_id == "ollama":
        try:
            result = subprocess.run(
                [
                    "journalctl", "-u", "ollama",
                    "-n", str(lines), "--no-pager", "-o", "short-iso",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                rows = result.stdout.splitlines()
                return result.stdout, len(rows), "journalctl:ollama"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    return "", 0, ""


@plugins_bp.route("", methods=["GET"])
@plugins_bp.route("/", methods=["GET"])
def list_plugins():
    """List all registered plugins with their status."""
    try:
        logger.debug("List plugins endpoint called")
        manager = get_plugin_manager()
        logger.debug(f"Plugin manager retrieved: {manager is not None}")
        
        plugins = manager.list_plugins()
        logger.debug(f"Retrieved {len(plugins)} plugins from manager")
        for plugin in plugins:
            enrich_with_secrets(plugin.get("id", ""), plugin)

        return success_response(
            data={
                "plugins": plugins,
                "count": len(plugins)
            },
            message="Plugins retrieved"
        )
    except Exception as e:
        logger.error(f"Error listing plugins: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGINS_LIST_ERROR")


@plugins_bp.route("/<plugin_id>", methods=["GET"])
def get_plugin(plugin_id):
    """Get detailed information about a specific plugin."""
    try:
        manager = get_plugin_manager()
        info = manager.get_plugin_info(plugin_id)
        
        if 'error' in info:
            return error_response(info['error'], 404, "PLUGIN_NOT_FOUND")

        enrich_with_secrets(plugin_id, info)
        return success_response(data=info, message="Plugin info retrieved")
    except Exception as e:
        logger.error(f"Error getting plugin info: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_INFO_ERROR")


@plugins_bp.route("/<plugin_id>/health", methods=["GET"])
def plugin_health(plugin_id):
    """Get health status of a plugin."""
    try:
        manager = get_plugin_manager()
        health = manager.health_check(plugin_id)
        
        return success_response(data=health, message="Health check completed")
    except Exception as e:
        logger.error(f"Error checking plugin health: {e}", exc_info=True)
        return error_response(str(e), 500, "HEALTH_CHECK_ERROR")


@plugins_bp.route("/<plugin_id>/start", methods=["POST"])
def start_plugin(plugin_id):
    """Start a plugin."""
    try:
        manager = get_plugin_manager()
        result = manager.start_plugin(plugin_id)

        if result.get('success'):
            try:
                from backend.services.plugin_bridge import mark_user_controlled
                mark_user_controlled(plugin_id)
            except Exception:
                pass
            return success_response(
                data=result,
                message=result.get('message', 'Plugin started')
            )
        # Gate rejections (cooldown / GPU exclusivity) are NOT errors — they're
        # expected backpressure. Return 200 with success=false so the frontend
        # can show a friendly snackbar instead of an error toast.
        if result.get('gated'):
            return success_response(
                data=result,
                message=result.get('error', 'Plugin operation rate-limited')
            )
        return error_response(
            result.get('error', 'Failed to start plugin'),
            400,
            "PLUGIN_START_ERROR"
        )
    except Exception as e:
        logger.error(f"Error starting plugin: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_START_ERROR")


@plugins_bp.route("/<plugin_id>/stop", methods=["POST"])
def stop_plugin(plugin_id):
    """Stop a plugin."""
    try:
        manager = get_plugin_manager()
        result = manager.stop_plugin(plugin_id)

        if result.get('success'):
            try:
                from backend.services.plugin_bridge import mark_user_released
                mark_user_released(plugin_id)
            except Exception:
                pass
            return success_response(
                data=result,
                message=result.get('message', 'Plugin stopped')
            )
        if result.get('gated'):
            return success_response(
                data=result,
                message=result.get('error', 'Plugin operation rate-limited')
            )
        return error_response(
            result.get('error', 'Failed to stop plugin'),
            400,
            "PLUGIN_STOP_ERROR"
        )
    except Exception as e:
        logger.error(f"Error stopping plugin: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_STOP_ERROR")


@plugins_bp.route("/<plugin_id>/restart", methods=["POST"])
def restart_plugin(plugin_id):
    """Restart a plugin."""
    try:
        manager = get_plugin_manager()
        result = manager.restart_plugin(plugin_id)
        
        if result.get('success'):
            return success_response(
                data=result,
                message=result.get('message', 'Plugin restarted')
            )
        else:
            return error_response(
                result.get('error', 'Failed to restart plugin'),
                400,
                "PLUGIN_RESTART_ERROR"
            )
    except Exception as e:
        logger.error(f"Error restarting plugin: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_RESTART_ERROR")


@plugins_bp.route("/<plugin_id>/enable", methods=["POST"])
def enable_plugin(plugin_id):
    """Enable a plugin."""
    try:
        manager = get_plugin_manager()
        result = manager.enable_plugin(plugin_id)
        
        if result.get('success'):
            return success_response(
                data=result,
                message=result.get('message', 'Plugin enabled')
            )
        else:
            return error_response(
                result.get('error', 'Failed to enable plugin'),
                400,
                "PLUGIN_ENABLE_ERROR"
            )
    except Exception as e:
        logger.error(f"Error enabling plugin: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_ENABLE_ERROR")


@plugins_bp.route("/<plugin_id>/disable", methods=["POST"])
def disable_plugin(plugin_id):
    """Disable a plugin (stops it first if running)."""
    try:
        manager = get_plugin_manager()
        result = manager.disable_plugin(plugin_id)
        
        if result.get('success'):
            try:
                # A manual disable is an explicit operator decision to keep this
                # plugin OFF — mark it user-controlled so the GPU/route auto-
                # orchestrator does NOT revive it on the next navigation. Using
                # mark_user_released here (the old behavior) handed the plugin
                # straight back to the orchestrator, which re-enabled + re-started
                # Ollama the instant the user opened /chat (the "I turn it off and
                # it relaunches itself" report).
                from backend.services.plugin_bridge import mark_user_controlled
                mark_user_controlled(plugin_id)
            except Exception:
                pass
            return success_response(
                data=result,
                message=result.get('message', 'Plugin disabled')
            )
        else:
            return error_response(
                result.get('error', 'Failed to disable plugin'),
                400,
                "PLUGIN_DISABLE_ERROR"
            )
    except Exception as e:
        logger.error(f"Error disabling plugin: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_DISABLE_ERROR")


@plugins_bp.route("/<plugin_id>/config", methods=["GET"])
def get_plugin_config(plugin_id):
    """Get plugin configuration."""
    try:
        manager = get_plugin_manager()
        info = manager.get_plugin_info(plugin_id)
        
        if 'error' in info:
            return error_response(info['error'], 404, "PLUGIN_NOT_FOUND")
        
        config = info.get('config', {})
        payload = {"config": config}
        enrich_with_secrets(plugin_id, payload)
        return success_response(data=payload, message="Config retrieved")
    except Exception as e:
        logger.error(f"Error getting plugin config: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_CONFIG_ERROR")


@plugins_bp.route("/<plugin_id>/config", methods=["PUT", "PATCH"])
def update_plugin_config(plugin_id):
    """Update plugin configuration.

    Secret fields (e.g. discord bot_token) are stripped and written to `.env`
    via the portable-env allowlist — never into plugin.json.
    """
    try:
        data = request.get_json()
        if not data:
            return error_response("No config data provided", 400, "INVALID_REQUEST")

        # Work on a shallow copy so we can strip secrets safely.
        updates = dict(data)
        secret_result = apply_secret_updates(plugin_id, updates)
        # env_result is present-but-None whenever no secret was written (i.e.
        # every plugin except discord, and discord itself when the token field
        # is untouched). dict.get's default only applies to a MISSING key, so
        # the old `.get("env_result", {})` handed back None and every config
        # save raised AttributeError -> 500.
        env_result = secret_result.get("env_result") or {}
        if env_result.get("error"):
            return error_response(
                f"Failed to save secret: {env_result['error']}",
                500,
                "PLUGIN_SECRET_WRITE_ERROR",
            )

        manager = get_plugin_manager()
        # Drop any residual secret keys that might have been nested oddly —
        # apply_secret_updates already pops known fields; this is belt+suspenders.
        for field in secret_field_names(plugin_id):
            updates.pop(field, None)

        manifest_updated = False
        if updates:
            success = manager.registry.update_plugin_config(plugin_id, updates)
            if not success:
                return error_response("Failed to update config", 400, "CONFIG_UPDATE_ERROR")
            manifest_updated = True
        elif not secret_result.get("updated") and not secret_result.get("skipped_empty"):
            # Nothing to do at all (no secrets, no manifest keys).
            return error_response("No config data provided", 400, "INVALID_REQUEST")

        restarted = False
        restart_result = None
        if secret_result.get("updated"):
            # Token change only takes effect after the bot reloads env.
            try:
                status = manager.get_status(plugin_id)
                from backend.plugins.plugin_base import PluginStatus
                if status == PluginStatus.RUNNING:
                    restart_result = manager.restart_plugin(plugin_id)
                    restarted = bool(restart_result.get("success"))
            except Exception as e:
                logger.warning(
                    "Secret updated for %s but restart failed: %s", plugin_id, e
                )

        message = "Config updated"
        if secret_result.get("updated"):
            message = (
                "Token saved and plugin restarted"
                if restarted
                else "Token saved — restart the plugin to apply"
            )
        elif secret_result.get("skipped_empty") and not manifest_updated:
            message = "No changes (token left blank)"

        secrets_status = {}
        if secret_field_names(plugin_id):
            secrets_status = enrich_with_secrets(plugin_id, {}).get("secrets", {})

        return success_response(
            data={
                "updated": updates,
                "secrets_updated": secret_result.get("updated", []),
                "restarted": restarted,
                "restart": restart_result,
                "secrets": secrets_status,
            },
            message=message,
        )
    except Exception as e:
        logger.error(f"Error updating plugin config: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_CONFIG_ERROR")


@plugins_bp.route("/refresh", methods=["POST"])
def refresh_plugins():
    """Refresh plugin registry by rescanning plugins directory."""
    try:
        manager = get_plugin_manager()
        discovered = manager.registry.refresh()
        manager._init_plugin_status()  # Reinitialize status
        
        return success_response(
            data={
                "discovered": discovered,
                "count": len(discovered)
            },
            message="Plugins refreshed"
        )
    except Exception as e:
        logger.error(f"Error refreshing plugins: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGINS_REFRESH_ERROR")


@plugins_bp.route("/orchestrator/state", methods=["GET"])
def plugin_orchestrator_state():
    """Auto-orchestrator claims and last route (for Plugins UI / debugging)."""
    try:
        from backend.services.plugin_bridge import get_orchestrator_state
        return success_response(data=get_orchestrator_state(), message="Plugin orchestrator state")
    except Exception as e:
        logger.error(f"Error reading orchestrator state: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_ORCHESTRATOR_STATE_ERROR")


@plugins_bp.route("/status", methods=["GET"])
def get_all_status():
    """Get status of all plugins."""
    try:
        manager = get_plugin_manager()
        status = manager.get_all_status()
        
        return success_response(data={"status": status}, message="Status retrieved")
    except Exception as e:
        logger.error(f"Error getting plugin status: {e}", exc_info=True)
        return error_response(str(e), 500, "STATUS_ERROR")


@plugins_bp.route("/<plugin_id>/logs", methods=["GET"])
def get_plugin_logs(plugin_id):
    """Get recent log output for a plugin."""
    try:
        lines = int(request.args.get('lines', 100))
        lines = min(lines, 500)

        log_text, line_count, source = _read_plugin_log_text(plugin_id, lines)
        if not log_text:
            if plugin_id not in PLUGIN_LOG_CANDIDATES:
                return success_response(
                    data={"logs": "", "lines": 0},
                    message="No log file configured",
                )
            return success_response(
                data={"logs": "", "lines": 0},
                message="Log file not found",
            )

        return success_response(
            data={"logs": log_text, "lines": line_count, "source": source},
            message="Logs retrieved",
        )
    except Exception as e:
        logger.error(f"Error reading plugin logs: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_LOGS_ERROR")


@plugins_bp.route("/stats/gpu", methods=["GET"])
def get_live_gpu_stats():
    """Get live GPU memory usage via nvidia-smi."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,memory.free,utilization.gpu,temperature.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            used_mb = int(parts[0])
            total_mb = int(parts[1])
            free_mb = int(parts[2])
            util_pct = int(parts[3])
            temp_c = int(parts[4])
            gpu_name = parts[5]
            return success_response(data={
                "used_mb": used_mb,
                "total_mb": total_mb,
                "free_mb": free_mb,
                "utilization_pct": util_pct,
                "temperature_c": temp_c,
                "gpu_name": gpu_name,
            }, message="Live GPU stats")

        return error_response("nvidia-smi failed", 500)
    except FileNotFoundError:
        return error_response("nvidia-smi not found", 404, "NO_GPU")
    except Exception as e:
        logger.error(f"Error getting live GPU stats: {e}", exc_info=True)
        return error_response(str(e), 500, "GPU_STATS_ERROR")


# --- Vision Pipeline camera proxy routes ---

VISION_PIPELINE_URL = "http://localhost:8201"

@plugins_bp.route("/vision_pipeline/camera/start", methods=["POST"])
def vision_camera_start():
    """Proxy camera start to the Vision Pipeline plugin."""
    try:
        data = request.get_json(silent=True) or {}
        resp = http_requests.post(
            f"{VISION_PIPELINE_URL}/camera/start", json=data, timeout=5
        )
        return resp.json(), resp.status_code
    except http_requests.ConnectionError:
        return error_response("Vision Pipeline not running", 503, "PLUGIN_OFFLINE")
    except Exception as e:
        return error_response(str(e), 500, "CAMERA_START_ERROR")


@plugins_bp.route("/vision_pipeline/camera/stop", methods=["POST"])
def vision_camera_stop():
    """Proxy camera stop to the Vision Pipeline plugin."""
    try:
        resp = http_requests.post(
            f"{VISION_PIPELINE_URL}/camera/stop", timeout=5
        )
        return resp.json(), resp.status_code
    except http_requests.ConnectionError:
        return error_response("Vision Pipeline not running", 503, "PLUGIN_OFFLINE")
    except Exception as e:
        return error_response(str(e), 500, "CAMERA_STOP_ERROR")


@plugins_bp.route("/vision_pipeline/camera/status", methods=["GET"])
def vision_camera_status():
    """Proxy camera status from the Vision Pipeline plugin."""
    try:
        resp = http_requests.get(
            f"{VISION_PIPELINE_URL}/camera/status", timeout=5
        )
        return resp.json(), resp.status_code
    except http_requests.ConnectionError:
        return error_response("Vision Pipeline not running", 503, "PLUGIN_OFFLINE")
    except Exception as e:
        return error_response(str(e), 500, "CAMERA_STATUS_ERROR")
