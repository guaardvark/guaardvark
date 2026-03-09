"""
Model Loader for GPU Embedding Service
Handles loading and caching of embedding models.
"""

import logging
import os
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Global model cache
_embedding_model = None
_model_name = None
_model_loaded = False


def initialize_model(model_name: Optional[str] = None, ollama_base_url: str = "http://localhost:11434") -> bool:
    """
    Initialize the embedding model.
    
    Args:
        model_name: Name of the model to load (defaults to config)
        ollama_base_url: Base URL for Ollama API
        
    Returns:
        True if model loaded successfully, False otherwise
    """
    global _embedding_model, _model_name, _model_loaded
    
    if _model_loaded and _embedding_model is not None:
        logger.info(f"Model already loaded: {_model_name}")
        return True
    
    try:
        # Ensure CUDA is available (this service runs in single process, so CUDA is safe)
        if os.environ.get('CUDA_VISIBLE_DEVICES', '').strip() == '':
            # If CUDA is explicitly disabled, use CPU
            logger.warning("CUDA_VISIBLE_DEVICES is empty - using CPU mode")
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
        else:
            # Enable CUDA for this service
            logger.info(f"Using GPU device: {os.environ.get('CUDA_VISIBLE_DEVICES', '0')}")
        
        # Import Ollama embedding model
        from llama_index.embeddings.ollama import OllamaEmbedding
        
        # Use provided model_name or get from config
        if not model_name:
            from .config import get_service_config
            config = get_service_config()
            
            # Check if plugin is configured to use system's active embedding model
            use_system_model = config.get("use_system_model", False)
            
            if use_system_model:
                # Get active embedding model from central config
                try:
                    # Add parent directory to path to import backend.config
                    import sys
                    from pathlib import Path
                    plugin_root = Path(__file__).parent.parent.parent.parent
                    if str(plugin_root) not in sys.path:
                        sys.path.insert(0, str(plugin_root))
                    
                    from backend.config import get_active_embedding_model
                    model_name = get_active_embedding_model()
                    logger.info(f"Using system's active embedding model: {model_name}")
                except ImportError as e:
                    logger.warning(f"Could not import get_active_embedding_model: {e}, using plugin config")
                    model_name = config.get("model", "embeddinggemma:latest")
                except Exception as e:
                    logger.warning(f"Could not get active embedding model: {e}, using plugin config")
                    model_name = config.get("model", "embeddinggemma:latest")
            else:
                # Use plugin's configured model
                model_name = config.get("model", "embeddinggemma:latest")
        
        logger.info(f"Loading embedding model: {model_name} from {ollama_base_url}")
        
        # Initialize Ollama embedding model
        _embedding_model = OllamaEmbedding(
            model_name=model_name,
            base_url=ollama_base_url,
            ollama_additional_kwargs={"mirostat": 0},
        )
        
        _model_name = model_name
        _model_loaded = True
        
        logger.info(f"Successfully loaded embedding model: {model_name}")
        return True
        
    except ImportError as e:
        logger.error(f"Failed to import OllamaEmbedding: {e}")
        logger.error("Ollama embeddings are required for GPU embedding service")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize embedding model: {e}", exc_info=True)
        _model_loaded = False
        return False


def get_model():
    """Get the loaded embedding model"""
    global _embedding_model
    if not _model_loaded or _embedding_model is None:
        raise RuntimeError("Model not initialized. Call initialize_model() first.")
    return _embedding_model


def get_model_name() -> Optional[str]:
    """Get the name of the loaded model"""
    return _model_name


def is_model_loaded() -> bool:
    """Check if model is loaded"""
    return _model_loaded


def generate_embedding(text: str) -> List[float]:
    """
    Generate embedding for a single text.
    
    Args:
        text: Text to embed
        
    Returns:
        Embedding vector as list of floats
    """
    if not _model_loaded:
        raise RuntimeError("Model not loaded")
    
    try:
        embedding = _embedding_model.get_text_embedding(text)
        return embedding
    except Exception as e:
        logger.error(f"Error generating embedding: {e}", exc_info=True)
        raise


def generate_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings for multiple texts (batch processing).
    
    Args:
        texts: List of texts to embed
        
    Returns:
        List of embedding vectors
    """
    if not _model_loaded:
        raise RuntimeError("Model not loaded")
    
    try:
        # Use batch embedding if available, otherwise process sequentially
        if hasattr(_embedding_model, 'get_text_embeddings'):
            embeddings = _embedding_model.get_text_embeddings(texts)
        else:
            # Fallback to sequential processing
            embeddings = [_embedding_model.get_text_embedding(text) for text in texts]
        
        return embeddings
    except Exception as e:
        logger.error(f"Error generating batch embeddings: {e}", exc_info=True)
        raise


def get_model_info() -> Dict[str, Any]:
    """Get information about the loaded model"""
    global _embedding_model, _model_name
    
    info = {
        "model_name": _model_name,
        "loaded": _model_loaded,
        "embed_dim": None
    }
    
    if _embedding_model and hasattr(_embedding_model, 'embed_dim'):
        info["embed_dim"] = _embedding_model.embed_dim
    
    return info

