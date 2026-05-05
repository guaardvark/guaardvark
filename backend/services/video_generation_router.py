# backend/services/video_generation_router.py
# Routes video generation requests to ComfyUI (primary) or Offline Diffusers (fallback).
# Manages on-demand ComfyUI lifecycle — starts when needed, stops after idle timeout.

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from backend.config import (
        COMFYUI_URL, COMFYUI_DIR, COMFYUI_VENV,
        VIDEO_GENERATION_BACKEND, COMFYUI_IDLE_TIMEOUT,
        LOG_DIR, GUAARDVARK_ROOT,
    )
except ImportError:
    COMFYUI_URL = os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")
    COMFYUI_DIR = os.environ.get("GUAARDVARK_COMFYUI_DIR", os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "plugins", "comfyui", "ComfyUI"))
    COMFYUI_VENV = os.environ.get("GUAARDVARK_COMFYUI_VENV", os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "backend", "venv"))
    VIDEO_GENERATION_BACKEND = os.environ.get("GUAARDVARK_VIDEO_BACKEND", "auto")
    COMFYUI_IDLE_TIMEOUT = int(os.environ.get("GUAARDVARK_COMFYUI_IDLE_TIMEOUT", "1800"))
    LOG_DIR = "logs"
    GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", ".")

# Re-export the shared dataclasses so batch_video_generator can import from here
from backend.services.comfyui_video_generator import VideoGenerationRequest, VideoGenerationResult


