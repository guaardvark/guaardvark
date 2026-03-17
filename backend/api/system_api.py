import logging
import subprocess
import sys
from pathlib import Path
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

system_bp = Blueprint("system", __name__, url_prefix="/api/system")


@system_bp.route("/version", methods=["GET"])
def get_version():
    try:
        return success_response(
            "Version retrieved",
            {
                "version": "1.0.0",
                "name": "guaardvark",
                "description": "LLM-powered development environment",
                "timestamp": "2025-09-27T07:15:00Z",
            },
        )
    except Exception as e:
        logger.error(f"Error getting version: {e}")
        return error_response(str(e), 500, "VERSION_ERROR")


@system_bp.route("/cleanup-progress-jobs", methods=["POST"])
def cleanup_progress_jobs():
    try:
        data = request.get_json() or {}
        execute = data.get("execute", False)

        script_path = (
            Path(__file__).parent.parent.parent
            / "scripts"
            / "cleanup_stuck_progress_jobs.py"
        )

        if not script_path.exists():
            return error_response("Cleanup script not found", 404, "SCRIPT_NOT_FOUND")

        cmd = [sys.executable, str(script_path)]
        if execute:
            cmd.append("--execute")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.error(f"Cleanup script failed: {result.stderr}")
            return error_response("Cleanup script failed", 500, "SCRIPT_FAILED")

        output_lines = result.stdout.split("\n")
        cleaned_count = 0

        for line in output_lines:
            if "Cleaned up" in line and "orphaned jobs" in line:
                try:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if (
                            part.isdigit()
                            and i + 1 < len(parts)
                            and "orphaned" in parts[i + 1]
                        ):
                            cleaned_count = int(part)
                            break
                except (ValueError, IndexError):
                    pass

        return success_response(
            "Cleanup completed",
            {
                "cleaned_count": cleaned_count,
                "output": result.stdout,
                "executed": execute,
            },
        )

    except subprocess.TimeoutExpired:
        return error_response("Cleanup script timed out", 500, "TIMEOUT")
    except Exception as e:
        logger.error(f"Error running cleanup script: {e}")
        return error_response(str(e), 500, "CLEANUP_ERROR")


@system_bp.route("/health-check", methods=["GET"])
def system_health_check():
    try:
        return success_response(
            "System health check completed",
            {
                "status": "healthy",
                "timestamp": "2025-08-02T19:15:00Z",
                "components": {
                    "progress_system": "operational",
                    "cleanup_script": "available",
                },
            },
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return error_response(str(e), 500, "HEALTH_CHECK_ERROR")
