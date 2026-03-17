# backend/services/image_content_service.py
# Image Content Extraction Service - Phase 2A.1
# Leverages existing Ollama vision model infrastructure for OCR capabilities

import logging
import os
import base64
import tempfile
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

# Enhanced import handling with multiple fallback patterns
try:
    # Try primary import pattern
    from llama_index.llms.ollama import Ollama

    ollama_available = True
    import_source = "llama_index.llms.ollama"
except ImportError:
    try:
        # Try alternative import pattern
        from llama_index_llms_ollama import Ollama

        ollama_available = True
        import_source = "llama_index_llms_ollama"
    except ImportError:
        try:
            # Try direct ollama python package
            import ollama

            Ollama = None  # Will use direct API calls
            ollama_available = True
            import_source = "ollama_direct"
        except ImportError:
            logger.warning(
                "Ollama package not available - image OCR service will be disabled"
            )
            ollama_available = False
            import_source = "none"

try:
    from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT
    from backend.utils.chat_utils import VISION_MODEL_PATTERNS
    import requests

    config_available = True
except ImportError as e:
    logger.error(f"Failed to import backend config dependencies: {e}")
    config_available = False
    OLLAMA_BASE_URL = "http://localhost:11434"
    LLM_REQUEST_TIMEOUT = 120
    VISION_MODEL_PATTERNS = ["vision", "llava", "gpt-4", "gpt4", "gpt-4o"]


