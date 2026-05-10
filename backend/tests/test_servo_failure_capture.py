"""
Tests for servo failure trace capture.

When the servo controller exhausts click corrections and gives up, it must save
the last screenshot + sidecar JSON (target_description, corrections_log,
vision_model, reason) to data/training/failures/.
"""
import json
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image


def test_capture_servo_failure_writes_image_and_metadata(tmp_path, monkeypatch):
    """Failure capture saves screenshot + sidecar JSON."""
    from backend.services import training_data_collector as tdc

    # Redirect output dir into tmp_path
    fake_root = tmp_path
    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    img = Image.new("RGB", (100, 100), color="red")
    out = tdc.capture_servo_failure(
        screenshot=img,
        target_description="red submit button",
        corrections_log=["attempt 1 missed", "attempt 2 missed"],
        vision_model="gemma4:e4b",
        reason="screen_unchanged",
    )
    
    assert out is not None
    assert out.exists()
    assert out.suffix == ".webp"
    
    sidecar = out.with_suffix(".json")
    assert sidecar.exists()
    
    meta = json.loads(sidecar.read_text())
    assert meta["target_description"] == "red submit button"
    assert meta["vision_model"] == "gemma4:e4b"
    assert meta["reason"] == "screen_unchanged"
    assert len(meta["corrections_log"]) == 2


def test_capture_servo_failure_swallows_save_errors(tmp_path, monkeypatch, caplog):
    """Save errors must not break the calling abort path."""
    from backend.services import training_data_collector as tdc
    import logging

    # Make the directory unwritable after creation
    fake_root = tmp_path / "readonly"
    fake_root.mkdir()
    failures_dir = fake_root / "data" / "training" / "failures"
    failures_dir.mkdir(parents=True)
    failures_dir.chmod(0o000)
    
    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    img = Image.new("RGB", (10, 10))
    
    # Should NOT raise, returns None on failure
    with caplog.at_level(logging.WARNING):
        result = tdc.capture_servo_failure(
            screenshot=img,
            target_description="x",
            corrections_log=[],
            vision_model="m",
            reason="r",
        )
    
    # Should either return None or fail silently
    # The key is: no exception escaped
    assert result is None or not result.exists()
    
    # Verify warning was logged
    assert any("failure-trace" in record.message for record in caplog.records)


def test_capture_servo_failure_handles_numpy_array(tmp_path, monkeypatch):
    """Failure capture works with numpy arrays (common screenshot format)."""
    from backend.services import training_data_collector as tdc
    import numpy as np

    fake_root = tmp_path
    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    # Create a numpy array
    arr = np.zeros((50, 50, 3), dtype=np.uint8)
    arr[:, :] = [255, 0, 0]  # Red

    out = tdc.capture_servo_failure(
        screenshot=arr,
        target_description="test target",
        corrections_log=[],
        vision_model="test_model",
        reason="test_reason",
    )
    
    assert out is not None
    assert out.exists()
    
    # Verify it's a valid image
    img = Image.open(out)
    assert img.size == (50, 50)
