# backend/api/model_api.py
# Version 1.5: Added model unloading, caching, and memory management.

import gc
import logging
import threading
import time
from functools import lru_cache

import requests
from flask import Blueprint, current_app, request, jsonify, copy_current_request_context
from backend.utils.response_utils import success_response, error_response
from backend.socketio_instance import socketio

# Model list cache with TTL
_model_list_cache = {"models": None, "timestamp": 0}
MODEL_LIST_CACHE_TTL = 30  # seconds

# --- LlamaIndex Imports ---
try:
    from llama_index.core import PromptTemplate, Settings

    # Import base engines for type checking if needed, not for creation here
    from llama_index.core.base.base_query_engine import BaseQueryEngine
    from llama_index.core.chat_engine.types import BaseChatEngine
    from llama_index.llms.ollama import Ollama

    llama_index_available = True
except ImportError:
    logging.getLogger(__name__).error(
        "LlamaIndex components not fully available in model_api."
    )
    Settings = PromptTemplate = Ollama = BaseQueryEngine = BaseChatEngine = None
    llama_index_available = False

# --- Local Imports ---
try:
    from backend.config import OLLAMA_BASE_URL  # Import base URL
    from backend.models import Setting, db  # Import Setting model
    from backend.utils import prompt_utils

    local_imports_ok = True
except ImportError as e:
    logging.getLogger(__name__).error(
        f"Failed to import local models/utils in model_api: {e}"
    )
    db = Setting = prompt_utils = None
    OLLAMA_BASE_URL = "http://localhost:11434"  # Fallback
    local_imports_ok = False

model_bp = Blueprint("model_api", __name__, url_prefix="/api/model")
logger = logging.getLogger(__name__)


def unload_model_from_ollama(model_name: str) -> bool:
    """
    Unload a model from Ollama's memory by sending a request with keep_alive=0.
    This frees up GPU/RAM memory held by the model.

    Args:
        model_name: The name of the model to unload (e.g., "llama3.2:latest")

    Returns:
        True if unload was successful, False otherwise
    """
    if not model_name:
        return False

    try:
        # Send a generate request with keep_alive=0 to unload the model.
        # Use num_ctx=1 to prevent Ollama from allocating a large KV cache
        # just to immediately discard the model.
        unload_url = f"{OLLAMA_BASE_URL}/api/generate"
        response = requests.post(
            unload_url,
            json={
                "model": model_name,
                "prompt": "",
                "keep_alive": 0,  # Unload immediately
                "options": {"num_ctx": 1},
            },
            timeout=30,
        )

        if response.ok:
            logger.info(
                f"Successfully unloaded model '{model_name}' from Ollama memory"
            )
            return True
        else:
            logger.warning(
                f"Failed to unload model '{model_name}': {response.status_code} {response.text}"
            )
            return False

    except requests.exceptions.RequestException as e:
        logger.warning(f"Error unloading model '{model_name}': {e}")
        return False


def get_loaded_models() -> list:
    """
    Get list of currently loaded models from Ollama.

    Returns:
        List of model info dicts, or empty list on error
    """
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=3)
        if response.ok:
            data = response.json()
            return data.get("models", [])
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not get loaded models: {e}")
    return []


def unload_all_except(keep_model: str = None) -> int:
    """
    Unload all loaded models except the specified one.

    Args:
        keep_model: Model name to keep loaded (optional)

    Returns:
        Number of models unloaded
    """
    loaded = get_loaded_models()
    unloaded_count = 0

    for model_info in loaded:
        model_name = model_info.get("name", "")
        if keep_model and model_name.lower() == keep_model.lower():
            continue
        if unload_model_from_ollama(model_name):
            unloaded_count += 1

    if unloaded_count > 0:
        logger.info(f"Unloaded {unloaded_count} models from Ollama memory")

    return unloaded_count