class VideoGenerationRouter:
    """Routes video generation to ComfyUI or Offline backend based on availability and config."""

    def __init__(self):
        self._comfyui_gen = None
        self._offline_gen = None
        self._backend_pref = VIDEO_GENERATION_BACKEND  # "auto", "comfyui", "offline"
        self._comfyui_process = None
        self._idle_timer = None
        self._active_generation_count = 0
        self._gen_count_lock = threading.Lock()
        self._pid_file = Path(GUAARDVARK_ROOT) / "pids" / "comfyui.pid"
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def service_available(self) -> bool:
        """Check if any video generation backend is available."""
        if self._check_comfyui():
            return True
        if self._backend_pref in ("offline", "auto"):
            off = self._get_offline()
            if off:
                return True
        return Path(COMFYUI_DIR).exists()

    def _get_comfyui(self, force_refresh=False):
        """Lazy-load ComfyUI generator."""
        if self._comfyui_gen is None or force_refresh:
            from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
            self._comfyui_gen = ComfyUIVideoGenerator()
        return self._comfyui_gen

    def _get_offline(self):
        """Lazy-load Offline Diffusers generator."""
        if self._offline_gen is None:
            try:
                from backend.services.offline_video_generator import OfflineVideoGenerator
                self._offline_gen = OfflineVideoGenerator()
            except ImportError as e:
                logger.warning(f"Offline video generator unavailable: {e}")
                return None
        return self._offline_gen

    def _check_comfyui(self) -> bool:
        """Ping ComfyUI to see if it's running."""
        try:
            import requests
            resp = requests.get(COMFYUI_URL, timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def get_active_generator(self):
        """Get the best available video generator based on config and availability."""
        if self._backend_pref == "offline":
            gen = self._get_offline()
            if gen:
                logger.info("Using Offline Diffusers backend (configured)")
                return gen
            raise RuntimeError("Offline video generator not available (missing torch/diffusers)")

        # Try ComfyUI first
        if self._check_comfyui():
            gen = self._get_comfyui(force_refresh=True)
            if gen.service_available:
                logger.info("Using ComfyUI backend (running)")
                self._cancel_idle_shutdown()
                return gen

        # ComfyUI not running — try to start it (auto or comfyui mode)
        if self._backend_pref in ("comfyui", "auto"):
            if self._start_comfyui():
                gen = self._get_comfyui(force_refresh=True)
                if gen.service_available:
                    logger.info("Using ComfyUI backend (just started)")
                    return gen

        # ComfyUI failed — fall back to offline if auto mode
        if self._backend_pref == "auto":
            gen = self._get_offline()
            if gen:
                logger.info("ComfyUI unavailable, falling back to Offline Diffusers backend")
                return gen

        raise RuntimeError(
            "No video generation backend available. "
            "ComfyUI is not running and Offline generator is not installed."
        )

    def interrupt(self) -> bool:
        """Tell whichever backend is currently sampling to abort.

        Returns True if a backend acknowledged the interrupt. ComfyUI honours
        this immediately; the offline generator can only stop between items.
        """
        if self._check_comfyui():
            try:
                comfy = self._get_comfyui()
                return comfy.interrupt()
            except Exception as e:
                logger.warning(f"Router interrupt to ComfyUI failed: {e}")
                return False
        # Offline generator: no in-flight cancel today.
        logger.info("Interrupt called but ComfyUI not running; offline backend cancels between items.")
        return False

    def generate_video(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Route a generation request to the best available backend."""
        with self._gen_count_lock:
            self._active_generation_count += 1
            self._cancel_idle_shutdown()
        try:
            generator = self.get_active_generator()
            result = generator.generate_video(request)
            return result
        except RuntimeError as e:
            return VideoGenerationResult(
                success=False,
                error=str(e),
                prompt_used=request.prompt,
            )
        finally:
            with self._gen_count_lock:
                self._active_generation_count = max(0, self._active_generation_count - 1)
                if self._active_generation_count == 0:
                    self._schedule_idle_shutdown()

    @property
    def is_generating(self) -> bool:
        """Return True if any video generation is currently in progress."""
        with self._gen_count_lock:
            return self._active_generation_count > 0

    # ── ComfyUI lifecycle management ──────────────────────────────────────

    def _start_comfyui(self) -> bool:
        """Start ComfyUI server, preferring the plugin start script for consistency."""
        # If already running (maybe started externally), just confirm
        if self._check_comfyui():
            logger.info("ComfyUI is already running")
            return True

        comfyui_dir = Path(COMFYUI_DIR)
        if not comfyui_dir.exists():
            logger.error(f"ComfyUI not installed at {comfyui_dir}")
            return False

        # Prefer the plugin start script — it handles duplicate detection,
        # correct cwd, PID file writing, and log setup consistently.
        plugin_start_script = Path(GUAARDVARK_ROOT) / "plugins" / "comfyui" / "scripts" / "start.sh"
        if plugin_start_script.exists():
            return self._start_comfyui_via_plugin(plugin_start_script)

        # Fallback: direct subprocess launch (e.g. if plugin scripts are missing)
        return self._start_comfyui_direct(comfyui_dir)

    def _start_comfyui_via_plugin(self, start_script: Path) -> bool:
        """Start ComfyUI using the plugin's start.sh script."""
        logger.info(f"Starting ComfyUI via plugin script: {start_script}")
        try:
            result = subprocess.run(
                ["bash", str(start_script)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.error(f"Plugin start script failed (rc={result.returncode}): {result.stderr}")
                return False
            logger.info(f"Plugin start script output: {result.stdout.strip()}")

            # Read PID from the file the script wrote
            if self._pid_file.exists():
                try:
                    pid = int(self._pid_file.read_text().strip())
                    # Wrap in a lightweight tracker so stop_comfyui can find it
                    self._comfyui_process = None  # plugin manages the process
                    logger.info(f"ComfyUI launched via plugin (PID: {pid})")
                except ValueError:
                    pass

            # Wait up to 90 seconds for ComfyUI to become ready (models can be slow to load)
            return self._wait_for_comfyui_ready(timeout_seconds=90)

        except subprocess.TimeoutExpired:
            logger.error("Plugin start script timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to start ComfyUI via plugin: {e}")
            return False

    def _start_comfyui_direct(self, comfyui_dir: Path) -> bool:
        """Fallback: start ComfyUI directly as a subprocess."""
        venv_python = Path(COMFYUI_VENV) / "bin" / "python"
        if not venv_python.exists():
            venv_python = "python3"

        main_py = comfyui_dir / "main.py"
        if not main_py.exists():
            logger.error(f"ComfyUI main.py not found at {main_py}")
            return False

        log_path = Path(LOG_DIR) / "comfyui.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Starting ComfyUI directly at {comfyui_dir}...")
        try:
            log_file = open(str(log_path), "a")
            proc = subprocess.Popen(
                [str(venv_python), str(main_py), "--listen", "--port", "8188"],
                cwd=str(comfyui_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._comfyui_process = proc

            # Write PID file
            self._pid_file.write_text(str(proc.pid))
            logger.info(f"ComfyUI started (PID: {proc.pid}), waiting for it to be ready...")

            # Wait up to 90 seconds for ComfyUI to respond
            for i in range(90):
                time.sleep(1)
                if self._check_comfyui():
                    logger.info(f"ComfyUI ready after {i + 1}s")
                    return True
                # Check process didn't crash
                if proc.poll() is not None:
                    logger.error(f"ComfyUI process exited with code {proc.returncode}")
                    self._pid_file.unlink(missing_ok=True)
                    return False

            logger.error("ComfyUI did not become ready within 90 seconds")
            return False

        except Exception as e:
            logger.error(f"Failed to start ComfyUI: {e}")
            return False

    def _wait_for_comfyui_ready(self, timeout_seconds: int = 90) -> bool:
        """Poll ComfyUI health endpoint until it responds or timeout expires."""
        for i in range(timeout_seconds):
            time.sleep(1)
            if self._check_comfyui():
                logger.info(f"ComfyUI ready after {i + 1}s")
                return True
            # If launched via plugin, check if PID is still alive
            if self._pid_file.exists():
                try:
                    pid = int(self._pid_file.read_text().strip())
                    os.kill(pid, 0)  # signal 0 = just check existence
                except (ProcessLookupError, ValueError):
                    logger.error("ComfyUI process died during startup")
                    return False
                except PermissionError:
                    pass  # process exists but we can't signal it, that's fine
        logger.error(f"ComfyUI did not become ready within {timeout_seconds} seconds")
        return False

    def stop_comfyui(self) -> bool:
        """Stop ComfyUI server."""
        stopped = False

        # Try our tracked process first
        if self._comfyui_process and self._comfyui_process.poll() is None:
            try:
                os.killpg(os.getpgid(self._comfyui_process.pid), signal.SIGTERM)
                self._comfyui_process.wait(timeout=10)
                stopped = True
                logger.info("Stopped ComfyUI (tracked process)")
            except Exception as e:
                logger.warning(f"Failed to stop tracked ComfyUI process: {e}")

        # Try PID file
        if not stopped and self._pid_file.exists():
            try:
                pid = int(self._pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                stopped = True
                logger.info(f"Stopped ComfyUI (PID file: {pid})")
            except (ProcessLookupError, ValueError):
                pass
            except Exception as e:
                logger.warning(f"Failed to stop ComfyUI via PID file: {e}")

        self._pid_file.unlink(missing_ok=True)
        self._comfyui_process = None
        self._comfyui_gen = None  # Force re-check on next use
        return stopped

    def _schedule_idle_shutdown(self):
        """Schedule ComfyUI shutdown after idle timeout to free VRAM."""
        self._cancel_idle_shutdown()
        if COMFYUI_IDLE_TIMEOUT > 0:
            self._idle_timer = threading.Timer(
                COMFYUI_IDLE_TIMEOUT,
                self._idle_shutdown,
            )
            self._idle_timer.daemon = True
            self._idle_timer.start()
            logger.info(f"ComfyUI idle shutdown scheduled in {COMFYUI_IDLE_TIMEOUT}s")

    def _cancel_idle_shutdown(self):
        """Cancel pending idle shutdown."""
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _idle_shutdown(self):
        """Called by timer — stop ComfyUI if still idle and no active generations."""
        if self.is_generating:
            logger.info("ComfyUI idle timeout fired but generation is active, rescheduling...")
            self._schedule_idle_shutdown()
            return
        if self._check_comfyui():
            logger.info("ComfyUI idle timeout reached, stopping to free VRAM...")
            self.stop_comfyui()

    def get_status(self) -> dict:
        """Get current status of the video generation system."""
        comfyui_running = self._check_comfyui()
        return {
            "backend_preference": self._backend_pref,
            "comfyui_running": comfyui_running,
            "comfyui_url": COMFYUI_URL,
            "comfyui_dir": COMFYUI_DIR,
            "comfyui_installed": Path(COMFYUI_DIR).exists(),
            "idle_timeout": COMFYUI_IDLE_TIMEOUT,
            "active_generations": self._active_generation_count,
            "is_generating": self.is_generating,
        }


# ── Singleton access ──────────────────────────────────────────────────────

_router_instance: Optional[VideoGenerationRouter] = None


def get_video_router() -> VideoGenerationRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = VideoGenerationRouter()
    return _router_instance


def get_video_generator() -> VideoGenerationRouter:
    """Alias for batch_video_generator compatibility — returns the router which has generate_video()."""
    return get_video_router()
