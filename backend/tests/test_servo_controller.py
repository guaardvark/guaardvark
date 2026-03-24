import unittest
from unittest.mock import MagicMock, patch
from PIL import Image


class TestServoController(unittest.TestCase):

    def _make_screen(self, cursor_pos=(640, 360)):
        screen = MagicMock()
        screen.capture.return_value = (Image.new("RGB", (1280, 720)), cursor_pos)
        screen.move.return_value = {"success": True}
        screen.click.return_value = {"success": True}
        screen.cursor_position.return_value = cursor_pos
        return screen

    def _make_analyzer(self, responses):
        analyzer = MagicMock()
        results = []
        for resp in responses:
            r = MagicMock()
            r.success = True
            r.description = resp
            r.model_used = "test-model"
            r.inference_ms = 100
            results.append(r)
        analyzer.analyze.side_effect = results
        return analyzer

    @patch("time.sleep")
    def test_on_target_first_try_clicks_immediately(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        screenshots = [
            (Image.new("RGB", (1280, 720), color=(50, 50, 50)), (400, 250)),
            (Image.new("RGB", (1280, 720), color=(50, 50, 50)), (400, 250)),
            (Image.new("RGB", (1280, 720), color=(200, 200, 200)), (400, 250)),
        ]
        screen.capture.side_effect = screenshots
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": true}',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is True
        screen.click.assert_called_once()

    @patch("time.sleep")
    def test_one_correction_then_click(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        same_img = Image.new("RGB", (1280, 720), color=(50, 50, 50))
        diff_img = Image.new("RGB", (1280, 720), color=(200, 200, 200))
        screen.capture.side_effect = [
            (same_img, (400, 250)),  # ballistic capture
            (same_img, (400, 250)),  # on-target check (miss)
            # attempt 1 only gets 1 correction, so it clicks and verifies
            (same_img, (400, 250)),  # verify (no change) → retry attempt 2
            (same_img, (400, 250)),  # attempt 2 ballistic
            (same_img, (400, 250)),  # on-target check
            (diff_img, (400, 250)),  # verify (changed)
        ]
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": false, "direction": "right", "distance": "small"}',
            '{"x": 410, "y": 250}',
            '{"on_target": true}',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is True
        assert result["verified"] is True

    @patch("time.sleep")
    def test_max_corrections_all_attempts_exhausted(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        same_img = Image.new("RGB", (1280, 720), color=(50, 50, 50))
        # 3 attempts, each with captures for estimate + corrections + verify, all same image
        screen.capture.return_value = (same_img, (400, 250))
        # Lots of "not on target" responses
        responses = []
        for _ in range(20):
            responses.extend(['{"x": 400, "y": 250}', '{"on_target": false, "direction": "left", "distance": "small"}'])
        analyzer = self._make_analyzer(responses)
        servo = ServoController(screen, analyzer, max_corrections=4)
        result = servo.click_target("Reply button")
        # All attempts exhausted but still returns success=True (best effort)
        assert result["success"] is True
        assert result["verified"] is False

    @patch("time.sleep")
    def test_oscillation_dampening(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        same_img = Image.new("RGB", (1280, 720), color=(50, 50, 50))
        diff_img = Image.new("RGB", (1280, 720), color=(200, 200, 200))
        # attempt 1: ballistic + 1 correction (max for attempt 1) → verify miss
        # attempt 2: ballistic + corrections with direction reversal → verify hit
        screen.capture.side_effect = [
            (same_img, (400, 250)),  # attempt 1 ballistic
            (same_img, (400, 250)),  # attempt 1 correction 1 (right)
            (same_img, (400, 250)),  # attempt 1 verify (no change)
            (same_img, (400, 250)),  # attempt 2 ballistic
            (same_img, (400, 250)),  # attempt 2 correction 1 (right)
            (same_img, (400, 250)),  # attempt 2 correction 2 (left = reversal)
            (same_img, (400, 250)),  # attempt 2 correction 3 (on_target)
            (diff_img, (400, 250)),  # attempt 2 verify (changed)
        ]
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": false, "direction": "right", "distance": "large"}',
            '{"x": 400, "y": 250}',
            '{"on_target": false, "direction": "right", "distance": "large"}',
            '{"on_target": false, "direction": "left", "distance": "medium"}',
            '{"on_target": true}',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is True
        assert result["verified"] is True

    @patch("time.sleep")
    def test_verification_detects_screen_change(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        screenshots = [
            (Image.new("RGB", (1280, 720), color=(50, 50, 50)), (400, 250)),
            (Image.new("RGB", (1280, 720), color=(50, 50, 50)), (400, 250)),
            (Image.new("RGB", (1280, 720), color=(200, 200, 200)), (400, 250)),
        ]
        screen.capture.side_effect = screenshots
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": true}',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["verified"] is True

    def test_nudge_distances(self):
        from backend.services.servo_controller import ServoController
        assert ServoController._nudge_pixels("small") == 10
        assert ServoController._nudge_pixels("medium") == 40
        assert ServoController._nudge_pixels("large") == 80

    def test_parse_coordinate_response(self):
        from backend.services.servo_controller import ServoController
        servo = ServoController.__new__(ServoController)
        x, y = servo._parse_coordinates('{"x": 412, "y": 287}')
        assert x == 412
        assert y == 287

    def test_parse_correction_response(self):
        from backend.services.servo_controller import ServoController
        servo = ServoController.__new__(ServoController)
        correction = servo._parse_correction('{"on_target": false, "direction": "right_and_up", "dx": 12, "dy": -13}')
        assert correction["on_target"] is False
        assert correction["direction"] == "right_and_up"

    def test_screen_changed_detection(self):
        from backend.services.servo_controller import ServoController
        img_a = Image.new("RGB", (1280, 720), color=(50, 50, 50))
        img_b = Image.new("RGB", (1280, 720), color=(200, 200, 200))
        assert ServoController._screen_changed(img_a, img_b) is True
        assert ServoController._screen_changed(img_a, img_a) is False
