"""Hardware self-profiling for cluster node registration.

Writes ~/.guaardvark/hardware.json with structured info about this box —
CPU, RAM, GPU (vendor-probed), disk, installed services, arch, master
eligibility. Runnable as __main__ with --output <path> so start.sh can
refresh the profile on every boot.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_NODE_ID_PATH = os.path.expanduser("~/.guaardvark/node_id")
DEFAULT_OUTPUT_PATH = os.path.expanduser("~/.guaardvark/hardware.json")
KNOWN_SERVICES = (
    "ollama", "comfyui", "whisper", "piper", "ffmpeg",
    "celery", "postgres", "redis", "mcp",
)


class HardwareDetector:
    def __init__(self, node_id_path: str = DEFAULT_NODE_ID_PATH):
        self._node_id_path = node_id_path

    def detect(self) -> dict[str, Any]:
        return {
            "node_id": self._get_or_create_node_id(),
            "hostname": socket.gethostname(),
            "os": self._detect_os(),
            "kernel": platform.release(),
            "arch": platform.machine(),  # "x86_64" / "aarch64" / "arm64"
            "master_eligible": self._detect_master_eligible(),
            "cpu": self._probe_cpu(),
            "ram": self._probe_ram(),
            "gpu": self._probe_gpu(),
            "benchmark_score": None,
            "disk": self._probe_disk(),
            "services": self._probe_services(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def read_profile(self, path: str) -> dict[str, Any] | None:
        try:
            return json.loads(Path(path).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def detect_changes(self, prev: dict, curr: dict) -> dict[str, Any]:
        if prev == curr:
            return {}
        diff = {}
        for key in ("cpu", "ram", "gpu", "disk", "services"):
            if prev.get(key) != curr.get(key):
                diff[key] = {"before": prev.get(key), "after": curr.get(key)}
        return diff

    # ---- node id -----------------------------------------------------

    def _get_or_create_node_id(self) -> str:
        p = Path(self._node_id_path)
        if p.exists():
            return p.read_text().strip()
        nid = str(uuid.uuid4())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(nid)
        return nid

    # ---- os/master ---------------------------------------------------

    def _detect_os(self) -> str:
        try:
            out = subprocess.check_output(["lsb_release", "-d", "-s"], text=True).strip().strip('"')
            return out
        except (FileNotFoundError, subprocess.CalledProcessError):
            return f"{platform.system()} {platform.release()}"

    def _detect_master_eligible(self) -> bool:
        # User-set via env var; defaults to True. start.sh can export
        # GUAARDVARK_MASTER_INELIGIBLE=1 on RPi / laptop.
        return os.environ.get("GUAARDVARK_MASTER_INELIGIBLE", "0") != "1"

    # ---- cpu/ram/disk ------------------------------------------------

    def _probe_cpu(self) -> dict:
        info = {"model": platform.processor() or "unknown",
                "cores": os.cpu_count() or 1,
                "threads": os.cpu_count() or 1}
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["model"] = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
        return info

    def _probe_ram(self) -> dict:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return {"total_gb": round(kb / 1024 / 1024, 1)}
        except OSError:
            pass
        return {"total_gb": 0}

    def _probe_disk(self) -> dict:
        try:
            st = shutil.disk_usage(os.path.expanduser("~"))
            return {"total_gb": round(st.total / 1024**3, 1),
                    "free_gb": round(st.free / 1024**3, 1)}
        except OSError:
            return {"total_gb": 0, "free_gb": 0}

    # ---- gpu ---------------------------------------------------------

    def _probe_gpu(self) -> dict:
        for probe in (self._probe_gpu_nvidia, self._probe_gpu_amd, self._probe_gpu_intel):
            result = probe()
            if result is not None:
                return result
        return {"vendor": "none"}

    def _probe_gpu_nvidia(self) -> dict | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if out.returncode != 0 or not out.stdout.strip():
                return None
            first = out.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            if len(parts) < 3:
                return None
            return {
                "vendor": "nvidia",
                "model": parts[0],
                "vram_mb": int(parts[1]),
                "driver": parts[2],
                "cuda": self._detect_cuda_version(),
            }
        except (FileNotFoundError, subprocess.SubprocessError, ValueError):
            return None

    def _probe_gpu_amd(self) -> dict | None:
        if shutil.which("rocm-smi") is None:
            return None
        return {"vendor": "amd", "vram_mb": None}

    def _probe_gpu_intel(self) -> dict | None:
        if shutil.which("intel_gpu_top") is None:
            return None
        return {"vendor": "intel", "vram_mb": None}

    def _detect_cuda_version(self) -> str | None:
        try:
            out = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, timeout=3)
            for line in out.stdout.splitlines():
                if "release" in line:
                    return line.split("release")[1].split(",")[0].strip()
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        return None

    # ---- services ----------------------------------------------------

    def _probe_services(self) -> dict:
        result = {}
        for svc in KNOWN_SERVICES:
            binary = {"postgres": "psql", "redis": "redis-cli"}.get(svc, svc)
            path = shutil.which(binary)
            result[svc] = {"installed": path is not None}
            if path and svc == "ollama":
                result[svc]["version"] = self._ollama_version()
        return result

    def _ollama_version(self) -> str | None:
        try:
            out = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=3)
            return out.stdout.strip().split()[-1] if out.stdout else None
        except (FileNotFoundError, subprocess.SubprocessError):
            return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH,
                        help="Path to write hardware.json")
    args = parser.parse_args()

    d = HardwareDetector()
    profile = d.detect()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profile, indent=2, sort_keys=True))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
