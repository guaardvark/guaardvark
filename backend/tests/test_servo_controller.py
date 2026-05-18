import unittest
from unittest.mock import MagicMock, patch
from PIL import Image


class TestServoController(unittest.TestCase):
    def setUp(self):
        self._archive_patcher = patch("backend.services.servo_controller.get_servo_archive")
        mocked = self._archive_patcher.start()
        archive = MagicMock()
        mocked.return_value = archive
        self._dom_patcher = patch(
            "backend.services.servo_controller.ServoController._lookup_dom_coordinates",
            return_value=None,
        )
        self._dom_patcher.start()

    def tearDown(self):
        self._dom_patcher.stop()
        self._archive_patcher.stop()

    def _make_screen(self, cursor_pos=(640, 360)):
        screen = MagicMock()
        screen.capture.return_value = (Image.new("RGB", (1024, 1024)), cursor_pos)
        screen.move.return_value = {"success": True}
        screen.click.return_value = {"success": True}
        screen.cursor_position.return_value = cursor_pos
        screen.screen_size.return_value = (1024, 1024)
        return screen

    def _make_analyzer(self, responses):
        """Build a mock analyzer.

        Responses are consumed in order across BOTH analyze() and analyze_fullsize().
        The hallucination guard calls analyze(), detection calls analyze_fullsize(),
        and correction checks call analyze().
        """
        analyzer = MagicMock()
        results = []
        for resp in responses:
            r = MagicMock()
            r.success = True
            r.description = resp
            r.model_used = "test-model"
            r.inference_ms = 100
            results.append(r)
        # Share the same response queue across both methods
        shared_iter = iter(results)
        analyzer.analyze.side_effect = lambda *a, **kw: next(shared_iter)
        analyzer.analyze_fullsize.side_effect = lambda *a, **kw: next(shared_iter)
        return analyzer

    @patch("time.sleep")
    def test_on_target_first_try_clicks_immediately(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        same_img = Image.new("RGB", (1024, 1024), color=(50, 50, 50))
        diff_img = Image.new("RGB", (1024, 1024), color=(200, 200, 200))
        screen.capture.return_value = (same_img, (400, 250))
        analyzer = self._make_analyzer([
            '```json\n[{"point": [400, 250], "label": "button"}]\n```',  # box_2d detection
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is True
        assert result["target_found"] is True
        assert result["click_issued"] is True
        assert result["verified"] is False
        screen.click.assert_called_once()

    @patch("time.sleep")
    def test_failed_move_does_not_issue_click(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        same_img = Image.new("RGB", (1024, 1024), color=(50, 50, 50))
        screen.capture.return_value = (same_img, (400, 250))
        screen.move.return_value = {"success": False, "error": "display gone"}
        analyzer = self._make_analyzer([
            '```json\n[{"point": [400, 250], "label": "button"}]\n```',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is False
        assert result["target_found"] is True
        assert result["click_issued"] is False
        assert result["reason"] == "move_failed"
        screen.click.assert_not_called()

    @patch("time.sleep")
    def test_max_corrections_all_attempts_exhausted(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        same_img = Image.new("RGB", (1024, 1024), color=(50, 50, 50))
        screen.capture.return_value = (same_img, (400, 250))
        analyzer = self._make_analyzer(['[]'])
        servo = ServoController(screen, analyzer, max_corrections=4)
        result = servo.click_target("Reply button")
        assert result["success"] is False
        assert result["verified"] is False
        assert result["target_found"] is False
        assert result["reason"] == "target_not_visible"

    @patch("time.sleep")
    def test_click_failure_keeps_found_but_not_issued(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        same_img = Image.new("RGB", (1024, 1024), color=(50, 50, 50))
        screen.capture.return_value = (same_img, (400, 250))
        screen.click.return_value = {"success": False, "error": "click failed"}
        analyzer = self._make_analyzer([
            '```json\n[{"point": [400, 250], "label": "button"}]\n```',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is False
        assert result["target_found"] is True
        assert result["click_issued"] is False
        assert result["reason"] == "click_failed"

    @patch("time.sleep")
    def test_verification_detects_screen_change(self, mock_sleep):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        same_img = Image.new("RGB", (1024, 1024), color=(50, 50, 50))
        diff_img = Image.new("RGB", (1024, 1024), color=(200, 200, 200))
        screen.capture.return_value = (same_img, (400, 250))
        analyzer = self._make_analyzer([
            '```json\n[{"point": [400, 250], "label": "button"}]\n```',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["success"] is True
        assert result["post_action_effect"] == "pending_observation"

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
        img_a = Image.new("RGB", (1024, 1024), color=(50, 50, 50))
        img_b = Image.new("RGB", (1024, 1024), color=(200, 200, 200))
        assert ServoController._screen_changed(img_a, img_b) is True
        assert ServoController._screen_changed(img_a, img_a) is False


class TestParseDetectionCoordOrder(unittest.TestCase):
    """vision_config['coord_order'] decides how box_2d / point arrays are read.

    Gemma4 via Ollama returns coordinates in Google's published box_2d format,
    which is y-first: [y1, x1, y2, x2]. The 'empirically verified xy-first'
    comment in the prior code was wrong and produced the 2026-05 click-cluster
    bug where every servo aim landed on the top-centre wallpaper regardless
    of the actual target.
    """

    def _servo(self, coord_order="xy", internal_width=0):
        # Bypass __init__ so the test stays a pure parse check — no screen,
        # no analyzer, no live config detection.
        from backend.services.servo_controller import ServoController
        s = ServoController.__new__(ServoController)
        s._vision_config = {
            "coord_order": coord_order,
            "internal_width": internal_width,
            "scale_x": 1.0,
            "scale_y": 1.0,
        }
        s.screen_w = 1000
        s.screen_h = 1000
        return s

    def test_box_2d_xy_order_reads_x_first(self):
        s = self._servo(coord_order="xy")
        coords = s._parse_detection_response(
            '```json\n[{"box_2d": [599, 37, 631, 83], "label": "Firefox"}]\n```'
        )
        # [x1, y1, x2, y2] → center (615, 60)
        self.assertEqual(coords, (615, 60))

    def test_box_2d_yx_order_reads_y_first(self):
        s = self._servo(coord_order="yx")
        coords = s._parse_detection_response(
            '```json\n[{"box_2d": [599, 37, 631, 83], "label": "Firefox"}]\n```'
        )
        # [y1, x1, y2, x2] → center (60, 615)
        self.assertEqual(coords, (60, 615))

    def test_point_xy_order_reads_x_first(self):
        s = self._servo(coord_order="xy")
        coords = s._parse_detection_response('[{"point": [614, 64], "label": "X"}]')
        self.assertEqual(coords, (614, 64))

    def test_point_yx_order_still_reads_x_first(self):
        s = self._servo(coord_order="yx")
        coords = s._parse_detection_response('[{"point": [614, 64], "label": "X"}]')
        self.assertEqual(coords, (614, 64))

    def test_box_2d_with_internal_grid_and_yx(self):
        # Some models normalize to 1024; when y-first the
        # scaling still has to apply to the right axis.
        s = self._servo(coord_order="yx", internal_width=1000)
        coords = s._parse_detection_response(
            '[{"box_2d": [500, 100, 600, 200], "label": "X"}]'
        )
        # y1=500,x1=100,y2=600,x2=200 → norm centres (cx=0.15, cy=0.55)
        # → on 1000x1000 screen: (150, 550)
        self.assertEqual(coords, (150, 550))
