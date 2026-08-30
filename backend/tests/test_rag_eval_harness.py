"""Tests for RAG eval harness — eval pair generation and LLM-as-judge scoring."""
import pytest
from unittest.mock import patch, MagicMock

try:
    from flask import Flask
    from backend.models import db
    from backend.services.rag_eval_harness import RAGEvalHarness
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


class TestEvalPairGeneration:
    def test_generate_eval_pair_from_chunk(self):
        """Given a text chunk, generates a question and expected answer."""
        harness = RAGEvalHarness()
        chunk_text = "The unified_chat_engine uses Socket.IO for real-time streaming."
        mock_response = '{"question": "How does the chat engine handle streaming?", "expected_answer": "It uses Socket.IO for real-time streaming via unified_chat_engine."}'

        with patch.object(harness, "_call_llm", return_value=mock_response):
            result = harness.generate_eval_pair(chunk_text, "code")
            assert "question" in result
            assert "expected_answer" in result
            assert result["corpus_type"] == "code"

    def test_generate_eval_pair_handles_malformed_llm_output(self):
        """Gracefully handles unparseable LLM output."""
        harness = RAGEvalHarness()
        with patch.object(harness, "_call_llm", return_value="not json"):
            result = harness.generate_eval_pair("some text", "code")
            assert result is None

    def test_minimum_corpus_check(self, app):
        """Returns False if corpus is below minimum threshold."""
        with app.app_context():
            harness = RAGEvalHarness()
            assert harness.has_sufficient_corpus() is False  # empty DB


class TestLLMJudge:
    def test_score_response_returns_composite(self):
        """LLM-as-judge returns relevance, grounding, completeness, composite."""
        harness = RAGEvalHarness()
        mock_judgment = '{"relevance": 4, "grounding": 5, "completeness": 3}'

        with patch.object(harness, "_call_llm", return_value=mock_judgment):
            score = harness.score_response(
                question="How does streaming work?",
                expected_answer="Socket.IO streaming",
                actual_response="The system uses Socket.IO for streaming.",
                retrieved_chunks=["chunk about Socket.IO"],
            )
            assert score["relevance"] == 4
            assert score["grounding"] == 5
            assert score["completeness"] == 3
            assert 1.0 <= score["composite"] <= 5.0

    def test_score_response_handles_malformed_judgment(self):
        """Parse failure is labeled and excluded from the mean, not floored to 1.0."""
        harness = RAGEvalHarness()
        with patch.object(harness, "_call_llm", return_value="garbage"):
            score = harness.score_response("q", "a", "r", [])
            assert score.get("judge_parse_failed") is True
            assert score["composite"] is None

    def test_score_response_extracts_json_from_prose(self):
        harness = RAGEvalHarness()
        with patch.object(
            harness, "_call_llm",
            return_value='Sure.\n{"relevance": 4, "grounding": 5, "completeness": 3}\nThanks.',
        ):
            score = harness.score_response("q", "a", "r", [])
            assert score["composite"] == 4.0
            assert not score.get("judge_parse_failed")

    def test_run_full_eval(self, app):
        """Full eval runs all eval pairs and returns average composite score."""
        with app.app_context():
            harness = RAGEvalHarness()
            with patch.object(harness, "_get_active_eval_pairs") as mock_pairs, \
                 patch.object(harness, "_eval_single_pair") as mock_eval:
                mock_pairs.return_value = [
                    {"id": "1", "question": "q1", "expected_answer": "a1"},
                    {"id": "2", "question": "q2", "expected_answer": "a2"},
                ]
                mock_eval.side_effect = [
                    {"composite": 4.0, "relevance": 4, "grounding": 4, "completeness": 4},
                    {"composite": 3.0, "relevance": 3, "grounding": 3, "completeness": 3},
                ]
                result = harness.run_full_eval(config={"top_k": 5})
                assert result["composite_score"] == 3.5
                assert result["num_pairs"] == 2
                assert result["parse_fail_ratio"] == 0.0

    def test_run_full_eval_drops_parse_failures_from_mean(self, app):
        with app.app_context():
            harness = RAGEvalHarness()
            with patch.object(harness, "_get_active_eval_pairs") as mock_pairs, \
                 patch.object(harness, "_eval_single_pair") as mock_eval:
                mock_pairs.return_value = [
                    {"id": "1", "question": "q1", "expected_answer": "a1"},
                    {"id": "2", "question": "q2", "expected_answer": "a2"},
                ]
                mock_eval.side_effect = [
                    {"composite": 4.0, "relevance": 4, "grounding": 4, "completeness": 4},
                    {"composite": None, "judge_parse_failed": True},
                ]
                result = harness.run_full_eval(config={"top_k": 5})
                assert result["composite_score"] == 4.0
                assert result["parse_fail_ratio"] == 0.5

    def test_multi_hop_pair_records_two_hashes(self):
        harness = RAGEvalHarness()
        mock_response = (
            '{"question": "How do A and B relate?", '
            '"expected_answer": "They share a mechanism.", "question_type": "multi_hop"}'
        )
        with patch.object(harness, "_call_llm", return_value=mock_response):
            result = harness.generate_eval_pair(
                "chunk A text", "knowledge",
                question_kind="multi_hop", chunk_b="chunk B text",
            )
            assert result is not None
            assert len(result["source_chunk_hashes"]) == 2
            assert result["source_chunk_hashes"][0] != result["source_chunk_hashes"][1]
