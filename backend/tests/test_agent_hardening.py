import unittest
from unittest.mock import MagicMock
import json
from pathlib import Path
import tempfile

from PIL import Image


class TestAgentKnowledgeValidator(unittest.TestCase):
    def test_rejects_coordinate_click_steps(self):
        from backend.services.agent_knowledge_validator import validate_recipe

        result = validate_recipe("bad", {
            "description": "bad coordinate recipe",
            "triggers": [r"^click\s+(\d+),(\d+)\s*$"],
            "steps": [{"action": "click", "x": "{1}", "y": "{2}"}],
        })

        self.assertFalse(result.ok)
        self.assertTrue(any("coordinates" in msg for msg in result.error_messages()))

    def test_accepts_short_vision_actionable_click_recipe(self):
        from backend.services.agent_knowledge_validator import validate_recipe

        result = validate_recipe("good", {
            "description": "Open Firefox",
            "triggers": [r"^open\s+firefox\s*$"],
            "steps": [{"action": "click", "target_description": "Firefox icon"}],
        })

        self.assertTrue(result.ok, result.error_messages())

    def test_current_recipe_library_has_no_validation_errors(self):
        from backend.services.agent_knowledge_validator import validate_recipe_library

        recipes_path = Path(__file__).resolve().parents[2] / "data" / "agent" / "recipes.json"
        recipes = json.loads(recipes_path.read_text())
        result = validate_recipe_library(recipes)

        self.assertTrue(result.ok, result.error_messages())


class TestVisionConfigSelection(unittest.TestCase):
    @unittest.skip("vision-model aliases removed 2026-05-16")
    def test_vision_model_aliases_do_not_cross_match(self):
        # Original test verified per-variant vision_config lookups; removed
        # along with the legacy multi-variant family on 2026-05-16.
        pass


class TestDisplayHealth(unittest.TestCase):
    def test_display_health_reports_healthy_capture(self):
        from backend.services.agent_control_service import AgentControlService

        screen = MagicMock()
        screen.display = ":99"
        screen.capture.return_value = (Image.new("RGB", (1024, 1024), color=(40, 40, 40)), (12, 34))

        result = AgentControlService().check_display_health(screen)

        self.assertTrue(result["success"])
        self.assertEqual(result["screen_size"], [1024, 1024])
        self.assertEqual(result["cursor_pos"], [12, 34])


class TestServoArchiveMetrics(unittest.TestCase):
    def test_run_metrics_aggregate_new_archive_fields(self):
        from backend.services.servo_knowledge_store import ServoArchive

        with tempfile.TemporaryDirectory() as tmp:
            old_root = __import__("os").environ.get("GUAARDVARK_ROOT")
            __import__("os").environ["GUAARDVARK_ROOT"] = tmp
            ServoArchive._instance = None
            try:
                archive = ServoArchive()
                archive.record(
                    target_description="Firefox icon",
                    model_used="gemma4:e4b",
                    raw_model_coords=(10, 20),
                    scaled_coords=(10, 20),
                    actual_click_coords=(10, 20),
                    scale_factor=(1.0, 1.0),
                    success=True,
                    target_found=True,
                    click_issued=True,
                    post_action_effect="verified",
                    parse_path="box_2d",
                    detection_source="vision",
                    inference_ms=120,
                )
                archive.record(
                    target_description="missing button",
                    model_used="gemma4:e4b",
                    raw_model_coords=(0, 0),
                    scaled_coords=(0, 0),
                    actual_click_coords=(0, 0),
                    scale_factor=(1.0, 1.0),
                    success=False,
                    target_found=False,
                    click_issued=False,
                    post_action_effect="not_checked",
                    parse_path="parse_failed",
                    detection_source="vision",
                    reason="target_not_visible",
                    inference_ms=80,
                )

                metrics = archive.get_run_metrics()
            finally:
                ServoArchive._instance = None
                if old_root is None:
                    __import__("os").environ.pop("GUAARDVARK_ROOT", None)
                else:
                    __import__("os").environ["GUAARDVARK_ROOT"] = old_root

        self.assertEqual(metrics["total"], 2)
        self.assertEqual(metrics["task_success_rate"], 50.0)
        self.assertEqual(metrics["verified_outcome_rate"], 50.0)
        self.assertEqual(metrics["target_not_visible_rate"], 50.0)
        self.assertEqual(metrics["parse_failure_rate"], 50.0)
        self.assertEqual(metrics["mean_vlm_latency_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
