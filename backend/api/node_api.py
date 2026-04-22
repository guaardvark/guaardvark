"""Per-node + master-only cluster-query endpoints.

GET /api/node/hardware-profile   — reads ~/.guaardvark/hardware.json
GET /api/node/live-state         — real-time snapshot (never cached)
GET /api/cluster/fleet           — master-only, returns FleetMap.get_fleet_summary()
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify

from backend.services.fleet_map import get_fleet_map

node_api_bp = Blueprint("node_api", __name__)


@node_api_bp.route("/api/node/hardware-profile", methods=["GET"])
def get_hardware_profile():
    path = Path(os.path.expanduser("~/.guaardvark/hardware.json"))
    if not path.exists():
        return jsonify({"error": "hardware.json not found; run hardware_detector"}), 404
    try:
        return jsonify(json.loads(path.read_text()))
    except json.JSONDecodeError as e:
        return jsonify({"error": f"malformed hardware.json: {e}"}), 500


@node_api_bp.route("/api/node/live-state", methods=["GET"])
def get_live_state():
    return jsonify({
        "gpu": _probe_live_gpu(),
        "ram": _probe_live_ram(),
        "cpu_percent": _probe_live_cpu_percent(),
        "services_running": _probe_services_running(),
        "loaded_models": _probe_loaded_models(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@node_api_bp.route("/api/cluster/fleet", methods=["GET"])
def get_cluster_fleet():
    if os.environ.get("CLUSTER_ROLE") != "master":
        return jsonify({"error": "master-only endpoint"}), 403
    return jsonify(get_fleet_map().get_fleet_summary())


# ---- live probes (fast, best-effort) ----------------------------------

def _probe_live_gpu() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.free,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return {}
        parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
        return {
            "vram_used_mb": int(parts[0]),
            "vram_free_mb": int(parts[1]),
            "utilization_percent": int(parts[2]),
            "temperature_c": int(parts[3]),
        }
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, IndexError):
        return {}


def _probe_live_ram() -> dict:
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        return {
            "used_gb": round((total_kb - available_kb) / 1024 / 1024, 1),
            "free_gb": round(available_kb / 1024 / 1024, 1),
        }
    except OSError:
        return {}


def _probe_live_cpu_percent() -> int:
    try:
        with open("/proc/loadavg") as f:
            load = float(f.read().split()[0])
        cores = os.cpu_count() or 1
        return min(int(load / cores * 100), 100)
    except (OSError, ValueError):
        return 0


def _probe_services_running() -> dict:
    import shutil
    candidates = {"ollama": 11434, "comfyui": 8188, "whisper": None, "piper": None,
                  "celery": None, "postgres": 5432, "redis": 6379}
    out = {}
    for name, port in candidates.items():
        if port is None:
            out[name] = shutil.which(name) is not None
            continue
        out[name] = _tcp_probe("127.0.0.1", port)
    return out


def _tcp_probe(host: str, port: int, timeout: float = 0.25) -> bool:
    import socket as sk
    try:
        with sk.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, sk.timeout):
        return False


def _probe_loaded_models() -> list:
    """Asks Ollama's /api/ps for currently-resident models. Fast (~50ms)."""
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read())
            return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
