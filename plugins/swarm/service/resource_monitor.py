"""
Resource Monitor for Swarm Orchestrator.

Tracks system load (CPU, RAM, and GPU VRAM) to decide if new
agents should be spawned or if the swarm should be throttled.
"""

import logging
import os
import psutil
import shutil
import time
from typing import Dict, Any

logger = logging.getLogger("swarm.resource_monitor")

class ResourceMonitor:
    """
    Tracks local system resources to prevent over-subscription
    during parallel agent execution.
    """

    def __init__(self, 
                 max_cpu_percent: float = 85.0, 
                 max_ram_percent: float = 90.0,
                 min_vram_mb: int = 500):
        self.max_cpu_percent = max_cpu_percent
        self.max_ram_percent = max_ram_percent
        self.min_vram_mb = min_vram_mb
        
    def get_system_stats(self) -> Dict[str, Any]:
        """Get current system resource utilization."""
        stats = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram_percent": psutil.virtual_memory().percent,
            "ram_available_mb": psutil.virtual_memory().available / (1024 * 1024),
            "load_avg": os.getloadavg() if hasattr(os, "getloadavg") else (0,0,0),
            "disk_free_gb": shutil.disk_usage("/").free / (1024**3),
            "timestamp": time.time()
        }
        
        # Optional GPU stats if nvidia-smi is available
        stats["vram_free_mb"] = self._get_free_vram()
        
        return stats

    def is_healthy(self) -> bool:
        """Check if system resources are within safe limits for spawning new agents."""
        stats = self.get_system_stats()
        
        if stats["cpu_percent"] > self.max_cpu_percent:
            logger.warning(f"Throttling: CPU usage too high ({stats['cpu_percent']}%)")
            return False
            
        if stats["ram_percent"] > self.max_ram_percent:
            logger.warning(f"Throttling: RAM usage too high ({stats['ram_percent']}%)")
            return False
            
        if stats["vram_free_mb"] is not None and stats["vram_free_mb"] < self.min_vram_mb:
            logger.warning(f"Throttling: VRAM too low ({stats['vram_free_mb']}MB free)")
            return False
            
        return True

    def _get_free_vram(self) -> float | None:
        """Try to get free VRAM via nvidia-smi."""
        try:
            import subprocess
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,nounits,noheader"],
                capture_output=True, text=True, timeout=1
            )
            if res.returncode == 0:
                # might return multiple lines if multiple GPUs
                lines = res.stdout.strip().split("\n")
                if lines:
                    return float(lines[0])
        except Exception:
            pass
        return None

# Global monitor
_monitor = ResourceMonitor()

def get_resource_monitor():
    return _monitor