def get_available_ollama_models(use_cache: bool = True, force_refresh: bool = False):
    """
    Fetches available models from the Ollama API, filtering out embedding-only models.
    Uses caching to reduce API calls.

    Args:
        use_cache: Whether to use cached results (default True)
        force_refresh: Force a cache refresh (default False)
    """
    global _model_list_cache

    # Check cache validity
    cache_age = time.time() - _model_list_cache["timestamp"]
    if use_cache and not force_refresh and _model_list_cache["models"] is not None:
        if cache_age < MODEL_LIST_CACHE_TTL:
            logger.debug(f"Returning cached model list (age: {cache_age:.1f}s)")
            return _model_list_cache["models"]

    ollama_tags_url = f"{OLLAMA_BASE_URL}/api/tags"
    logger.info(f"Querying Ollama for available models at: {ollama_tags_url}")
    try:
        # Increased timeout for fine-tuned models which may take longer to enumerate
        response = requests.get(ollama_tags_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        models = data.get("models", [])
        if not isinstance(models, list):
            logger.error(
                f"Unexpected format for 'models' in Ollama response: {type(models)}"
            )
            return {"error": "Invalid format received from Ollama API."}

        # Filter out embedding-only models (cannot be used for chat)
        embedding_patterns = [
            "embed",
            "nomic-embed",
            "all-minilm",
            "bge",
            "gte",
            "e5-",
            "mxbai-embed",
        ]
        chat_models = []
        filtered_count = 0

        for model_item in models:
            model_name = model_item.get("name", "").lower()
            # Skip if model name contains any embedding pattern
            is_embedding = any(pattern in model_name for pattern in embedding_patterns)
            if not is_embedding:
                chat_models.append(model_item)
            else:
                filtered_count += 1
                logger.debug(f"Filtered out embedding model: {model_item.get('name')}")

        logger.info(
            f"Found {len(chat_models)} chat models from Ollama API (filtered {filtered_count} embedding models)."
        )

        # Add a simple id for frontend key prop if missing
        for i, model_item in enumerate(chat_models):
            if "id" not in model_item:
                model_item["id"] = f"ollama_model_{i}"
            if "name" not in model_item:
                model_item["name"] = "unknown:latest"
            model_item["full_name"] = model_item["name"]

        # Update cache
        _model_list_cache["models"] = chat_models
        _model_list_cache["timestamp"] = time.time()

        return chat_models
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Ollama API at {ollama_tags_url}: {e}")
        return {"error": f"Could not connect to Ollama API: {e}"}
    except Exception as e:
        logger.error(f"Error processing Ollama API response: {e}", exc_info=True)
        return {"error": f"Failed to process Ollama API response: {e}"}


@model_bp.route("/list", methods=["GET"])
def list_models():
    """API endpoint to list available models from Ollama."""
    force_refresh = request.args.get("refresh", "").lower() == "true"
    models_data = get_available_ollama_models(
        use_cache=True, force_refresh=force_refresh
    )
    if isinstance(models_data, dict) and models_data.get("error"):
        return error_response(models_data["error"], 502, "OLLAMA_ERROR")
    logger.info(f"Returning {len(models_data)} available models from Ollama.")
    return success_response("Models retrieved", {"models": models_data})


@model_bp.route("/loaded", methods=["GET"])
def list_loaded_models():
    """API endpoint to list currently loaded models in Ollama memory."""
    loaded = get_loaded_models()
    return success_response(
        "Loaded models retrieved", {"models": loaded, "count": len(loaded)}
    )


@model_bp.route("/unload", methods=["POST"])
def unload_models():
    """API endpoint to unload models from Ollama memory."""
    data = request.get_json() or {}
    model_name = data.get("model")
    unload_all = data.get("all", False)

    if unload_all:
        keep_model = data.get("keep")
        count = unload_all_except(keep_model)
        return success_response(f"Unloaded {count} models", {"unloaded_count": count})
    elif model_name:
        success = unload_model_from_ollama(model_name)
        if success:
            return success_response(
                f"Unloaded model {model_name}", {"model": model_name}
            )
        else:
            return error_response(
                f"Failed to unload model {model_name}", 500, "UNLOAD_FAILED"
            )
    else:
        return error_response(
            "Specify 'model' name or 'all': true", 400, "INVALID_REQUEST"
        )


@model_bp.route("/vision", methods=["GET"])
def list_vision_models():
    """API endpoint to list currently available vision models."""
    try:
        from backend.utils.chat_utils import (
            get_available_vision_models,
            clear_vision_models_cache,
        )

        # Check if force refresh requested
        force_refresh = request.args.get("refresh", "").lower() == "true"
        if force_refresh:
            clear_vision_models_cache()
            logger.info("Vision models cache cleared due to force refresh")

        vision_models = get_available_vision_models()

        logger.info(f"Returning {len(vision_models)} vision models")
        return success_response(
            "Vision models retrieved",
            {
                "vision_models": vision_models,
                "count": len(vision_models),
                "cache_refreshed": force_refresh,
            },
        )

    except Exception as e:
        logger.error(f"Error listing vision models: {e}", exc_info=True)
        return error_response(str(e), 500, "VISION_MODELS_ERROR")


@model_bp.route("/vision/check/<model_name>", methods=["GET"])
def check_vision_capability(model_name):
    """API endpoint to check if a specific model supports vision."""
    try:
        from backend.utils.chat_utils import is_vision_model

        is_vision_capable = is_vision_model(model_name)

        logger.debug(f"Vision capability check for '{model_name}': {is_vision_capable}")
        return success_response(
            "Vision capability checked",
            {"model_name": model_name, "is_vision_capable": is_vision_capable},
        )

    except Exception as e:
        logger.error(
            f"Error checking vision capability for '{model_name}': {e}", exc_info=True
        )
        return error_response(str(e), 500, "VISION_CHECK_ERROR")


@model_bp.route("/", methods=["GET"])
def get_current_model():
    """API endpoint to get the currently active model name."""
    if not llama_index_available:
        return error_response("LlamaIndex components unavailable.", 503, "UNAVAILABLE")

    try:
        llm = Settings.llm
        if llm and hasattr(llm, "model"):
            model_name = getattr(llm, "model")
            logger.info(
                f"Returning current active model from LlamaIndex Settings: {model_name}"
            )
            return success_response("Current model retrieved", {"model": model_name})
        else:
            llm_from_config = current_app.config.get("LLAMA_INDEX_LLM")
            if llm_from_config and hasattr(llm_from_config, "model"):
                model_name = getattr(llm_from_config, "model")
                logger.warning(
                    f"LLM not found in Settings, returning model from app config: {model_name}"
                )
                return success_response(
                    "Current model retrieved", {"model": model_name}
                )
            else:
                logger.error(
                    "Could not determine active model from Settings or app config."
                )
                return error_response(
                    "Active model not configured or found.", 500, "NOT_FOUND"
                )
    except Exception as e:
        logger.error(f"Error retrieving current model: {e}", exc_info=True)
        return error_response(
            "Failed to retrieve current model.", 500, "RETRIEVAL_ERROR"
        )


@model_bp.route("/health", methods=["GET"])
def model_health():
    """Return active model name and availability from Ollama."""
    if not llama_index_available:
        return error_response("LlamaIndex components unavailable.", 503, "UNAVAILABLE")

    llm = Settings.llm or current_app.config.get("LLAMA_INDEX_LLM")
    model_name = getattr(llm, "model", None) if llm else None
    if not model_name:
        return error_response("Active model not configured", 503, "NOT_CONFIGURED")
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/show", json={"name": model_name}, timeout=10
        )
        available = bool(resp.ok)
    except requests.RequestException as e:
        logger.error("Model health check failed: %s", e)
        return error_response(str(e), 503, "HEALTH_CHECK_FAILED")
    return success_response(
        "Model health checked", {"active_model": model_name, "available": available}
    )


