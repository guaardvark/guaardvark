"""
Tests for pasted image analysis through vision model.
Prevents regression: LLM must not hallucinate about pasted images.
The image must go through moondream/qwen3-vl for a real description.
"""
import base64
import io
import pytest
from unittest.mock import patch, MagicMock

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def _make_test_image_b64(width=64, height=64, color=(255, 0, 0)):
    """Create a small test image and return base64-encoded JPEG."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestVisionAnalyzer:
    """Tests for backend/utils/vision_analyzer.py"""

    def test_auto_detect_prefers_moondream(self):
        """moondream should be preferred over qwen3-vl for speed."""
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:latest"},
                {"name": "qwen3-vl:2b-instruct"},
                {"name": "moondream:latest"},
            ]
        }
        with patch("requests.get", return_value=mock_response):
            analyzer = VisionAnalyzer()
            assert analyzer.default_model == "moondream:latest"

    def test_auto_detect_falls_back_to_qwen_vl(self):
        """If moondream is unavailable, fall back to qwen3-vl."""
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:latest"},
                {"name": "qwen3-vl:2b-instruct"},
            ]
        }
        with patch("requests.get", return_value=mock_response):
            analyzer = VisionAnalyzer()
            assert analyzer.default_model == "qwen3-vl:2b-instruct"

    def test_encode_image_resizes_large_images(self):
        """Images wider than max_width should be resized."""
        from backend.utils.vision_analyzer import VisionAnalyzer

        analyzer = VisionAnalyzer(default_model="moondream:latest", max_width=128)
        large_img = Image.new("RGB", (1920, 1080), (0, 128, 255))
        b64 = analyzer.encode_image(large_img)
        # Decode and check size
        decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert decoded.width == 128
        assert decoded.height == 72  # 1080 * (128/1920)

    def test_analyze_calls_ollama_with_image(self):
        """analyze() should POST to Ollama with image data."""
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "A red square on white background"}
        }

        analyzer = VisionAnalyzer(default_model="moondream:latest")
        img = Image.new("RGB", (64, 64), (255, 0, 0))

        with patch("requests.post", return_value=mock_response) as mock_post:
            result = analyzer.analyze(img, "Describe this image")

        assert result.success is True
        assert "red" in result.description.lower() or result.description  # moondream response
        # Verify the call included images
        call_args = mock_post.call_args
        messages = call_args.kwargs.get("json", call_args[1].get("json", {}))["messages"]
        assert "images" in messages[0]
        assert len(messages[0]["images"]) == 1


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestUnifiedChatImagePath:
    """Tests that unified_chat_engine routes pasted images through vision model."""

    def test_analyze_pasted_image_returns_description(self):
        """_analyze_pasted_image should return vision model's description."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        image_b64 = _make_test_image_b64()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.description = "A solid red square"
        mock_result.model_used = "moondream:latest"
        mock_result.inference_ms = 150

        with patch("backend.utils.vision_analyzer.VisionAnalyzer") as MockAnalyzer:
            MockAnalyzer.return_value.analyze.return_value = mock_result
            desc = engine._analyze_pasted_image(image_b64, "What is this?")

        assert desc == "A solid red square"

    def test_analyze_pasted_image_returns_none_on_failure(self):
        """If both PIL and base64 fallback fail, return None (don't crash)."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        image_b64 = _make_test_image_b64()

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.description = ""
        mock_result.error = "Model not loaded"

        with patch("backend.utils.vision_analyzer.VisionAnalyzer") as MockAnalyzer:
            MockAnalyzer.return_value.analyze.return_value = mock_result
            MockAnalyzer.return_value.analyze_base64.return_value = mock_result
            desc = engine._analyze_pasted_image(image_b64, "What is this?")

        assert desc is None

    def test_analyze_pasted_image_falls_back_to_base64(self):
        """If PIL cannot decode the image, fall back to analyze_base64."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        # Use valid base64 but of a format PIL might not support
        raw_bytes = b"\x00\x00\x00\x20ftypavif"  # AVIF-like header
        image_b64 = base64.b64encode(raw_bytes).decode()

        mock_pil_fail = MagicMock()
        mock_pil_fail.success = False
        mock_pil_fail.error = "PIL decode failed"
        mock_pil_fail.description = ""

        mock_b64_ok = MagicMock()
        mock_b64_ok.success = True
        mock_b64_ok.description = "A colorful image"
        mock_b64_ok.model_used = "moondream:latest"
        mock_b64_ok.inference_ms = 200

        with patch("backend.utils.vision_analyzer.VisionAnalyzer") as MockAnalyzer:
            # PIL path will throw because Image.open fails on fake AVIF
            MockAnalyzer.return_value.analyze_base64.return_value = mock_b64_ok
            desc = engine._analyze_pasted_image(image_b64, "What is this?")

        assert desc == "A colorful image"

    def test_analyze_pasted_image_handles_corrupt_data(self):
        """Corrupt base64 should return None, not crash."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        # Both PIL and base64 fallback should fail gracefully
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.description = ""
        mock_result.error = "Invalid image"

        with patch("backend.utils.vision_analyzer.VisionAnalyzer") as MockAnalyzer:
            MockAnalyzer.return_value.analyze_base64.return_value = mock_result
            desc = engine._analyze_pasted_image("not-valid-base64!!!", "What is this?")
        assert desc is None


@pytest.mark.skipif(not PIL_AVAILABLE, reason="PIL not available")
class TestVisionAnalyzerBase64:
    """Tests for VisionAnalyzer.analyze_base64 method."""

    def test_analyze_base64_calls_ollama(self):
        """analyze_base64 should POST to Ollama with raw base64 image."""
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "A blue rectangle"}
        }

        analyzer = VisionAnalyzer(default_model="moondream:latest")
        test_b64 = _make_test_image_b64(color=(0, 0, 255))

        with patch("requests.post", return_value=mock_response) as mock_post:
            result = analyzer.analyze_base64(test_b64, "What is this?")

        assert result.success is True
        assert result.description == "A blue rectangle"
        call_json = mock_post.call_args.kwargs.get("json", mock_post.call_args[1].get("json", {}))
        assert call_json["messages"][0]["images"] == [test_b64]

    def test_analyze_base64_handles_timeout(self):
        """analyze_base64 should handle timeouts gracefully."""
        import requests as req_mod
        from backend.utils.vision_analyzer import VisionAnalyzer

        analyzer = VisionAnalyzer(default_model="moondream:latest")

        with patch("requests.post", side_effect=req_mod.Timeout("timed out")):
            result = analyzer.analyze_base64("dGVzdA==", "Describe")

        assert result.success is False
        assert "timed out" in result.error.lower()
