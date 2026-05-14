import unittest
from unittest.mock import MagicMock

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


class TestVisionConfigSelection(unittest.TestCase):
    def test_qwen_aliases_do_not_cross_match(self):
        from backend.services.servo_knowledge_store import get_vision_config

        two_b = get_vision_config("qwen3-vl:2b")
        eight_b = get_vision_config("qwen3-vl:8b")

        self.assertEqual(two_b["source"], "16_9_screen_calibration_2026_04_10")
        self.assertEqual(eight_b["source"], "16_9_screen_calibration_2026_04_10")
        self.assertIsNot(two_b, eight_b)


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


if __name__ == "__main__":
    unittest.main()
