"""
Flask Application for GPU Embedding Service
"""

import logging
import time
from flask import Flask, request, jsonify
from typing import Dict, Any, List

from .config import get_service_config
from .model_loader import (
    initialize_model,
    generate_embedding,
    generate_embeddings_batch,
    get_model_info,
    is_model_loaded,
)
from .health import get_health_status

logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)

# Initialize configuration
config = get_service_config()


# Initialize service on module load (before first request)
def _initialize_service():
    """Initialize the service"""
    logger.info("Initializing GPU Embedding Service...")

    # Initialize model
    model_name = config.get("model")
    ollama_base_url = config.get("ollama_base_url", "http://localhost:11434")

    success = initialize_model(model_name, ollama_base_url)
    if success:
        logger.info("GPU Embedding Service initialized successfully")
    else:
        logger.error("GPU Embedding Service initialization failed")

    return success


# Initialize on import
_initialize_service()


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    try:
        health = get_health_status()
        status_code = 200 if health["status"] == "healthy" else 503
        return jsonify(health), status_code
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/embed", methods=["POST"])
def embed_text():
    """Generate embedding for a single text"""
    if not is_model_loaded():
        return jsonify({"error": "Model not loaded", "status": "unavailable"}), 503

    try:
        data = request.get_json()
        if not data or "text" not in data:
            return jsonify({"error": "Missing 'text' field in request body"}), 400

        text = data["text"]
        if not isinstance(text, str):
            return jsonify({"error": "'text' must be a string"}), 400

        # Check text length
        max_length = config.get("max_text_length", 8192)
        if len(text) > max_length:
            return (
                jsonify({"error": f"Text too long (max {max_length} characters)"}),
                400,
            )

        # Generate embedding
        start_time = time.time()
        embedding = generate_embedding(text)
        processing_time = (time.time() - start_time) * 1000  # Convert to ms

        model_info = get_model_info()

        return (
            jsonify(
                {
                    "embedding": embedding,
                    "model": model_info.get("model_name"),
                    "dimension": model_info.get("embed_dim"),
                    "processing_time_ms": round(processing_time, 2),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error generating embedding: {e}", exc_info=True)
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route("/embed_batch", methods=["POST"])
def embed_batch():
    """Generate embeddings for multiple texts (batch processing)"""
    if not is_model_loaded():
        return jsonify({"error": "Model not loaded", "status": "unavailable"}), 503

    try:
        data = request.get_json()
        if not data or "texts" not in data:
            return jsonify({"error": "Missing 'texts' field in request body"}), 400

        texts = data["texts"]
        if not isinstance(texts, list):
            return jsonify({"error": "'texts' must be a list"}), 400

        if len(texts) == 0:
            return jsonify({"error": "'texts' list cannot be empty"}), 400

        # Check batch size
        batch_size = config.get("batch_size", 32)
        if len(texts) > batch_size:
            return jsonify({"error": f"Batch too large (max {batch_size} texts)"}), 400

        # Check text lengths
        max_length = config.get("max_text_length", 8192)
        for i, text in enumerate(texts):
            if not isinstance(text, str):
                return jsonify({"error": f"Text at index {i} must be a string"}), 400
            if len(text) > max_length:
                return (
                    jsonify(
                        {
                            "error": f"Text at index {i} too long (max {max_length} characters)"
                        }
                    ),
                    400,
                )

        # Generate embeddings
        start_time = time.time()
        embeddings = generate_embeddings_batch(texts)
        processing_time = (time.time() - start_time) * 1000  # Convert to ms

        model_info = get_model_info()

        return (
            jsonify(
                {
                    "embeddings": embeddings,
                    "model": model_info.get("model_name"),
                    "dimension": model_info.get("embed_dim"),
                    "count": len(embeddings),
                    "processing_time_ms": round(processing_time, 2),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error generating batch embeddings: {e}", exc_info=True)
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route("/models", methods=["GET"])
def list_models():
    """List available models"""
    try:
        model_info = get_model_info()

        return (
            jsonify(
                {
                    "current_model": model_info.get("model_name"),
                    "loaded": model_info.get("loaded", False),
                    "embed_dim": model_info.get("embed_dim"),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error listing models: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return (
        jsonify(
            {
                "error": "Endpoint not found",
                "available_endpoints": ["/health", "/embed", "/embed_batch", "/models"],
            }
        ),
        404,
    )


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {error}", exc_info=True)
    return jsonify({"error": "Internal server error", "status": "error"}), 500


def create_app():
    """Factory function to create the Flask app"""
    # Service is already initialized on module load
    return app


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO, format="[%(asctime)s: %(levelname)s/%(name)s] %(message)s"
    )

    # Get configuration
    config = get_service_config()
    host = config.get("host", "127.0.0.1")
    port = config.get("port", 5002)
    debug = config.get("debug", False)

    logger.info(f"Starting GPU Embedding Service on {host}:{port}")

    # Initialize model before starting server
    model_name = config.get("model")
    ollama_base_url = config.get("ollama_base_url", "http://localhost:11434")
    initialize_model(model_name, ollama_base_url)

    # Run Flask app
    app.run(host=host, port=port, debug=debug, threaded=False)
