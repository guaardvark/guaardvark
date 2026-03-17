import torch
import os
import subprocess
import logging

logger = logging.getLogger(__name__)


def check_system_optimizations() -> dict:
    """
    Retrieves system optimizations via nvidia-smi.

    Returns:
        dict: A dictionary containing system optimizations.
    """
    optimizations = {
        "persistence_mode": "Unknown",
        "power_limit_w": None,
        "default_power_limit_w": None,
        "high_performance_mode": False,
    }

    try:
        # Run nvidia-smi command to retrieve system optimizations
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=persistence_mode,power.limit,power.default_limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            # Parse output to extract system optimizations
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 3:
                optimizations["persistence_mode"] = parts[0]
                try:
                    optimizations["power_limit_w"] = float(parts[1])
                    optimizations["default_power_limit_w"] = float(parts[2])
                    # Check if power limit is high enough to enable high performance mode
                    if optimizations["power_limit_w"] >= 280:
                        optimizations["high_performance_mode"] = True
                except (ValueError, IndexError):
                    pass
    except subprocess.SubprocessError as e:
        logger.debug(f"Could not check system optimizations via nvidia-smi: {e}")

    return optimizations


def configure_cuda_optimizations(verbose: bool = True) -> dict:
    """
    Configures CUDA optimizations for Guaardvark.

    Args:
        verbose (bool, optional): Whether to print verbose output. Defaults to True.

    Returns:
        dict: A dictionary containing configuration status.
    """
    if not torch.cuda.is_available():
        if verbose:
            print("CUDA not available - skipping GPU optimizations")
        return {"cuda_available": False}

    if verbose:
        print("=" * 60)
        print("Configuring CUDA Optimizations for Guaardvark")
        print("=" * 60)

    config_status = {
        "cuda_available": True,
        "optimizations_applied": [],
        "gpu_info": {},
        "system_optimizations": check_system_optimizations(),
    }

    # Enable cuDNN benchmark mode
    torch.backends.cudnn.benchmark = True
    config_status["optimizations_applied"].append("cuDNN benchmark mode")
    if verbose:
        print("  ✓ cuDNN benchmark mode enabled")

    # Enable TF32 precision for faster matrix operations
    if hasattr(torch.backends.cuda.matmul, "allow_tf32"):
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        config_status["optimizations_applied"].append("TF32 precision")
        if verbose:
            print("  ✓ TF32 precision enabled for faster matrix operations")

    # Enable cuDNN
    torch.backends.cudnn.enabled = True
    config_status["optimizations_applied"].append("cuDNN enabled")
    if verbose:
        print("  ✓ cuDNN enabled")

    # Retrieve GPU information
    gpu_info = {
        "name": torch.cuda.get_device_name(0),
        "memory_total_gb": round(
            torch.cuda.get_device_properties(0).total_memory / 1024**3, 2
        ),
        "compute_capability": ".".join(map(str, torch.cuda.get_device_capability(0))),
        "multi_processor_count": torch.cuda.get_device_properties(
            0
        ).multi_processor_count,
        "cuda_version": torch.version.cuda,
        "cudnn_version": (
            torch.backends.cudnn.version()
            if torch.backends.cudnn.is_available()
            else None
        ),
    }
    config_status["gpu_info"] = gpu_info

    if verbose:
        print(f"\nGPU Information:")
        print(f"  Device: {gpu_info['name']}")
        print(f"  Memory: {gpu_info['memory_total_gb']} GB")
        print(f"  Compute Capability: {gpu_info['compute_capability']}")
        print(f"  Multiprocessors: {gpu_info['multi_processor_count']}")
        print(f"  CUDA Version: {gpu_info['cuda_version']}")
        print(f"  cuDNN Version: {gpu_info['cudnn_version']}")

        sys_opt = config_status["system_optimizations"]
        print(f"\nSystem Optimizations:")
        print(f"  Persistence Mode: {sys_opt['persistence_mode']}")
        print(
            f"  Power Limit: {sys_opt['power_limit_w']}W (Default: {sys_opt['default_power_limit_w']}W)"
        )
        if sys_opt["high_performance_mode"]:
            print(f"  ✓ High Performance Mode Detected")

    # Warm up GPU
    try:
        dummy = torch.zeros(1, device="cuda")
        _ = dummy + dummy
        del dummy
        torch.cuda.synchronize()
        config_status["optimizations_applied"].append("GPU warmup")
        if verbose:
            print("  ✓ GPU warmed up and ready")
    except RuntimeError as e:
        if verbose:
            print(f"  ⚠ GPU warmup warning: {e}")

    # Retrieve memory allocator configuration
    alloc_conf = os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "Not set")
    if verbose:
        print(f"\nMemory Allocator Config: {alloc_conf}")

    config_status["memory_allocator_conf"] = alloc_conf

    if verbose:
        print("\n" + "=" * 60)
        print("CUDA optimizations applied successfully!")
        print("=" * 60 + "\n")

    return config_status


def get_gpu_memory_info() -> dict:
    """
    Retrieves GPU memory information.

    Returns:
        dict: A dictionary containing GPU memory information.
    """
    if not torch.cuda.is_available():
        return None

    return {
        "allocated_gb": round(torch.cuda.memory_allocated(0) / 1024**3, 2),
        "reserved_gb": round(torch.cuda.memory_reserved(0) / 1024**3, 2),
        "total_gb": round(
            torch.cuda.get_device_properties(0).total_memory / 1024**3, 2
        ),
    }


def clear_gpu_cache() -> bool:
    """
    Clears GPU cache.

    Returns:
        bool: Whether the cache was cleared successfully.
    """
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        return True
    return False


def enable_mixed_precision() -> tuple:
    """
    Enables mixed precision training.

    Returns:
        tuple: A tuple containing autocast and GradScaler objects.
    """
    if not torch.cuda.is_available():
        return None, None

    from torch.cuda.amp import autocast, GradScaler

    return autocast, GradScaler


def get_optimal_batch_size(
    model_memory_gb: float, available_memory_gb: float = None
) -> int:
    """
    Estimates optimal batch size based on model size and available memory.

    Args:
        model_memory_gb (float): Model memory size in GB.
        available_memory_gb (float, optional): Available memory size in GB. Defaults to None.

    Returns:
        int: Optimal batch size.
    """
    if not torch.cuda.is_available():
        return 1

    if available_memory_gb is None:
        total_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
        allocated_gb = torch.cuda.memory_allocated(0) / 1024**3
        available_memory_gb = total_gb - allocated_gb

    usable_memory = available_memory_gb * 0.8

    estimated_batch_size = int(usable_memory / model_memory_gb)

    return max(1, min(estimated_batch_size, 32))


if __name__ == "__main__":
    status = configure_cuda_optimizations(verbose=True)

    print("\nConfiguration Status:")
    print(f"  CUDA Available: {status['cuda_available']}")
    if status["cuda_available"]:
        print(f"  Optimizations Applied: {', '.join(status['optimizations_applied'])}")

        print("\nCurrent GPU Memory:")
        mem_info = get_gpu_memory_info()
        print(f"  Allocated: {mem_info['allocated_gb']} GB")
        print(f"  Reserved: {mem_info['reserved_gb']} GB")
        print(f"  Total: {mem_info['total_gb']} GB")

        print("\nOptimal Batch Size Estimates:")
        for model_size in [0.5, 1.0, 2.0, 4.0, 7.0]:
            batch_size = get_optimal_batch_size(model_size)
            print(f"  {model_size} GB model: batch size ~{batch_size}")
