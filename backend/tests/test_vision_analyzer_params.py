import unittest
from unittest.mock import patch, MagicMock
from PIL import Image


class TestVisionAnalyzerParams(unittest.TestCase):

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_uses_default_options(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test description"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        img = Image.new("RGB", (100, 100))
        analyzer.analyze(img, prompt="test")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 256
        assert call_json["options"]["temperature"] == 0.3

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_accepts_num_predict_override(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        img = Image.new("RGB", (100, 100))
        analyzer.analyze(img, prompt="test", num_predict=32)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 32

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_accepts_temperature_override(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        img = Image.new("RGB", (100, 100))
        analyzer.analyze(img, prompt="test", temperature=0.1)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["temperature"] == 0.1

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_base64_uses_default_options(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test description"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        analyzer.analyze_base64("dGVzdA==", prompt="test")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 256
        assert call_json["options"]["temperature"] == 0.3

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_base64_accepts_num_predict_override(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        analyzer.analyze_base64("dGVzdA==", prompt="test", num_predict=64)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 64

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_base64_accepts_temperature_override(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        analyzer.analyze_base64("dGVzdA==", prompt="test", temperature=0.2)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["temperature"] == 0.2


if __name__ == "__main__":
    unittest.main()
