"""Health and GPU status reporting."""
import logging
import torch
from typing import Any, Dict, Optional

logger = logging.getLogger("upscaling.health")


def get_gpu_info() -> Dict[str, Any]:
    """Return GPU name, total VRAM, and used VRAM."""
    if not torch.cuda.is_available():
        return {"available": False}

    props = torch.cuda.get_device_properties(0)
    free, total = torch.cuda.mem_get_info(0)
    used = total - free
    return {
        "available": True,
        "name": props.name,
        "vram_total_mb": round(total / (1024 * 1024)),
        "vram_used_mb": round(used / (1024 * 1024)),
        "vram_free_mb": round(free / (1024 * 1024)),
        "compute_capability": f"{props.major}.{props.minor}",
        "bf16_supported": torch.cuda.is_bf16_supported(),
    }


def get_health_status(
    model_loaded: Optional[str],
    active_jobs: int,
    compile_enabled: bool,
) -> Dict[str, Any]:
    """Build health response dict."""
    gpu = get_gpu_info()
    status = "healthy" if gpu.get("available") else "degraded"
    return {
        "status": status,
        "gpu": gpu.get("name", "N/A"),
        "vram_total_mb": gpu.get("vram_total_mb", 0),
        "vram_used_mb": gpu.get("vram_used_mb", 0),
        "model_loaded": model_loaded,
        "active_jobs": active_jobs,
        "compile_enabled": compile_enabled,
    }
