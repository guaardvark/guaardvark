"""
HTTP Client for GPU Embedding Service
"""

import logging
import time
import requests
from typing import List, Optional, Dict, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class GPUEmbeddingClient:
    """Client for communicating with GPU Embedding Service"""

    def __init__(
        self,
        service_url: str = "http://localhost:5002",
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
    ):
        """
        Initialize GPU Embedding Client.

        Args:
            service_url: Base URL of the GPU embedding service
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_backoff: Backoff multiplier for retries
        """
        self.service_url = service_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        # Create session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=retry_backoff,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        logger.info(f"Initialized GPU Embedding Client for {self.service_url}")

    def health_check(self) -> Dict[str, Any]:
        """
        Check health of the GPU embedding service.

        Returns:
            Health status dictionary
        """
        try:
            response = self.session.get(f"{self.service_url}/health", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Health check failed: {e}")
            return {"status": "unavailable", "error": str(e), "available": False}

    def is_available(self) -> bool:
        """Check if the service is available"""
        health = self.health_check()
        return health.get("status") == "healthy" and health.get("model_loaded", False)

    def generate_embedding(
        self, text: str, model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed
            model: Optional model name (uses service default if not provided)

        Returns:
            Dictionary with embedding and metadata

        Raises:
            requests.exceptions.RequestException: If request fails
        """
        payload = {"text": text}
        if model:
            payload["model"] = model

        try:
            response = self.session.post(
                f"{self.service_url}/embed", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"Embedding request timed out after {self.timeout}s")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Embedding request failed: {e}")
            raise

    def generate_embeddings_batch(
        self, texts: List[str], model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate embeddings for multiple texts (batch processing).

        Args:
            texts: List of texts to embed
            model: Optional model name (uses service default if not provided)

        Returns:
            Dictionary with embeddings list and metadata

        Raises:
            requests.exceptions.RequestException: If request fails
        """
        payload = {"texts": texts}
        if model:
            payload["model"] = model

        try:
            response = self.session.post(
                f"{self.service_url}/embed_batch",
                json=payload,
                timeout=self.timeout * len(texts),  # Increase timeout for batch
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"Batch embedding request timed out")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Batch embedding request failed: {e}")
            raise

    def get_models(self) -> Dict[str, Any]:
        """
        Get information about available models.

        Returns:
            Dictionary with model information
        """
        try:
            response = self.session.get(f"{self.service_url}/models", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to get models: {e}")
            return {"error": str(e)}


def create_client(
    service_url: Optional[str] = None, timeout: Optional[int] = None
) -> GPUEmbeddingClient:
    """
    Factory function to create a GPU Embedding Client.

    Args:
        service_url: Optional service URL (defaults to plugin config)
        timeout: Optional timeout (defaults to plugin config)

    Returns:
        GPUEmbeddingClient instance
    """
    # Load defaults from plugin config if not provided
    if service_url is None or timeout is None:
        try:
            import json
            from pathlib import Path

            plugin_root = Path(__file__).parent.parent
            plugin_config_file = plugin_root / "plugin.json"

            with open(plugin_config_file, "r", encoding="utf-8") as f:
                plugin_config = json.load(f)

            config = plugin_config.get("config", {})

            if service_url is None:
                service_url = config.get("service_url", "http://localhost:5002")
            if timeout is None:
                timeout = config.get("timeout", 30)
        except Exception as e:
            logger.warning(f"Failed to load plugin config: {e}, using defaults")
            if service_url is None:
                service_url = "http://localhost:5002"
            if timeout is None:
                timeout = 30

    return GPUEmbeddingClient(service_url=service_url, timeout=timeout)
