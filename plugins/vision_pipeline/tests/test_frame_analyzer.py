import pytest
import json
from unittest.mock import patch, MagicMock
from PIL import Image
import io
import base64

from service.frame_analyzer import FrameAnalyzer, FrameAnalysis


def _make_frame(color="red", size=(64, 64)):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class TestFrameAnalyzer:
    @patch("service.frame_analyzer.requests.post")
    def test_analyze_returns_frame_analysis(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": "A person sitting at a desk"},
            "done": True
        }
        mock_post.return_value = mock_resp

        fa = FrameAnalyzer(ollama_url="http://localhost:11434")
        result = fa.analyze(_make_frame(), "moondream", "Describe what you see.")

        assert isinstance(result, FrameAnalysis)
        assert result.description == "A person sitting at a desk"
        assert result.model_used == "moondream"
        assert result.inference_ms >= 0

    @patch("service.frame_analyzer.requests.post")
    def test_analyze_direct_uses_user_message(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": "I see a red square"},
            "done": True
        }
        mock_post.return_value = mock_resp

        fa = FrameAnalyzer(ollama_url="http://localhost:11434")
        fa.escalation_model = "llava:13b"
        result = fa.analyze_direct(_make_frame(), "What do you see?")

        call_body = mock_post.call_args[1]["json"]
        assert call_body["messages"][0]["content"] == "What do you see?"
        assert result.description == "I see a red square"

    @patch("service.frame_analyzer.requests.post")
    def test_analyze_handles_ollama_error(self, mock_post):
        mock_post.side_effect = Exception("Connection refused")

        fa = FrameAnalyzer(ollama_url="http://localhost:11434")
        result = fa.analyze(_make_frame(), "moondream", "Describe.")
        assert result.description == ""
        assert result.inference_ms >= 0
