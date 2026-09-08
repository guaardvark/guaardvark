from __future__ import annotations
import ctypes
import json
import logging
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _set_pdeathsig():
    """preexec_fn: ask the kernel to SIGKILL this daemon when its parent (the
    celery worker that spawned it) dies. The trainer daemon is a plain Popen
    child with no lifecycle link to the worker, so a worker restart/crash/recycle
    used to ORPHAN it — and an orphaned daemon keeps ~7GB of SDXL resident,
    OOMing the next training job until someone kills it by hand. PR_SET_PDEATHSIG
    closes that gap: parent gone → daemon dies → VRAM freed automatically.

    Linux-only (prctl); a silent no-op on other platforms. Runs in the forked
    child between fork() and exec(), so it must stay tiny and lock-free — a single
    libc call is safe; touching Python-level locks here is not."""
    if sys.platform != "linux":
        return
    PR_SET_PDEATHSIG = 1
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception:
        # Best-effort: if prctl is unavailable the daemon simply behaves as before
        # (the per-job shutdown() still reaps it on normal completion).
        pass

class RealLoraTrainer:
    """Multi-base LoRA trainer daemon driver.

    - sdxl-legacy → scripts/run_trainer.py + venv-torch (PEFT UNet)
    - zimage-turbo → scripts/run_zimage_trainer.py + backend/venv (ZImagePipeline)

    Public API matches mock_trainer.train_subject_lora so lora_trainer_tasks can swap.
    """

    _PLUGIN_ROOT = Path(__file__).resolve().parent
    _REPO_ROOT = _PLUGIN_ROOT.parent.parent
    _RUNNER_SCRIPT = _PLUGIN_ROOT / "scripts" / "run_trainer.py"
    _ZIMAGE_RUNNER = _PLUGIN_ROOT / "scripts" / "run_zimage_trainer.py"
    _VENV_PYTHON = _PLUGIN_ROOT / "venv-torch" / "bin" / "python"
    _BACKEND_PYTHON = _REPO_ROOT / "backend" / "venv" / "bin" / "python"
    _LOAD_TIMEOUT_S = 900    # first download / cold load
    _TRAIN_TIMEOUT_S = 1800  # 30 min cap per subject

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._loaded = False
        self._active_backend: str | None = None  # "sdxl" | "zimage"

    @staticmethod
    def _gpu_env() -> dict:
        """Environment for the trainer subprocesses, immune to the backend's own
        CPU-forcing env poison.

        The backend sets ``CUDA_VISIBLE_DEVICES=''`` IN-PROCESS to push RAG /
        embeddings / indexing onto the CPU (see indexing_service.py,
        llama_index_local_config.py, gpu_embedding). A celery worker that has
        touched that code carries the empty value in its os.environ for the rest
        of its life — and a child spawned with the default (inherited) env would
        see ZERO GPUs (torch.cuda.device_count() == 0), which is exactly the
        "CUDA probe failed" that blocked subject 16's amend runs even on a free
        card. We force a real device for our GPU subprocess. An explicit non-empty
        value (a real multi-GPU pin) is respected; only ''/unset is repaired.

        On Apple Silicon there is no CUDA; the trainer scripts fall back to the
        MPS backend. Enable the MPS fallback env so unsupported ops degrade to
        CPU instead of hard-failing mid-training.
        """
        import os
        env = dict(os.environ)
        if not env.get("CUDA_VISIBLE_DEVICES"):  # '' or missing → the poisoned case
            env["CUDA_VISIBLE_DEVICES"] = "0"
        # Reduce VRAM fragmentation on tight 16GB cards — the OOM error message
        # explicitly recommends this when "reserved but unallocated" is large.
        env.setdefault(
            "PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"
        )
        # Apple Silicon: let unsupported MPS ops fall back to CPU rather than
        # raising, so LoRA training can proceed on unified memory.
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        return env

    @classmethod
    def is_available(cls) -> bool:
        """True if any real train Python (venv-torch OR backend/venv) sees an
        accelerator (CUDA or Apple MPS).

        Z-Image training uses backend/venv; SDXL uses venv-torch. Either is enough
        to pick the real backend (the per-base daemon will fail clearly if its own
        python is missing). On Apple Silicon the backend/venv reports MPS (no CUDA),
        which is sufficient for the Z-Image default path.
        """
        pythons = []
        if cls._VENV_PYTHON.exists():
            pythons.append(cls._VENV_PYTHON)
        if cls._BACKEND_PYTHON.exists() and cls._BACKEND_PYTHON not in pythons:
            pythons.append(cls._BACKEND_PYTHON)
        if not pythons:
            return False

        attempts = 3
        last = ""
        for py in pythons:
            for i in range(attempts):
                try:
                    probe = subprocess.run(
                        [
                            str(py),
                            "-c",
                            "import torch, sys; "
                            "cuda = torch.cuda.is_available() and torch.cuda.device_count() > 0; "
                            "mps = hasattr(torch.backends, 'mps') and torch.backends.mps.is_available(); "
                            "ok = cuda or mps; "
                            "print('OK' if ok else 'NO'); "
                            "print((torch.cuda.get_device_name(0) if cuda else 'mps'), file=sys.stderr)",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        env=cls._gpu_env(),
                    )
                    if probe.returncode == 0 and "OK" in probe.stdout:
                        if i:
                            logger.info(
                                "real_trainer: accelerator probe OK (%s) attempt %d/%d",
                                py, i + 1, attempts,
                            )
                        return True
                    last = f"py={py} stdout={probe.stdout.strip()!r} stderr={probe.stderr.strip()!r}"
                except Exception as e:
                    last = f"py={py} probe crashed: {e}"
                if i < attempts - 1:
                    time.sleep(2)
        logger.warning(
            "real_trainer: accelerator probe failed for all train pythons after retries: %s",
            last,
        )
        return False

    def _backend_paths(self, backend: str) -> tuple[Path, Path, str]:
        """Return (python, runner_script, load_model_id) for a train backend."""
        if backend == "zimage":
            py = self._BACKEND_PYTHON if self._BACKEND_PYTHON.exists() else self._VENV_PYTHON
            if not py.exists():
                raise RuntimeError(
                    f"Z-Image trainer needs backend/venv (diffusers ZImagePipeline); "
                    f"missing {self._BACKEND_PYTHON}"
                )
            if not self._ZIMAGE_RUNNER.exists():
                raise RuntimeError(f"Z-Image trainer script missing: {self._ZIMAGE_RUNNER}")
            return py, self._ZIMAGE_RUNNER, "Tongyi-MAI/Z-Image-Turbo"
        # default SDXL
        if not self._VENV_PYTHON.exists():
            raise RuntimeError(f"venv-torch not found at {self._VENV_PYTHON}")
        if not self._RUNNER_SCRIPT.exists():
            raise RuntimeError(f"Trainer script missing at {self._RUNNER_SCRIPT}")
        return self._VENV_PYTHON, self._RUNNER_SCRIPT, "stabilityai/stable-diffusion-xl-base-1.0"

    def _ensure_proc(self, backend: str = "sdxl") -> None:
        """Start (or reuse) the daemon for the requested backend. Switches kill the old one."""
        if (
            self._proc is not None
            and self._proc.poll() is None
            and self._active_backend == backend
        ):
            return

        if self._proc is not None:
            logger.info(
                "LoRA trainer switching backend %s → %s; shutting down prior daemon",
                self._active_backend, backend,
            )
            self.shutdown()

        py, runner, _load_id = self._backend_paths(backend)
        logger.info("Spawning LoRA trainer daemon (%s): %s %s", backend, py, runner)
        self._proc = subprocess.Popen(
            [str(py), "-u", str(runner)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self._PLUGIN_ROOT),
            # Force a real GPU device: the worker may carry CUDA_VISIBLE_DEVICES=''
            # from the backend's CPU-forced RAG/embeddings, which the daemon would
            # otherwise inherit and train blind (or fail). See _gpu_env().
            env=self._gpu_env(),
            # Die with the worker so a restart/crash can't orphan a 7GB daemon.
            preexec_fn=_set_pdeathsig,
        )
        self._active_backend = backend
        self._loaded = False

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

    @staticmethod
    def _resolve_backend(base_model_id: str | None) -> str:
        mid = (base_model_id or "sdxl-legacy").strip().lower()
        if mid in ("zimage-turbo", "zimage", "z-image-turbo", "tongyi-mai/z-image-turbo"):
            return "zimage"
        if mid in ("flux-dev", "flux"):
            return "flux"  # not implemented in-daemon yet
        return "sdxl"

    def train_subject_lora(
        self,
        *,
        subject_id: int,
        subject_name: str,
        ref_image_paths: list[str],
        output_dir: str,
        trigger_word: str | None = None,
        resolution: int = 512,
        image_prompts: list[str] | None = None,
        rank: int = 16,
        alpha: int = 16,
        learning_rate: float = 1.0e-4,
        steps: int | None = None,
        base_model_id: str | None = None,
        **_,
    ) -> dict:
        # resolution is the dominant VRAM lever. Default 512 fits a 16 GB card
        # with desktop/other CUDA present; run_zimage_trainer also soft-caps.
        resolution = max(512, (int(resolution) // 64) * 64)
        if not ref_image_paths:
            return {"status": "failed", "error": "no reference images provided"}

        token = (trigger_word or "").strip() or subject_name
        backend = self._resolve_backend(base_model_id)
        if backend == "flux":
            return {
                "status": "failed",
                "error": (
                    "FLUX character training is registered but not implemented in-process yet. "
                    "Use base_model_id=zimage-turbo (default product) or sdxl-legacy."
                ),
            }

        registry_base = "zimage-turbo" if backend == "zimage" else "sdxl-legacy"
        train_backend_name = "peft_zimage" if backend == "zimage" else "peft_sdxl"

        with self._lock:
            try:
                self._ensure_proc(backend=backend)
            except Exception as e:
                return {"status": "failed", "error": str(e)}

            # Absolute path is mandatory: daemon cwd is plugin root.
            target_dir = Path(output_dir).resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c if c.isalnum() else "_" for c in subject_name) or "subject"

            v = 1
            while (target_dir / f"{safe_name}_v{v}.safetensors").exists():
                v += 1
            output_path = target_dir / f"{safe_name}_v{v}.safetensors"

            _, _, load_model_id = self._backend_paths(backend)
            if not self._loaded:
                load_resp = self._send(
                    {"op": "load", "model_id": load_model_id},
                    timeout_s=self._LOAD_TIMEOUT_S,
                )
                if not load_resp.get("ok"):
                    return {"status": "failed", "error": load_resp.get("error", "load failed")}
                self._loaded = True

            if steps is None:
                if backend == "zimage":
                    steps = min(1200, max(400, len(ref_image_paths) * 80))
                else:
                    steps = min(1500, max(400, len(ref_image_paths) * 100))
            prompts = list(image_prompts or [])
            if not prompts:
                prompts = [f"a photo of {token}"] * len(ref_image_paths)
            while len(prompts) < len(ref_image_paths):
                prompts.append(prompts[-1] if prompts else f"a photo of {token}")

            train_resp = self._send({
                "op": "train",
                "params": {
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "ref_image_paths": [str(Path(p).resolve()) for p in ref_image_paths],
                    "output_path": str(output_path),
                    "rank": int(rank),
                    "alpha": int(alpha),
                    "steps": int(steps),
                    "learning_rate": float(learning_rate),
                    "resolution": resolution,
                    "seed": 42,
                    "instance_prompt": f"a photo of {token}",
                    "image_prompts": prompts[: len(ref_image_paths)],
                }
            }, timeout_s=self._TRAIN_TIMEOUT_S)

            if not train_resp.get("ok"):
                return {"status": "failed", "error": train_resp.get("error", "train failed")}

            try:
                from backend.services.media_model_registry import write_lora_sidecar
                write_lora_sidecar(
                    output_path,
                    subject_id=subject_id,
                    subject_name=subject_name,
                    trigger_word=token,
                    base_model_id=registry_base,
                    ref_count=len(ref_image_paths),
                    steps=steps,
                    mock=False,
                    extra={
                        "train_backend": train_backend_name,
                        "resolution": resolution,
                        "rank": rank,
                        "alpha": alpha,
                    },
                )
            except Exception as e:
                logger.warning("write_lora_sidecar failed (%s); writing minimal sidecar", e)
                sidecar = output_path.with_suffix(".json")
                sidecar.write_text(json.dumps({
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "trigger_word": token,
                    "base_model_id": registry_base,
                    "lora_format": "zimage" if backend == "zimage" else "kohya_sdxl",
                    "instance_prompt": f"a photo of {token}",
                    "ref_count": len(ref_image_paths),
                    "mock": False,
                    "steps": steps,
                    "schema_version": 2,
                }))

            if not output_path.is_file() or output_path.stat().st_size < 1024:
                return {
                    "status": "failed",
                    "error": f"trainer reported ok but LoRA file missing/tiny: {output_path}",
                }

            return {
                "status": "ok",
                "lora_path": str(output_path),
                "lora_version": v,
                "base_model_id": registry_base,
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
        self._active_backend = None
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
