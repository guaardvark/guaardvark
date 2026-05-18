import json
import os
import tempfile
import unittest
from PIL import Image


class TestTrainingDataCollector(unittest.TestCase):

    def test_record_creates_log_entry(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1024, 1024))
            collector.record(
                screenshot_before=img,
                crosshair_pos=(400, 300),
                target_description="Reply button",
                target_actual=(412, 287),
                corrections=[{"direction": "right", "distance": "small", "pixels": 10}],
                success=True,
            )
            log_files = [f for f in os.listdir(os.path.join(tmpdir, "servo_logs")) if f.endswith(".jsonl")]
            assert len(log_files) == 1
            with open(os.path.join(tmpdir, "servo_logs", log_files[0])) as f:
                entry = json.loads(f.readline())
            assert entry["crosshair_pos"] == [400, 300]
            assert entry["target_actual"] == [412, 287]
            assert entry["success"] is True

    def test_record_saves_screenshot(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1024, 1024), color=(255, 0, 0))
            collector.record(
                screenshot_before=img,
                crosshair_pos=(100, 100),
                target_description="test",
                target_actual=(100, 100),
                corrections=[],
                success=True,
            )
            screenshots = os.listdir(os.path.join(tmpdir, "screenshots"))
            assert len(screenshots) == 1
            assert screenshots[0].endswith(".jpg")

    def test_mark_unreliable(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1024, 1024))
            collector.record(
                screenshot_before=img,
                crosshair_pos=(400, 300),
                target_description="button",
                target_actual=(400, 300),
                corrections=[],
                success=False,
            )
            log_files = [f for f in os.listdir(os.path.join(tmpdir, "servo_logs")) if f.endswith(".jsonl")]
            with open(os.path.join(tmpdir, "servo_logs", log_files[0])) as f:
                entry = json.loads(f.readline())
            assert entry["success"] is False

    def test_stats(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1024, 1024))
            for i in range(3):
                collector.record(
                    screenshot_before=img,
                    crosshair_pos=(i*100, i*100),
                    target_description=f"target_{i}",
                    target_actual=(i*100+5, i*100+5),
                    corrections=[],
                    success=i < 2,
                )
            stats = collector.stats()
            assert stats["total"] == 3
            assert stats["successful"] == 2
