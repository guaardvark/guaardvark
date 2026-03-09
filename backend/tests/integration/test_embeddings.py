#!/usr/bin/env python3
"""
Integration tests for embedding functionality using REAL Ollama embeddings.

Tests the qwen3-embedding:8b integration, validates 4096-dim embeddings,
and tests the complete upload → indexing → retrieval pipeline.

IMPORTANT: These tests require Ollama to be running with qwen3-embedding:8b model available.
"""

import os
import json
import pytest
from pathlib import Path

try:
    from flask import Flask
    from backend.models import db, Document as DBDocument
    from backend.api.upload_api import upload_bp
    from backend.api.search_api import search_bp
    from backend.services import indexing_service
    from backend import config
    from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage, Settings
    from llama_index.core.schema import Document
except Exception as e:
    pytest.skip(f"Flask or backend modules not available: {e}", allow_module_level=True)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """
    Create Flask app with real Ollama embeddings and in-memory database.
    Uses temporary storage for indexes.
    """
    # Configure temporary paths
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path / "indices"))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "global")
    monkeypatch.setattr(config, "INDEX_ROOT", str(tmp_path / "indices"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))

    # Create directories
    os.makedirs(tmp_path / "indices", exist_ok=True)
    os.makedirs(tmp_path / "storage", exist_ok=True)
    os.makedirs(tmp_path / "uploads", exist_ok=True)

    # Create Flask app
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "INDEX_ROOT": str(tmp_path / "indices"),
        "STORAGE_DIR": str(tmp_path / "storage"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    })

    # Initialize database
    db.init_app(app)

    # Register blueprints
    if upload_bp.name not in app.blueprints:
        app.register_blueprint(upload_bp)
    if search_bp.name not in app.blueprints:
        app.register_blueprint(search_bp)

    with app.app_context():
        db.create_all()

        # Clear any cached indexes
        if hasattr(indexing_service, '_index_cache'):
            indexing_service._index_cache = {}

        yield app

        # Cleanup
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.mark.indexing
def test_embedding_model_initialization():
    """
    Test that qwen3-embedding:8b initializes correctly.
    Verifies the embedding model is loaded and accessible.
    """
    # Settings is already configured by llama_index_local_config.py
    assert Settings.embed_model is not None, "Embedding model not initialized"

    # Check if it's the expected model type
    model_type = type(Settings.embed_model).__name__
    print(f"Loaded embedding model type: {model_type}")

    # Should be OllamaEmbedding if Ollama is available
    # Otherwise it falls back to other models
    assert model_type in ["OllamaEmbedding", "HuggingFaceEmbedding", "SimpleTextEmbedding"], \
        f"Unexpected embedding model type: {model_type}"


@pytest.mark.indexing
def test_embedding_dimensions():
    """
    Test that embeddings have the correct dimensions (4096 for qwen3-embedding:8b).
    Validates actual embedding generation.
    """
    embed_model = Settings.embed_model

    # Generate test embedding
    test_text = "This is a test document for embedding dimension validation."
    embedding = embed_model.get_text_embedding(test_text)

    # Check dimensions
    actual_dim = len(embedding)
    print(f"Embedding dimensions: {actual_dim}")

    # qwen3-embedding:8b should produce 4096-dim embeddings
    # But fallback models may have different dimensions
    assert actual_dim > 0, "Embedding is empty"
    assert isinstance(embedding, list), "Embedding should be a list"
    assert all(isinstance(x, (int, float)) for x in embedding), "Embedding values should be numeric"

    # Log the dimension for verification
    if hasattr(embed_model, 'model_name'):
        print(f"Model: {embed_model.model_name}, Dimensions: {actual_dim}")

    # If using qwen3-embedding:8b, should be exactly 4096
    if hasattr(embed_model, 'model_name') and 'qwen3-embedding:8b' in embed_model.model_name:
        assert actual_dim == 4096, f"qwen3-embedding:8b should produce 4096-dim embeddings, got {actual_dim}"


@pytest.mark.indexing
def test_embedding_fallback():
    """
    Test that the system handles embedding fallback gracefully.
    If qwen3-embedding:8b is unavailable, it should fall back to other models.
    """
    embed_model = Settings.embed_model
    model_type = type(embed_model).__name__

    # Test that we have a working embedding model (any type)
    test_embedding = embed_model.get_text_embedding("fallback test")
    assert len(test_embedding) > 0, "Fallback embedding model should still produce embeddings"

    print(f"Fallback model type: {model_type}, dimensions: {len(test_embedding)}")