@model_bp.route("/status", methods=["GET"])
def model_status():
    """Return comprehensive model status including text, vision, and image generation models."""
    if not llama_index_available:
        return jsonify({"error": "LlamaIndex components unavailable."}), 503

    try:
        # Get current text model
        llm = Settings.llm or current_app.config.get("LLAMA_INDEX_LLM")
        text_model = getattr(llm, "model", None) if llm else None

        # Check if current text model has vision capabilities
        vision_model = text_model  # Default to text model
        vision_loaded = False

        if text_model:
            try:
                from backend.utils.chat_utils import is_vision_model

                # Check if the current text model supports vision
                if is_vision_model(text_model):
                    # Current model is vision-capable, check if it's loaded
                    try:
                        resp = requests.post(
                            f"{OLLAMA_BASE_URL}/api/show",
                            json={"name": text_model},
                            timeout=5,
                        )
                        vision_loaded = bool(resp.ok)
                        logger.debug(
                            f"Text model '{text_model}' is vision-capable and loaded: {vision_loaded}"
                        )
                    except (
                        requests.RequestException,
                        requests.Timeout,
                        ConnectionError,
                    ) as e:
                        logger.debug(
                            f"Vision model check failed for '{text_model}': {e}"
                        )
                        vision_loaded = False
                else:
                    # Text model doesn't support vision, check for separate vision models
                    vision_model = "llava"  # Fallback to separate vision model
                    try:
                        resp = requests.post(
                            f"{OLLAMA_BASE_URL}/api/show",
                            json={"name": vision_model},
                            timeout=5,
                        )
                        vision_loaded = bool(resp.ok)
                        logger.debug(
                            f"Separate vision model '{vision_model}' loaded: {vision_loaded}"
                        )
                    except (
                        requests.RequestException,
                        requests.Timeout,
                        ConnectionError,
                    ) as e:
                        logger.debug(f"Separate vision model check failed: {e}")
                        vision_loaded = False

            except ImportError as e:
                logger.warning(f"Could not import vision detection utilities: {e}")
                # Fallback to checking for llava
                vision_model = "llava"
                try:
                    resp = requests.post(
                        f"{OLLAMA_BASE_URL}/api/show",
                        json={"name": vision_model},
                        timeout=5,
                    )
                    vision_loaded = bool(resp.ok)
                except (
                    requests.RequestException,
                    requests.Timeout,
                    ConnectionError,
                ) as e:
                    logger.debug(f"Fallback vision model check failed: {e}")
                    vision_loaded = False

        # Default image generation model (separate from vision)
        image_gen_model = "sdxl"

        # Check if image gen model is loaded (dummy check for now)
        image_gen_loaded = False
        try:
            # Try to check if sdxl is available
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/show", json={"name": image_gen_model}, timeout=5
            )
            image_gen_loaded = bool(resp.ok)
        except (requests.RequestException, requests.Timeout, ConnectionError) as e:
            logger.debug(f"Image gen model check failed: {e}")
            image_gen_loaded = False

        status_data = {
            "text_model": text_model,
            "vision_model": vision_model,
            "vision_loaded": vision_loaded,
            "image_gen_model": image_gen_model,
            "image_gen_loaded": image_gen_loaded,
        }

        logger.info(f"Model status: {status_data}")
        return success_response("Model status retrieved", status_data)

    except Exception as e:
        logger.error(f"Error getting model status: {e}", exc_info=True)
        return error_response("Failed to get model status", 500, "MODEL_STATUS_ERROR")


