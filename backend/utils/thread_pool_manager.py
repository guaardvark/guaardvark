# backend/utils/thread_pool_manager.py
# Shared global thread pools for efficient resource management
# Replaces per-batch thread pool creation with centralized pool management

import logging
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Dict, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class PoolType(Enum):
    """Thread pool types for different workload categories"""

    CPU_BOUND = "cpu_bound"  # CPU-intensive tasks
    IO_BOUND = "io_bound"  # I/O-intensive tasks (network, disk)
    GPU_BOUND = "gpu_bound"  # GPU tasks (serialized to prevent conflicts)
    MIXED = "mixed"  # Mixed workload


class ThreadPoolManager:
    """
    Centralized thread pool manager for efficient resource allocation.

    Features:
    - Shared global thread pools to avoid per-task creation overhead
    - Dynamic pool sizing based on system resources
    - Workload-aware pool selection
    - GPU task serialization to prevent CUDA context conflicts
    - Automatic pool lifecycle management
    """

    def __init__(self):
        """Initialize the thread pool manager."""
        self._pools: Dict[PoolType, ThreadPoolExecutor] = {}
        self._lock = threading.RLock()
        self._initialized = False

        # Detect system resources
        self._cpu_count = multiprocessing.cpu_count()
        self._gpu_count = self._detect_gpu_count()

        # Dynamic sizing configuration
        self._auto_scale_enabled = False
        self._scale_monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()
        self._task_queues: Dict[PoolType, list] = {}  # Track submitted tasks
        self._load_history: Dict[PoolType, list] = {}  # Track load over time

        logger.info(
            f"ThreadPoolManager: CPU cores={self._cpu_count}, GPUs={self._gpu_count}"
        )

    def _detect_gpu_count(self) -> int:
        """Detect number of available GPUs."""
        try:
            import torch

            if torch.cuda.is_available():
                gpu_count = torch.cuda.device_count()
                logger.info(f"Detected {gpu_count} CUDA GPU(s)")
                return gpu_count
        except ImportError:
            pass

        # Fallback: Check nvidia-smi
        try:
            import subprocess

            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                gpu_count = len(result.stdout.strip().split("\n"))
                logger.info(f"Detected {gpu_count} GPU(s) via nvidia-smi")
                return gpu_count
        except Exception:
            pass

        logger.info("No GPUs detected")
        return 0

    def initialize(self):
        """Initialize all thread pools."""
        with self._lock:
            if self._initialized:
                logger.warning("ThreadPoolManager already initialized")
                return

            # CPU-bound pool: Use number of CPU cores
            cpu_workers = self._cpu_count
            self._pools[PoolType.CPU_BOUND] = ThreadPoolExecutor(
                max_workers=cpu_workers, thread_name_prefix="cpu_pool"
            )
            logger.info(f"Initialized CPU-bound pool with {cpu_workers} workers")

            # I/O-bound pool: Use 2x CPU cores (I/O tasks spend time waiting)
            io_workers = self._cpu_count * 2
            self._pools[PoolType.IO_BOUND] = ThreadPoolExecutor(
                max_workers=io_workers, thread_name_prefix="io_pool"
            )
            logger.info(f"Initialized I/O-bound pool with {io_workers} workers")

            # GPU-bound pool: Use 1 worker per GPU (serialized to prevent CUDA conflicts)
            # If no GPU, use 1 worker for CPU fallback
            gpu_workers = max(1, self._gpu_count)
            self._pools[PoolType.GPU_BOUND] = ThreadPoolExecutor(
                max_workers=gpu_workers, thread_name_prefix="gpu_pool"
            )
            logger.info(f"Initialized GPU-bound pool with {gpu_workers} workers")

            # Mixed workload pool: Balanced configuration
            mixed_workers = max(4, self._cpu_count)
            self._pools[PoolType.MIXED] = ThreadPoolExecutor(
                max_workers=mixed_workers, thread_name_prefix="mixed_pool"
            )
            logger.info(f"Initialized mixed workload pool with {mixed_workers} workers")

            self._initialized = True
            logger.info(" ThreadPoolManager initialization complete")

    def submit(self, pool_type: PoolType, fn: Callable, *args, **kwargs) -> Future:
        """
        Submit a task to the appropriate thread pool.

        Args:
            pool_type: Type of pool to use
            fn: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Future object representing the task

        Raises:
            RuntimeError: If pools not initialized
        """
        if not self._initialized:
            self.initialize()

        with self._lock:
            pool = self._pools.get(pool_type)
            if pool is None:
                raise ValueError(f"Invalid pool type: {pool_type}")

            return pool.submit(fn, *args, **kwargs)

    def get_pool(self, pool_type: PoolType) -> ThreadPoolExecutor:
        """
        Get a specific thread pool for manual management.

        Args:
            pool_type: Type of pool to retrieve

        Returns:
            ThreadPoolExecutor instance

        Raises:
            RuntimeError: If pools not initialized
            ValueError: If invalid pool type
        """
        if not self._initialized:
            self.initialize()

        with self._lock:
            pool = self._pools.get(pool_type)
            if pool is None:
                raise ValueError(f"Invalid pool type: {pool_type}")
            return pool

    def shutdown(self, wait: bool = True):
        """
        Shutdown all thread pools.

        Args:
            wait: If True, wait for all tasks to complete
        """
        with self._lock:
            if not self._initialized:
                return

            logger.info("Shutting down thread pools...")
            for pool_type, pool in self._pools.items():
                try:
                    pool.shutdown(wait=wait)
                    logger.info(f"Shut down {pool_type.value} pool")
                except Exception as e:
                    logger.error(f"Error shutting down {pool_type.value} pool: {e}")

            self._pools.clear()
            self._initialized = False
            logger.info(" All thread pools shut down")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about thread pools.

        Returns:
            Dictionary with pool statistics
        """
        if not self._initialized:
            return {"initialized": False}

        stats = {
            "initialized": True,
            "cpu_count": self._cpu_count,
            "gpu_count": self._gpu_count,
            "pools": {},
        }

        with self._lock:
            for pool_type, pool in self._pools.items():
                stats["pools"][pool_type.value] = {
                    "max_workers": pool._max_workers,
                    "thread_name_prefix": pool._thread_name_prefix,
                }

        return stats

    def resize_pool(self, pool_type: PoolType, new_size: int):
        """
        Resize a thread pool (recreate with new size).

        Args:
            pool_type: Type of pool to resize
            new_size: New number of workers

        Note: This will shutdown the existing pool and create a new one.
              Any pending tasks will be lost.
        """
        if not self._initialized:
            raise RuntimeError("ThreadPoolManager not initialized")

        if new_size < 1:
            raise ValueError("Pool size must be at least 1")

        with self._lock:
            old_pool = self._pools.get(pool_type)
            if old_pool is None:
                raise ValueError(f"Invalid pool type: {pool_type}")

            # Shutdown old pool
            logger.info(
                f"Resizing {pool_type.value} pool from {old_pool._max_workers} to {new_size} workers"
            )
            old_pool.shutdown(wait=True)

            # Create new pool
            new_pool = ThreadPoolExecutor(
                max_workers=new_size, thread_name_prefix=f"{pool_type.value}_pool"
            )
            self._pools[pool_type] = new_pool

            logger.info(f" Resized {pool_type.value} pool to {new_size} workers")

    def enable_auto_scaling(self, check_interval: int = 30):
        """
        Enable automatic pool sizing based on load.

        Args:
            check_interval: Interval in seconds between load checks
        """
        if not self._initialized:
            raise RuntimeError("ThreadPoolManager not initialized")

        if self._auto_scale_enabled:
            logger.warning("Auto-scaling already enabled")
            return

        self._auto_scale_enabled = True
        self._stop_monitoring.clear()

        # Start monitoring thread
        self._scale_monitor_thread = threading.Thread(
            target=self._monitor_and_scale,
            args=(check_interval,),
            daemon=True,
            name="pool_auto_scaler",
        )
        self._scale_monitor_thread.start()

        logger.info(f"✅ Auto-scaling enabled (check interval: {check_interval}s)")

    def disable_auto_scaling(self):
        """Disable automatic pool sizing."""
        if not self._auto_scale_enabled:
            return

        self._auto_scale_enabled = False
        self._stop_monitoring.set()

        if self._scale_monitor_thread:
            self._scale_monitor_thread.join(timeout=5)

        logger.info("Auto-scaling disabled")

    def _monitor_and_scale(self, check_interval: int):
        """
        Monitor pool load and adjust sizes dynamically.

        Args:
            check_interval: Interval between checks in seconds
        """
        import time

        logger.info("Auto-scaling monitor started")

        while not self._stop_monitoring.is_set():
            try:
                # Check load for each pool
                for pool_type in PoolType:
                    if pool_type not in self._pools:
                        continue

                    pool = self._pools[pool_type]
                    current_size = pool._max_workers

                    # Estimate load (this is a simplified heuristic)
                    load_factor = self._estimate_pool_load(pool_type)

                    # Scale up if load is high (>80%)
                    if load_factor > 0.8 and current_size < self._get_max_size(
                        pool_type
                    ):
                        new_size = min(current_size + 2, self._get_max_size(pool_type))
                        logger.info(
                            f"Scaling up {pool_type.value} pool: {current_size} -> {new_size} (load: {load_factor:.0%})"
                        )
                        self.resize_pool(pool_type, new_size)

                    # Scale down if load is low (<30%) and above minimum
                    elif load_factor < 0.3 and current_size > self._get_min_size(
                        pool_type
                    ):
                        new_size = max(current_size - 1, self._get_min_size(pool_type))
                        logger.info(
                            f"Scaling down {pool_type.value} pool: {current_size} -> {new_size} (load: {load_factor:.0%})"
                        )
                        self.resize_pool(pool_type, new_size)

                    # Record load history
                    if pool_type not in self._load_history:
                        self._load_history[pool_type] = []
                    self._load_history[pool_type].append(load_factor)
                    # Keep only last 10 measurements
                    self._load_history[pool_type] = self._load_history[pool_type][-10:]

            except Exception as e:
                logger.error(f"Error in auto-scaling monitor: {e}")

            # Wait for next check
            self._stop_monitoring.wait(timeout=check_interval)

        logger.info("Auto-scaling monitor stopped")

    def _estimate_pool_load(self, pool_type: PoolType) -> float:
        """
        Estimate current load factor for a pool.

        Returns:
            Load factor between 0.0 and 1.0
        """
        pool = self._pools.get(pool_type)
        if not pool:
            return 0.0

        # Return average of historical load or 0.5 as placeholder
        if pool_type in self._load_history and self._load_history[pool_type]:
            return sum(self._load_history[pool_type]) / len(
                self._load_history[pool_type]
            )

        return 0.5  # Neutral load estimate

    def _get_min_size(self, pool_type: PoolType) -> int:
        """Get minimum pool size for a pool type."""
        minimums = {
            PoolType.CPU_BOUND: 2,
            PoolType.IO_BOUND: 4,
            PoolType.GPU_BOUND: 1,
            PoolType.MIXED: 2,
        }
        return minimums.get(pool_type, 1)

    def _get_max_size(self, pool_type: PoolType) -> int:
        """Get maximum pool size for a pool type."""
        maximums = {
            PoolType.CPU_BOUND: self._cpu_count * 2,
            PoolType.IO_BOUND: self._cpu_count * 4,
            PoolType.GPU_BOUND: max(2, self._gpu_count),
            PoolType.MIXED: self._cpu_count * 2,
        }
        return maximums.get(pool_type, self._cpu_count)


# Global singleton instance
_thread_pool_manager: Optional[ThreadPoolManager] = None
_manager_lock = threading.Lock()


def get_thread_pool_manager() -> ThreadPoolManager:
    """
    Get the global thread pool manager instance.

    Returns:
        ThreadPoolManager singleton instance
    """
    global _thread_pool_manager

    if _thread_pool_manager is None:
        with _manager_lock:
            if _thread_pool_manager is None:
                _thread_pool_manager = ThreadPoolManager()
                _thread_pool_manager.initialize()

    return _thread_pool_manager


def submit_cpu_task(fn: Callable, *args, **kwargs) -> Future:
    """
    Submit a CPU-bound task to the global thread pool.

    Args:
        fn: Function to execute
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Future object
    """
    manager = get_thread_pool_manager()
    return manager.submit(PoolType.CPU_BOUND, fn, *args, **kwargs)


def submit_io_task(fn: Callable, *args, **kwargs) -> Future:
    """
    Submit an I/O-bound task to the global thread pool.

    Args:
        fn: Function to execute
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Future object
    """
    manager = get_thread_pool_manager()
    return manager.submit(PoolType.IO_BOUND, fn, *args, **kwargs)


def submit_gpu_task(fn: Callable, *args, **kwargs) -> Future:
    """
    Submit a GPU-bound task to the global thread pool.

    Args:
        fn: Function to execute
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Future object
    """
    manager = get_thread_pool_manager()
    return manager.submit(PoolType.GPU_BOUND, fn, *args, **kwargs)


def shutdown_all_pools(wait: bool = True):
    """
    Shutdown all global thread pools.

    Args:
        wait: If True, wait for all tasks to complete
    """
    global _thread_pool_manager

    if _thread_pool_manager is not None:
        _thread_pool_manager.shutdown(wait=wait)
        _thread_pool_manager = None


__all__ = [
    "PoolType",
    "ThreadPoolManager",
    "get_thread_pool_manager",
    "submit_cpu_task",
    "submit_io_task",
    "submit_gpu_task",
    "shutdown_all_pools",
]
