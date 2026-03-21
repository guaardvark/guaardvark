# backend/tests/test_vision_analyzer.py
#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestVisionAnalyzer(unittest.TestCase):

    def test_encode_image_returns_base64_string(self):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image
        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        b64 = analyzer.encode_image(img)
        self.assertIsInstance(b64, str)
        self.assertTrue(len(b64) > 0)

    def test_encode_image_respects_max_width(self):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image
        analyzer = VisionAnalyzer(max_width=256)
        img = Image.new("RGB", (1920, 1080), color=(0, 0, 0))
        b64 = analyzer.encode_image(img)
        # Decode and check dimensions
        import base64
        from io import BytesIO
        decoded = Image.open(BytesIO(base64.b64decode(b64)))
        self.assertEqual(decoded.width, 256)

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_calls_ollama_correctly(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "A red square on white background"}
        }
        mock_post.return_value = mock_response

        analyzer = VisionAnalyzer(ollama_url="http://localhost:11434")
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        result = analyzer.analyze(img, prompt="What do you see?")

        self.assertEqual(result.description, "A red square on white background")
        self.assertEqual(result.model_used, "qwen3-vl:2b-instruct")

        # Verify Ollama was called with correct structure
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], "http://localhost:11434/api/chat")
        payload = call_args[1]["json"]
        self.assertEqual(payload["model"], "qwen3-vl:2b-instruct")
        self.assertEqual(len(payload["messages"]), 1)
        self.assertIn("images", payload["messages"][0])

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_with_custom_model(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Detailed description"}
        }
        mock_post.return_value = mock_response

        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100))
        result = analyzer.analyze(img, prompt="Describe", model="llava:13b")

        self.assertEqual(result.model_used, "llava:13b")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "llava:13b")

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_handles_timeout(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer, VisionResult
        from PIL import Image
        import requests

        mock_post.side_effect = requests.Timeout("Connection timed out")

        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100))
        result = analyzer.analyze(img, prompt="What is this?")

        self.assertFalse(result.success)
        self.assertIn("timed out", result.error.lower())

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_handles_connection_error(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image
        import requests

        mock_post.side_effect = requests.ConnectionError("Ollama not running")

        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100))
        result = analyzer.analyze(img, prompt="Describe")

        self.assertFalse(result.success)
        self.assertIn("connection", result.error.lower())

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_text_query_calls_ollama_without_images(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": '{"action": "click", "target_cell": "D4"}'}
        }
        mock_post.return_value = mock_response

        analyzer = VisionAnalyzer()
        result = analyzer.text_query("Decide next action", model="llama3:8b")

        self.assertTrue(result.success)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "llama3:8b")
        # text_query should NOT include images
        self.assertNotIn("images", payload["messages"][0])

    @patch("backend.utils.vision_analyzer.requests.get")
    def test_get_decision_model_prefers_text_models(self, mock_get):
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "moondream"},
                {"name": "llava:7b"},
                {"name": "qwen3:8b"},
                {"name": "llama3:8b"},
            ]
        }
        mock_get.return_value = mock_response

        analyzer = VisionAnalyzer()
        model = analyzer._get_decision_model()
        self.assertEqual(model, "qwen3:8b")  # First preferred text model


if __name__ == "__main__":
    unittest.main()