@model_bp.route("/resources", methods=["GET"])
def get_resources():
    """Return current GPU/RAM usage, loaded models, and available budget.

    Uses nvidia-smi and psutil directly (non-blocking). Avoids torch.cuda
    which can deadlock when Ollama holds the GPU.
    """
    import subprocess

    gpu_free = 0.0
    gpu_total = 0.0
    ram_free = 0.0
    ram_total = 0.0

    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.free,memory.total",
                "--format=csv,nounits,noheader",
            ],
            timeout=5,
            text=True,
        )
        parts = out.strip().split(",")
        if len(parts) == 2:
            gpu_free = float(parts[0].strip())
            gpu_total = float(parts[1].strip())
    except Exception:
        pass

    try:
        import psutil

        vm = psutil.virtual_memory()
        ram_free = vm.available / (1024 * 1024)
        ram_total = vm.total / (1024 * 1024)
    except Exception:
        pass

    gpu_used = gpu_total - gpu_free
    logger.info("RESOURCES: nvidia-smi done, calling get_loaded_models...")

    loaded = get_loaded_models()
    logger.info("RESOURCES: get_loaded_models done, %d models", len(loaded))
    loaded_summary = [
        {
            "name": m.get("name", "unknown"),
            "vram_mb": round(m.get("size_vram", 0) / (1024 * 1024)),
            "ram_mb": round(m.get("size", 0) / (1024 * 1024)),
        }
        for m in loaded
    ]

    logger.info("RESOURCES: building loaded_summary...")
    # Embedding model name — read from DB if available
    embed_model_name = None
    try:
        if db and Setting:
            logger.info("RESOURCES: querying DB for embedding model...")
            setting = db.session.get(Setting, "active_embedding_model")
            if setting and setting.value:
                embed_model_name = setting.value
            logger.info("RESOURCES: DB query done, model=%s", embed_model_name)
    except Exception as e:
        logger.info("RESOURCES: DB query failed: %s", e)

    # Router stats — read cached state only (no lazy init)
    router_stats = None
    logger.info("RESOURCES: checking router...")
    try:
        from backend.utils.embedding_router import _embedding_router

        if _embedding_router is not None and _embedding_router._initialized:
            router_stats = {
                "hardware_profile": _embedding_router.hardware_profile.value,
                "gpu_enabled": _embedding_router.profile_config.get(
                    "gpu_enabled", False
                ),
                "parallel_threshold": _embedding_router.profile_config.get(
                    "parallel_threshold", 0
                ),
                "gpu_initialized": _embedding_router._gpu_embedding is not None,
                "cpu_initialized": _embedding_router._cpu_embedding is not None,
                "active_model": _embedding_router._active_model_name,
                "embed_dim": _embedding_router._embed_dim,
                "latency": _embedding_router.latency_tracker.get_stats(),
            }
    except Exception:
        pass

    return success_response(
        "Resources retrieved",
        {
            "gpu": {
                "total_mb": round(gpu_total),
                "used_mb": round(gpu_used),
                "free_mb": round(gpu_free),
                "utilization_pct": (
                    round(gpu_used / gpu_total * 100, 1) if gpu_total > 0 else 0
                ),
            },
            "ram": {
                "total_mb": round(ram_total),
                "free_mb": round(ram_free),
            },
            "loaded_models": loaded_summary,
            "embedding_model": embed_model_name,
            "embedding_router": router_stats,
        },
    )


@model_bp.route("/embedding/list", methods=["GET"])
def list_embedding_models():
    """List available embedding models from Ollama."""
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        response.raise_for_status()
        all_models = response.json().get("models", [])
    except Exception as e:
        return error_response(f"Cannot reach Ollama: {e}", 502, "OLLAMA_ERROR")

    embedding_patterns = [
        "embed",
        "nomic-embed",
        "all-minilm",
        "bge",
        "gte",
        "e5-",
        "mxbai-embed",
    ]
    embedding_models = []
    for m in all_models:
        name = m.get("name", "").lower()
        if any(p in name for p in embedding_patterns):
            embedding_models.append(
                {
                    "name": m.get("name"),
                    "size_mb": round(m.get("size", 0) / (1024 * 1024)),
                }
            )

    # Also get the currently active embedding model
    active_embed = None
    try:
        from backend.config import get_active_embedding_model

        active_embed = get_active_embedding_model()
    except Exception:
        pass

    return success_response(
        {
            "models": embedding_models,
            "active": active_embed,
        },
        "Embedding models retrieved",
    )


