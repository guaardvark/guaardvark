"""Local device camera capture for the Vision Pipeline.

Opens a webcam via OpenCV, grabs frames in a background thread, and feeds
them into the existing StreamManager.  The analysis pipeline, change
detection, and context buffer all work unchanged — camera frames enter
through the same submit_frame() path as external POST /frame data.
"""

import base64
import logging
import os
import threading
import time
from dataclasses import dataclass

import cv2

logger = logging.getLogger("vision_pipeline.camera")


# -- Exceptions -------------------------------------------------------------

class CameraError(Exception):
    pass


class CameraNotFoundError(CameraError):
    pass


class CameraInUseError(CameraError):
    pass


# -- CameraCapture ----------------------------------------------------------

class CameraCapture:
    """Captures frames from a local camera and feeds them to StreamManager."""

    def __init__(self, stream_manager, config):
        self._stream_manager = stream_manager
        self._config = config
        self._cap = None
        self._thread = None
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
        self._stream_id = None
        self._device_index = 0
        self._frames_captured = 0
        self._started_at = 0.0
        self._resolution = (0, 0)

    # -- Public API ----------------------------------------------------------

    def start(self, device_index: int = None) -> dict:
        """Open the camera and start the capture thread.

        Returns stream info dict on success.
        Raises CameraNotFoundError or CameraInUseError on failure.
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {
                    "active": True,
                    "stream_id": self._stream_id,
                    "message": "Camera already running",
                }

            if device_index is None:
                device_index = getattr(self._config, "camera_device_index", 0)
            self._device_index = device_index

            # Open camera
            cap = cv2.VideoCapture(device_index)
            if not cap.isOpened():
                cap.release()
                # Try to distinguish the error
                dev_path = f"/dev/video{device_index}"
                if not os.path.exists(dev_path):
                    raise CameraNotFoundError(
                        f"No camera device found at index {device_index} ({dev_path} does not exist)"
                    )
                raise CameraInUseError(
                    f"Camera at index {device_index} exists but could not be opened (may be in use)"
                )

            # Probe a frame to confirm it works
            ret, frame = cap.read()
            if not ret or frame is None:
                cap.release()
                raise CameraError("Camera opened but cannot capture frames")

            self._resolution = (frame.shape[1], frame.shape[0])
            self._cap = cap
            self._frames_captured = 0
            self._started_at = time.time()
            self._shutdown_event.clear()

            # Create a stream in the manager
            stream = self._stream_manager.start_stream(
                source_type="camera_local"
            )
            self._stream_id = stream.id

            # Spawn capture thread
            self._thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="camera-capture",
            )
            self._thread.start()

            logger.info(
                f"Camera started: device={device_index} stream={self._stream_id} "
                f"resolution={self._resolution}"
            )
            return {
                "active": True,
                "stream_id": self._stream_id,
                "device_index": device_index,
                "resolution": list(self._resolution),
            }

    def stop(self) -> dict:
        """Stop the capture thread and release the camera."""
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return {"active": False, "message": "Camera not running"}

            self._shutdown_event.set()

        # Join outside the lock so the thread can finish
        self._thread.join(timeout=5)

        with self._lock:
            if self._cap:
                self._cap.release()
                self._cap = None

            # Stop the stream in the manager
            stats = {}
            if self._stream_id:
                stats = self._stream_manager.stop_stream(self._stream_id)
                self._stream_id = None

            duration = round(time.time() - self._started_at, 1) if self._started_at else 0
            result = {
                "active": False,
                "frames_captured": self._frames_captured,
                "duration_seconds": duration,
                "stream_stats": stats,
            }
            self._thread = None
            logger.info(f"Camera stopped: {self._frames_captured} frames in {duration}s")
            return result

    def status(self) -> dict:
        """Return current capture state."""
        active = bool(self._thread and self._thread.is_alive())
        result = {
            "active": active,
            "stream_id": self._stream_id,
            "device_index": self._device_index,
            "frames_captured": self._frames_captured,
            "resolution": list(self._resolution) if self._resolution != (0, 0) else None,
        }
        if active and self._started_at:
            result["uptime_seconds"] = round(time.time() - self._started_at, 1)
        return result

    # -- Capture loop --------------------------------------------------------

    def _capture_loop(self):
        """Background thread: read frames and submit to the stream manager."""
        interval = 1.0 / max(self._config.max_fps, 0.1)
        reconnect_attempts = getattr(self._config, "camera_reconnect_attempts", 3)
        reconnect_delay = getattr(self._config, "camera_reconnect_delay_seconds", 2)
        quality = self._config.frame_quality
        target_width = self._config.frame_width
        consecutive_failures = 0

        while not self._shutdown_event.is_set():
            ret, frame = self._cap.read()

            if not ret or frame is None:
                consecutive_failures += 1
                logger.warning(
                    f"Camera read failed ({consecutive_failures}/{reconnect_attempts})"
                )
                if consecutive_failures >= reconnect_attempts:
                    logger.error("Camera capture failed after max retries, stopping")
                    break
                self._shutdown_event.wait(timeout=reconnect_delay)
                continue

            consecutive_failures = 0

            # Resize maintaining aspect ratio
            h, w = frame.shape[:2]
            if w > target_width:
                scale = target_width / w
                new_h = int(h * scale)
                frame = cv2.resize(frame, (target_width, new_h))

            # Encode to JPEG
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            ok, buf = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue

            frame_b64 = base64.b64encode(buf.tobytes()).decode("ascii")

            # Submit to the stream manager
            try:
                self._stream_manager.submit_frame(self._stream_id, frame_b64)
                self._frames_captured += 1
            except Exception as e:
                logger.warning(f"Frame submit failed: {e}")

            # Wait for next frame interval (interruptible)
            self._shutdown_event.wait(timeout=interval)

        # Cleanup on exit (if loop broke due to errors)
        logger.info("Camera capture loop exited")
