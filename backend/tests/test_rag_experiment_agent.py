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
                history=[],
                current_config={"top_k": 5},
                phase=1,
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
            assert not (
                proposal["parameter"] == "top_k" and proposal["new_value"] in [8, 10]
            )


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
