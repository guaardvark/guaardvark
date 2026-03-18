# backend/api/plugins_api.py
"""Plugin management API endpoints."""

import collections
import logging
from pathlib import Path

from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response
from ..plugins import get_plugin_manager

logger = logging.getLogger(__name__)

plugins_bp = Blueprint("plugins", __name__, url_prefix="/api/plugins")


@plugins_bp.route("", methods=["GET"])
@plugins_bp.route("/", methods=["GET"])
def list_plugins():
    """List all registered plugins with their status."""
    try:
        logger.debug("List plugins endpoint called")
        manager = get_plugin_manager()
        logger.debug(f"Plugin manager retrieved: {manager is not None}")
        
        plugins = manager.list_plugins()
        logger.info(f"Retrieved {len(plugins)} plugins from manager")
        
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
            return success_response(
                data=result,
                message=result.get('message', 'Plugin started')
            )
        else:
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
            return success_response(
                data=result,
                message=result.get('message', 'Plugin stopped')
            )
        else:
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
        return success_response(data={"config": config}, message="Config retrieved")
    except Exception as e:
        logger.error(f"Error getting plugin config: {e}", exc_info=True)
        return error_response(str(e), 500, "PLUGIN_CONFIG_ERROR")


@plugins_bp.route("/<plugin_id>/config", methods=["PUT", "PATCH"])
def update_plugin_config(plugin_id):
    """Update plugin configuration."""
    try:
        data = request.get_json()
        if not data:
            return error_response("No config data provided", 400, "INVALID_REQUEST")
        
        manager = get_plugin_manager()
        success = manager.registry.update_plugin_config(plugin_id, data)
        
        if success:
            return success_response(data={"updated": data}, message="Config updated")
        else:
            return error_response("Failed to update config", 400, "CONFIG_UPDATE_ERROR")
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

        log_file_map = {
            'comfyui': 'comfyui.log',
            'ollama': 'ollama.log',
            'gpu_embedding': 'gpu_embedding_service.log',
            'discord': 'discord_bot.log',
        }

        log_name = log_file_map.get(plugin_id)
        if not log_name:
            return success_response(data={"logs": "", "lines": 0}, message="No log file configured")

        from backend.config import LOG_DIR
        log_path = Path(LOG_DIR) / log_name

        if not log_path.exists():
            return success_response(data={"logs": "", "lines": 0}, message="Log file not found")

        with open(log_path, 'r', errors='replace') as f:
            tail = collections.deque(f, maxlen=lines)

        log_text = ''.join(tail)
        return success_response(
            data={"logs": log_text, "lines": len(tail)},
            message="Logs retrieved"
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
