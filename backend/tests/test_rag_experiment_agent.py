"""Tests for the RAG experiment agent — hypothesis engine."""
import pytest
from unittest.mock import patch, MagicMock
from backend.services.rag_experiment_agent import RAGExperimentAgent


class TestPropose:
    def test_proposes_valid_phase1_experiment(self):
        """Agent proposes a change within Phase 1 parameters."""
        agent = RAGExperimentAgent()
        mock_response = '{"parameter": "top_k", "new_value": 8, "hypothesis": "More chunks may help"}'

        with patch.object(agent, "_call_llm", return_value=mock_response):
            proposal = agent.propose_experiment(
                history=[],
                current_config={"top_k": 5, "dedup_threshold": 0.85},
                phase=1,
            )
            assert proposal["parameter"] == "top_k"
            assert proposal["new_value"] == 8
            assert "hypothesis" in proposal

    def test_handles_malformed_llm_output(self):
        """Falls back to random parameter on parse failure."""
        agent = RAGExperimentAgent()
        with patch.object(agent, "_call_llm", return_value="not json at all"):
            proposal = agent.propose_experiment(
                history=[], current_config={"top_k": 5}, phase=1,
            )
            assert "parameter" in proposal
            assert "new_value" in proposal
            assert "hypothesis" in proposal

    def test_avoids_recently_tried_experiments(self):
        """Agent does not propose the same experiment twice."""
        agent = RAGExperimentAgent()
        history = [
            {"parameter_changed": "top_k", "new_value": "8", "status": "discard"},
            {"parameter_changed": "top_k", "new_value": "10", "status": "discard"},
        ]
        mock_response = '{"parameter": "dedup_threshold", "new_value": 0.75, "hypothesis": "Lower dedup"}'

        with patch.object(agent, "_call_llm", return_value=mock_response):
            proposal = agent.propose_experiment(
                history=history,
                current_config={"top_k": 5, "dedup_threshold": 0.85},
                phase=1,
            )
            assert not (proposal["parameter"] == "top_k" and proposal["new_value"] in [8, 10])


class TestPhaseTransition:
    def test_should_advance_phase_after_plateau(self):
        """Advances phase after 10 consecutive discards."""
        agent = RAGExperimentAgent()
        history = [{"status": "discard"} for _ in range(10)]
        assert agent.should_advance_phase(history) is True

    def test_should_not_advance_if_recent_keep(self):
        """Does not advance if there was a recent keep."""
        agent = RAGExperimentAgent()
        history = [{"status": "discard"} for _ in range(9)]
        history.append({"status": "keep"})
        assert agent.should_advance_phase(history) is False


class TestTpeAndProgram:
    def test_research_program_path_is_shared(self):
        from backend.services.rag_experiment_agent import RESEARCH_PROGRAM_RELPATH
        assert RESEARCH_PROGRAM_RELPATH.replace("\\", "/").endswith(
            "data/rag_research_program.md"
        )

    def test_tpe_proposes_when_history_is_rich(self):
        agent = RAGExperimentAgent()
        history = [
            {
                "parameter_changed": "top_k",
                "new_value": "8",
                "composite_score": 4.0 + i * 0.01,
                "status": "keep",
            }
            for i in range(10)
        ]
        with patch.object(agent, "_call_llm", return_value="not json"):
            proposal = agent.propose_experiment(
                history=history,
                current_config={"top_k": 5, "dedup_threshold": 0.85},
                phase=1,
            )
        assert proposal["source"] == "tpe"
        assert proposal["parameter"] == "top_k"
        assert proposal["new_value"] == 8


def test_proposer_binds_saved_active_model_when_app_has_no_llm():
    """The proposer runs in a Celery worker whose app never builds LLAMA_INDEX_LLM."""
    from unittest.mock import MagicMock, patch
    from backend.services.rag_experiment_agent import RAGExperimentAgent
    agent = RAGExperimentAgent()
    calls = []

    def fake_get_llm_instance(model=None):
        calls.append(model)
        return MagicMock(model=model) if model else None

    with patch("backend.utils.llm_service.get_llm_instance", side_effect=fake_get_llm_instance), \
         patch("backend.utils.llm_service.get_saved_active_model_name", return_value="gemma4:12b"), \
         patch("backend.models.Setting") as setting:
        setting.query.filter_by.return_value.first.return_value = None
        llm = agent._get_llm()
    assert llm is not None and agent.proposer_model_name == "gemma4:12b"
    assert calls == [None, "gemma4:12b"]


def test_phase_without_tunable_params_does_not_fabricate_a_proposal():
    from backend.services.rag_experiment_agent import RAGExperimentAgent
    with pytest.raises(ValueError):
        RAGExperimentAgent().propose_experiment([], {"top_k": 5}, phase=3)
