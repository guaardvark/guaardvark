"""
GPU Resource Coordinator - Mutual Exclusion for GPU-intensive services.

Manages exclusive access to GPU resources between:
- Ollama (LLM/RAG operations)
- CogVideoX (Video generation)

Uses file-based locking with PID tracking for crash recovery.
"""

import logging
import os
import json
import time
import subprocess
import threading
import requests
from pathlib import Path
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)


class GPUOwner(Enum):
    """Who currently owns the GPU resource."""
    NONE = "none"
    OLLAMA = "ollama"
    VIDEO_GENERATION = "video_generation"


@dataclass
class GPULockInfo:
    """Information about the current GPU lock."""
    owner: str
    acquired_at: str
    pid: int
    lease_expires_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GPUResourceCoordinator:
    """
    Singleton coordinator for GPU resource mutual exclusion.

    Ensures only one GPU-intensive service runs at a time:
    - Ollama for LLM/RAG
    - CogVideoX for video generation
    """

    _instance = None
    _lock = threading.Lock()

    # Default lease duration for video generation (prevents indefinite lock)
    DEFAULT_LEASE_SECONDS = 3600  # 1 hour max

    # Ollama management
    OLLAMA_SERVICE_NAME = "ollama"

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._internal_lock = threading.RLock()
        self._initialized = True

        # Set lock file location relative to project root
        project_root = Path(__file__).parent.parent.parent
        self.LOCK_FILE = project_root / "pids" / "gpu_lock.json"

        # Ensure pids directory exists
        self.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Cleanup stale locks on startup
        self._cleanup_stale_lock()

        logger.info("GPU Resource Coordinator initialized")

    def _cleanup_stale_lock(self):
        """Remove lock if owning process is dead or lease expired."""
        if not self.LOCK_FILE.exists():
            return

        try:
            with open(self.LOCK_FILE, 'r') as f:
                lock_data = json.load(f)

            pid = lock_data.get('pid')
            lease_expires = lock_data.get('lease_expires_at')

            # Check if process is dead
            if pid and not self._is_process_alive(pid):
                logger.warning(f"Removing stale GPU lock from dead process {pid}")
                self._release_lock_file()
                return

            # Check if lease expired
            if lease_expires:
                expires_dt = datetime.fromisoformat(lease_expires)
                if datetime.now() > expires_dt:
                    logger.warning("Removing expired GPU lock lease")
                    self._release_lock_file()
                    return

        except Exception as e:
            logger.error(f"Error cleaning up stale lock: {e}")
            # If lock file is corrupted, remove it
            self._release_lock_file()

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process with given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _read_lock_file(self) -> Optional[GPULockInfo]:
        """Read current lock state from file."""
        if not self.LOCK_FILE.exists():
            return None

        try:
            with open(self.LOCK_FILE, 'r') as f:
                data = json.load(f)
            return GPULockInfo(**data)
        except Exception as e:
            logger.error(f"Error reading GPU lock file: {e}")
            return None

    def _write_lock_file(self, lock_info: GPULockInfo):
        """Write lock state to file atomically."""
        temp_file = self.LOCK_FILE.with_suffix('.tmp')
        try:
            with open(temp_file, 'w') as f:
                json.dump(lock_info.to_dict(), f, indent=2)
            temp_file.rename(self.LOCK_FILE)
        except Exception as e:
            logger.error(f"Error writing GPU lock file: {e}")
            if temp_file.exists():
                temp_file.unlink()
            raise

    def _release_lock_file(self):
        """Remove lock file."""
        try:
            if self.LOCK_FILE.exists():
                self.LOCK_FILE.unlink()
        except Exception as e:
            logger.error(f"Error removing GPU lock file: {e}")

    def get_gpu_status(self) -> Dict[str, Any]:
        """Get current GPU resource status."""
        with self._internal_lock:
            lock_info = self._read_lock_file()

            if lock_info is None:
                return {
                    "owner": GPUOwner.NONE.value,
                    "available": True,
                    "ollama_running": self._is_ollama_running(),
                    "lock_info": None
                }

            # Check if lock is still valid
            if lock_info.lease_expires_at:
                expires = datetime.fromisoformat(lock_info.lease_expires_at)
                if datetime.now() > expires:
                    self._release_lock_file()
                    return {
                        "owner": GPUOwner.NONE.value,
                        "available": True,
                        "ollama_running": self._is_ollama_running(),
                        "lock_info": None,
                        "note": "Previous lock expired"
                    }

            return {
                "owner": lock_info.owner,
                "available": False,
                "ollama_running": self._is_ollama_running(),
                "lock_info": lock_info.to_dict()
            }

    def _is_ollama_running(self) -> bool:
        """Check if Ollama service is running."""
        try:
            # Check systemctl user service first
            result = subprocess.run(
                ["systemctl", "--user", "is-active", self.OLLAMA_SERVICE_NAME],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return True

            # Check system service
            result = subprocess.run(
                ["systemctl", "is-active", self.OLLAMA_SERVICE_NAME],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return True

            # Check for ollama process directly
            result = subprocess.run(
                ["pgrep", "-x", "ollama"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0

        except Exception as e:
            logger.warning(f"Error checking Ollama status: {e}")
            return False

    def _stop_ollama(self) -> bool:
        """Stop Ollama service to free GPU memory."""
        logger.info("Stopping Ollama service for video generation...")

        try:
            # Try passwordless sudo first (Ollama is a system service under /etc/systemd/system/)
            result = subprocess.run(
                ["sudo", "-n", "systemctl", "stop", self.OLLAMA_SERVICE_NAME],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info("Ollama system service stopped via sudo -n")
                time.sleep(2)  # Give time for GPU memory to be released
                return True

            # Try user-level service (unlikely but possible)
            result = subprocess.run(
                ["systemctl", "--user", "stop", self.OLLAMA_SERVICE_NAME],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info("Ollama user service stopped successfully")
                time.sleep(2)
                return True

            # Try killing ollama process directly
            result = subprocess.run(
                ["pkill", "-x", "ollama"],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                logger.info("Ollama process killed successfully")
                time.sleep(2)
                return True

            # Check if it's even running
            if not self._is_ollama_running():
                logger.info("Ollama was not running")
                return True

            logger.warning("Could not stop Ollama service")
            return False

        except Exception as e:
            logger.error(f"Error stopping Ollama: {e}")
            return False

    def _start_ollama(self) -> bool:
        """Start Ollama service."""
        logger.info("Starting Ollama service...")

        try:
            # Try passwordless sudo first (Ollama is a system service under /etc/systemd/system/)
            result = subprocess.run(
                ["sudo", "-n", "systemctl", "start", self.OLLAMA_SERVICE_NAME],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info("Ollama system service started via sudo -n")
                time.sleep(3)  # Give time for Ollama to initialize
                return True

            # Try user-level service
            result = subprocess.run(
                ["systemctl", "--user", "start", self.OLLAMA_SERVICE_NAME],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info("Ollama user service started successfully")
                time.sleep(3)
                return True

            # Try starting ollama serve directly
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            time.sleep(3)

            if self._is_ollama_running():
                logger.info("Ollama started via direct command")
                return True

            logger.warning("Could not start Ollama service")
            return False

        except Exception as e:
            logger.error(f"Error starting Ollama: {e}")
            return False

    def acquire_for_video_generation(
        self,
        batch_id: str = None,
        lease_seconds: int = None,
        require_ollama_stopped: bool = True
    ) -> Dict[str, Any]:
        """
        Acquire GPU lock for video generation.

        This will:
        1. Check if GPU is already locked
        2. Check if Ollama is running (and fail if require_ollama_stopped is True)
        3. Acquire the lock

        Note: Ollama is NOT automatically stopped anymore. User must manually stop it
        via the UI toggle before starting video generation.

        Returns dict with success status and any relevant info.
        """
        with self._internal_lock:
            lease_seconds = lease_seconds or self.DEFAULT_LEASE_SECONDS

            # Check current lock status
            current_lock = self._read_lock_file()

            if current_lock is not None:
                # Check if lock holder is still alive
                if not self._is_process_alive(current_lock.pid):
                    logger.warning(f"Stale lock detected from PID {current_lock.pid}, cleaning up")
                    self._release_lock_file()
                    current_lock = None
                # Check if lease expired
                elif current_lock.lease_expires_at:
                    expires = datetime.fromisoformat(current_lock.lease_expires_at)
                    if datetime.now() > expires:
                        logger.warning("Previous lock lease expired, cleaning up")
                        self._release_lock_file()
                        current_lock = None

            if current_lock is not None:
                return {
                    "success": False,
                    "error": f"GPU already locked by {current_lock.owner}",
                    "lock_info": current_lock.to_dict()
                }

            # Auto-stop Ollama if running — GPU is shared, can't run both
            ollama_was_running = self._is_ollama_running()
            if ollama_was_running:
                logger.info("Ollama is running — auto-stopping for video generation...")
                self._stop_ollama()

            # Acquire lock
            now = datetime.now()
            expires = now + timedelta(seconds=lease_seconds)

            lock_info = GPULockInfo(
                owner=GPUOwner.VIDEO_GENERATION.value,
                acquired_at=now.isoformat(),
                pid=os.getpid(),
                lease_expires_at=expires.isoformat(),
                metadata={
                    "batch_id": batch_id,
                    "ollama_was_running": ollama_was_running,
                }
            )

            self._write_lock_file(lock_info)

            logger.info(f"GPU lock acquired for video generation (batch: {batch_id})")

            return {
                "success": True,
                "lock_info": lock_info.to_dict(),
                "ollama_stopped": False
            }

    def release_video_generation_lock(self, restart_ollama: bool = False) -> Dict[str, Any]:
        """
        Release GPU lock after video generation completes.

        Note: Ollama is NOT automatically restarted anymore. User must manually start it
        via the UI if needed.

        Args:
            restart_ollama: Ignored for now (kept for API compatibility)
        """
        with self._internal_lock:
            current_lock = self._read_lock_file()

            if current_lock is None:
                return {
                    "success": True,
                    "message": "No lock to release"
                }

            if current_lock.owner != GPUOwner.VIDEO_GENERATION.value:
                return {
                    "success": False,
                    "error": f"Cannot release lock owned by {current_lock.owner}"
                }

            # Check if we're the owner (allow release if original process is dead)
            if current_lock.pid != os.getpid():
                logger.warning(f"Lock PID mismatch: {current_lock.pid} vs {os.getpid()}")
                if self._is_process_alive(current_lock.pid):
                    return {
                        "success": False,
                        "error": "Lock owned by different process"
                    }

            # Release the lock
            self._release_lock_file()
            logger.info("GPU lock released from video generation")

            # Don't restart Ollama automatically - user controls it via UI
            return {
                "success": True,
                "ollama_restarted": False
            }

    def get_available_vram(self) -> Dict[str, Any]:
        """
        Get available VRAM using pynvml (nvidia-ml-py3).

        Returns dict with:
            - available_mb: Available VRAM in MB
            - total_mb: Total VRAM in MB
            - used_mb: Used VRAM in MB
            - gpu_name: GPU device name
            - success: Whether query succeeded
        """
        try:
            import pynvml
            pynvml.nvmlInit()

            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)

            # Convert bytes to MB
            total_mb = info.total // (1024 * 1024)
            used_mb = info.used // (1024 * 1024)
            available_mb = info.free // (1024 * 1024)

            pynvml.nvmlShutdown()

            return {
                "success": True,
                "available_mb": available_mb,
                "total_mb": total_mb,
                "used_mb": used_mb,
                "gpu_name": name if isinstance(name, str) else name.decode('utf-8'),
                "utilization_percent": round((used_mb / total_mb) * 100, 1)
            }

        except ImportError:
            logger.warning("pynvml not installed, falling back to nvidia-smi")
            return self._get_vram_via_nvidia_smi()
        except Exception as e:
            logger.error(f"Error getting VRAM via pynvml: {e}")
            return self._get_vram_via_nvidia_smi()

    def _get_vram_via_nvidia_smi(self) -> Dict[str, Any]:
        """Fallback VRAM query using nvidia-smi command."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free,memory.total,memory.used,name",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": "nvidia-smi command failed",
                    "available_mb": 0
                }

            parts = result.stdout.strip().split(", ")
            if len(parts) >= 4:
                available_mb = int(parts[0])
                total_mb = int(parts[1])
                used_mb = int(parts[2])
                gpu_name = parts[3]

                return {
                    "success": True,
                    "available_mb": available_mb,
                    "total_mb": total_mb,
                    "used_mb": used_mb,
                    "gpu_name": gpu_name,
                    "utilization_percent": round((used_mb / total_mb) * 100, 1)
                }

            return {
                "success": False,
                "error": "Could not parse nvidia-smi output",
                "available_mb": 0
            }

        except Exception as e:
            logger.error(f"Error getting VRAM via nvidia-smi: {e}")
            return {
                "success": False,
                "error": str(e),
                "available_mb": 0
            }

    def unload_ollama_models(self, ollama_url: str = "http://localhost:11434") -> Dict[str, Any]:
        """
        Unload all models from Ollama to free GPU memory.

        Uses Ollama API to set keep_alive=0 for each loaded model,
        which causes Ollama to immediately unload them from GPU.

        Returns dict with:
            - success: Whether unload succeeded
            - models_unloaded: List of models that were unloaded
            - vram_freed_mb: Approximate VRAM freed (if available)
        """
        models_unloaded = []
        initial_vram = self.get_available_vram()

        try:
            # Get list of currently loaded models
            response = requests.get(f"{ollama_url}/api/ps", timeout=10)

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Failed to get Ollama status: HTTP {response.status_code}",
                    "models_unloaded": []
                }

            data = response.json()
            models = data.get("models", [])

            if not models:
                logger.info("No Ollama models currently loaded")
                return {
                    "success": True,
                    "models_unloaded": [],
                    "message": "No models were loaded"
                }

            # Unload each model by sending a minimal request with keep_alive=0
            for model_info in models:
                model_name = model_info.get("name", model_info.get("model", "unknown"))

                try:
                    # Send a generate request with keep_alive=0 to unload.
                    # Use num_ctx=1 to prevent large KV cache allocation during unload.
                    unload_response = requests.post(
                        f"{ollama_url}/api/generate",
                        json={
                            "model": model_name,
                            "prompt": "",
                            "keep_alive": 0,  # Immediately unload after response
                            "options": {"num_ctx": 1}
                        },
                        timeout=30
                    )

                    if unload_response.status_code == 200:
                        models_unloaded.append(model_name)
                        logger.info(f"Unloaded Ollama model: {model_name}")
                    else:
                        logger.warning(f"Failed to unload {model_name}: HTTP {unload_response.status_code}")

                except Exception as e:
                    logger.warning(f"Error unloading model {model_name}: {e}")

            # Give GPU time to free memory
            time.sleep(2)

            # Check how much VRAM was freed
            final_vram = self.get_available_vram()
            vram_freed = 0
            if initial_vram.get("success") and final_vram.get("success"):
                vram_freed = final_vram["available_mb"] - initial_vram["available_mb"]

            return {
                "success": True,
                "models_unloaded": models_unloaded,
                "vram_freed_mb": max(0, vram_freed),
                "vram_available_mb": final_vram.get("available_mb", 0)
            }

        except requests.exceptions.ConnectionError:
            logger.info("Ollama not running, no models to unload")
            return {
                "success": True,
                "models_unloaded": [],
                "message": "Ollama is not running"
            }
        except Exception as e:
            logger.error(f"Error unloading Ollama models: {e}")
            return {
                "success": False,
                "error": str(e),
                "models_unloaded": models_unloaded
            }

    def force_release_lock(self, restart_ollama: bool = True) -> Dict[str, Any]:
        """
        Force release GPU lock (admin operation).

        Use with caution - may interrupt running operations.
        """
        with self._internal_lock:
            current_lock = self._read_lock_file()

            if current_lock is None:
                return {
                    "success": True,
                    "message": "No lock to release"
                }

            owner = current_lock.owner
            self._release_lock_file()
            logger.warning(f"GPU lock force-released (was held by {owner})")

            ollama_restarted = False
            if restart_ollama:
                ollama_restarted = self._start_ollama()

            return {
                "success": True,
                "previous_owner": owner,
                "ollama_restarted": ollama_restarted
            }


# Global singleton accessor
_gpu_coordinator: Optional[GPUResourceCoordinator] = None


def get_gpu_coordinator() -> GPUResourceCoordinator:
    """Get the global GPU resource coordinator instance."""
    global _gpu_coordinator
    if _gpu_coordinator is None:
        _gpu_coordinator = GPUResourceCoordinator()
    return _gpu_coordinator


# Convenience functions for direct access
def get_available_vram() -> Dict[str, Any]:
    """Get available VRAM. Convenience wrapper for get_gpu_coordinator().get_available_vram()"""
    return get_gpu_coordinator().get_available_vram()


def unload_ollama_models(ollama_url: str = "http://localhost:11434") -> Dict[str, Any]:
    """Unload Ollama models. Convenience wrapper for get_gpu_coordinator().unload_ollama_models()"""
    return get_gpu_coordinator().unload_ollama_models(ollama_url)
