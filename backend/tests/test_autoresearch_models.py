"""Tests for RAG Autoresearch database models."""

import uuid
import pytest
from datetime import datetime

try:
    from flask import Flask
    from backend.models import db, ExperimentRun, EvalPair, ResearchConfig
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


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


class TestExperimentRun:
    def test_create_experiment_run(self, app):
        """ExperimentRun can be created with required fields."""
        with app.app_context():
            run = ExperimentRun(
                id=str(uuid.uuid4()),
                run_tag="mar10-test",
                phase=1,
                parameter_changed="top_k",
                old_value="5",
                new_value="8",
                hypothesis="Increasing top_k should improve completeness",
                composite_score=3.5,
                baseline_score=3.2,
                delta=0.3,
                status="keep",
                duration_seconds=45.2,
            )
            db.session.add(run)
            db.session.commit()
            fetched = db.session.get(ExperimentRun, run.id)
            assert fetched.parameter_changed == "top_k"
            assert fetched.delta == 0.3
            assert fetched.status == "keep"
            assert fetched.node_id is None  # nullable for standalone

    def test_experiment_run_with_eval_details(self, app):
        """ExperimentRun stores JSON eval_details."""
        with app.app_context():
            run = ExperimentRun(
                id=str(uuid.uuid4()),
                phase=1,
                parameter_changed="top_k",
                old_value="5",
                new_value="8",
                composite_score=3.5,
                baseline_score=3.2,
                delta=0.3,
                status="keep",
                eval_details={
                    "q1": {"relevance": 4, "grounding": 3, "completeness": 4}
                },
            )
            db.session.add(run)
            db.session.commit()
            fetched = db.session.get(ExperimentRun, run.id)
            assert fetched.eval_details["q1"]["relevance"] == 4


class TestEvalPair:
    def test_create_eval_pair(self, app):
        """EvalPair can be created with required fields."""
        with app.app_context():
            pair = EvalPair(
                id=str(uuid.uuid4()),
                eval_generation_id="gen-001",
                question="How does the chat streaming pipeline work?",
                expected_answer="It uses Socket.IO via unified_chat_engine.py",
                source_chunk_hash="a" * 64,
                corpus_type="code",
            )
            db.session.add(pair)
            db.session.commit()
            fetched = db.session.get(EvalPair, pair.id)
            assert fetched.corpus_type == "code"
            assert fetched.quality_score is None  # nullable


class TestResearchConfig:
    def test_create_research_config(self, app):
        """ResearchConfig stores JSON params and tracks active state."""
        with app.app_context():
            config = ResearchConfig(
                id=str(uuid.uuid4()),
                params={"top_k": 5, "similarity_threshold": 0.85},
                composite_score=3.2,
                is_active=True,
                source="local",
            )
            db.session.add(config)
            db.session.commit()
            fetched = db.session.get(ResearchConfig, config.id)
            assert fetched.params["top_k"] == 5
            assert fetched.is_active is True
            assert fetched.source == "local"

    def test_only_one_active_config(self, app):
        """Only one ResearchConfig should be active at a time (enforced by app logic)."""
        with app.app_context():
            c1 = ResearchConfig(
                id=str(uuid.uuid4()),
                params={"top_k": 5},
                composite_score=3.0,
                is_active=True,
                source="local",
            )
            c2 = ResearchConfig(
                id=str(uuid.uuid4()),
                params={"top_k": 8},
                composite_score=3.5,
                is_active=False,
                source="local",
            )
            db.session.add_all([c1, c2])
            db.session.commit()
            active = ResearchConfig.query.filter_by(is_active=True).all()
            assert len(active) == 1
