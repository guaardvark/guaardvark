import pytest
from unittest.mock import patch, MagicMock
from service.benchmarker import Benchmarker, BenchmarkResult


class TestBenchmarker:
    @patch("service.benchmarker.requests.post")
    def test_benchmark_single_model(self, mock_post):
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
        assert not hasattr(results[0], "quality_score")

    def test_discovery_uses_capabilities(self):
        caps = MagicMock()
        caps.vision_models.return_value = ["qwen3.5:4b", "gemma4:12b"]
        b = Benchmarker(ollama_url="http://localhost:11434", capabilities=caps)
        assert b._discover_vision_models() == ["qwen3.5:4b", "gemma4:12b"]

    def test_recommendations_are_by_latency(self):
        b = Benchmarker(ollama_url="http://localhost:11434")
        b.results = [
            BenchmarkResult("fast", (512, 512), 150, 100, 200, 6.6, 0, "monitor"),
            BenchmarkResult("mid-a", (512, 512), 500, 400, 600, 2.0, 0, "escalation"),
            BenchmarkResult("mid-b", (512, 512), 300, 200, 400, 3.3, 0, "escalation"),
        ]
        rec = b.get_recommendations()
        assert rec["monitor_model"] == "fast"
        assert rec["escalation_model"] == "mid-b"

    def test_save_results_writes_a_run(self, tmp_path):
        b = Benchmarker(ollama_url="http://localhost:11434")
        b.results = [BenchmarkResult("fast", (512, 512), 150, 100, 200, 6.6, 0, "monitor")]
        path = tmp_path / "benchmark_results.json"
        b.save_results(str(path))
        import json
        data = json.loads(path.read_text())
        assert data["last_run"] and data["results"][0]["model"] == "fast"

    def test_role_assignment(self):
        from service.benchmarker import _assign_role
        assert _assign_role(100) == "monitor"
        assert _assign_role(400) == "escalation"
        assert _assign_role(1200) == "too_slow"
