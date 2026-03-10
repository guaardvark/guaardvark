"""End-to-end test for the autoresearch experiment loop."""
import pytest
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


class TestEndToEnd:
    def test_full_experiment_cycle(self, app):
        """Complete cycle: propose -> eval -> keep/discard -> log.

        This test mocks at the lowest level (_call_llm, search) so the full
        propose -> eval -> score -> keep/discard pipeline runs end-to-end.
        """
        with app.app_context():
            svc = RAGAutoresearchService()

            with patch.object(svc.eval_harness, "_call_llm") as mock_llm, \
                 patch.object(svc.agent, "_call_llm") as mock_agent_llm, \
                 patch.object(svc.eval_harness, "_get_active_eval_pairs") as mock_pairs, \
                 patch.object(svc.eval_harness, "has_sufficient_corpus", return_value=True), \
                 patch("backend.services.indexing_service.search_with_llamaindex") as mock_search, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"), \
                 patch.object(svc, "_emit_socket_event"), \
                 patch.object(svc, "_broadcast_to_family"):

                # Provide a clean config so test is deterministic
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0,
                    "phase": 1, "phase_plateau_count": 0,
                }

                mock_pairs.return_value = [
                    {"id": "p1", "question": "q1", "expected_answer": "a1"},
                ]

                # Agent proposes: change top_k from 5 -> 8
                mock_agent_llm.return_value = (
                    '{"parameter": "top_k", "new_value": 8, "hypothesis": "test"}'
                )

                # RAG retrieval returns a chunk
                mock_search.return_value = [{"text": "relevant context chunk"}]

                # First call: LLM generates a response; second call: judge scores it
                mock_llm.side_effect = [
                    "The answer based on context.",
                    '{"relevance": 4, "grounding": 4, "completeness": 4}',
                ]

                result = svc.run_single_experiment()
                assert result["status"] in ("keep", "discard")
                assert result["parameter"] == "top_k"
                assert "composite_score" in result

    def test_keep_updates_config(self, app):
        """A winning experiment persists to config and DB."""
        with app.app_context():
            svc = RAGAutoresearchService()

            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config") as mock_save, \
                 patch.object(svc, "_log_experiment") as mock_log, \
                 patch.object(svc, "_emit_socket_event"), \
                 patch.object(svc, "_broadcast_to_family"), \
                 patch.object(svc, "_promote_config") as mock_promote:
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                    "phase_plateau_count": 0,
                }
                mock_propose.return_value = {
                    "parameter": "top_k", "new_value": 8,
                    "hypothesis": "try more chunks",
                }
                mock_eval.return_value = {
                    "composite_score": 3.5, "num_pairs": 10, "details": [],
                }

                result = svc.run_single_experiment()
                assert result["status"] == "keep"
                assert result["delta"] == 0.5
                mock_save.assert_called()
                mock_promote.assert_called_once()
                mock_log.assert_called_once()

    def test_discard_reverts_config(self, app):
        """A losing experiment does NOT promote config."""
        with app.app_context():
            svc = RAGAutoresearchService()

            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"), \
                 patch.object(svc, "_emit_socket_event"), \
                 patch.object(svc, "_broadcast_to_family"), \
                 patch.object(svc, "_promote_config") as mock_promote:
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                    "phase_plateau_count": 0,
                }
                mock_propose.return_value = {
                    "parameter": "top_k", "new_value": 2,
                    "hypothesis": "try fewer chunks",
                }
                mock_eval.return_value = {
                    "composite_score": 2.5, "num_pairs": 10, "details": [],
                }

                result = svc.run_single_experiment()
                assert result["status"] == "discard"
                assert result["delta"] == -0.5
                mock_promote.assert_not_called()

    def test_crash_is_logged(self, app):
        """Eval crash produces a crash result."""
        with app.app_context():
            svc = RAGAutoresearchService()

            with patch.object(svc.agent, "propose_experiment") as mock_propose, \
                 patch.object(svc.eval_harness, "run_full_eval") as mock_eval, \
                 patch.object(svc, "_load_config") as mock_load, \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment") as mock_log:
                mock_load.return_value = {
                    "params": {"top_k": 5}, "baseline_score": 3.0, "phase": 1,
                    "phase_plateau_count": 0,
                }
                mock_propose.return_value = {
                    "parameter": "top_k", "new_value": 8,
                    "hypothesis": "test",
                }
                mock_eval.side_effect = RuntimeError("GPU OOM")

                result = svc.run_single_experiment()
                assert result["status"] == "crash"
                mock_log.assert_called_once()

    def test_loop_respects_pause(self, app):
        """Loop stops when paused."""
        with app.app_context():
            svc = RAGAutoresearchService()
            svc._paused = True

            with patch.object(svc, "_check_prerequisites", return_value=True):
                svc.run_loop(max_experiments=5)
                assert not svc.is_running()

    def test_loop_runs_multiple_experiments(self, app):
        """Loop can run multiple experiments before stopping."""
        with app.app_context():
            svc = RAGAutoresearchService()
            call_count = 0

            def fake_experiment():
                nonlocal call_count
                call_count += 1
                return {
                    "experiment_id": f"exp-{call_count}",
                    "parameter": "top_k",
                    "status": "keep",
                    "composite_score": 3.0,
                }

            with patch.object(svc, "_check_prerequisites", return_value=True), \
                 patch.object(svc, "run_single_experiment", side_effect=fake_experiment), \
                 patch.object(svc, "_get_recent_history", return_value=[]):
                svc.run_loop(max_experiments=3)
                assert call_count == 3
                assert not svc.is_running()

    def test_loop_stops_on_consecutive_crashes(self, app):
        """Three consecutive crashes stop the loop."""
        with app.app_context():
            svc = RAGAutoresearchService()

            crash_result = {
                "experiment_id": "crash-exp",
                "parameter": "top_k",
                "status": "crash",
                "composite_score": 0.0,
            }

            with patch.object(svc, "_check_prerequisites", return_value=True), \
                 patch.object(svc, "run_single_experiment", return_value=crash_result), \
                 patch.object(svc, "_get_recent_history", return_value=[
                     {"status": "crash"}, {"status": "crash"}, {"status": "crash"},
                 ]):
                svc.run_loop(max_experiments=10)
                # Should have stopped after detecting 3 crashes, not run all 10
                assert not svc.is_running()
