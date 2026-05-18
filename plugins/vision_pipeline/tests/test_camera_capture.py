"""Tests for CameraCapture — lifecycle, frame submission, error handling."""

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np
import pytest

from service.camera_capture import (
    CameraCapture,
    CameraError,
    CameraInUseError,
    CameraNotFoundError,
)


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.max_fps = 10.0  # fast for tests
    config.frame_quality = 70
    config.frame_width = 512
    config.camera_device_index = 0
    config.camera_reconnect_attempts = 3
    config.camera_reconnect_delay_seconds = 0.01  # fast for tests
    return config


@pytest.fixture
def mock_stream_manager():
    mgr = MagicMock()
    stream = MagicMock()
    stream.id = "test-stream-001"
    mgr.start_stream.return_value = stream
    mgr.submit_frame.return_value = {"queued": True}
    mgr.stop_stream.return_value = {"stream_id": "test-stream-001", "total_frames": 5}
    return mgr


def _make_fake_frame(w=640, h=480):
    """Create a synthetic BGR frame like OpenCV would return."""
    return np.zeros((h, w, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Start / Stop lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @patch("service.camera_capture.cv2")
    def test_start_opens_camera_and_creates_stream(self, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.return_value = (True, _make_fake_frame())
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        result = cc.start(device_index=0)

        assert result["active"] is True
        assert result["stream_id"] == "test-stream-001"
        mock_cv2.VideoCapture.assert_called_once_with(0)
        mock_stream_manager.start_stream.assert_called_once_with(source_type="camera_local")
        cc.stop()

    @patch("service.camera_capture.cv2")
    def test_stop_releases_camera_and_stream(self, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.return_value = (True, _make_fake_frame())
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        cc.start()
        result = cc.stop()

        assert result["active"] is False
        cap_instance.release.assert_called_once()
        mock_stream_manager.stop_stream.assert_called_once_with("test-stream-001")

    @patch("service.camera_capture.cv2")
    def test_start_when_already_running(self, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.return_value = (True, _make_fake_frame())
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        cc.start()
        result = cc.start()  # second call

        assert result["active"] is True
        assert "already running" in result.get("message", "").lower()
        cc.stop()

    @patch("service.camera_capture.cv2")
    def test_double_stop_is_safe(self, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.return_value = (True, _make_fake_frame())
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        cc.start()
        cc.stop()
        result = cc.stop()
        assert result["active"] is False


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    @patch("service.camera_capture.cv2")
    @patch("service.camera_capture.os.path.exists", return_value=False)
    def test_camera_not_found(self, mock_exists, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = False
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        with pytest.raises(CameraNotFoundError):
            cc.start(device_index=5)

    @patch("service.camera_capture.cv2")
    @patch("service.camera_capture.os.path.exists", return_value=True)
    def test_camera_in_use(self, mock_exists, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = False
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        with pytest.raises(CameraInUseError):
            cc.start()

    @patch("service.camera_capture.cv2")
    def test_camera_opens_but_no_frames(self, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.return_value = (False, None)
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        with pytest.raises(CameraError, match="cannot capture frames"):
            cc.start()


# ---------------------------------------------------------------------------
# Frame submission
# ---------------------------------------------------------------------------

class TestFrameSubmission:
    @patch("service.camera_capture.cv2")
    def test_frames_submitted_to_stream_manager(self, mock_cv2, mock_stream_manager, mock_config):
        frame = _make_fake_frame()
        call_count = 0

        def fake_read():
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                return (True, frame.copy())
            return (False, None)

        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.side_effect = fake_read
        mock_cv2.VideoCapture.return_value = cap_instance
        mock_cv2.resize.return_value = frame
        mock_cv2.imencode.return_value = (True, np.array([0xFF, 0xD8], dtype=np.uint8))
        mock_cv2.IMWRITE_JPEG_QUALITY = 1

        cc = CameraCapture(mock_stream_manager, mock_config)
        cc.start()
        time.sleep(0.3)  # let a few frames through
        cc.stop()

        assert mock_stream_manager.submit_frame.call_count >= 1
        # All calls should use the correct stream_id
        for call in mock_stream_manager.submit_frame.call_args_list:
            assert call[0][0] == "test-stream-001"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_when_inactive(self, mock_stream_manager, mock_config):
        cc = CameraCapture(mock_stream_manager, mock_config)
        status = cc.status()
        assert status["active"] is False
        assert status["stream_id"] is None

    @patch("service.camera_capture.cv2")
    def test_status_when_active(self, mock_cv2, mock_stream_manager, mock_config):
        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.return_value = (True, _make_fake_frame())
        mock_cv2.VideoCapture.return_value = cap_instance
        mock_cv2.imencode.return_value = (True, np.array([0xFF], dtype=np.uint8))
        mock_cv2.IMWRITE_JPEG_QUALITY = 1

        cc = CameraCapture(mock_stream_manager, mock_config)
        cc.start()
        time.sleep(0.1)
        status = cc.status()

        assert status["active"] is True
        assert status["stream_id"] == "test-stream-001"
        assert status["resolution"] == [640, 480]
        assert "uptime_seconds" in status
        cc.stop()


# ---------------------------------------------------------------------------
# Reconnect behavior
# ---------------------------------------------------------------------------

class TestReconnect:
    @patch("service.camera_capture.cv2")
    def test_stops_after_max_reconnect_failures(self, mock_cv2, mock_stream_manager, mock_config):
        """Capture loop should exit after camera_reconnect_attempts consecutive failures."""
        call_count = 0

        def failing_read():
            nonlocal call_count
            call_count += 1
            # First read succeeds (for start() probe), then all fail
            if call_count == 1:
                return (True, _make_fake_frame())
            return (False, None)

        cap_instance = MagicMock()
        cap_instance.isOpened.return_value = True
        cap_instance.read.side_effect = failing_read
        mock_cv2.VideoCapture.return_value = cap_instance

        cc = CameraCapture(mock_stream_manager, mock_config)
        cc.start()
        # Wait for reconnect attempts to exhaust (fast due to 0.01s delay)
        time.sleep(0.5)

        status = cc.status()
        assert status["active"] is False  # thread should have exited
        cc.stop()  # cleanup
