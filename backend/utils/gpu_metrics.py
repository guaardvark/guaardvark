"""AMD GPU metrics through rocm-smi, shared by the system metrics endpoint and GPUMonitor."""
import json
import logging
import shutil
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ROCM_SMI_ARGS = [
    "--showtemp", "--showuse", "--showmemuse", "--showpower", "--showmeminfo", "vram", "--json",
]
_TOTAL = "VRAM Total Memory (B)"
_USED = "VRAM Total Used Memory (B)"
_TEMP_KEYS = ("Temperature (Sensor edge) (C)", "Temperature (Sensor junction) (C)")
# The discrete card and an iGPU spell the power reading differently.
_POWER_KEYS = (
    "Average Graphics Package Power (W)",
    "Current Socket Graphics Package Power (W)",
)
_MIB = 1024 * 1024


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_rocm_smi_json(text: str) -> Optional[dict]:
    """The JSON object in rocm-smi's output; it can print warning lines before it."""
    start = (text or "").find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def pick_card(data: dict) -> Optional[dict]:
    """The card with the most VRAM (the discrete one next to an iGPU)."""
    best, best_total = None, -1.0
    for key, card in data.items():
        if not str(key).startswith("card") or not isinstance(card, dict):
            continue
        total = _num(card.get(_TOTAL)) or 0.0
        if total > best_total:
            best, best_total = card, total
    return best


def _first(card: dict, keys) -> Optional[float]:
    for key in keys:
        value = _num(card.get(key))
        if value is not None:
            return value
    return None


def metrics_from_rocm_json(data: dict) -> Dict[str, Any]:
    card = pick_card(data)
    if card is None:
        return {"available": False, "error": "rocm-smi reported no card"}
    total = _num(card.get(_TOTAL))
    used = _num(card.get(_USED))
    total_mb = total / _MIB if total else None
    used_mb = used / _MIB if used is not None else None
    percent = None
    if total_mb and used_mb is not None:
        percent = round(used_mb / total_mb * 100, 2)
    return {
        "available": True,
        "temperature": _first(card, _TEMP_KEYS),
        "utilization": _num(card.get("GPU use (%)")),
        "memory_used_mb": used_mb,
        "memory_total_mb": total_mb,
        "memory_percent": percent,
        "power_draw_w": _first(card, _POWER_KEYS),
        "power_limit_w": None,
        "power_percent": None,
        "tool": "rocm-smi",
    }


def rocm_smi_metrics(path: Optional[str] = None) -> Dict[str, Any]:
    """Run rocm-smi and return the same fields GPUMonitor reports for nvidia-smi."""
    path = path or shutil.which("rocm-smi")
    if not path:
        return {"available": False, "error": "rocm-smi not found"}
    try:
        result = subprocess.run(
            [str(path), *ROCM_SMI_ARGS], capture_output=True, text=True, timeout=5, shell=False
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "rocm-smi timeout"}
    except Exception as e:
        logger.debug(f"rocm-smi failed: {e}")
        return {"available": False, "error": f"rocm-smi error: {e}"}
    data = parse_rocm_smi_json(result.stdout) or parse_rocm_smi_json(result.stderr)
    if data is None:
        return {"available": False, "error": "Invalid rocm-smi output"}
    return metrics_from_rocm_json(data)
