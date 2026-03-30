"""Test that TrainingDataCollector supports source tagging."""
import json
import tempfile
from pathlib import Path
from PIL import Image
from backend.services.training_data_collector import TrainingDataCollector


def test_record_with_source_tag():
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = TrainingDataCollector(base_dir=tmpdir)
        img = Image.new("RGB", (100, 100), color="red")
        collector.record(
            screenshot_before=img,
            crosshair_pos=(50, 50),
            target_description="test button",
            target_actual=(55, 48),
            corrections=[],
            success=True,
            app_context="test",
            source="human_demonstration",
        )
        log_files = list(Path(tmpdir).glob("servo_logs/*.jsonl"))
        assert len(log_files) == 1
        with open(log_files[0]) as f:
            entry = json.loads(f.readline())
        assert entry["source"] == "human_demonstration"


def test_record_default_source_is_servo():
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = TrainingDataCollector(base_dir=tmpdir)
        img = Image.new("RGB", (100, 100), color="blue")
        collector.record(
            screenshot_before=img,
            crosshair_pos=(50, 50),
            target_description="test button",
            target_actual=(55, 48),
            corrections=[],
            success=True,
        )
        log_files = list(Path(tmpdir).glob("servo_logs/*.jsonl"))
        with open(log_files[0]) as f:
            entry = json.loads(f.readline())
        assert entry["source"] == "servo"