@model_bp.route("/embedding/set", methods=["POST"])
def set_embedding_model():
    """Switch the active embedding model at runtime.

    This reinitializes the EmbeddingRouter singleton and updates the
    LlamaIndex embed model stored in app config. Existing indexes
    keep their vectors — only new embeddings use the new model.
    """
    data = request.get_json() or {}
    model_name = data.get("model")
    if not model_name:
        return error_response("Missing 'model' in request body", 400, "INVALID_REQUEST")

    logger.info(f"Embedding model switch requested: {model_name}")

    # Verify model exists in Ollama
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/show", json={"name": model_name}, timeout=10
        )
        if not resp.ok:
            return error_response(
                f"Model '{model_name}' not found in Ollama", 404, "NOT_FOUND"
            )
    except Exception as e:
        return error_response(f"Cannot reach Ollama: {e}", 502, "OLLAMA_ERROR")

    try:
        from llama_index.embeddings.ollama import OllamaEmbedding

        # Test the embedding model
        test_embed = OllamaEmbedding(
            model_name=model_name,
            base_url=OLLAMA_BASE_URL,
        )
        test_vec = test_embed.get_text_embedding("test")
        embed_dim = len(test_vec)
        logger.info(f"Embedding model '{model_name}' produces {embed_dim}-dim vectors")

        # Reset the EmbeddingRouter singleton so it picks up the new model
        try:
            from backend.utils.embedding_router import (
                EmbeddingRouter,
                RouterEmbeddingAdapter,
            )

            router = EmbeddingRouter()
            router._cpu_embedding = test_embed
            router._active_model_name = model_name
            router._embed_dim = embed_dim
            adapter = RouterEmbeddingAdapter(router)
            current_app.config["LLAMA_INDEX_EMBED_MODEL"] = adapter
            logger.info(f"EmbeddingRouter updated with model: {model_name}")
        except Exception as router_err:
            logger.warning(
                f"EmbeddingRouter update failed, using direct OllamaEmbedding: {router_err}"
            )
            current_app.config["LLAMA_INDEX_EMBED_MODEL"] = test_embed

        # Update LlamaIndex global Settings
        try:
            from llama_index.core import Settings as LISettings

            LISettings.embed_model = current_app.config["LLAMA_INDEX_EMBED_MODEL"]
        except Exception:
            pass

        # Clear vector store to prevent mixed-dimension corruption
        try:
            import os

            storage_dir = current_app.config.get(
                "STORAGE_DIR",
                os.path.join(os.environ.get("GUAARDVARK_ROOT", ""), "data"),
            )
            cleared_files = []
            for fname in ("default__vector_store.json", "vector_store.json"):
                fpath = os.path.join(storage_dir, fname)
                if os.path.exists(fpath):
                    os.remove(fpath)
                    cleared_files.append(fname)
            if cleared_files:
                logger.info(
                    f"Cleared vector store files after embedding switch: {cleared_files}"
                )
                # Reset in-memory index so it rebuilds fresh on next use
                try:
                    import backend.services.indexing_service as idx_svc

                    idx_svc.index = None
                    idx_svc.storage_context = None
                except Exception:
                    pass
        except Exception as vs_err:
            logger.warning(f"Failed to clear vector store: {vs_err}")

        # Persist choice to database
        try:
            setting = db.session.get(Setting, "active_embedding_model")
            if setting:
                setting.value = model_name
            else:
                setting = Setting(key="active_embedding_model", value=model_name)
                db.session.add(setting)
            db.session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist embedding model to DB: {e}")

        return success_response(
            f"Embedding model switched to {model_name}",
            {
                "model": model_name,
                "dimensions": embed_dim,
            },
        )

    except Exception as e:
        logger.error(f"Failed to switch embedding model: {e}", exc_info=True)
        return error_response(
            f"Failed to switch embedding model: {str(e)}", 500, "SWITCH_FAILED"
        )


