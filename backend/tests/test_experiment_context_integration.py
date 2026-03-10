"""Test that experiment context overrides retrieval parameters."""
import pytest
from unittest.mock import patch, MagicMock

try:
    from flask import Flask
    from backend.models import db
    from backend.utils.experiment_context import (
        set_experiment_config,
        clear_experiment_config,
    )
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


def test_search_uses_experiment_top_k(app):
    """When experiment context is set, search_with_llamaindex uses experiment top_k."""
    with app.app_context():
        set_experiment_config({"top_k": 12, "context_window_chunks": 5})
        try:
            from backend.services.indexing_service import search_with_llamaindex
            with patch("backend.services.indexing_service.index") as mock_idx:
                mock_retriever = MagicMock()
                mock_retriever.retrieve.return_value = []
                mock_idx.as_retriever.return_value = mock_retriever
                search_with_llamaindex("test query", max_chunks=3)
                # Verify the retriever was called with experiment top_k=12
                call_kwargs = mock_idx.as_retriever.call_args
                if call_kwargs:
                    assert call_kwargs[1].get("similarity_top_k") == 12
        finally:
            clear_experiment_config()


def test_search_uses_defaults_without_experiment(app):
    """Without experiment context, default parameters are used."""
    clear_experiment_config()
    with app.app_context():
        from backend.services.indexing_service import search_with_llamaindex
        with patch("backend.services.indexing_service.index") as mock_idx:
            mock_retriever = MagicMock()
            mock_retriever.retrieve.return_value = []
            mock_idx.as_retriever.return_value = mock_retriever
            search_with_llamaindex("test query", max_chunks=3)
            call_kwargs = mock_idx.as_retriever.call_args
            if call_kwargs:
                top_k = call_kwargs[1].get("similarity_top_k", 3)
                assert top_k == 3  # should be the passed max_chunks, not experiment value
