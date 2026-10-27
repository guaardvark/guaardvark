"""Single source of truth for hardware-derived provisioning decisions.

Pure functions over the dict produced by HardwareDetector.detect()["gpu"]
(and ram/arch). Stdlib-only: this module may be imported before heavy deps
(torch, etc.) are installed, by both the backend and the dep_reconciler.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Overridable to match install_pytorch.sh's GUAARDVARK_ROCM_WHL default.
DEFAULT_ROCM_WHL = "rocm6.3"


def _compute_major(gpu: dict[str, Any]) -> int | None:
    cap = gpu.get("compute_cap")
    if not cap:
        return None
    try:
        return int(str(cap).split(".")[0])
    except (ValueError, IndexError):
        return None


def torch_channel(gpu: dict[str, Any], rocm_whl: str = DEFAULT_ROCM_WHL) -> str:
    """Map a GPU descriptor to a PyTorch wheel channel.

    Mirrors the arch table historically embedded in scripts/install_pytorch.sh.
    NVIDIA: Blackwell/Hopper(>=9) -> cu128 ; Ampere/Ada(8) -> cu124 ;
    Volta/Turing/Pascal(6-7) -> cu118 ; older/unknown -> cpu.
    """
    vendor = (gpu or {}).get("vendor", "none")
    if vendor == "amd":
        return rocm_whl
    if vendor != "nvidia":
        return "cpu"
    major = _compute_major(gpu)
    if major is None:
        return "cpu"
    if major >= 9:
        return "cu128"
    if major >= 8:
        # cu124, NOT cu121: the cu121 wheel index tops out at torch 2.5.1, which is
        # permanently vulnerable to CVE-2025-32434 (torch.load RCE, fixed in 2.6.0).
        # Routing Ampere/Ada here made that CVE structurally unpatchable for every
        # 30/40-series user. cu124 reaches 2.6.0 and supports sm_80-sm_89.
        return "cu124"
    if major >= 6:
        return "cu118"
    return "cpu"


def gpu_memory_mb(gpu: dict[str, Any]) -> int:
    """Memory the GPU can use, in MB: its VRAM, or for an NVIDIA GPU that shares
    the machine's memory (GB10 / DGX Spark, where nvidia-smi has no VRAM figure)
    the unified pool. 0 when unknown."""
    gpu = gpu or {}
    vram = gpu.get("vram_mb") or 0
    if vram > 0:
        return int(vram)
    if gpu.get("vendor") == "nvidia" and (gpu.get("unified_memory_gb") or 0) > 0:
        return int(gpu["unified_memory_gb"] * 1024)
    return 0


def ollama_tuning(gpu: dict[str, Any]) -> dict[str, Any]:
    """Derive Ollama server env from VRAM.

    The 16 GB ceiling is the design target (see CLAUDE.md). On a 16 GB card,
    4 parallel slots quadrupled the KV reservation and forced partial CPU
    offload — so NUM_PARALLEL scales with VRAM, not a baked constant.

    FLASH_ATTENTION is suppressed when no GPU is present or VRAM is unreported
    (the degraded path below), since it only helps on a real GPU.

    KV_CACHE_TYPE stays f16 (Ollama default) — q8_0 quantization degrades
    Gemma4 box_2d spatial outputs; May-2026 working aim used full-precision KV.
    """
    vendor = (gpu or {}).get("vendor", "none")
    vram = gpu_memory_mb(gpu)
    if vendor not in ("nvidia", "amd") or vram <= 0:
        return {
            "NUM_PARALLEL": 1,
            "KV_CACHE_TYPE": "f16",
            "FLASH_ATTENTION": 0,
            "MAX_LOADED_MODELS": 1,
            "KEEP_ALIVE": "15m",
            "VULKAN": 0,
        }
    # >= ~19.5 GB ⇒ 24 GB-class cards (RTX 3090/4090): headroom for 2 parallel
    # slots + 2 loaded models. A 16 GB-class card keeps one slot but holds the
    # embedding model beside a ~9 GB chat model at a 16k window, so retrieval and
    # answer do not swap models. Below 15 GB, one model.
    if vram >= 20000:
        num_parallel = 2
        max_loaded = 2
    elif vram >= 15000:
        num_parallel = 1
        max_loaded = 2
    else:
        num_parallel = 1
        max_loaded = 1
    # Force CUDA on NVIDIA (VULKAN=0) so a CUDA-init failure can't silently
    # fall back to the slower Vulkan backend. AMD legitimately uses Vulkan.
    vulkan = 0 if vendor == "nvidia" else 1
    return {
        "NUM_PARALLEL": num_parallel,
        "KV_CACHE_TYPE": "f16",
        "FLASH_ATTENTION": 1,
        "MAX_LOADED_MODELS": max_loaded,
        "KEEP_ALIVE": "15m",
        "VULKAN": vulkan,
    }


# Default chat models. Plan-mode test, held split x3, 2026-10-26/27
# (model_capability_data.PLAN_MODE_MEASURED): gemma4:12b 85/96, about 8.4 GB of
# GPU memory at an 8192 context on a 16 GB card; qwen3.5:4b 67/96, a 3.3 GB
# download; qwen3.5:2b 50/96, 2.7 GB. They replace gemma4:e2b (75/96, a ~7 GB
# download, kept for people who choose it) and llama3.2:1b (31/96). The standard
# model stays a Gemma4 because the screen agent is tuned to its box_2d answers;
# the screen agent reads Qwen3.5's xy answers through its family entry.
STANDARD_CHAT_MODEL = "gemma4:12b"
STANDARD_MIN_GPU_MB = 11500        # 12 GB cards report 12,000-12,288 MB
APPLE_STANDARD_MIN_RAM_GB = 16     # Apple silicon: the GPU uses the system memory
SMALL_CHAT_MODEL = "qwen3.5:4b"
TINY_CHAT_MODEL = "qwen3.5:2b"
SMALL_MAX_RAM_GB = 8
TINY_MAX_RAM_GB = 5                # 4 GB boards report about 3.7-3.9 GB


def model_tier(ram_gb: float, gpu: dict[str, Any], arch: str) -> dict[str, str]:
    """Pick chat + embed models for the host.

    Mirrors start.sh's bootstrap tiers. 8 GB of RAM or less, or an ARM board
    with no NVIDIA or Apple GPU (a Raspberry Pi class machine): the small model,
    or the tiny one at 5 GB or less. Otherwise the standard model when the GPU
    has room for it (an NVIDIA or AMD card with 12 GB, an NVIDIA GPU sharing
    the machine's memory, a 16 GB Apple silicon Mac), and the small model when
    it does not. The embed model is constant.
    """
    arch = (arch or "").lower()
    gpu = gpu or {}
    vendor = gpu.get("vendor")
    ram = ram_gb or 0
    small_arm = arch in ("aarch64", "arm64") and vendor not in ("nvidia", "apple")
    if small_arm or 0 < ram <= SMALL_MAX_RAM_GB:
        chat = TINY_CHAT_MODEL if 0 < ram <= TINY_MAX_RAM_GB else SMALL_CHAT_MODEL
    elif gpu_memory_mb(gpu) >= STANDARD_MIN_GPU_MB or (
            vendor == "apple" and ram >= APPLE_STANDARD_MIN_RAM_GB):
        chat = STANDARD_CHAT_MODEL
    else:
        chat = SMALL_CHAT_MODEL
    return {"chat": chat, "embed": "nomic-embed-text"}


# Training below this is "not practical" (Settings > Training libraries). It is the
# floor of hardware_service's 8 GB tier (8 GB cards report 7600-8192 MB), under which
# that table drops to 512-token sequences with CPU offload; taken from it, not measured.
TRAINING_MIN_VRAM_MB = 7000
# The same small-machine line as model_tier: 8 GB of RAM or less.
TRAINING_MAX_SMALL_RAM_GB = 8

_VENDOR_LABELS = {"amd": "an AMD", "apple": "an Apple", "intel": "an Intel"}


def training_fit(ram_gb: float, gpu: dict[str, Any], arch: str) -> dict[str, Any]:
    """Whether LoRA fine-tuning with the training libraries is practical here.

    Returns {"practical": bool, "reason": str}; the reason is shown to the
    person as written. Guaardvark's trainer loads the base model in 4-bit
    through Unsloth and bitsandbytes on an NVIDIA GPU, so any other machine is
    not practical whatever its size. `arch` is accepted for the same call shape
    as model_tier; an ARM board without an NVIDIA GPU already fails the GPU test.
    """
    gpu = gpu or {}
    vendor = gpu.get("vendor") or "none"
    vram = gpu.get("vram_mb") or 0
    if vendor != "nvidia":
        if vendor == "none":
            reason = "No GPU was found. Guaardvark's trainer needs an NVIDIA GPU."
        else:
            label = _VENDOR_LABELS.get(vendor, f"a {vendor}")
            reason = f"Guaardvark's trainer is set up for NVIDIA GPUs; this machine has {label} GPU."
        return {"practical": False, "reason": reason}
    if vram < TRAINING_MIN_VRAM_MB:
        if vram <= 0:
            reason = "The GPU's memory could not be read. Fine-tuning here needs an NVIDIA card with 8 GB or more."
        else:
            reason = (f"The GPU has {vram / 1024:.1f} GB of memory. Fine-tuning here needs "
                      f"an NVIDIA card with 8 GB or more.")
        return {"practical": False, "reason": reason}
    if 0 < (ram_gb or 0) <= TRAINING_MAX_SMALL_RAM_GB:
        return {"practical": False,
                "reason": (f"This machine has {ram_gb:g} GB of memory. Loading a base model "
                           f"for training needs more than {TRAINING_MAX_SMALL_RAM_GB} GB.")}
    return {"practical": True, "reason": f"NVIDIA GPU with {vram / 1024:.0f} GB of memory."}


def policy_fingerprint(hardware: dict[str, Any]) -> str:
    """Stable short hash of the *decisions* (not raw hardware) so an env only
    rebuilds when a decision actually changes. Folded into reconciler hashes.
    """
    gpu = (hardware or {}).get("gpu", {}) or {}
    arch = (hardware or {}).get("arch", "")
    ram_gb = ((hardware or {}).get("ram", {}) or {}).get("total_gb", 0)
    decisions = "|".join([
        f"torch={torch_channel(gpu)}",
        f"ollama_np={ollama_tuning(gpu)['NUM_PARALLEL']}",
        f"tier={model_tier(ram_gb, gpu, arch)['chat']}",
    ])
    return "hwfp:" + hashlib.sha256(decisions.encode("utf-8")).hexdigest()[:16]


def _is_stale_profile(profile: dict[str, Any]) -> bool:
    """A cached hardware.json is stale if it lacks a decision-critical field a
    current detector would provide.

    Today that field is ``gpu.compute_cap`` (added 2026-06-14): an NVIDIA GPU
    cached by an older detector has no compute_cap, which would make
    ``torch_channel`` silently fall back to ``cpu`` on a perfectly good GPU —
    the exact silent-CPU failure this module exists to prevent.
    """
    gpu = (profile or {}).get("gpu", {}) or {}
    if gpu.get("vendor") == "nvidia" and not gpu.get("compute_cap"):
        return True
    # Extend here whenever torch_channel/ollama_tuning/model_tier start
    # depending on a new field a current detector provides but an old cache lacks.
    return False


def _load_hardware() -> dict[str, Any]:
    """Read cached hardware.json if present AND current, else detect live.

    Trusting a stale cache (missing fields a newer detector adds) would corrupt
    every downstream provisioning decision, so a stale profile is rejected in
    favor of a live probe.
    """
    import os
    from backend.services.hardware_detector import HardwareDetector
    det = HardwareDetector()
    path = os.environ.get("GUAARDVARK_HARDWARE_JSON",
                          os.path.expanduser("~/.guaardvark/hardware.json"))
    profile = det.read_profile(path)
    if profile and not _is_stale_profile(profile):
        return profile
    return det.detect()


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m backend.services.hardware_policy <key>`

    Keys: torch_channel | ollama_env | model_tier | fingerprint | training_fit
    Prints a shell-consumable value to stdout.
    """
    import sys
    args = argv if argv is not None else sys.argv[1:]
    key = args[0] if args else ""
    hw = _load_hardware()
    gpu = hw.get("gpu", {}) or {}
    if key == "torch_channel":
        print(torch_channel(gpu))
    elif key == "fingerprint":
        print(policy_fingerprint(hw))
    elif key == "training_fit":
        ram_gb = (hw.get("ram", {}) or {}).get("total_gb", 0)
        fit = training_fit(ram_gb, gpu, hw.get("arch", ""))
        print(f"{'practical' if fit['practical'] else 'not-practical'}\t{fit['reason']}")
    elif key == "ollama_env":
        for k, v in ollama_tuning(gpu).items():
            print(f'Environment="OLLAMA_{k}={v}"')
    elif key == "model_tier":
        ram_gb = (hw.get("ram", {}) or {}).get("total_gb", 0)
        t = model_tier(ram_gb, gpu, hw.get("arch", ""))
        print(f"{t['chat']}\t{t['embed']}")
    else:
        print(f"unknown key: {key!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