def switch_active_model(new_model_name: str):
    """Switch the active LLM model without using the HTTP API."""
    if not llama_index_available or not local_imports_ok:
        raise RuntimeError("Core components unavailable.")

    # Get current model name for later unloading
    old_model_name = None
    current_llm = Settings.llm or current_app.config.get("LLAMA_INDEX_LLM")
    if current_llm and hasattr(current_llm, "model"):
        old_model_name = getattr(current_llm, "model", None)
        logger.info(f"Current model before switch: {old_model_name}")

    # Use cached model list to avoid slow re-fetch
    available_models_data = get_available_ollama_models(use_cache=True)
    if isinstance(available_models_data, dict) and available_models_data.get("error"):
        raise RuntimeError(
            f"Failed to validate model existence: {available_models_data['error']}"
        )

    available_model_names = [
        m.get("name", "").lower() for m in available_models_data if isinstance(m, dict)
    ]
    if not any(
        n == new_model_name.lower() or new_model_name.lower() in n
        for n in available_model_names
    ):
        raise ValueError(
            f"Model '{new_model_name}' not found or available via Ollama API."
        )

    # Unload old model FIRST to free VRAM before loading new one
    if old_model_name and old_model_name.lower() != new_model_name.lower():
        logger.info(f"Unloading old model '{old_model_name}' to free VRAM")
        unload_model_from_ollama(old_model_name)
        gc.collect()
        time.sleep(1)

    # Adaptive context window based on available resources (computed AFTER unload)
    try:
        from backend.utils.ollama_resource_manager import compute_optimal_num_ctx

        num_ctx = compute_optimal_num_ctx(new_model_name)
    except Exception as e:
        logger.warning(f"Failed to compute adaptive num_ctx: {e}, using 8192")
        num_ctx = 8192

    logger.info(
        f"Creating new Ollama instance for model: {new_model_name} (num_ctx={num_ctx})"
    )
    new_llm = Ollama(
        model=new_model_name,
        base_url=OLLAMA_BASE_URL,
        request_timeout=120.0,
        temperature=0.4,
        context_window=num_ctx,
        additional_kwargs={"num_ctx": num_ctx, "top_p": 0.8, "top_k": 30},
    )
    new_llm.complete("Test.")
    logger.info(f"Successfully created and tested Ollama instance for {new_model_name}")

    logger.info("Updating global LlamaIndex Settings.llm...")
    Settings.llm = new_llm

    # Import database components once at function level for consistent access
    db = None
    Setting = None
    try:
        from backend.models import db, Setting
    except ImportError as e:
        logger.warning(f"Failed to import database models: {e}")

    index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
    if not index_instance:
        logger.warning(
            "Index instance not found in app config. Attempting to continue without query engine recreation..."
        )
        # Don't raise an error - allow model switching to continue
        # Query engine will be recreated when index is next loaded
        logger.info(
            "Model switch will continue without query engine recreation - engines will be recreated when index is loaded"
        )
    else:
        logger.debug(
            f"Fetching QA template for new model '{new_model_name}' to recreate engines."
        )

        # Import rule_utils for database-based rule fetching
        try:
            from backend import rule_utils

            # Fetch qa_default template from database via RulesPage
            qa_template_string, rule_id = rule_utils.get_active_qa_default_template(
                db.session, model_name=new_model_name
            )

            if rule_id:
                logger.info(
                    f"Using qa_default rule ID {rule_id} from database for model '{new_model_name}'"
                )
            else:
                logger.info(
                    f"Using fallback qa_default template for model '{new_model_name}'"
                )

        except ImportError as e:
            logger.error(f"Failed to import rule_utils for QA template: {e}")
            # Fallback to basic template
            qa_template_string = "{context_str}\n\n{query_str}"
        except Exception as e:
            logger.error(f"Error fetching QA template from database: {e}")
            # Fallback to basic template
            qa_template_string = "{context_str}\n\n{query_str}"

        if not qa_template_string or "{query_str}" not in qa_template_string:
            logger.error(
                "Failed to fetch a valid QA prompt template from database. Cannot recreate engines."
            )
            raise ValueError("Invalid QA prompt template fetched.")

        if "{rules_str}" not in qa_template_string:
            logger.warning(
                "QA template fetched for engine recreation missing {rules_str}. Adding."
            )
            qa_template_string = "{rules_str}\n\n" + qa_template_string

        try:
            current_format_args = {
                "rules_str": "",
                "context_str": "{context_str}",
                "query_str": "{query_str}",
                "show_reasoning_text_block": "",
                "show_reasoning": "",
            }
            if "{show_reasoning_text_block}" not in qa_template_string:
                current_format_args.pop("show_reasoning_text_block")
            if "{show_reasoning}" not in qa_template_string:
                current_format_args.pop("show_reasoning")

            formatted_template_for_engine = qa_template_string.format(
                **current_format_args
            )
            base_qa_template_obj = PromptTemplate(formatted_template_for_engine)

        except KeyError as fmt_err:
            logger.error(
                f"Failed to format base QA template for engine recreation: {fmt_err}. Template: '{qa_template_string[:200]}...'"
            )
            raise ValueError(
                f"Invalid QA prompt template structure (missing key '{fmt_err.args[0]}')."
            )

        logger.info("Re-creating query engine with new LLM settings...")
        new_query_engine = index_instance.as_query_engine(
            llm=new_llm,
            streaming=True,
            text_qa_template=base_qa_template_obj,
        )
        logger.info("Query engine re-created successfully.")

        logger.info("Updating Flask app context with new LlamaIndex components...")
        current_app.config["LLAMA_INDEX_QUERY_ENGINE"] = new_query_engine

    # Always update the LLM regardless of index availability
    current_app.config["LLAMA_INDEX_LLM"] = new_llm
    logger.info(f"Successfully updated runtime model to: {new_model_name}")

    # Persist to JSON file and database
    try:
        from backend.utils.llm_service import persist_active_model_name

        persist_active_model_name(new_model_name)
    except Exception as e:
        logger.warning("Failed to persist active model: %s", e)

    # Force garbage collection to free Python memory
    gc.collect()
    logger.debug("Garbage collection completed after model switch")


