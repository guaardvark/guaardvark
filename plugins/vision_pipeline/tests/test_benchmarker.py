import pytest
from unittest.mock import patch, MagicMock
from service.benchmarker import Benchmarker, BenchmarkResult


class TestBenchmarker:
    @patch("service.benchmarker.requests.post")
    @patch("service.benchmarker.requests.get")
    def test_benchmark_single_model(self, mock_get, mock_post):
        # Mock Ollama model list
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"models": [{"name": "moondream"}]})
        )
        # Mock Ollama inference
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "message": {"content": "A red square on white background"},
                "done": True
            })
        )

        b = Benchmarker(ollama_url="http://localhost:11434")
        results = list(b.run(models=["moondream"], frame_count=2, resolutions=[(64, 64)]))

        assert len(results) == 1
        assert isinstance(results[0], BenchmarkResult)
        assert results[0].model == "moondream"
        assert results[0].sustainable_fps > 0

    def test_quality_score_rubric(self):
        from service.benchmarker import _compute_quality_score
        # Long description with entities and spatial refs
        score = _compute_quality_score(
            "A red car is parked on the left side of the road next to a blue truck behind the building"
        )
        assert score >= 5  # tokens + entities + spatial

    def test_role_assignment(self):
        from service.benchmarker import _assign_role
        assert _assign_role(100) == "monitor"
        assert _assign_role(400) == "escalation"
        assert _assign_role(1200) == "too_slow"
