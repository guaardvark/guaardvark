"""
Health check utilities for GPU Embedding Service
"""

import logging
import time
import subprocess
from typing import Dict, Any
from .model_loader import is_model_loaded, get_model_name, get_model_info

logger = logging.getLogger(__name__)

# Track service startup time
_start_time = time.time()


def get_uptime_seconds() -> float:
    """Get service uptime in seconds"""
    return time.time() - _start_time


def check_gpu_available() -> Dict[str, Any]:
    """Check if GPU is available and get GPU info"""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total,memory.used', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            # Parse GPU info
            lines = result.stdout.strip().split('\n')
            gpu_info = []
            for line in lines:
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 3:
                    gpu_info.append({
                        "name": parts[0],
                        "memory_total_mb": int(parts[1]) if parts[1].isdigit() else 0,
                        "memory_used_mb": int(parts[2]) if parts[2].isdigit() else 0
                    })
            
            return {
                "available": True,
                "gpus": gpu_info,
                "count": len(gpu_info)
            }
        else:
            return {"available": False, "error": "nvidia-smi returned no output"}
            
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi not found"}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "nvidia-smi timeout"}
    except Exception as e:
        logger.warning(f"GPU check failed: {e}")
        return {"available": False, "error": str(e)}


def get_health_status() -> Dict[str, Any]:
    """
    Get comprehensive health status of the service.
    
    Returns:
        Dictionary with health information
    """
    gpu_status = check_gpu_available()
    model_info = get_model_info()
    
    health = {
        "status": "healthy" if (is_model_loaded() and gpu_status.get("available", False)) else "degraded",
        "gpu_available": gpu_status.get("available", False),
        "model_loaded": is_model_loaded(),
        "model_name": get_model_name(),
        "uptime_seconds": get_uptime_seconds(),
        "gpu_info": gpu_status.get("gpus", []),
        "embed_dim": model_info.get("embed_dim")
    }
    
    # Determine overall status
    if not is_model_loaded():
        health["status"] = "unhealthy"
        health["issues"] = ["Model not loaded"]
    elif not gpu_status.get("available", False):
        health["status"] = "degraded"
        health["issues"] = ["GPU not available - running in CPU mode"]
    
    return health