def _switch_model_background(app, new_model_name: str):
    """Background task to switch the active LLM model with SocketIO notifications.

    Strategy: unload the OLD model FIRST to free VRAM before loading the new one.
    If the new model fails to load, attempt to reload the old model as rollback.
    """
    with app.app_context():
        old_model_name = None
        try:
            # Get current model name for unloading
            current_llm = Settings.llm or current_app.config.get("LLAMA_INDEX_LLM")
            if current_llm and hasattr(current_llm, "model"):
                old_model_name = getattr(current_llm, "model", None)
                logger.info(f"Current model before switch: {old_model_name}")

            # Emit started event
            socketio.emit(
                "model_switch",
                {
                    "status": "loading",
                    "model": new_model_name,
                    "message": f"Loading model {new_model_name}...",
                },
                namespace="/",
            )

            # Validate model exists (uses cached list)
            available_models_data = get_available_ollama_models(use_cache=True)
            if isinstance(available_models_data, dict) and available_models_data.get(
                "error"
            ):
                raise RuntimeError(
                    f"Failed to validate model: {available_models_data['error']}"
                )

            available_model_names = [
                m.get("name", "").lower()
                for m in available_models_data
                if isinstance(m, dict)
            ]
            if not any(
                n == new_model_name.lower() or new_model_name.lower() in n
                for n in available_model_names
            ):
                raise ValueError(f"Model '{new_model_name}' not found in Ollama.")

            # --- Unload old model FIRST to free VRAM ---
            if old_model_name and old_model_name.lower() != new_model_name.lower():
                socketio.emit(
                    "model_switch",
                    {
                        "status": "loading",
                        "model": new_model_name,
                        "message": f"Unloading {old_model_name} to free resources...",
                    },
                    namespace="/",
                )
                unload_model_from_ollama(old_model_name)
                gc.collect()
                logger.info(
                    f"Unloaded old model '{old_model_name}' before loading new one"
                )
                # Brief pause to let Ollama release memory
                time.sleep(1)

            socketio.emit(
                "model_switch",
                {
                    "status": "loading",
                    "model": new_model_name,
                    "message": f"Creating LLM instance for {new_model_name}...",
                },
                namespace="/",
            )

            # Adaptive context window based on available resources (computed AFTER unload)
            try:
                from backend.utils.ollama_resource_manager import (
                    compute_optimal_num_ctx,
                )

                num_ctx = compute_optimal_num_ctx(new_model_name)
            except Exception as e:
                logger.warning(f"Failed to compute adaptive num_ctx: {e}, using 8192")
                num_ctx = 8192

            # Create new LLM instance
            logger.info(
                f"Creating new Ollama instance for model: {new_model_name} (num_ctx={num_ctx})"
            )
            new_llm = Ollama(
                model=new_model_name,
                base_url=OLLAMA_BASE_URL,
                request_timeout=300.0,
                temperature=0.4,
                context_window=num_ctx,
                additional_kwargs={"num_ctx": num_ctx, "top_p": 0.8, "top_k": 30},
            )

            socketio.emit(
                "model_switch",
                {
                    "status": "loading",
                    "model": new_model_name,
                    "message": f"Warming up {new_model_name} (this may take a moment)...",
                },
                namespace="/",
            )

            # Test the model — this triggers Ollama to actually load it into VRAM
            new_llm.complete("Hello")
            logger.info(
                f"Successfully created and tested Ollama instance for {new_model_name}"
            )

            # Update global settings
            Settings.llm = new_llm

            socketio.emit(
                "model_switch",
                {
                    "status": "loading",
                    "model": new_model_name,
                    "message": "Updating query engines...",
                },
                namespace="/",
            )

            # Update app config
            current_app.config["LLAMA_INDEX_LLM"] = new_llm

            # Recreate query engine if index exists
            index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
            if index_instance:
                try:
                    from backend import rule_utils
                    from backend.models import db as db_instance

                    qa_template_string, rule_id = (
                        rule_utils.get_active_qa_default_template(
                            db_instance.session, model_name=new_model_name
                        )
                    )
                    if (
                        not qa_template_string
                        or "{query_str}" not in qa_template_string
                    ):
                        qa_template_string = "{context_str}\n\n{query_str}"

                    if "{rules_str}" not in qa_template_string:
                        qa_template_string = "{rules_str}\n\n" + qa_template_string

                    current_format_args = {
                        "rules_str": "",
                        "context_str": "{context_str}",
                        "query_str": "{query_str}",
                        "show_reasoning_text_block": "",
                        "show_reasoning": "",
                    }
                    if "{show_reasoning_text_block}" not in qa_template_string:
                        current_format_args.pop("show_reasoning_text_block")
                    if "{show_reasoning}" not in qa_template_string:
                        current_format_args.pop("show_reasoning")

                    formatted_template = qa_template_string.format(
                        **current_format_args
                    )
                    base_qa_template_obj = PromptTemplate(formatted_template)

                    new_query_engine = index_instance.as_query_engine(
                        llm=new_llm,
                        streaming=True,
                        text_qa_template=base_qa_template_obj,
                    )
                    current_app.config["LLAMA_INDEX_QUERY_ENGINE"] = new_query_engine
                    logger.info("Query engine re-created successfully.")
                except Exception as e:
                    logger.warning(f"Failed to recreate query engine: {e}")

            # Persist to file and database
            try:
                from backend.utils.llm_service import persist_active_model_name

                persist_active_model_name(new_model_name)
            except Exception as e:
                logger.warning(f"Failed to persist model: {e}")

            # Force garbage collection
            gc.collect()

            # Emit success
            socketio.emit(
                "model_switch",
                {
                    "status": "complete",
                    "model": new_model_name,
                    "message": f"Successfully switched to {new_model_name}",
                },
                namespace="/",
            )
            logger.info(f"Model switch complete: {new_model_name}")

        except Exception as e:
            logger.error(f"Background model switch failed: {e}", exc_info=True)

            # Rollback: try to reload the old model if the new one failed
            if old_model_name and old_model_name.lower() != new_model_name.lower():
                logger.info(f"Attempting rollback to previous model: {old_model_name}")
                socketio.emit(
                    "model_switch",
                    {
                        "status": "loading",
                        "model": new_model_name,
                        "message": f"Failed — rolling back to {old_model_name}...",
                    },
                    namespace="/",
                )
                try:
                    rollback_llm = Ollama(
                        model=old_model_name,
                        base_url=OLLAMA_BASE_URL,
                        request_timeout=300.0,
                        temperature=0.4,
                        context_window=8192,
                        additional_kwargs={"num_ctx": 8192, "top_p": 0.8, "top_k": 30},
                    )
                    rollback_llm.complete("Hello")
                    Settings.llm = rollback_llm
                    current_app.config["LLAMA_INDEX_LLM"] = rollback_llm
                    logger.info(f"Rollback to {old_model_name} succeeded")
                except Exception as rollback_err:
                    logger.error(
                        f"Rollback to {old_model_name} also failed: {rollback_err}"
                    )

            socketio.emit(
                "model_switch",
                {
                    "status": "error",
                    "model": new_model_name,
                    "message": f"Failed to switch model: {str(e)}",
                },
                namespace="/",
            )
            gc.collect()