class ImageContentExtractor:
    """Extracts text content from images using vision models."""

    def __init__(self):
        # Fallback vision models if current model doesn't support vision
        self.fallback_models = [
            "llava:latest",
            "llava:7b",
            "llava:13b",
            "llava:34b",
            "moondream",
            "moondream:latest",
        ]
        self.max_image_size_mb = 10  # 10MB limit
        self.supported_formats = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        self.service_available = ollama_available and config_available
        self.import_source = import_source

    def is_image_file(self, file_path: str) -> bool:
        """Check if file is a supported image format."""
        return Path(file_path).suffix.lower() in self.supported_formats

    def _get_available_vision_model(self) -> Optional[str]:
        """Get the first available vision model from Ollama, prioritizing current model."""
        if not self.service_available:
            logger.warning("Ollama service not available for vision model detection")
            return None

        # First check if current text model supports vision
        try:
            from backend.utils.chat_utils import is_vision_model
            from backend.config import Settings

            # Get current text model
            llm = Settings.llm
            current_model = getattr(llm, "model", None) if llm else None

            if current_model and is_vision_model(current_model):
                # Check if current model is loaded and available
                try:
                    resp = requests.post(
                        f"{OLLAMA_BASE_URL}/api/show",
                        json={"name": current_model},
                        timeout=5,
                    )
                    if resp.ok:
                        logger.info(
                            f"Using current vision-capable model: {current_model}"
                        )
                        return current_model
                except Exception as e:
                    logger.debug(f"Current model check failed: {e}")

        except ImportError as e:
            logger.warning(f"Could not import vision detection utilities: {e}")
        except Exception as e:
            logger.debug(f"Error checking current model for vision: {e}")

        # Fallback to checking predefined models
        for model_name in self.fallback_models:
            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/show", json={"name": model_name}, timeout=5
                )
                if resp.ok:
                    logger.info(f"Found available fallback vision model: {model_name}")
                    return model_name
            except (requests.RequestException, requests.Timeout, ConnectionError) as e:
                logger.debug(f"Vision model {model_name} check failed: {e}")
                continue

        # Try to find any model with vision patterns
        try:
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
            if resp.ok:
                models_data = resp.json().get("models", [])
                for model_data in models_data:
                    if isinstance(model_data, dict):
                        model_name = model_data.get("name", "").lower()
                        for pattern in VISION_MODEL_PATTERNS:
                            if pattern in model_name:
                                logger.info(
                                    f"Found vision model by pattern: {model_name}"
                                )
                                return model_data.get("name")
        except Exception as e:
            logger.warning(f"Failed to search for vision models by pattern: {e}")

        logger.warning("No vision models found in Ollama")
        return None

    def _encode_image_to_base64(self, image_path: str) -> Optional[str]:
        """Encode image file to base64 string."""
        try:
            # Check file size
            file_size_mb = os.path.getsize(image_path) / (1024 * 1024)
            if file_size_mb > self.max_image_size_mb:
                logger.warning(
                    f"Image {image_path} too large: {file_size_mb:.1f}MB > {self.max_image_size_mb}MB"
                )
                return None

            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
                logger.debug(
                    f"Encoded image {image_path} to base64 ({len(encoded_string)} chars)"
                )
                return encoded_string

        except Exception as e:
            logger.error(f"Failed to encode image {image_path}: {e}")
            return None

    def _create_vision_llm(self, model_name: str) -> Optional[Any]:
        """Create Ollama vision model instance with fallback support."""
        if not self.service_available:
            return None

        try:
            if (
                self.import_source == "llama_index.llms.ollama"
                or self.import_source == "llama_index_llms_ollama"
            ):
                # Use LlamaIndex Ollama wrapper with adaptive context
                timeout_value = min(
                    LLM_REQUEST_TIMEOUT, 120.0
                )  # Cap at 2 minutes for vision
                try:
                    from backend.utils.ollama_resource_manager import (
                        compute_optimal_num_ctx,
                    )

                    num_ctx = compute_optimal_num_ctx(model_name)
                except Exception:
                    num_ctx = 4096  # Conservative default for vision models
                vision_llm = Ollama(
                    model=model_name,
                    base_url=OLLAMA_BASE_URL,
                    request_timeout=timeout_value,
                    context_window=num_ctx,
                    additional_kwargs={"num_ctx": num_ctx},
                )

                # Test the model with a simple prompt
                test_response = vision_llm.complete("Test")
                logger.debug(
                    f"Vision model {model_name} test successful (num_ctx={num_ctx})"
                )
                return vision_llm

            elif self.import_source == "ollama_direct":
                # Use direct ollama client
                import ollama

                client = ollama.Client(host=OLLAMA_BASE_URL)
                # Test the model with explicit num_ctx to prevent huge KV cache
                try:
                    from backend.utils.ollama_resource_manager import (
                        compute_optimal_num_ctx,
                    )

                    num_ctx = compute_optimal_num_ctx(model_name)
                except Exception:
                    num_ctx = 4096
                response = client.generate(
                    model=model_name, prompt="Test", options={"num_ctx": num_ctx}
                )
                logger.debug(
                    f"Direct Ollama model {model_name} test successful (num_ctx={num_ctx})"
                )
                return client

        except Exception as e:
            logger.error(f"Failed to create vision LLM for {model_name}: {e}")
            return None

    def _extract_with_direct_api(
        self, model_name: str, image_path: str
    ) -> Dict[str, Any]:
        """Extract text using direct Ollama API calls."""
        try:
            import ollama

            # Create vision prompt for OCR
            ocr_prompt = """Please extract all text content from this image. Include:
1. Any visible text, words, or characters
2. Text from signs, labels, documents, or screenshots
3. Handwritten text if legible
4. Text in any language

Please provide only the extracted text content, without explanations or descriptions of the image itself. If no text is visible, respond with 'NO_TEXT_FOUND'."""

            # Use direct API call with image, with adaptive context to prevent OOM
            client = ollama.Client(host=OLLAMA_BASE_URL)
            try:
                from backend.utils.ollama_resource_manager import (
                    compute_optimal_num_ctx,
                )

                num_ctx = compute_optimal_num_ctx(model_name)
            except Exception:
                num_ctx = 4096

            with open(image_path, "rb") as image_file:
                response = client.generate(
                    model=model_name,
                    prompt=ocr_prompt,
                    images=[image_file.read()],
                    options={"num_ctx": num_ctx},
                )

            extracted_text = response.get("response", "").strip()

            return {
                "success": True,
                "text_content": (
                    extracted_text if extracted_text != "NO_TEXT_FOUND" else ""
                ),
                "confidence": (
                    0.8 if extracted_text and extracted_text != "NO_TEXT_FOUND" else 0.9
                ),
                "model_used": model_name,
                "method": "direct_api",
            }

        except Exception as e:
            logger.error(f"Direct API extraction failed: {e}")
            return {
                "success": False,
                "text_content": "",
                "confidence": 0.0,
                "model_used": model_name,
                "error": f"Direct API call failed: {str(e)}",
            }

    def extract_text_from_image(self, image_path: str) -> Dict[str, Any]:
        """
        Extract text content from an image using vision model.

        Returns:
            Dict with keys: 'success', 'text_content', 'confidence', 'model_used', 'error'
        """
        result = {
            "success": False,
            "text_content": "",
            "confidence": 0.0,
            "model_used": None,
            "error": None,
            "service_info": {
                "service_available": self.service_available,
                "import_source": self.import_source,
            },
        }

        if not self.is_image_file(image_path):
            result["error"] = f"Unsupported image format: {Path(image_path).suffix}"
            return result

        if not os.path.exists(image_path):
            result["error"] = f"Image file not found: {image_path}"
            return result

        if not self.service_available:
            result["error"] = (
                "Ollama service not available - install llama-index-llms-ollama package"
            )
            return result

        # Get available vision model
        vision_model = self._get_available_vision_model()
        if not vision_model:
            result["error"] = "No vision models available in Ollama"
            return result

        logger.info(
            f"Extracting text from image: {image_path} using model: {vision_model}"
        )

        # Try direct API approach first (more reliable)
        if self.import_source == "ollama_direct":
            return self._extract_with_direct_api(vision_model, image_path)

        # Fallback to LlamaIndex wrapper approach
        try:
            # Encode image to base64
            encoded_image = self._encode_image_to_base64(image_path)
            if not encoded_image:
                result["error"] = "Failed to encode image to base64"
                return result

            # Create vision LLM
            vision_llm = self._create_vision_llm(vision_model)
            if not vision_llm:
                result["error"] = f"Failed to create vision LLM for {vision_model}"
                return result

            # Create vision prompt for OCR
            ocr_prompt = """Please extract all text content from this image. Include:
1. Any visible text, words, or characters
2. Text from signs, labels, documents, or screenshots
3. Handwritten text if legible
4. Text in any language

Please provide only the extracted text content, without explanations or descriptions of the image itself. If no text is visible, respond with 'NO_TEXT_FOUND'."""

            # Send prompt to vision model
            response = vision_llm.complete(ocr_prompt)
            extracted_text = str(response).strip()

            if extracted_text and extracted_text != "NO_TEXT_FOUND":
                result["success"] = True
                result["text_content"] = extracted_text
                result["confidence"] = (
                    0.8  # Default confidence for successful extraction
                )
                result["model_used"] = vision_model

                logger.info(
                    f"Successfully extracted {len(extracted_text)} characters from {image_path}"
                )
                logger.debug(f"Extracted text preview: {extracted_text[:200]}...")
            else:
                result["success"] = True  # Successful processing, but no text found
                result["text_content"] = ""
                result["confidence"] = 0.9  # High confidence in "no text" result
                result["model_used"] = vision_model
                logger.info(f"No text found in image: {image_path}")

        except Exception as e:
            logger.error(f"Vision model extraction failed for {image_path}: {e}")
            result["error"] = f"Vision model processing failed: {str(e)}"

        return result

    def batch_extract_text(self, image_paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Extract text from multiple images."""
        results = {}

        for image_path in image_paths:
            try:
                results[image_path] = self.extract_text_from_image(image_path)
            except Exception as e:
                logger.error(f"Failed to process image {image_path}: {e}")
                results[image_path] = {
                    "success": False,
                    "text_content": "",
                    "confidence": 0.0,
                    "model_used": None,
                    "error": str(e),
                }

        return results

    def get_service_status(self) -> Dict[str, Any]:
        """Get status of image content extraction service."""
        status = {
            "service_available": self.service_available,
            "vision_model_available": False,
            "current_vision_model": None,
            "supported_formats": list(self.supported_formats),
            "max_image_size_mb": self.max_image_size_mb,
            "import_source": self.import_source,
            "ollama_base_url": OLLAMA_BASE_URL,
        }

        if self.service_available:
            vision_model = self._get_available_vision_model()
            if vision_model:
                status["vision_model_available"] = True
                status["current_vision_model"] = vision_model

        return status


# Singleton instance for global use
image_extractor = ImageContentExtractor()


def extract_text_from_image(image_path: str) -> Dict[str, Any]:
    """Convenience function for extracting text from a single image."""
    return image_extractor.extract_text_from_image(image_path)


def is_image_file(file_path: str) -> bool:
    """Convenience function to check if file is a supported image."""
    return image_extractor.is_image_file(file_path)


def get_image_service_status() -> Dict[str, Any]:
    """Convenience function to get service status."""
    return image_extractor.get_service_status()
