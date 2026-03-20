"""GPU-aware adaptive FPS throttle.

Combines three signals to self-tune analysis rate:
1. GPU utilization (pynvml / nvidia-smi)
2. Inference latency feedback (rolling average)
3. Scene activity (consecutive no-change count)
"""
import time
import subprocess
import logging
from collections import deque

logger = logging.getLogger("vision_pipeline.adaptive_throttle")


class AdaptiveThrottle:
    def __init__(self, config):
        self.max_fps = getattr(config, 'max_fps', 2.0)
        self.min_fps = getattr(config, 'min_fps', 0.25)
        self.pause_threshold = getattr(config, 'utilization_pause_threshold', 90)
        self.throttle_threshold = getattr(config, 'utilization_throttle_threshold', 75)
        self.contention_behavior = getattr(config, 'contention_behavior', 'min_fps')
        self.current_fps = self.max_fps
        self._inference_history = deque(maxlen=20)
        self._consecutive_no_change = 0
        self._gpu_contention = False
        self._gpu_utilization = 0
        self._last_gpu_check = 0
        self._gpu_check_interval = 5  # seconds

    def get_interval(self) -> float:
        """Return seconds to wait between frame analyses."""
        if self.is_paused:
            return float('inf')

        fps = self.max_fps

        # Signal 1: GPU utilization
        gpu_pct = self._get_gpu_utilization()
        if gpu_pct > self.pause_threshold:
            return float('inf')
        elif gpu_pct > self.throttle_threshold:
            # Linear scale from max_fps to min_fps between throttle and pause thresholds
            ratio = (gpu_pct - self.throttle_threshold) / (self.pause_threshold - self.throttle_threshold)
            fps = self.max_fps - ratio * (self.max_fps - self.min_fps)
        elif gpu_pct > 50:
            # Linear scale from max_fps to 50% of max_fps
            ratio = (gpu_pct - 50) / (self.throttle_threshold - 50)
            fps = self.max_fps * (1.0 - 0.5 * ratio)

        # Signal 2: Inference latency feedback
        if self._inference_history:
            avg_ms = sum(self._inference_history) / len(self._inference_history)
            interval_ms = 1000.0 / fps if fps > 0 else float('inf')
            if avg_ms > 0.8 * interval_ms:
                # Falling behind — reduce to what we can sustain
                sustainable = 1000.0 / avg_ms if avg_ms > 0 else self.max_fps
                fps = min(fps, sustainable * 0.8)

        # Signal 3: Scene inactivity
        if self._consecutive_no_change >= 5:
            fps = fps / 2

        # Contention override
        if self._gpu_contention:
            if self.contention_behavior == "pause":
                return float('inf')
            fps = min(fps, self.min_fps)

        # Clamp
        fps = max(self.min_fps, min(fps, self.max_fps))
        self.current_fps = fps
        return 1.0 / fps if fps > 0 else float('inf')

    def record_inference(self, inference_ms: int):
        """Track inference timing for latency feedback."""
        self._inference_history.append(inference_ms)

    def record_scene_state(self, changed: bool):
        """Track consecutive no-change frames."""
        if changed:
            self._consecutive_no_change = 0
        else:
            self._consecutive_no_change += 1

    def notify_gpu_contention(self):
        """External signal: heavy GPU task started."""
        self._gpu_contention = True
        logger.debug("GPU contention signaled — throttling vision pipeline")

    def notify_gpu_available(self):
        """External signal: GPU contention cleared."""
        self._gpu_contention = False
        logger.debug("GPU contention cleared — restoring vision pipeline FPS")

    @property
    def is_paused(self) -> bool:
        """True when GPU utilization is critical or contention mode is 'pause'."""
        if self._gpu_contention and self.contention_behavior == "pause":
            return True
        return self._get_gpu_utilization() > self.pause_threshold

    def _get_gpu_utilization(self) -> int:
        """Query GPU utilization %. Cached for gpu_check_interval seconds."""
        now = time.time()
        if now - self._last_gpu_check < self._gpu_check_interval:
            return self._gpu_utilization

        self._last_gpu_check = now
        # Try pynvml
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            self._gpu_utilization = util.gpu
            return self._gpu_utilization
        except Exception:
            pass

        # Fallback: nvidia-smi
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self._gpu_utilization = int(result.stdout.strip().split('\n')[0])
                return self._gpu_utilization
        except Exception:
            pass

        self._gpu_utilization = 0
        return 0