@model_bp.route("/set", methods=["POST"])
def set_current_model():
    """API endpoint to set the active LLM model (async with SocketIO notifications)."""
    logger.info("Received request for POST /api/model/set")
    if not llama_index_available or not local_imports_ok:
        logger.error("Cannot set model: LlamaIndex or local imports unavailable.")
        return jsonify({"error": "Core components unavailable."}), 503

    data = request.get_json()
    if not data or "model" not in data:
        return jsonify({"error": "Missing 'model' in request body"}), 400

    new_model_name = data["model"]
    logger.info(f"Starting async model switch to: {new_model_name}")

    # Quick validation using cached model list (avoids slow re-fetch)
    available_models_data = get_available_ollama_models(use_cache=True)
    if isinstance(available_models_data, dict) and available_models_data.get("error"):
        return (
            jsonify(
                {"error": f"Cannot verify model: {available_models_data['error']}"}
            ),
            500,
        )

    available_model_names = [
        m.get("name", "").lower() for m in available_models_data if isinstance(m, dict)
    ]
    if not any(
        n == new_model_name.lower() or new_model_name.lower() in n
        for n in available_model_names
    ):
        return jsonify({"error": f"Model '{new_model_name}' not found in Ollama"}), 404

    # Start background thread for model switching
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_switch_model_background, args=(app, new_model_name), daemon=True
    )
    thread.start()

    # Return immediately - frontend will get updates via SocketIO
    return (
        jsonify(
            {
                "message": f"Model switch to {new_model_name} started",
                "status": "switching",
            }
        ),
        202,
    )