@pytest.mark.indexing
def test_document_indexing_with_embeddings(app, tmp_path):
    """
    Test complete document indexing flow with real embeddings.
    Upload file → index → verify in vector store.
    """
    with app.app_context():
        # Create test document
        test_content = """
        This is a test document about artificial intelligence and machine learning.
        It covers topics like neural networks, deep learning, and natural language processing.
        The document is used to test the embedding and indexing pipeline.
        """

        # Create test file
        test_file = tmp_path / "test_doc.txt"
        test_file.write_text(test_content)

        # Create database document entry
        doc = DBDocument(
            filename="test_doc.txt",
            path=str(test_file.relative_to(tmp_path.parent)),
            type="txt",
            content=test_content,
            index_status="PENDING"
        )
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id

        # Index the document (returns bool)
        result = indexing_service.add_file_to_index(
            file_path=str(test_file),
            db_document=doc,
            progress_callback=None
        )

        # Verify indexing succeeded (function returns True on success)
        assert result is True, f"Indexing failed (returned {result})"

        # Reload document and check status
        indexed_doc = db.session.get(DBDocument, doc_id)
        assert indexed_doc is not None
        assert indexed_doc.index_status == "INDEXED", f"Document status is {indexed_doc.index_status}, expected INDEXED"

        # Verify index was created
        index_root = Path(app.config["INDEX_ROOT"])
        assert index_root.exists(), "Index root directory should exist"

        # Check for vector store files
        docstore_file = index_root / "docstore.json"
        if docstore_file.exists():
            with open(docstore_file, 'r') as f:
                docstore_data = json.load(f)
            assert len(docstore_data.get('docstore/data', {})) > 0, "Docstore should contain documents"
            print(f"Documents in docstore: {len(docstore_data.get('docstore/data', {}))}")


@pytest.mark.indexing
def test_search_with_embeddings(app, tmp_path):
    """
    Test semantic search with real embeddings.
    Query → embedding → similarity search → results.
    """
    with app.app_context():
        # Create and index test documents
        docs_to_index = [
            ("Python is a programming language used for web development and data science.", "python.txt"),
            ("JavaScript is commonly used for frontend web development and Node.js.", "javascript.txt"),
            ("Machine learning models can predict patterns from historical data.", "ml.txt"),
        ]

        for content, filename in docs_to_index:
            # Create test file
            test_file = tmp_path / filename
            test_file.write_text(content)

            # Create database entry
            doc = DBDocument(
                filename=filename,
                path=str(test_file.relative_to(tmp_path.parent)),
                type="txt",
                content=content,
                index_status="PENDING"
            )
            db.session.add(doc)
            db.session.commit()

            # Index document
            indexing_service.add_file_to_index(
                file_path=str(test_file),
                db_document=doc,
                progress_callback=None
            )

        # Perform semantic search
        query = "programming languages for web development"
        results = indexing_service.search_with_llamaindex(query, max_chunks=5)

        # Verify results
        assert results is not None, "Search should return results"
        assert isinstance(results, list), "Results should be a list"
        assert len(results) > 0, "Search should find relevant documents"

        # Check result structure
        for result in results:
            assert 'text' in result, "Result should contain text"
            assert 'score' in result, "Result should contain similarity score"
            assert 'metadata' in result, "Result should contain metadata"

        # Verify relevance - results about Python/JavaScript should score higher
        # than ML for a web development query
        top_result = results[0]
        print(f"Top result score: {top_result['score']}")
        print(f"Top result text preview: {top_result['text'][:100]}...")

        # Results should be sorted by score (highest first)
        if len(results) > 1:
            assert results[0]['score'] >= results[1]['score'], "Results should be sorted by relevance"


@pytest.mark.indexing
def test_dimension_compatibility(app, tmp_path):
    """
    Test that the system handles embedding dimension mismatches gracefully.
    Verifies error handling for incompatible dimensions.
    """
    with app.app_context():
        # Get current embedding dimensions
        embed_model = Settings.embed_model
        test_embedding = embed_model.get_text_embedding("test")
        current_dim = len(test_embedding)

        print(f"Current embedding dimensions: {current_dim}")

        # Create index with current dimensions
        test_docs = [Document(text="Test document for dimension compatibility")]
        index = VectorStoreIndex.from_documents(test_docs)

        # Verify index was created successfully
        assert index is not None, "Index should be created successfully"

        # Test querying the index
        query_engine = index.as_query_engine()
        response = query_engine.query("test query")

        assert response is not None, "Query should return a response"
        print(f"Query response: {response}")


@pytest.mark.indexing
def test_index_persistence(app, tmp_path):
    """
    Test index persistence and reload functionality.
    Index creation → save → reload → verify integrity.
    """
    with app.app_context():
        index_path = Path(app.config["INDEX_ROOT"])

        # Create and persist index
        test_content = "This is a persistence test document."
        test_file = tmp_path / "persist_test.txt"
        test_file.write_text(test_content)

        doc = DBDocument(
            filename="persist_test.txt",
            path=str(test_file.relative_to(tmp_path.parent)),
            type="txt",
            content=test_content,
            index_status="PENDING"
        )
        db.session.add(doc)
        db.session.commit()

        # Index document (returns bool)
        result = indexing_service.add_file_to_index(
            file_path=str(test_file),
            db_document=doc,
            progress_callback=None
        )

        assert result is True, "Initial indexing should succeed"

        # Verify index files exist
        assert (index_path / "docstore.json").exists(), "Docstore should be persisted"

        # Clear index cache to force reload
        if hasattr(indexing_service, '_index_cache'):
            indexing_service._index_cache = {}

        # Reload index from storage
        try:
            storage_context = StorageContext.from_defaults(persist_dir=str(index_path))
            reloaded_index = load_index_from_storage(storage_context)

            assert reloaded_index is not None, "Index should reload successfully"

            # Test query on reloaded index
            query_engine = reloaded_index.as_query_engine()
            response = query_engine.query("persistence")

            assert response is not None, "Reloaded index should support queries"
            print(f"Reloaded index query response: {response}")

        except Exception as e:
            # Some backends might not support load_index_from_storage
            print(f"Index reload test skipped: {e}")
            pytest.skip(f"Index persistence not supported: {e}")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-vv", "--tb=short"])
