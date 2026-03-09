"""
GPU Resource API - Endpoints for GPU resource management and monitoring.

Provides status information and manual control over GPU resource allocation
between Ollama (LLM/RAG) and CogVideoX (video generation).
"""

import logging
from flask import Blueprint, request

from backend.utils.response_utils import success_response, error_response
from backend.services.gpu_resource_coordinator import get_gpu_coordinator
from backend.cuda_config import check_system_optimizations

logger = logging.getLogger(__name__)

gpu_bp = Blueprint("gpu_api", __name__, url_prefix="/api/gpu")


@gpu_bp.route("/status", methods=["GET"])
def get_gpu_status():
    """
    Get current GPU resource status.

    Returns:
        - owner: Current lock owner (none, ollama, video_generation)
        - available: Whether GPU is available for new work
        - ollama_running: Whether Ollama service is running
        - lock_info: Details about current lock if any
        - system_optimizations: Status of persistence mode, power limits, etc.
    """
    try:
        coordinator = get_gpu_coordinator()
        status = coordinator.get_gpu_status()

        # Add system optimization status
        status["system_optimizations"] = check_system_optimizations()

        return success_response(status)
    except Exception as e:
        logger.error(f"Error getting GPU status: {e}")
        return error_response(str(e), 500)


@gpu_bp.route("/lock/release", methods=["POST"])
def release_gpu_lock():
    """
    Release GPU lock after video generation.

    Body (optional JSON):
        - restart_ollama: bool (default: true) - Whether to restart Ollama after release
    """
    try:
        data = request.get_json(silent=True) or {}
        restart_ollama = data.get("restart_ollama", True)

        coordinator = get_gpu_coordinator()
        result = coordinator.release_video_generation_lock(restart_ollama=restart_ollama)

        if result.get("success"):
            return success_response(result)
        else:
            return error_response(result.get("error", "Unknown error"), 400)

    except Exception as e:
        logger.error(f"Error releasing GPU lock: {e}")
        return error_response(str(e), 500)


@gpu_bp.route("/lock/force-release", methods=["POST"])
def force_release_gpu_lock():
    """
    Force release GPU lock (admin operation).

    Use with caution - may interrupt running video generation operations.

    Body (optional JSON):
        - restart_ollama: bool (default: true) - Whether to restart Ollama after release
    """
    try:
        data = request.get_json(silent=True) or {}
        restart_ollama = data.get("restart_ollama", True)

        coordinator = get_gpu_coordinator()
        result = coordinator.force_release_lock(restart_ollama=restart_ollama)

        return success_response(result)

    except Exception as e:
        logger.error(f"Error force-releasing GPU lock: {e}")
        return error_response(str(e), 500)


@gpu_bp.route("/ollama/start", methods=["POST"])
def start_ollama():
    """
    Manually start Ollama service.

    Will fail if GPU is currently locked by video generation.
    """
    try:
        coordinator = get_gpu_coordinator()

        # Check if GPU is locked
        status = coordinator.get_gpu_status()
        if not status.get("available"):
            return error_response(
                f"Cannot start Ollama - GPU locked by {status.get('owner')}",
                409
            )

        success = coordinator._start_ollama()
        if success:
            return success_response({"message": "Ollama started successfully"})
        else:
            return error_response("Failed to start Ollama", 500)

    except Exception as e:
        logger.error(f"Error starting Ollama: {e}")
        return error_response(str(e), 500)


@gpu_bp.route("/ollama/stop", methods=["POST"])
def stop_ollama():
    """Manually stop Ollama service."""
    try:
        coordinator = get_gpu_coordinator()
        success = coordinator._stop_ollama()

        if success:
            return success_response({"message": "Ollama stopped successfully"})
        else:
            return error_response("Failed to stop Ollama", 500)

    except Exception as e:
        logger.error(f"Error stopping Ollama: {e}")
        return error_response(str(e), 500)
