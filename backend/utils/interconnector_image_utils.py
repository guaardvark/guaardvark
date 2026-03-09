# backend/utils/interconnector_image_utils.py
"""
Utility functions for handling image storage with interconnector.
When use_master_image_repository is enabled, client nodes forward images to master.
"""

import logging
import requests
from typing import Optional, Tuple
from flask import current_app

logger = logging.getLogger(__name__)

# Suppress SSL warnings for self-signed certs
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass


def should_use_master_image_repository() -> bool:
    """Check if images should be stored on master server."""
    try:
        from backend.api.interconnector_api import _get_config
        
        config = _get_config()
        if not config.get("is_enabled"):
            return False
        if config.get("node_mode") == "master":
            return False  # Master stores images locally
        return config.get("use_master_image_repository", True)
    except Exception as e:
        logger.error(f"Error checking master image repository config: {e}")
        return False


def get_master_image_url(image_path: str) -> Optional[str]:
    """Get the URL to access an image from the master server."""
    try:
        from backend.api.interconnector_api import _get_config
        
        config = _get_config()
        if not config.get("is_enabled") or config.get("node_mode") == "master":
            return None
        
        master_url = config.get("master_url", "").rstrip("/")
        if not master_url:
            return None
        
        # Construct URL to access image from master
        # image_path is typically relative like "uploads/filename.png" or "outputs/batch_images/..."
        if image_path.startswith("/"):
            image_path = image_path[1:]
        
        # Determine the appropriate API endpoint based on path
        if image_path.startswith("uploads/"):
            return f"{master_url}/api/uploads/{image_path.replace('uploads/', '')}"
        elif "batch_images" in image_path:
            # Extract batch_id and image name from path
            # Path format: outputs/batch_images/batch_xxx/images/filename.png
            parts = image_path.split("/")
            if "batch_images" in parts and "images" in parts:
                batch_idx = parts.index("batch_images")
                images_idx = parts.index("images")
                if batch_idx + 1 < len(parts) and images_idx + 1 < len(parts):
                    batch_id = parts[batch_idx + 1]
                    image_name = parts[images_idx + 1]
                    return f"{master_url}/api/batch-image/image/{batch_id}/{image_name}"
        elif image_path.startswith("outputs/"):
            return f"{master_url}/api/outputs/{image_path.replace('outputs/', '')}"
        
        return None
    except Exception as e:
        logger.error(f"Error getting master image URL: {e}")
        return None


def forward_image_to_master(file_data: bytes, filename: str, endpoint: str = "/api/uploads/") -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Forward an image upload to the master server.
    
    Args:
        file_data: Binary image data
        filename: Name of the file
        endpoint: API endpoint on master server (default: /api/uploads/)
    
    Returns:
        Tuple of (success, file_path_on_master, error_message)
    """
    try:
        from backend.api.interconnector_api import _get_config, _verify_api_key
        
        config = _get_config()
        if not config.get("is_enabled") or config.get("node_mode") == "master":
            return False, None, "Not configured for master forwarding"
        
        master_url = config.get("master_url", "").rstrip("/")
        master_api_key = config.get("master_api_key", "")
        
        if not master_url or not master_api_key:
            return False, None, "Master URL or API key not configured"
        
        # Forward file to master server
        url = f"{master_url}{endpoint}"
        files = {'file': (filename, file_data)}
        headers = {'X-API-Key': master_api_key}
        
        response = requests.post(
            url,
            files=files,
            headers=headers,
            verify=False,  # Allow self-signed certs
            timeout=30
        )
        
        if response.status_code == 200 or response.status_code == 201:
            try:
                result = response.json()
                if result.get("success"):
                    # Extract file path from response
                    data = result.get("data", {})
                    file_path = data.get("path") or data.get("file_path") or filename
                    return True, file_path, None
                else:
                    error_msg = result.get("error", "Unknown error from master")
                    return False, None, error_msg
            except Exception as e:
                logger.error(f"Error parsing master response: {e}")
                return False, None, f"Invalid response from master: {e}"
        else:
            error_msg = f"Master server returned {response.status_code}"
            try:
                error_data = response.json()
                if error_data.get("error"):
                    error_msg = error_data["error"]
            except (ValueError, KeyError):
                pass
            return False, None, error_msg
            
    except Exception as e:
        logger.error(f"Error forwarding image to master: {e}")
        return False, None, str(e)


def proxy_image_from_master(image_path: str) -> Tuple[bool, Optional[bytes], Optional[str]]:
    """
    Proxy an image request from the master server.
    
    Args:
        image_path: Path to the image
        
    Returns:
        Tuple of (success, image_data, error_message)
    """
    try:
        master_url = get_master_image_url(image_path)
        if not master_url:
            return False, None, "Could not construct master URL"
        
        from backend.api.interconnector_api import _get_config
        
        config = _get_config()
        headers = {}
        if config.get("master_api_key"):
            headers['X-API-Key'] = config.get("master_api_key")
        
        response = requests.get(
            master_url,
            headers=headers,
            verify=False,  # Allow self-signed certs
            timeout=30
        )
        
        if response.status_code == 200:
            return True, response.content, None
        else:
            return False, None, f"Master server returned {response.status_code}"
            
    except Exception as e:
        logger.error(f"Error proxying image from master: {e}")
        return False, None, str(e)

