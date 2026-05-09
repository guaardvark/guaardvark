from __future__ import annotations
import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class RealLoraTrainer:
    """Spawns and drives plugins/lora_trainer/scripts/run_trainer.py inside
    venv-torch. Subprocess is lazily started on first use.
    
    Public API matches mock_trainer.train_subject_lora's contract so the
    selector in lora_trainer_tasks._train_impl can swap them."""

    _PLUGIN_ROOT = Path(__file__).resolve().parent
    _RUNNER_SCRIPT = _PLUGIN_ROOT / "scripts" / "run_trainer.py"
    _VENV_PYTHON = _PLUGIN_ROOT / "venv-torch" / "bin" / "python"
    _LOAD_TIMEOUT_S = 900    # SDXL is ~7 GB on first download
    _TRAIN_TIMEOUT_S = 1800  # 30 min cap per subject

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._loaded = False

    @classmethod
    def is_available(cls) -> bool:
        """True iff venv-torch/bin/python exists. Used by the selector."""
        return cls._VENV_PYTHON.exists()

    def _ensure_proc(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return

        if not self._VENV_PYTHON.exists():
            raise RuntimeError(f"venv-torch not found at {self._VENV_PYTHON}")
        if not self._RUNNER_SCRIPT.exists():
            raise RuntimeError(f"Trainer script missing at {self._RUNNER_SCRIPT}")

        logger.info("Spawning LoRA trainer daemon: %s %s", self._VENV_PYTHON, self._RUNNER_SCRIPT)
        self._proc = subprocess.Popen(
            [str(self._VENV_PYTHON), "-u", str(self._RUNNER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self._PLUGIN_ROOT),
        )

        def _pump_stderr():
            log_file = self._PLUGIN_ROOT.parent.parent / "logs" / "lora_trainer_daemon.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a") as f:
                if self._proc and self._proc.stderr:
                    for line in self._proc.stderr:
                        line = line.rstrip()
                        if line:
                            logger.info("lora_trainer daemon: %s", line)
                            f.write(line + "\n")
                            f.flush()

        threading.Thread(target=_pump_stderr, daemon=True).start()

        pong = self._send({"op": "ping"}, timeout_s=10)
        if not pong.get("ok"):
            self._kill_proc()
            raise RuntimeError(f"LoRA trainer daemon ping failed: {pong}")

    def _send(self, msg: dict, timeout_s: float) -> dict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("LoRA trainer daemon not running")

        line = json.dumps(msg) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"LoRA trainer daemon stdin closed: {e}") from e

        result_holder: dict[str, Any] = {}

        def _watchdog() -> None:
            time.sleep(timeout_s)
            if not result_holder:
                logger.error("LoRA trainer daemon timed out after %ss; killing", timeout_s)
                self._kill_proc()

        threading.Thread(target=_watchdog, daemon=True).start()

        response_line = self._proc.stdout.readline()
        result_holder["done"] = True

        if not response_line:
            raise RuntimeError("LoRA trainer daemon closed stdout (likely crashed or timed out)")

        try:
            return json.loads(response_line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LoRA trainer daemon returned non-JSON: {response_line!r} ({e})") from e

    def train_subject_lora(self, *, subject_id: int, subject_name: str, ref_image_paths: list[str], output_dir: str, **_) -> dict:
        if not ref_image_paths:
            return {"status": "failed", "error": "no reference images provided"}

        with self._lock:
            try:
                self._ensure_proc()
            except Exception as e:
                return {"status": "failed", "error": str(e)}
            
            target_dir = Path(output_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c if c.isalnum() else "_" for c in subject_name) or "subject"
            
            # Find next version
            v = 1
            while (target_dir / f"{safe_name}_v{v}.safetensors").exists():
                v += 1
            output_path = target_dir / f"{safe_name}_v{v}.safetensors"

            if not self._loaded:
                load_resp = self._send({"op": "load", "model_id": "stabilityai/stable-diffusion-xl-base-1.0"}, timeout_s=self._LOAD_TIMEOUT_S)
                if not load_resp.get("ok"):
                    return {"status": "failed", "error": load_resp.get("error", "load failed")}
                self._loaded = True

            steps = min(1500, max(400, len(ref_image_paths) * 100))

            train_resp = self._send({
                "op": "train",
                "params": {
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "ref_image_paths": ref_image_paths,
                    "output_path": str(output_path),
                    "rank": 16,
                    "alpha": 16,
                    "steps": steps,
                    "learning_rate": 1.0e-4,
                    "resolution": 1024,
                    "seed": 42,
                    "instance_prompt": f"a photo of {subject_name}"
                }
            }, timeout_s=self._TRAIN_TIMEOUT_S)

            if not train_resp.get("ok"):
                return {"status": "failed", "error": train_resp.get("error", "train failed")}

            sidecar = output_path.with_suffix(".json")
            sidecar.write_text(json.dumps({
                "subject_id": subject_id,
                "subject_name": subject_name,
                "ref_count": len(ref_image_paths),
                "mock": False,
                "steps": steps,
            }))

            return {
                "status": "ok",
                "lora_path": str(output_path),
                "lora_version": v
            }

    def shutdown(self) -> None:
        if self._proc is None:
            return
        try:
            self._send({"op": "shutdown"}, timeout_s=15)
        except Exception as e:
            logger.warning("LoRA trainer daemon graceful shutdown failed (%s); killing", e)
        try:
            self._proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, AttributeError):
            self._kill_proc()
        self._proc = None
        self._loaded = False
        logger.info("LoRA trainer daemon stopped")

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except Exception:
            pass
        self._proc = None
        self._loaded = False

_TRAINER = RealLoraTrainer()
