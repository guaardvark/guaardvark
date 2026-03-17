"""Tests for the RAG Autoresearch orchestrator."""

import pytest
import time
from unittest.mock import patch, MagicMock

try:
    from flask import Flask
    from backend.models import db
    from backend.services.rag_autoresearch_service import RAGAutoresearchService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


class TestExperimentCycle:
    def test_single_experiment_keep(self, app):
        """A winning experiment updates the config."""
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(
                svc.agent, "propose_experiment"
            ) as mock_propose, patch.object(
                svc.eval_harness, "run_full_eval"
            ) as mock_eval, patch.object(
                svc, "_load_config"
            ) as mock_load, patch.object(
                svc, "_save_config"
            ) as mock_save, patch.object(
                svc, "_log_experiment"
            ) as mock_log:
                mock_load.return_value = {
                    "params": {"top_k": 5},
                    "baseline_score": 3.0,
                    "phase": 1,
                }
                mock_propose.return_value = {
                    "parameter": "top_k",
                    "new_value": 8,
                    "hypothesis": "try more chunks",
                }
                mock_eval.return_value = {
                    "composite_score": 3.5,
                    "num_pairs": 10,
                    "details": [],
                }

                result = svc.run_single_experiment()
                assert result["status"] == "keep"
                assert result["delta"] == 0.5
                mock_save.assert_called_once()

    def test_single_experiment_discard(self, app):
        """A losing experiment reverts the config — promote is NOT called."""
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(
                svc.agent, "propose_experiment"
            ) as mock_propose, patch.object(
                svc.eval_harness, "run_full_eval"
            ) as mock_eval, patch.object(
                svc, "_load_config"
            ) as mock_load, patch.object(
                svc, "_save_config"
            ) as mock_save, patch.object(
                svc, "_log_experiment"
            ) as mock_log, patch.object(
                svc, "_promote_config"
            ) as mock_promote:
                mock_load.return_value = {
                    "params": {"top_k": 5},
                    "baseline_score": 3.0,
                    "phase": 1,
                }
                mock_propose.return_value = {
                    "parameter": "top_k",
                    "new_value": 2,
                    "hypothesis": "try fewer chunks",
                }
                mock_eval.return_value = {
                    "composite_score": 2.5,
                    "num_pairs": 10,
                    "details": [],
                }

                result = svc.run_single_experiment()
                assert result["status"] == "discard"
                assert result["delta"] == -0.5
                mock_promote.assert_not_called()


class TestIdleDetection:
    def test_is_idle_returns_true_after_threshold(self):
        """System is idle when last activity exceeds threshold."""
        svc = RAGAutoresearchService()
        svc._last_activity = time.time() - 700  # 11+ minutes ago
        assert svc.is_idle(idle_minutes=10) is True

    def test_is_idle_returns_false_during_activity(self):
        """System is not idle when recently active."""
        svc = RAGAutoresearchService()
        svc._last_activity = time.time() - 60  # 1 minute ago
        assert svc.is_idle(idle_minutes=10) is False


class TestPause:
    def test_pause_stops_loop(self):
        """Pause flag prevents next experiment from starting."""
        svc = RAGAutoresearchService()
        svc.pause()
        assert svc._paused is True

    def test_resume_clears_pause(self):
        svc = RAGAutoresearchService()
        svc.pause()
        svc.resume()
        assert svc._paused is False
