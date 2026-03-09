# backend/api/interconnector_api.py
"""
Network Interconnector API endpoints.
Manages configuration and synchronization between LLM010 instances.
"""

import json
import logging
import secrets
import hashlib
import uuid
import time
import mimetypes
import os
import requests
from celery import group  # pyright: ignore[reportMissingImports]
from backend.celery_app import celery
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

# Suppress SSL warnings for self-signed certs (common in local networks)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from flask import Blueprint, request, current_app, send_file
from typing import Optional, Dict, Any
from backend.models import (
    Setting, db,
    InterconnectorNode, InterconnectorSyncHistory, InterconnectorConflict, InterconnectorPendingChange,
    InterconnectorSyncProfile, InterconnectorBroadcast, InterconnectorBroadcastTarget,
    InterconnectorPendingApproval
)
from backend.services.interconnector_sync_service import get_sync_service
from backend.services.interconnector_file_sync_service import get_file_sync_service
from backend.utils.response_utils import (
    success_response,
    error_response,
    validation_error_response,
    not_found_response,
)

# Try to import psutil for system capabilities detection
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

logger = logging.getLogger(__name__)

interconnector_bp = Blueprint("interconnector", __name__, url_prefix="/api/interconnector")

# Default configuration
DEFAULT_CONFIG = {
    "is_enabled": False,
    "node_mode": "client",
    "node_name": "",
    "master_url": "",
    "master_api_key": "",
    "api_key_hash": "",
    "require_api_key": True,  # Require API key authentication (set to False for trusted local networks)
    "auto_sync_enabled": False,
    "sync_interval_seconds": 300,
    "sync_entities": ["clients", "projects", "websites"],
    "use_master_image_repository": True,  # Use master server for all image storage
    "require_file_approval": True,
}

def _is_production():
    """Detect production-like environments."""
    env = os.getenv("FLASK_ENV") or os.getenv("ENV") or os.getenv("ENVIRONMENT")
    return env and env.lower() in {"prod", "production"}


def _validate_master_url(url: str) -> Optional[str]:
    """Ensure master_url is well-formed and secure when required."""
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned.startswith(("http://", "https://")):
        return "Master URL must start with http:// or https://"
    if _is_production() and cleaned.startswith("http://"):
        return "HTTPS is required for master_url in production"
    return None


def _log_interconnector_alert(message: str, details: Optional[Dict[str, Any]] = None):
    """Lightweight alert hook for interconnector failures."""
    logger.warning(f"[INTERCONNECTOR ALERT] {message} | details={details or {}}")


def _get_config():
    """Get interconnector configuration from database."""
    try:
        setting = db.session.get(Setting, "interconnector_config")
        if setting and setting.value:
            config = json.loads(setting.value)
            # Ensure all default keys exist
            for key, value in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = value
            return config
        return DEFAULT_CONFIG.copy()
    except Exception as e:
        # Suppress expected error during app initialization when context isn't available
        if "application context" not in str(e).lower():
            logger.error(f"Error reading interconnector config: {e}")
        return DEFAULT_CONFIG.copy()


def _save_config(config):
    """Save interconnector configuration to database."""
    try:
        setting = db.session.get(Setting, "interconnector_config")
        if setting:
            setting.value = json.dumps(config)
        else:
            setting = Setting(
                key="interconnector_config",
                value=json.dumps(config),
            )
            db.session.add(setting)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error saving interconnector config: {e}")
        return False


def _generate_api_key():
    """Generate a secure random API key."""
    return secrets.token_urlsafe(32)


def _hash_api_key(api_key):
    """Hash an API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def _verify_api_key(provided_key, stored_hash):
    """Verify an API key against a stored hash."""
    return _hash_api_key(provided_key) == stored_hash


def _check_api_key(config, provided_key=None):
    """
    Check if API key is required and valid.
    
    Args:
        config: Interconnector configuration
        provided_key: API key from request (optional)
    
    Returns:
        Tuple of (is_valid, error_message)
        - (True, None) if API key check passes
        - (False, error_message) if API key check fails
    """
    require_api_key = config.get("require_api_key", True)
    
    if not require_api_key:
        logger.debug("[SYNC] API key check skipped (require_api_key=False)")
        return (True, None)
    
    if not provided_key:
        return (False, "API key is required")
    
    stored_hash = config.get("api_key_hash", "")
    if not stored_hash:
        # API key required but not generated yet - reject connections
        logger.warning("[SYNC] API key required but no key has been generated yet. Generate an API key first.")
        return (False, "API key authentication is enabled but no API key has been generated. The master must generate an API key first.")
    
    if not _verify_api_key(provided_key, stored_hash):
        return (False, "Invalid API key")
    
    return (True, None)


def _should_use_master_image_repository():
    """Check if images should be stored on master server."""
    config = _get_config()
    if not config.get("is_enabled"):
        return False
    if config.get("node_mode") == "master":
        return False  # Master stores images locally
    return config.get("use_master_image_repository", True)


def _get_master_image_url(image_path: str) -> Optional[str]:
    """Get the URL to access an image from the master server."""
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


def _get_outputs_root() -> Path:
    """Resolve the outputs directory using the file sync service root."""
    try:
        project_root = get_file_sync_service().get_project_root()
    except Exception:
        project_root = Path.cwd()
    return project_root / "outputs"


def _load_profile(profile_name: Optional[str] = None, profile_id: Optional[int] = None) -> Optional[InterconnectorSyncProfile]:
    """Fetch a sync profile by name or id."""
    try:
        if profile_id:
            return db.session.get(InterconnectorSyncProfile, profile_id)
        if profile_name:
            return InterconnectorSyncProfile.query.filter(InterconnectorSyncProfile.name == profile_name).first()
        return InterconnectorSyncProfile.query.filter(InterconnectorSyncProfile.is_default.is_(True)).first()
    except Exception as e:
        logger.error(f"[SYNC PROFILE] Error loading profile: {e}", exc_info=True)
        return None


def _seed_default_profiles():
    """Ensure a few default sync profiles exist."""
    existing = {p.name for p in InterconnectorSyncProfile.query.all()}
    defaults = [
        {
            "name": "full",
            "profile_type": "full",
            "description": "Full sync: code, scripts, and system-wide config (rules).",
            "entity_config": {"entities": ["rules"]},
            "file_config": {"paths": ["backend/api/", "backend/services/", "backend/utils/", "backend/routes/", "frontend/src/", "scripts/", "start.sh", "stop.sh", "start_redis.sh", "start_celery.sh"], "include_patterns": ["**/*.py", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.sh"]},
            "is_default": True,
        },
        {
            "name": "code_only",
            "profile_type": "code_only",
            "description": "Only code and scripts, no config data.",
            "entity_config": {"entities": []},
            "file_config": {"paths": ["backend/api/", "backend/services/", "backend/utils/", "frontend/src/", "scripts/", "start.sh", "stop.sh", "start_redis.sh", "start_celery.sh"], "include_patterns": ["**/*.py", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.sh"]},
            "is_default": False,
        },
        {
            "name": "config_only",
            "profile_type": "config_only",
            "description": "System-wide config only (rules), no code files.",
            "entity_config": {"entities": ["rules"]},
            "file_config": {"paths": [], "include_patterns": []},
            "is_default": False,
        },
    ]
    created = []
    for profile in defaults:
        if profile["name"] in existing:
            continue
        rec = InterconnectorSyncProfile(
            name=profile["name"],
            description=profile["description"],
            profile_type=profile["profile_type"],
            entity_config=json.dumps(profile["entity_config"]),
            file_config=json.dumps(profile["file_config"]),
            is_default=profile["is_default"],
        )
        db.session.add(rec)
        created.append(profile["name"])
    if created:
        db.session.commit()
        logger.info(f"[SYNC PROFILE] Seeded default profiles: {created}")


def _list_output_files(sub_path: Optional[str], limit: int = 200, offset: int = 0):
    """List output files (metadata only) for browse-only sharing."""
    outputs_root = _get_outputs_root()
    results = []

    if sub_path:
        base = outputs_root / sub_path
    else:
        base = outputs_root

    if not base.exists():
        return results

    try:
        for idx, path in enumerate(base.rglob("*")):
            if not path.is_file():
                continue
            if idx < offset:
                continue

            rel_path = path.relative_to(outputs_root)
            try:
                stat = path.stat()
                size = stat.st_size
                modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
            except Exception:
                continue

            mime_type, _ = mimetypes.guess_type(str(path))
            # Build download URL using forwarded proto/host if present
            scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
            host = request.headers.get("X-Forwarded-Host", request.host)
            download_url = f"{scheme}://{host}/api/interconnector/outputs/content?path={quote(str(rel_path))}"

            results.append(
                {
                    "path": f"outputs/{rel_path}",
                    "relative_path": str(rel_path),
                    "size": size,
                    "modified_at": modified_at,
                    "mime_type": mime_type or "application/octet-stream",
                    "download_url": download_url,
                }
            )

            if len(results) >= limit:
                break
    except Exception as e:
        logger.error(f"[SYNC OUTPUTS] Failed to list outputs: {e}", exc_info=True)

    return results


@interconnector_bp.route("/config", methods=["GET"])
def get_config():
    """Get current interconnector configuration."""
    try:
        config = _get_config()
        # Don't send the master_api_key in responses for security
        response_config = config.copy()
        if "master_api_key" in response_config and response_config["master_api_key"]:
            response_config["master_api_key"] = "***HIDDEN***"

        return success_response({"config": response_config}, "Configuration retrieved")
    except Exception as e:
        logger.error(f"Error getting interconnector config: {e}")
        return error_response(f"Failed to get configuration: {str(e)}", 500)


@interconnector_bp.route("/config", methods=["POST"])
def update_config():
    """Update interconnector configuration."""
    try:
        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json()
        if not data:
            return validation_error_response("No configuration data provided")

        # Get current config
        config = _get_config()

        # Update with provided values
        for key in DEFAULT_CONFIG.keys():
            if key in data:
                # Special handling for master_api_key - don't update if it's the placeholder
                if key == "master_api_key" and data[key] == "***HIDDEN***":
                    continue
                if key == "require_api_key" and _is_production() and data[key] is False:
                    return validation_error_response("require_api_key cannot be disabled in production")
                config[key] = data[key]

        # Validate required fields when enabled
        if config.get("is_enabled"):
            if not config.get("node_name"):
                return validation_error_response("Node name is required when interconnector is enabled")

            if config.get("node_mode") == "client":
                if not config.get("master_url"):
                    return validation_error_response("Master URL is required in client mode")
                url_error = _validate_master_url(config.get("master_url"))
                if url_error:
                    return validation_error_response(url_error)
                # API key only required if require_api_key is True
                if config.get("require_api_key", True) and not config.get("master_api_key"):
                    return validation_error_response("Master API key is required when API key authentication is enabled")

        # Enforce minimum sync interval (avoid hammering master)
        interval = config.get("sync_interval_seconds")
        if interval and interval < 60:
            config["sync_interval_seconds"] = 60

        # Save configuration
        if not _save_config(config):
            return error_response("Failed to save configuration", 500)

        logger.info(f"Interconnector configuration updated: mode={config.get('node_mode')}, enabled={config.get('is_enabled')}")

        # Return sanitized config
        response_config = config.copy()
        if "master_api_key" in response_config and response_config["master_api_key"]:
            response_config["master_api_key"] = "***HIDDEN***"

        return success_response({"config": response_config}, "Configuration updated successfully")

    except Exception as e:
        logger.error(f"Error updating interconnector config: {e}")
        return error_response(f"Failed to update configuration: {str(e)}", 500)


@interconnector_bp.route("/config/generate-key", methods=["POST"])
def generate_api_key():
    """Generate a new API key for master mode."""
    try:
        config = _get_config()

        if config.get("node_mode") != "master":
            return error_response("API key generation is only available in master mode", 400)

        # Generate new API key
        api_key = _generate_api_key()
        api_key_hash = _hash_api_key(api_key)

        # Update config with hash
        config["api_key_hash"] = api_key_hash

        if not _save_config(config):
            return error_response("Failed to save API key", 500)

        logger.info("New interconnector API key generated")

        return success_response(
            {
                "api_key": api_key,
                "api_key_hash": api_key_hash,
            },
            "API key generated successfully",
        )

    except Exception as e:
        logger.error(f"Error generating API key: {e}")
        return error_response(f"Failed to generate API key: {str(e)}", 500)


@interconnector_bp.route("/status", methods=["GET"])
def get_status():
    """Get current interconnector status."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return success_response(
                {
                    "status": {
                        "is_enabled": False,
                        "node_mode": config.get("node_mode"),
                        "node_name": config.get("node_name"),
                    }
                },
                "Interconnector is disabled",
            )

        # Get last sync time from history
        last_sync_time = None
        if config.get("node_mode") == "client":
            # For clients, get last sync from this node's history
            # (we'll need to track local node_id - for now use node_name as identifier)
            last_sync = db.session.query(InterconnectorSyncHistory).order_by(
                InterconnectorSyncHistory.sync_timestamp.desc()
            ).first()
            if last_sync:
                last_sync_time = last_sync.sync_timestamp.isoformat()
        else:
            # For master, get most recent sync from any node
            last_sync = db.session.query(InterconnectorSyncHistory).order_by(
                InterconnectorSyncHistory.sync_timestamp.desc()
            ).first()
            if last_sync:
                last_sync_time = last_sync.sync_timestamp.isoformat()

        # Determine connection status
        connection_status = "idle"
        if config.get("node_mode") == "client":
            # Check if we can reach master
            master_url = config.get("master_url")
            if master_url:
                try:
                    # Quick check - in production this would be more sophisticated
                    connection_status = "connected"
                except Exception:
                    connection_status = "disconnected"

        # Build status response
        status = {
            "is_enabled": True,
            "node_mode": config.get("node_mode"),
            "node_name": config.get("node_name"),
            "auto_sync_enabled": config.get("auto_sync_enabled"),
            "sync_interval_seconds": config.get("sync_interval_seconds"),
            "sync_entities": config.get("sync_entities"),
            "last_sync_time": last_sync_time,
            "connection_status": connection_status,
        }

        return success_response({"status": status}, "Status retrieved")

    except Exception as e:
        logger.error(f"Error getting interconnector status: {e}")
        return error_response(f"Failed to get status: {str(e)}", 500)


def _get_client_ip():
    """Get client IP address from request."""
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _get_local_network_ip():
    """Get the local network IP address of this device (not 127.0.0.1)."""
    import socket
    try:
        # Create a socket to determine the outbound IP
        # This doesn't actually send data, just determines the route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.1)
            # Connect to a public IP (doesn't actually connect, just determines route)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return ip
    except Exception:
        pass
    
    # Fallback: try to get from hostname
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    
    # Last resort: check all network interfaces
    try:
        import subprocess
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            for ip in ips:
                if ip and not ip.startswith("127.") and ":" not in ip:
                    return ip
    except Exception:
        pass
    
    return "127.0.0.1"


@interconnector_bp.route("/network-info", methods=["GET"])
def get_network_info():
    """Get network information for this node including its LAN IP."""
    import socket
    
    try:
        network_ip = _get_local_network_ip()
        hostname = socket.gethostname()
        
        # Get the port from environment or config
        port = os.environ.get("FLASK_PORT", "5000")
        
        return success_response({
            "network_ip": network_ip,
            "hostname": hostname,
            "port": int(port),
            "full_address": f"{network_ip}:{port}"
        }, "Network info retrieved")
    except Exception as e:
        logger.error(f"Error getting network info: {e}")
        return error_response(f"Failed to get network info: {str(e)}", 500)


def _detect_system_capabilities():
    """Detect system capabilities (CPU, memory, disk, GPU)."""
    capabilities = {
        "cpu_cores": 1,
        "memory_mb": 1024,
        "disk_space_gb": 10,
        "gpu_available": False,
    }
    
    if PSUTIL_AVAILABLE:
        try:
            capabilities["cpu_cores"] = psutil.cpu_count(logical=True) or 1
            memory = psutil.virtual_memory()
            capabilities["memory_mb"] = int(memory.total / (1024 * 1024))
            disk = psutil.disk_usage('/')
            capabilities["disk_space_gb"] = int(disk.free / (1024 * 1024 * 1024))
        except Exception as e:
            logger.warning(f"Failed to detect system capabilities: {e}")
    
    # Check for GPU availability
    try:
        import subprocess
        result = subprocess.run(['which', 'nvidia-smi'], capture_output=True, timeout=1)
        capabilities["gpu_available"] = result.returncode == 0
    except Exception:
        capabilities["gpu_available"] = False
    
    return capabilities


@interconnector_bp.route("/debug/nodes", methods=["GET"])
def debug_nodes():
    """Debug endpoint to inspect all nodes in database (master mode only)."""
    try:
        logger.info("[SYNC DEBUG] Debug nodes request received")
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Debug endpoint is only available in master mode", 400)

        # Get ALL nodes regardless of status
        all_nodes = db.session.query(InterconnectorNode).all()
        
        debug_info = {
            "total_nodes": len(all_nodes),
            "nodes": [],
            "by_status": {},
            "by_node_id": {}
        }
        
        for node in all_nodes:
            node_dict = {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "host": node.host,
                "port": node.port,
                "status": node.status,
                "node_mode": node.node_mode,
                "last_heartbeat": node.last_heartbeat.isoformat() if node.last_heartbeat else None,
                "registered_at": node.registered_at.isoformat() if node.registered_at else None,
                "time_since_heartbeat": None,
            }
            
            if node.last_heartbeat:
                delta = datetime.now() - node.last_heartbeat
                node_dict["time_since_heartbeat"] = f"{delta.total_seconds():.0f} seconds"
            
            debug_info["nodes"].append(node_dict)
            
            # Count by status
            status = node.status or "unknown"
            debug_info["by_status"][status] = debug_info["by_status"].get(status, 0) + 1
            
            # Index by node_id
            debug_info["by_node_id"][node.node_id] = node_dict
        
        logger.info(f"[SYNC DEBUG] Debug info: {len(all_nodes)} total nodes, "
                   f"status breakdown: {debug_info['by_status']}")
        
        return success_response(debug_info, "Debug information retrieved")
        
    except Exception as e:
        logger.error(f"[SYNC DEBUG] Error in debug endpoint: {e}", exc_info=True)
        return error_response(f"Debug failed: {str(e)}", 500)


@interconnector_bp.route("/nodes/register", methods=["POST"])
def register_node():
    """Register a client node with the master."""
    try:
        logger.info("[SYNC] Node registration request received")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] Node registration rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            logger.warning(f"[SYNC] Node registration rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("Node registration is only available in master mode", 400)

        if not request.is_json:
            logger.warning("[SYNC] Node registration rejected: Request not JSON")
            return error_response("Request must be JSON", 400)

        # Verify API key for security (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC] Node registration failed: {error_msg} from {request.remote_addr}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        data = request.get_json()
        node_name = data.get("node_name")
        node_mode = data.get("node_mode", "client")
        capabilities = data.get("capabilities", {})
        sync_entities = data.get("sync_entities", [])

        logger.info(f"[SYNC] Registration data: node_name={node_name}, node_mode={node_mode}, "
                   f"provided_node_id={data.get('node_id')}")

        if not node_name:
            logger.error("[SYNC] Node registration rejected: Node name missing")
            return validation_error_response("Node name is required")

        # Get client IP and port
        client_ip = _get_client_ip()
        logger.info(f"[SYNC] Detected client IP: {client_ip}")
        logger.info(f"[SYNC] Request headers - Host: {request.headers.get('Host')}, "
                   f"X-Forwarded-For: {request.headers.get('X-Forwarded-For')}, "
                   f"Remote-Addr: {request.remote_addr}")
        
        # Allow client to provide their own IP if they know it better
        # (useful for NAT/firewall scenarios)
        provided_ip = data.get("client_ip")
        provided_port = data.get("client_port")
        
        if provided_ip:
            logger.info(f"[SYNC] Using client-provided IP: {provided_ip}")
            client_ip = provided_ip
        
        # Try to extract port from Host header or use default
        host_header = request.headers.get('Host', '')
        port = 5000  # Default
        if provided_port:
            port = provided_port
            logger.info(f"[SYNC] Using client-provided port: {port}")
        elif ':' in host_header:
            try:
                port = int(host_header.split(':')[1])
                logger.info(f"[SYNC] Extracted port from Host header: {port}")
            except ValueError:
                logger.warning(f"[SYNC] Could not parse port from Host header: {host_header}")
        else:
            logger.info(f"[SYNC] Using default port: {port}")

        # Generate node ID (use provided or generate new)
        node_id = data.get("node_id") or str(uuid.uuid4())
        logger.info(f"[SYNC] Using node_id: {node_id}")

        # Check if node already exists
        existing_node = db.session.get(InterconnectorNode, node_id)
        if existing_node:
            # Update existing node
            logger.info(f"[SYNC] Updating existing node: {node_id}")
            existing_node.node_name = node_name
            existing_node.host = client_ip
            existing_node.port = port
            existing_node.node_mode = node_mode
            existing_node.status = "active"
            existing_node.last_heartbeat = datetime.now()
            existing_node.capabilities = json.dumps(capabilities)
            existing_node.sync_entities = json.dumps(sync_entities)
            db.session.commit()
            logger.info(f"[SYNC] Updated existing node registration: {node_id} ({node_name}) from {client_ip}:{port}")
        else:
            # Create new node
            logger.info(f"[SYNC] Creating new node: {node_id}")
            new_node = InterconnectorNode(
                node_id=node_id,
                node_name=node_name,
                host=client_ip,
                port=port,
                node_mode=node_mode,
                status="active",
                last_heartbeat=datetime.now(),
                capabilities=json.dumps(capabilities),
                sync_entities=json.dumps(sync_entities),
                registered_at=datetime.now(),
            )
            db.session.add(new_node)
            db.session.commit()
            logger.info(f"[SYNC] Registered new node: {node_id} ({node_name}) from {client_ip}:{port}")
            
            # Verify node was saved
            verify_node = db.session.get(InterconnectorNode, node_id)
            if verify_node:
                logger.info(f"[SYNC] Node verified in database: {verify_node.node_name}, status={verify_node.status}")
            else:
                logger.error(f"[SYNC] CRITICAL: Node {node_id} was not saved to database!")

        # Return node ID
        return success_response(
            {"node_id": node_id, "registered_at": datetime.now().isoformat()},
            "Node registered successfully"
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"[SYNC] Error registering node: {e}", exc_info=True)
        return error_response(f"Failed to register node: {str(e)}", 500)


@interconnector_bp.route("/nodes/<node_id>/heartbeat", methods=["GET", "POST"])
def node_heartbeat(node_id):
    """Update heartbeat for a registered node."""
    try:
        logger.debug(f"[SYNC] Heartbeat received for node: {node_id}")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] Heartbeat rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            logger.warning(f"[SYNC] Heartbeat rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("Heartbeat is only available in master mode", 400)

        # Verify API key (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC] Heartbeat rejected: {error_msg} for node {node_id}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        node = db.session.get(InterconnectorNode, node_id)
        if not node:
            logger.warning(f"[SYNC] Heartbeat failed: Node {node_id} not found")
            return error_response(f"Node {node_id} not found", 404)

        # Update heartbeat
        node.last_heartbeat = datetime.now()
        node.status = "active"
        
        # Update capabilities if provided
        if request.is_json:
            data = request.get_json()
            if "capabilities" in data:
                node.capabilities = json.dumps(data["capabilities"])
            if "sync_entities" in data:
                node.sync_entities = json.dumps(data["sync_entities"])

        db.session.commit()
        logger.debug(f"[SYNC] Heartbeat updated for node {node_id} ({node.node_name})")

        return success_response(
            {"node_id": node_id, "heartbeat_at": node.last_heartbeat.isoformat()},
            "Heartbeat updated"
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"[SYNC] Error updating heartbeat for node {node_id}: {e}", exc_info=True)
        return error_response(f"Failed to update heartbeat: {str(e)}", 500)


@interconnector_bp.route("/nodes", methods=["GET"])
def get_nodes():
    """Get list of connected client nodes (master mode only)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Node listing is only available in master mode", 400)

        # Clean up stale nodes (no heartbeat in 5+ minutes)
        cutoff_time = datetime.now() - timedelta(minutes=5)
        stale_nodes = db.session.query(InterconnectorNode).filter(
            InterconnectorNode.last_heartbeat < cutoff_time,
            InterconnectorNode.status == "active"
        ).all()
        
        logger.info(f"[SYNC] Found {len(stale_nodes)} stale nodes (heartbeat older than 5 minutes)")
        for node in stale_nodes:
            logger.info(f"[SYNC] Marking node {node.node_id} ({node.node_name}) as disconnected - "
                        f"last heartbeat: {node.last_heartbeat}")
            node.status = "disconnected"
        
        db.session.commit()

        # Get all active/inactive nodes
        nodes = db.session.query(InterconnectorNode).filter(
            InterconnectorNode.status.in_(["active", "inactive"])
        ).all()

        logger.info(f"[SYNC] Returning {len(nodes)} active/inactive nodes")
        for node in nodes:
            logger.info(f"[SYNC] Active node: {node.node_name} ({node.node_id}), "
                        f"status={node.status}, heartbeat={node.last_heartbeat}")

        nodes_data = [node.to_dict() for node in nodes]

        return success_response({"nodes": nodes_data}, "Nodes retrieved")

    except Exception as e:
        logger.error(f"Error getting interconnector nodes: {e}")
        return error_response(f"Failed to get nodes: {str(e)}", 500)


@interconnector_bp.route("/sync/history", methods=["GET"])
def get_sync_history():
    """Get sync history for this node (client mode only)."""
    try:
        logger.info("[SYNC] Sync history request received")
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "client":
            return error_response("Sync history endpoint is only available in client mode", 400)

        # Get last N sync history records
        limit = int(request.args.get("limit", 10))
        
        local_node_id = config.get("node_name", "local_node")
        
        sync_history_records = db.session.query(InterconnectorSyncHistory).filter(
            InterconnectorSyncHistory.node_id == local_node_id
        ).order_by(
            InterconnectorSyncHistory.sync_timestamp.desc()
        ).limit(limit).all()

        history_data = [record.to_dict() for record in sync_history_records]
        
        # Get latest sync info
        latest_sync = sync_history_records[0] if sync_history_records else None
        
        logger.info(f"[SYNC] Returning {len(history_data)} sync history records")
        
        return success_response(
            {
                "history": history_data,
                "latest_sync": latest_sync.to_dict() if latest_sync else None,
                "total_records": len(history_data),
            },
            "Sync history retrieved"
        )

    except Exception as e:
        logger.error(f"[SYNC] Error getting sync history: {e}", exc_info=True)
        return error_response(f"Failed to get sync history: {str(e)}", 500)


@interconnector_bp.route("/nodes/test-all", methods=["POST"])
def test_all_client_connections():
    """Test connections to all client nodes and fetch their sync history (master mode only)."""
    try:
        logger.info("[SYNC] Testing all client connections and fetching sync history")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] Client connections test rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            logger.warning(f"[SYNC] Client connections test rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("Client connections test is only available on master nodes", 400)

        # Get all active nodes
        nodes = db.session.query(InterconnectorNode).filter(
            InterconnectorNode.status.in_(["active", "inactive"])
        ).all()

        if not nodes:
            return success_response(
                {
                    "nodes_tested": 0,
                    "results": [],
                    "summary": {
                        "successful": 0,
                        "failed": 0,
                        "with_sync_history": 0,
                    }
                },
                "No client nodes found"
            )

        results = []
        summary = {
            "successful": 0,
            "failed": 0,
            "with_sync_history": 0,
        }

        for node in nodes:
            node_result = {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "host": node.host,
                "port": node.port,
                "status": node.status,
                "connection_status": "unknown",
                "latency_ms": None,
                "sync_history": None,
                "latest_sync": None,
                "error": None,
            }

            # Construct client URL
            client_host = node.host
            client_port = node.port
            client_url = f"http://{client_host}:{client_port}"
            if client_host.startswith(('http://', 'https://')):
                client_url = client_host
                if ':' not in client_host.split('://')[1]:
                    client_url = f"{client_url}:{client_port}"

            try:
                # Test connection and get sync history
                sync_history_url = f"{client_url}/api/interconnector/sync/history"
                
                logger.info(f"[SYNC] Testing connection to client {node.node_name} ({node.node_id}) at {sync_history_url}")
                
                start_time = time.time()
                response = requests.get(
                    sync_history_url,
                    timeout=10,
                    verify=False
                )
                latency_ms = int((time.time() - start_time) * 1000)
                
                node_result["latency_ms"] = latency_ms
                
                if response.status_code == 200:
                    sync_data = response.json()
                    history_data = sync_data.get("data", {})
                    
                    node_result["connection_status"] = "success"
                    node_result["sync_history"] = history_data.get("history", [])
                    node_result["latest_sync"] = history_data.get("latest_sync")
                    
                    if node_result["latest_sync"]:
                        summary["with_sync_history"] += 1
                        logger.info(f"[SYNC] Client {node.node_name} has sync history: last sync at {node_result['latest_sync'].get('sync_timestamp')}")
                    
                    summary["successful"] += 1
                else:
                    node_result["connection_status"] = "error"
                    node_result["error"] = f"HTTP {response.status_code}"
                    summary["failed"] += 1
                    logger.warning(f"[SYNC] Client {node.node_name} returned HTTP {response.status_code}")

            except requests.exceptions.Timeout:
                node_result["connection_status"] = "timeout"
                node_result["error"] = "Connection timeout"
                summary["failed"] += 1
                logger.error(f"[SYNC] Client {node.node_name} connection timeout")
            except requests.exceptions.ConnectionError as e:
                node_result["connection_status"] = "failed"
                node_result["error"] = str(e)
                summary["failed"] += 1
                logger.error(f"[SYNC] Client {node.node_name} connection failed: {e}")
            except Exception as e:
                node_result["connection_status"] = "error"
                node_result["error"] = str(e)
                summary["failed"] += 1
                logger.error(f"[SYNC] Error testing client {node.node_name}: {e}", exc_info=True)

            results.append(node_result)

        logger.info(f"[SYNC] Client connections test complete: {summary['successful']} successful, "
                   f"{summary['failed']} failed, {summary['with_sync_history']} with sync history")

        return success_response(
            {
                "nodes_tested": len(nodes),
                "results": results,
                "summary": summary,
                "timestamp": datetime.now().isoformat(),
            },
            f"Tested {len(nodes)} client nodes"
        )

    except Exception as e:
        logger.error(f"[SYNC] Error testing all client connections: {e}", exc_info=True)
        return error_response(f"Failed to test client connections: {str(e)}", 500)


@interconnector_bp.route("/nodes/<node_id>/test", methods=["POST"])
def test_client_connection(node_id):
    """Test connection to a specific client node (master mode only)."""
    try:
        logger.info(f"[SYNC] Testing connection to client node: {node_id}")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] Client connection test rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            logger.warning(f"[SYNC] Client connection test rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("Client connection test is only available on master nodes", 400)

        # Get node information
        node = db.session.get(InterconnectorNode, node_id)
        if not node:
            logger.warning(f"[SYNC] Client node not found: {node_id}")
            return error_response(f"Node {node_id} not found", 404)

        # Construct client URL
        client_host = node.host
        client_port = node.port
        
        # Try to determine protocol (http by default)
        client_url = f"http://{client_host}:{client_port}"
        if client_host.startswith(('http://', 'https://')):
            client_url = client_host
            if ':' not in client_host.split('://')[1]:
                client_url = f"{client_url}:{client_port}"
        
        # Test connection by calling client's status endpoint
        test_url = f"{client_url}/api/interconnector/status"
        
        logger.info(f"[SYNC] Testing connection to client: {test_url}")
        
        try:
            start_time = time.time()
            response = requests.get(
                test_url,
                timeout=10,
                verify=False  # Allow self-signed certs
            )
            latency_ms = int((time.time() - start_time) * 1000)
            
            if response.status_code == 200:
                client_status = response.json()
                logger.info(f"[SYNC] Successfully connected to client {node_id}: {latency_ms}ms latency")
                
                return success_response(
                    {
                        "node_id": node_id,
                        "node_name": node.node_name,
                        "client_url": client_url,
                        "connection_status": "success",
                        "latency_ms": latency_ms,
                        "http_status": response.status_code,
                        "client_status": client_status.get("data", {}).get("status", {}),
                    },
                    f"Connection test successful: {latency_ms}ms latency"
                )
            else:
                logger.warning(f"[SYNC] Client connection test returned HTTP {response.status_code}")
                return success_response(
                    {
                        "node_id": node_id,
                        "node_name": node.node_name,
                        "client_url": client_url,
                        "connection_status": "partial",
                        "latency_ms": latency_ms,
                        "http_status": response.status_code,
                        "error": f"HTTP {response.status_code}",
                    },
                    f"Connection test completed with HTTP {response.status_code}"
                )
        
        except requests.exceptions.Timeout:
            logger.error(f"[SYNC] Client connection test timeout: {node_id}")
            return success_response(
                {
                    "node_id": node_id,
                    "node_name": node.node_name,
                    "client_url": client_url,
                    "connection_status": "timeout",
                    "error": "Connection timeout",
                },
                "Connection test failed: Timeout"
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[SYNC] Client connection test failed: {e}")
            return success_response(
                {
                    "node_id": node_id,
                    "node_name": node.node_name,
                    "client_url": client_url,
                    "connection_status": "failed",
                    "error": str(e),
                },
                f"Connection test failed: Unable to reach client"
            )
        except Exception as e:
            logger.error(f"[SYNC] Error testing client connection: {e}", exc_info=True)
            return error_response(f"Connection test failed: {str(e)}", 500)

    except Exception as e:
        logger.error(f"[SYNC] Error in client connection test: {e}", exc_info=True)
        return error_response(f"Connection test failed: {str(e)}", 500)


@interconnector_bp.route("/nodes/<node_id>", methods=["DELETE"])
def disconnect_node(node_id):
    """Disconnect a client node (master mode only)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Node disconnection is only available in master mode", 400)

        node = db.session.get(InterconnectorNode, node_id)
        if not node:
            return error_response(f"Node {node_id} not found", 404)

        # Mark as disconnected
        node.status = "disconnected"
        db.session.commit()

        logger.info(f"Node {node_id} disconnected successfully")

        return success_response(
            {"node_id": node_id},
            f"Node {node_id} disconnected successfully",
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error disconnecting node {node_id}: {e}")
        return error_response(f"Failed to disconnect node: {str(e)}", 500)


@interconnector_bp.route("/sync/profiles", methods=["GET"])
def get_sync_profiles():
    """List available sync profiles."""
    try:
        _seed_default_profiles()
        profiles = InterconnectorSyncProfile.query.order_by(InterconnectorSyncProfile.is_default.desc(), InterconnectorSyncProfile.name.asc()).all()
        return success_response({"profiles": [p.to_dict() for p in profiles]}, "Sync profiles retrieved")
    except Exception as e:
        logger.error(f"[SYNC PROFILE] Error fetching profiles: {e}", exc_info=True)
        return error_response(f"Failed to get profiles: {str(e)}", 500)


@interconnector_bp.route("/sync/pull", methods=["GET"])
def pull_entities():
    """Pull entities from master (for client nodes to call)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Pull endpoint is only available on master nodes", 400)

        # Verify API key (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC] Pull entities rejected: {error_msg}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        # Get requested entity types
        entity_types = request.args.getlist("entities")
        if not entity_types:
            entity_types = config.get("sync_entities", ["clients", "projects"])

        # Get since timestamp if provided (for incremental sync)
        since_str = request.args.get("since")
        since = None
        if since_str:
            try:
                since = datetime.fromisoformat(since_str.replace('Z', '+00:00'))
            except Exception:
                pass

        sync_service = get_sync_service()
        all_entities = {}

        for entity_type in entity_types:
            entities = sync_service.get_entities_for_sync(entity_type, since)
            all_entities[entity_type] = entities

        return success_response(
            {
                "entities": all_entities,
                "entity_types": entity_types,
                "since": since.isoformat() if since else None,
                "timestamp": datetime.now().isoformat(),
            },
            "Entities retrieved for sync"
        )

    except Exception as e:
        logger.error(f"Error pulling entities: {e}")
        return error_response(f"Failed to pull entities: {str(e)}", 500)


@interconnector_bp.route("/sync/push", methods=["POST"])
def push_entities():
    """Push entities to master (for client nodes to call)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Push endpoint is only available on master nodes", 400)

        # Verify API key (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC] Pull entities rejected: {error_msg}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json()
        entities_data = data.get("entities", {})
        node_id = data.get("node_id")  # ID of the client node pushing data
        conflict_strategy = data.get("conflict_strategy", "last_write_wins")

        if not entities_data:
            return validation_error_response("No entities provided")

        sync_service = get_sync_service()
        summary = {
            "total_processed": 0,
            "total_created": 0,
            "total_updated": 0,
            "total_conflicts": 0,
            "total_errors": 0,
        }
        details = {}

        # Process each entity type
        for entity_type, entity_list in entities_data.items():
            if entity_type not in sync_service.supported_entities:
                continue

            entity_stats = {"processed": 0, "created": 0, "updated": 0, "conflicts": 0, "errors": 0}

            for entity_data in entity_list:
                try:
                    success, conflict_id, stats = sync_service.apply_entity(
                        entity_type, entity_data, conflict_strategy, node_id
                    )

                    entity_stats["processed"] += 1
                    summary["total_processed"] += 1

                    if stats.get("created"):
                        entity_stats["created"] += 1
                        summary["total_created"] += 1
                    elif stats.get("updated"):
                        entity_stats["updated"] += 1
                        summary["total_updated"] += 1
                    elif stats.get("skipped"):
                        pass  # Skipped but still processed

                    if conflict_id:
                        entity_stats["conflicts"] += 1
                        summary["total_conflicts"] += 1

                except Exception as e:
                    logger.error(f"Error applying {entity_type} entity: {e}")
                    entity_stats["errors"] += 1
                    summary["total_errors"] += 1

            details[entity_type] = entity_stats

        # Commit all changes
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error committing sync changes: {e}")
            return error_response(f"Failed to commit sync changes: {str(e)}", 500)

        return success_response(
            {
                "summary": summary,
                "details": details,
                "timestamp": datetime.now().isoformat(),
            },
            "Entities applied successfully"
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error pushing entities: {e}")
        return error_response(f"Failed to push entities: {str(e)}", 500)


@interconnector_bp.route("/approvals/pending", methods=["GET"])
def get_pending_approvals():
    """Get list of pending approval requests."""
    try:
        approvals = InterconnectorPendingApproval.query.filter_by(status="pending").order_by(
            InterconnectorPendingApproval.received_at.desc()
        ).all()
        return success_response(
            {"pending_count": len(approvals), "approvals": [a.to_dict() for a in approvals]},
            "Pending approvals retrieved",
        )
    except Exception as e:
        logger.error(f"[APPROVAL] Error fetching pending approvals: {e}", exc_info=True)
        return error_response(f"Failed to get pending approvals: {str(e)}", 500)


@interconnector_bp.route("/approvals/<int:approval_id>/decide", methods=["POST"])
def decide_approval(approval_id: int):
    """Approve or decline a pending approval."""
    try:
        approval = db.session.get(InterconnectorPendingApproval, approval_id)
        if not approval:
            return not_found_response(f"Approval {approval_id} not found")

        if approval.status != "pending":
            return validation_error_response("Approval already processed")

        data = request.get_json() or {}
        decision = data.get("decision")
        if decision not in {"approve", "decline", "partial"}:
            return validation_error_response("decision must be approve|decline|partial")

        approved_files = set(data.get("approved_files") or [])
        file_sync_service = get_file_sync_service()

        files = approval.files_data or "[]"
        try:
            files_list = json.loads(files)
        except Exception:
            files_list = []

        # Determine files to apply
        files_to_apply = files_list
        if decision == "partial" and approved_files:
            files_to_apply = [f for f in files_list if f.get("path") in approved_files]

        apply_results = {"processed": 0, "created": 0, "updated": 0, "conflicts": 0, "errors": 0}
        details = []

        if decision in {"approve", "partial"} and files_to_apply:
            success, apply_result = file_sync_service.apply_files_atomic(
                files_to_apply, "last_write_wins", create_backup=True
            )
            asum = apply_result.get("summary", {})
            apply_results["processed"] = asum.get("total_processed", 0)
            apply_results["created"] = asum.get("total_created", 0)
            apply_results["updated"] = asum.get("total_updated", 0)
            apply_results["conflicts"] = asum.get("total_conflicts", 0)
            apply_results["errors"] = asum.get("total_errors", 0)
            details = [
                {"path": d.get("path"), "status": d.get("status", "success"), "error": d.get("error")}
                for d in apply_result.get("details", [])
            ]

        approval.status = "approved" if decision in {"approve", "partial"} else "declined"
        approval.reviewed_at = datetime.now()
        approval.approved_files = json.dumps(list(approved_files)) if approved_files else None
        approval.decision_reason = data.get("reason")
        db.session.commit()

        return success_response(
            {
                "approval_id": approval_id,
                "status": approval.status,
                "apply_results": apply_results,
                "details": details,
            },
            f"Approval {decision}d",
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"[APPROVAL] Error deciding approval {approval_id}: {e}", exc_info=True)
        return error_response(f"Failed to process approval: {str(e)}", 500)


def _build_client_url(node: InterconnectorNode) -> str:
    """Construct base URL for a client node."""
    return f"http://{node.host}:{node.port}"


@celery.task(bind=True, name="interconnector.broadcast.push_to_client", max_retries=2)
def broadcast_push_to_client(self, broadcast_id: str, node_id: str, payload: Dict):
    """Trigger a client to pull from master by invoking its manual sync."""
    try:
        target = InterconnectorBroadcastTarget.query.filter_by(broadcast_id=broadcast_id, node_id=node_id).first()
        node = db.session.get(InterconnectorNode, node_id)
        if not node or not target:
            return {"status": "error", "error": "Node or target not found"}

        client_url = f"{_build_client_url(node)}/api/interconnector/sync/manual"
        target.status = "in_progress"
        target.started_at = datetime.now()
        db.session.commit()

        resp = requests.post(client_url, json=payload, timeout=120, verify=False)
        if resp.status_code == 200:
            target.status = "success"
            target.completed_at = datetime.now()
        else:
            target.status = "failed"
            target.error_message = f"HTTP {resp.status_code}"
            _log_interconnector_alert(
                "Broadcast push to client failed",
                {"url": client_url, "status": resp.status_code, "node_id": node_id},
            )
        db.session.commit()
        return {"status": target.status, "node_id": node_id}
    except Exception as e:
        db.session.rollback()
        try:
            target = InterconnectorBroadcastTarget.query.filter_by(broadcast_id=broadcast_id, node_id=node_id).first()
            if target:
                target.status = "failed"
                target.error_message = str(e)
                db.session.commit()
        except Exception:
            pass
        raise self.retry(exc=e, countdown=30 * (self.request.retries + 1))


@interconnector_bp.route("/broadcast/push", methods=["POST"])
def broadcast_push():
    """Master initiates push (client pull) to multiple clients."""
    try:
        config = _get_config()
        if not config.get("is_enabled") or config.get("node_mode") != "master":
            return error_response("Broadcast is only available in master mode with interconnector enabled", 400)

        data = request.get_json() or {}
        target_clients = data.get("target_clients")  # list or "all"
        sync_type = data.get("sync_type", "entities")
        entities = data.get("entities", config.get("sync_entities", []))
        file_paths = data.get("file_paths")
        require_approval = data.get("require_approval", True)
        priority = data.get("priority", "normal")
        profile_name = data.get("profile") or data.get("profile_name")
        profile_id = data.get("profile_id")

        profile = _load_profile(profile_name, profile_id)
        include_patterns = data.get("include_patterns")
        exclude_patterns = data.get("exclude_patterns")
        if profile:
            try:
                entity_cfg = json.loads(profile.entity_config) if profile.entity_config else {}
                file_cfg = json.loads(profile.file_config) if profile.file_config else {}
                if entity_cfg.get("entities") is not None:
                    entities = entity_cfg.get("entities") or []
                if file_cfg.get("paths"):
                    file_paths = file_cfg.get("paths")
                include_patterns = include_patterns or file_cfg.get("include_patterns")
                exclude_patterns = exclude_patterns or file_cfg.get("exclude_patterns")
            except Exception as e:
                logger.error(f"[BROADCAST] Failed to apply profile {profile.name}: {e}", exc_info=True)

        # Select targets
        query = InterconnectorNode.query.filter(InterconnectorNode.node_mode == "client", InterconnectorNode.status == "active")
        if target_clients and target_clients != "all":
            query = query.filter(InterconnectorNode.node_id.in_(target_clients))
        nodes = query.all()
        if not nodes:
            return validation_error_response("No target clients found for broadcast")

        broadcast = InterconnectorBroadcast(
            id=str(uuid.uuid4()),
            sync_type=sync_type,
            entities=json.dumps(entities or []),
            file_paths=json.dumps(file_paths or []),
            require_approval=require_approval,
            priority=priority,
            status="pending",
            total_clients=len(nodes),
            pending_count=len(nodes),
        )
        db.session.add(broadcast)
        db.session.commit()

        # Create targets
        for node in nodes:
            tgt = InterconnectorBroadcastTarget(
                broadcast_id=broadcast.id,
                node_id=node.node_id,
                status="pending",
            )
            db.session.add(tgt)
        db.session.commit()

        # Build payload for clients (they will pull from master)
        payload = {
            "direction": "pull" if sync_type in {"entities", "both"} else "bidirectional",
            "entities": entities,
            "sync_files": sync_type in {"files", "both"},
            "file_paths": file_paths,
            "require_file_approval": require_approval,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
        }

        tasks = []
        for node in nodes:
            tasks.append(broadcast_push_to_client.s(broadcast.id, node.node_id, payload))

        if tasks:
            group(tasks).apply_async()
            broadcast.status = "in_progress"
            db.session.commit()

        return success_response({"broadcast_id": broadcast.id, "targets": [n.node_id for n in nodes]}, "Broadcast initiated")
    except Exception as e:
        db.session.rollback()
        logger.error(f"[BROADCAST] Error initiating broadcast: {e}", exc_info=True)
        return error_response(f"Failed to start broadcast: {str(e)}", 500)


@interconnector_bp.route("/broadcast/status/<broadcast_id>", methods=["GET"])
def get_broadcast_status(broadcast_id: str):
    """Get status of a broadcast operation."""
    try:
        broadcast = db.session.get(InterconnectorBroadcast, broadcast_id)
        if not broadcast:
            return not_found_response("Broadcast not found")

        targets = InterconnectorBroadcastTarget.query.filter_by(broadcast_id=broadcast_id).all()
        return success_response(
            {
                "broadcast": broadcast.to_dict(),
                "targets": [t.to_dict() for t in targets],
            },
            "Broadcast status",
        )
    except Exception as e:
        logger.error(f"[BROADCAST] Error getting status for {broadcast_id}: {e}", exc_info=True)
        return error_response(f"Failed to get broadcast status: {str(e)}", 500)


@interconnector_bp.route("/sync/files/test", methods=["GET"])
def test_file_scanning():
    """Test file scanning on server (master mode only, no API key required for local testing)."""
    try:
        logger.info("[SYNC] File scanning test request received")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] File scan test rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            logger.warning(f"[SYNC] File scan test rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("File scan test is only available on master nodes", 400)

        file_sync_service = get_file_sync_service()
        logger.info("[SYNC] File scan test: Starting file scan...")
        
        # Test scanning with default paths
        files_list = file_sync_service.scan_files(None, None)
        
        # Calculate statistics
        total_size = sum(f.get("size", 0) for f in files_list)
        total_files = len(files_list)
        
        # Group by directory
        by_directory = {}
        for file_data in files_list:
            path = file_data.get("path", "")
            dir_path = str(Path(path).parent) if path else "unknown"
            if dir_path not in by_directory:
                by_directory[dir_path] = {"count": 0, "size": 0, "files": []}
            by_directory[dir_path]["count"] += 1
            by_directory[dir_path]["size"] += file_data.get("size", 0)
            # Keep first 3 files per directory as examples
            if len(by_directory[dir_path]["files"]) < 3:
                by_directory[dir_path]["files"].append(path)
        
        # Get sample files from key directories (not just first 10)
        sample_files = []
        key_dirs = ["backend/api", "backend/services", "backend/utils", "frontend/src"]
        for key_dir in key_dirs:
            matching_files = [f for f in files_list if f.get("path", "").startswith(key_dir)]
            if matching_files:
                sample_files.extend(matching_files[:2])  # 2 files per key directory
        
        # Fill remaining slots with any files if we don't have enough
        if len(sample_files) < 10:
            remaining = [f for f in files_list if f not in sample_files]
            sample_files.extend(remaining[:10 - len(sample_files)])
        
        # Verify critical files are present
        critical_files = [
            "backend/api/interconnector_api.py",
            "backend/services/interconnector_file_sync_service.py",
            "backend/models.py",
        ]
        found_critical = []
        missing_critical = []
        for critical_file in critical_files:
            if any(f.get("path") == critical_file for f in files_list):
                found_critical.append(critical_file)
            else:
                missing_critical.append(critical_file)
        
        logger.info(f"[SYNC] File scan test complete: {total_files} files, "
                   f"{total_size / 1024 / 1024:.2f} MB total")
        logger.info(f"[SYNC] Critical files check: {len(found_critical)} found, {len(missing_critical)} missing")
        
        return success_response(
            {
                "success": True,
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "by_directory": by_directory,
                "sample_files": sample_files[:10],  # Strategic sample files
                "critical_files": {
                    "found": found_critical,
                    "missing": missing_critical,
                },
                "sync_paths": file_sync_service.default_sync_paths,
                "project_root": str(file_sync_service.get_project_root()),
            },
            f"File scan test successful: Found {total_files} files"
        )

    except Exception as e:
        logger.error(f"[SYNC] Error in file scan test: {e}", exc_info=True)
        return error_response(f"File scan test failed: {str(e)}", 500)


@interconnector_bp.route("/sync/files/verify", methods=["POST"])
def verify_files():
    """Verify file integrity after manual copy (for debugging)."""
    try:
        logger.info("[SYNC] File verification request received")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] File verification rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json() or {}
        file_checks = data.get("files", [])
        
        if not file_checks:
            # If no files specified, verify all sync paths
            logger.info("[SYNC] No files specified, verifying all sync paths")
            file_sync_service = get_file_sync_service()
            files_list = file_sync_service.scan_files(None, None)
            
            # Convert to verification format
            file_checks = [
                {"path": f["path"], "hash": f.get("hash"), "size": f.get("size")}
                for f in files_list
            ]
            logger.info(f"[SYNC] Verifying {len(file_checks)} files from sync paths")

        file_sync_service = get_file_sync_service()
        verification_results = file_sync_service.verify_files_batch(file_checks)
        
        logger.info(f"[SYNC] Verification complete: {verification_results['matches']} matches, "
                   f"{verification_results['mismatches']} mismatches, "
                   f"{verification_results['missing']} missing")

        return success_response(
            verification_results,
            "File verification completed"
        )

    except Exception as e:
        logger.error(f"[SYNC] Error verifying files: {e}", exc_info=True)
        return error_response(f"Failed to verify files: {str(e)}", 500)


@interconnector_bp.route("/outputs/index", methods=["GET"])
def list_outputs_index():
    """Browse-only index of generated outputs (master mode only)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Outputs index is only available on master nodes", 400)

        # Verify API key (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC OUTPUTS] Outputs index rejected: {error_msg}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        sub_path = request.args.get("path")
        limit = int(request.args.get("limit", 200))
        offset = int(request.args.get("offset", 0))
        files = _list_output_files(sub_path, limit=limit, offset=offset)

        return success_response(
            {
                "path": sub_path or "",
                "files": files,
                "count": len(files),
                "limit": limit,
                "offset": offset,
                "timestamp": datetime.now().isoformat(),
            },
            f"Found {len(files)} output files"
        )
    except Exception as e:
        logger.error(f"[SYNC OUTPUTS] Error listing outputs: {e}", exc_info=True)
        return error_response(f"Failed to list outputs: {str(e)}", 500)


@interconnector_bp.route("/outputs/content", methods=["GET"])
def fetch_output_content():
    """Serve a single output file for browse-only access (master mode)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Output content is only available on master nodes", 400)

        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            return error_response(error_msg or "Invalid or missing API key", 401)

        rel_path = request.args.get("path")
        if not rel_path:
            return validation_error_response("Missing required 'path' parameter")

        outputs_root = _get_outputs_root().resolve()
        target_path = (outputs_root / rel_path).resolve()

        # Prevent path traversal
        try:
            target_path.relative_to(outputs_root)
        except ValueError:
            return validation_error_response("Invalid path")

        if not target_path.exists() or not target_path.is_file():
            return not_found_response(f"Output file not found: {rel_path}")

        mime_type, _ = mimetypes.guess_type(str(target_path))
        return send_file(target_path, mimetype=mime_type or "application/octet-stream")
    except Exception as e:
        logger.error(f"[SYNC OUTPUTS] Error serving output content: {e}", exc_info=True)
        return error_response(f"Failed to fetch output content: {str(e)}", 500)


@interconnector_bp.route("/sync/files/pull", methods=["GET"])
def pull_files():
    """Pull code files from master (for client nodes to call)."""
    try:
        logger.info("[SYNC] File pull request received (master endpoint)")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] File pull rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            logger.warning(f"[SYNC] File pull rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("Pull files endpoint is only available on master nodes", 400)

        # Verify API key (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC] File pull rejected: {error_msg}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        # Get requested sync paths
        sync_paths = request.args.getlist("paths")
        if not sync_paths:
            sync_paths = None  # Use defaults
            logger.info("[SYNC] File pull: Using default sync paths")
        else:
            logger.info(f"[SYNC] File pull: Using custom sync paths: {sync_paths}")

        include_patterns = request.args.getlist("include_patterns") or None
        exclude_patterns = request.args.getlist("exclude_patterns") or None

        # Get since timestamp if provided (for incremental sync)
        since_str = request.args.get("since")
        since = None
        if since_str:
            try:
                since = datetime.fromisoformat(since_str.replace('Z', '+00:00'))
                logger.info(f"[SYNC] File pull: Filtering files modified after: {since}")
            except Exception as e:
                logger.warning(f"[SYNC] File pull: Could not parse since timestamp '{since_str}': {e}")

        file_sync_service = get_file_sync_service()
        logger.info("[SYNC] File pull: Starting file scan...")
        files_list = file_sync_service.scan_files(
            sync_paths, since, include_content=True,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
        logger.info(f"[SYNC] File pull: Scan complete, found {len(files_list)} files")
        
        # Pre-send validation: filter out files missing content (prevents CLIENT crash)
        valid_files, invalid_files = file_sync_service.validate_files_batch(files_list)
        invalid_paths = [f.get("path", "?") for f in invalid_files]
        
        if invalid_files:
            logger.warning(
                f"[SYNC] File pull: Excluding {len(invalid_files)} files without content: {invalid_paths[:10]}"
            )
        
        # Log details about files being sent
        total_size = sum(f.get("size", 0) for f in valid_files)
        logger.info(
            f"[SYNC] File pull: Sending {len(valid_files)} valid files "
            f"({total_size / 1024 / 1024:.2f} MB), excluded {len(invalid_files)} invalid"
        )

        return success_response(
            {
                "files": valid_files,
                "invalid_files": invalid_paths,
                "paths": sync_paths or file_sync_service.default_sync_paths,
                "since": since.isoformat() if since else None,
                "timestamp": datetime.now().isoformat(),
            },
            "Files retrieved for sync"
        )

    except Exception as e:
        logger.error(f"[SYNC] Error pulling files: {e}", exc_info=True)
        return error_response(f"Failed to pull files: {str(e)}", 500)


@interconnector_bp.route("/sync/files/push", methods=["POST"])
def push_files():
    """Push code files to master (for client nodes to call)."""
    try:
        config = _get_config()

        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)

        if config.get("node_mode") != "master":
            return error_response("Push files endpoint is only available on master nodes", 400)

        # Enforce master -> client only for code sync
        return error_response("Code sync is master-to-client only. Pushing files to master is not allowed.", 400)

        # Verify API key (if enabled)
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[SYNC] Push entities rejected: {error_msg}")
            return error_response(error_msg or "Invalid or missing API key", 401)

        data = request.get_json() or {}
        files_data = data.get("files", [])
        conflict_strategy = data.get("conflict_strategy", "last_write_wins")

        if not files_data:
            return validation_error_response("No files provided")

        file_sync_service = get_file_sync_service()
        summary = {
            "total_processed": 0,
            "total_created": 0,
            "total_updated": 0,
            "total_conflicts": 0,
            "total_errors": 0,
            "total_backed_up": 0,
        }
        details = []

        # Process each file
        for file_data in files_data:
            try:
                success, conflict_id, stats = file_sync_service.apply_file(
                    file_data, conflict_strategy, create_backup=True
                )

                summary["total_processed"] += 1

                file_result = {
                    "path": file_data.get("path"),
                    "status": "success" if success else "conflict",
                    "created": stats.get("created", False),
                    "updated": stats.get("updated", False),
                    "skipped": stats.get("skipped", False),
                    "backed_up": stats.get("backed_up", False),
                }

                if stats.get("created"):
                    summary["total_created"] += 1
                elif stats.get("updated"):
                    summary["total_updated"] += 1
                    if stats.get("backed_up"):
                        summary["total_backed_up"] += 1
                elif stats.get("skipped"):
                    pass

                if conflict_id:
                    summary["total_conflicts"] += 1
                    file_result["conflict_id"] = conflict_id

                details.append(file_result)

            except Exception as e:
                logger.error(f"Error applying file {file_data.get('path')}: {e}")
                summary["total_errors"] += 1
                details.append({
                    "path": file_data.get("path"),
                    "status": "error",
                    "error": str(e),
                })

        return success_response(
            {
                "summary": summary,
                "details": details,
                "timestamp": datetime.now().isoformat(),
            },
            "Files applied successfully"
        )

    except Exception as e:
        logger.error(f"Error pushing files: {e}")
        return error_response(f"Failed to push files: {str(e)}", 500)


@interconnector_bp.route("/sync/manual", methods=["POST"])
def trigger_manual_sync():
    """Trigger a manual synchronization."""
    try:
        logger.info("[SYNC] Manual sync request received")
        config = _get_config()

        if not config.get("is_enabled"):
            logger.warning("[SYNC] Manual sync rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)

        if not request.is_json:
            logger.warning("[SYNC] Manual sync rejected: Request not JSON")
            return error_response("Request must be JSON", 400)

        data = request.get_json() or {}
        direction = data.get("direction", "bidirectional")
        entities = data.get("entities", config.get("sync_entities", []))
        conflict_strategy = data.get("conflict_strategy", "last_write_wins")
        sync_files = data.get("sync_files", False)  # Option to sync code files
        file_paths = data.get("file_paths", None)  # Optional specific paths to sync
        profile_name = data.get("profile") or data.get("profile_name")
        profile_id = data.get("profile_id")
        include_patterns = data.get("include_patterns")
        exclude_patterns = data.get("exclude_patterns")

        # Apply profile if provided
        profile = _load_profile(profile_name, profile_id)
        if profile:
            try:
                entity_cfg = json.loads(profile.entity_config) if profile.entity_config else {}
                file_cfg = json.loads(profile.file_config) if profile.file_config else {}
                if entity_cfg.get("entities") is not None:
                    entities = entity_cfg.get("entities") or []
                if file_cfg.get("paths"):
                    file_paths = file_cfg.get("paths")
                include_patterns = include_patterns or file_cfg.get("include_patterns")
                exclude_patterns = exclude_patterns or file_cfg.get("exclude_patterns")
                logger.info(f"[SYNC] Applying profile '{profile.name}' to manual sync")
            except Exception as e:
                logger.error(f"[SYNC] Failed to apply profile {profile.name}: {e}", exc_info=True)

        logger.info(f"[SYNC] Manual sync parameters: direction={direction}, entities={entities}, "
                   f"sync_files={sync_files}, file_paths={file_paths}, node_mode={config.get('node_mode')}")

        if direction not in ["bidirectional", "pull", "push"]:
            logger.error(f"[SYNC] Invalid sync direction: {direction}")
            return validation_error_response("Invalid sync direction. Must be: bidirectional, pull, or push")

        if sync_files and direction != "pull":
            logger.error(f"[SYNC] Code sync rejected: direction={direction} (code sync is pull-only from master)")
            return validation_error_response("Code sync is master-to-client only. Use direction='pull' for code updates.")

        if config.get("node_mode") != "client":
            logger.warning(f"[SYNC] Manual sync rejected: Not in client mode (mode={config.get('node_mode')})")
            return error_response("Manual sync is only available in client mode", 400)

        master_url = config.get("master_url")
        master_api_key = config.get("master_api_key")

        if not master_url or not master_api_key:
            logger.error("[SYNC] Manual sync rejected: Master URL or API key missing")
            return error_response("Master URL and API key must be configured", 400)

        # Clean up URL
        master_url = master_url.rstrip('/')
        if not master_url.startswith(('http://', 'https://')):
            master_url = f"http://{master_url}"

        logger.info(f"[SYNC] Starting sync to master: {master_url}, direction={direction}")
        sync_start_time = time.time()
        summary = {
            "total_processed": 0,
            "total_created": 0,
            "total_updated": 0,
            "total_conflicts": 0,
            "total_errors": 0,
        }
        details = {}

        # Get local node ID (use node_name as identifier for now)
        local_node_id = config.get("node_name", "local_node")

        try:
            # Pull sync
            if direction in ["pull", "bidirectional"]:
                logger.info(f"Starting pull sync for entities: {entities}")

                # Get last sync time for incremental sync
                last_sync = db.session.query(InterconnectorSyncHistory).filter(
                    InterconnectorSyncHistory.node_id == local_node_id
                ).order_by(InterconnectorSyncHistory.sync_timestamp.desc()).first()

                since = None
                if last_sync:
                    since = last_sync.sync_timestamp

                # Request entities from master
                pull_url = f"{master_url}/api/interconnector/sync/pull"
                params = {"entities": entities}
                if since:
                    params["since"] = since.isoformat()

                # Build headers - conditionally include API key
                headers = {}
                if master_api_key:
                    headers["X-API-Key"] = master_api_key

                response = requests.get(
                    pull_url,
                    headers=headers,
                    params=params,
                    timeout=60,
                    verify=False
                )

                if response.status_code == 200:
                    pull_data = response.json()
                    entities_data = pull_data.get("data", {}).get("entities", {})

                    sync_service = get_sync_service()

                    # Apply each entity type
                    for entity_type, entity_list in entities_data.items():
                        if entity_type not in sync_service.supported_entities:
                            continue

                        entity_stats = {"processed": 0, "created": 0, "updated": 0, "conflicts": 0, "errors": 0}

                        for entity_data in entity_list:
                            try:
                                success, conflict_id, stats = sync_service.apply_entity(
                                    entity_type, entity_data, conflict_strategy, "master"
                                )

                                entity_stats["processed"] += 1
                                summary["total_processed"] += 1

                                if stats.get("created"):
                                    entity_stats["created"] += 1
                                    summary["total_created"] += 1
                                    
                                    # For clients with logos, download logo file from master
                                    if entity_type == "clients" and entity_data.get("logo_path"):
                                        try:
                                            logo_filename = entity_data.get("logo_path")
                                            logo_url = f"{master_url}/api/uploads/logos/{logo_filename}"
                                            
                                            # Build headers - conditionally include API key
                                            logo_headers = {}
                                            if master_api_key:
                                                logo_headers["X-API-Key"] = master_api_key
                                            
                                            logo_response = requests.get(
                                                logo_url,
                                                headers=logo_headers,
                                                timeout=10,
                                                verify=False
                                            )
                                            if logo_response.status_code == 200:
                                                # Save logo to local uploads/logos directory
                                                from flask import current_app
                                                import os
                                                from werkzeug.utils import secure_filename
                                                
                                                upload_base = current_app.config.get("CLIENT_LOGO_FOLDER") or os.path.join(
                                                    current_app.config["UPLOAD_FOLDER"], "logos"
                                                )
                                                os.makedirs(upload_base, exist_ok=True)
                                                safe_filename = secure_filename(logo_filename)
                                                save_path = os.path.join(upload_base, safe_filename)
                                                
                                                with open(save_path, 'wb') as f:
                                                    f.write(logo_response.content)
                                                logger.info(f"Downloaded logo for client {entity_data.get('id')}: {safe_filename}")
                                        except Exception as logo_error:
                                            logger.warning(f"Failed to download logo for client {entity_data.get('id')}: {logo_error}")
                                    
                                elif stats.get("updated"):
                                    entity_stats["updated"] += 1
                                    summary["total_updated"] += 1
                                    
                                    # Also check for logo updates
                                    if entity_type == "clients" and entity_data.get("logo_path"):
                                        try:
                                            logo_filename = entity_data.get("logo_path")
                                            from flask import current_app
                                            import os
                                            
                                            upload_base = current_app.config.get("CLIENT_LOGO_FOLDER") or os.path.join(
                                                current_app.config["UPLOAD_FOLDER"], "logos"
                                            )
                                            logo_path = os.path.join(upload_base, logo_filename)
                                            
                                            # Download if file doesn't exist locally
                                            if not os.path.exists(logo_path):
                                                logo_url = f"{master_url}/api/uploads/logos/{logo_filename}"
                                                logo_response = requests.get(
                                                    logo_url,
                                                    headers={"X-API-Key": master_api_key},
                                                    timeout=10,
                                                    verify=False
                                                )
                                                if logo_response.status_code == 200:
                                                    os.makedirs(upload_base, exist_ok=True)
                                                    with open(logo_path, 'wb') as f:
                                                        f.write(logo_response.content)
                                                    logger.info(f"Downloaded updated logo for client {entity_data.get('id')}: {logo_filename}")
                                        except Exception as logo_error:
                                            logger.warning(f"Failed to download logo update for client {entity_data.get('id')}: {logo_error}")

                                if conflict_id:
                                    entity_stats["conflicts"] += 1
                                    summary["total_conflicts"] += 1

                            except Exception as e:
                                logger.error(f"Error applying {entity_type} entity during pull: {e}")
                                entity_stats["errors"] += 1
                                summary["total_errors"] += 1

                        details[entity_type] = entity_stats

                    db.session.commit()
                    logger.info(f"Pull sync completed: {summary}")
                else:
                    error_msg = f"Pull sync failed: HTTP {response.status_code}"
                    logger.error(error_msg)
                    _log_interconnector_alert(
                        "Pull sync failed",
                        {"url": pull_url, "status": response.status_code},
                    )
                    return error_response(error_msg, response.status_code)

            # Push sync
            if direction in ["push", "bidirectional"]:
                logger.info(f"Starting push sync for entities: {entities}")

                sync_service = get_sync_service()
                entities_to_push = {}

                # Get local entities
                for entity_type in entities:
                    if entity_type in sync_service.supported_entities:
                        entities_to_push[entity_type] = sync_service.get_entities_for_sync(entity_type)

                # Send to master
                push_url = f"{master_url}/api/interconnector/sync/push"
                push_data = {
                    "entities": entities_to_push,
                    "node_id": local_node_id,
                    "conflict_strategy": conflict_strategy,
                }

                # Build headers - conditionally include API key
                push_headers = {
                    "Content-Type": "application/json",
                }
                if master_api_key:
                    push_headers["X-API-Key"] = master_api_key

                response = requests.post(
                    push_url,
                    headers=push_headers,
                    json=push_data,
                    timeout=60,
                    verify=False
                )

                if response.status_code == 200:
                    push_result = response.json()
                    push_summary = push_result.get("data", {}).get("summary", {})
                    push_details = push_result.get("data", {}).get("details", {})

                    # Merge push results into summary
                    summary["total_processed"] += push_summary.get("total_processed", 0)
                    summary["total_created"] += push_summary.get("total_created", 0)
                    summary["total_updated"] += push_summary.get("total_updated", 0)
                    summary["total_conflicts"] += push_summary.get("total_conflicts", 0)
                    summary["total_errors"] += push_summary.get("total_errors", 0)

                    # Merge details
                    for entity_type, entity_stats in push_details.items():
                        if entity_type not in details:
                            details[entity_type] = {"processed": 0, "created": 0, "updated": 0, "conflicts": 0, "errors": 0}
                        for key in ["processed", "created", "updated", "conflicts", "errors"]:
                            details[entity_type][key] += entity_stats.get(key, 0)

                    logger.info(f"Push sync completed: {push_summary}")
                else:
                    error_msg = f"Push sync failed: HTTP {response.status_code}"
                    logger.error(error_msg)
                    _log_interconnector_alert(
                        "Push sync failed",
                        {"url": push_url, "status": response.status_code},
                    )
                    # Don't fail entire sync if push fails after successful pull
                    summary["total_errors"] += 1

            # File sync (if enabled)
            if sync_files:
                logger.info(f"[SYNC] Starting file sync (direction={direction})")
                
                file_sync_service = get_file_sync_service()
                file_summary = {
                    "total_processed": 0,
                    "total_created": 0,
                    "total_updated": 0,
                    "total_conflicts": 0,
                    "total_errors": 0,
                    "total_backed_up": 0,
                }
                file_details = []

                # Pull files
                if direction in ["pull", "bidirectional"]:
                    try:
                        logger.info(f"[SYNC] File pull: Getting last sync time for node_id={local_node_id}")
                        # Get last sync time for incremental file sync
                        # Only use incremental sync if we have a previous file sync (not just entity sync)
                        last_sync = db.session.query(InterconnectorSyncHistory).filter(
                            InterconnectorSyncHistory.node_id == local_node_id
                        ).order_by(
                            InterconnectorSyncHistory.sync_timestamp.desc()
                        ).first()
                        
                        # Check if last sync actually included files
                        # We need to check if the sync history includes file sync info
                        # For now, we'll check if there's a sync history entry that suggests files were synced
                        last_sync_had_files = False
                        if last_sync:
                            logger.info(f"[SYNC] File pull: Last sync found: {last_sync.sync_timestamp}, "
                                       f"sync_direction={last_sync.sync_direction}")
                            # Check if there are any file sync records by looking for syncs with file counts
                            # Since we don't track file sync separately, we'll check if this is a recent sync
                            # and if sync_files flag was used. For now, be conservative and assume first 
                            # file sync should be full sync
                            
                            # Check if last sync was very recent (within last hour) - if so, might be incremental
                            # Otherwise, do full sync to be safe
                            time_since_last_sync = datetime.now() - last_sync.sync_timestamp
                            if time_since_last_sync.total_seconds() < 3600:  # Less than 1 hour
                                logger.info(f"[SYNC] File pull: Last sync was recent ({time_since_last_sync.total_seconds():.0f}s ago), "
                                           f"assuming it included files, will use incremental sync")
                                last_sync_had_files = True
                            else:
                                logger.info(f"[SYNC] File pull: Last sync was {time_since_last_sync.total_seconds()/3600:.1f} hours ago, "
                                           f"performing full sync to be safe")
                                last_sync_had_files = False
                        else:
                            logger.info(f"[SYNC] File pull: No previous sync found, performing full sync")

                        since = None
                        if last_sync and last_sync_had_files:
                            since = last_sync.sync_timestamp
                            logger.info(f"[SYNC] File pull: Using incremental sync since: {since}, "
                                       f"will only sync files modified after this time")
                        else:
                            logger.info(f"[SYNC] File pull: Performing full file sync (no previous file sync found)")
                            since = None  # Explicitly set to None for full sync

                        # Request files from master
                        files_pull_url = f"{master_url}/api/interconnector/sync/files/pull"
                        files_params = {}
                        if file_paths:
                            files_params["paths"] = file_paths
                            logger.info(f"[SYNC] File pull: Using custom paths: {file_paths}")
                        if since:
                            files_params["since"] = since.isoformat()
                            logger.info(f"[SYNC] File pull: Using since timestamp: {since.isoformat()}")
                        if include_patterns:
                            files_params["include_patterns"] = include_patterns
                        if exclude_patterns:
                            files_params["exclude_patterns"] = exclude_patterns

                        # Build headers - conditionally include API key
                        headers = {}
                        if master_api_key:
                            headers["X-API-Key"] = master_api_key

                        logger.info(f"[SYNC] File pull: Requesting files from {files_pull_url} with params={files_params}")
                        files_response = requests.get(
                            files_pull_url,
                            headers=headers,
                            params=files_params,
                            timeout=120,  # Longer timeout for file sync
                            verify=False
                        )

                        logger.info(f"[SYNC] File pull: Response status={files_response.status_code}")
                        if files_response.status_code == 200:
                            files_data = files_response.json()
                            files_list = files_data.get("data", {}).get("files", [])
                            logger.info(f"[SYNC] File pull: Received {len(files_list)} files from master")
                            
                            if config.get("require_file_approval", True) and files_list:
                                # Queue approval instead of applying immediately
                                approval = InterconnectorPendingApproval(
                                    push_id=str(uuid.uuid4()),
                                    source_node="master",
                                    sync_type="files",
                                    files_data=json.dumps(files_list),
                                    entities_data=json.dumps({}),
                                    status="pending",
                                )
                                db.session.add(approval)
                                db.session.commit()
                                logger.info(f"[SYNC] Queued {len(files_list)} files for approval (id={approval.id})")
                                
                                summary["files"] = {
                                    "summary": {
                                        "total_processed": 0,
                                        "total_created": 0,
                                        "total_updated": 0,
                                        "total_conflicts": 0,
                                        "total_errors": 0,
                                        "total_backed_up": 0,
                                    },
                                    "details": [],
                                    "pending_approval_id": approval.id,
                                }
                                # Skip applying files; continue to history recording
                                files_list = []
                            
                            # Log sample of files received
                            if files_list:
                                sample_files = files_list[:5]
                                logger.info(f"[SYNC] File pull: Sample files received:")
                                for f in sample_files:
                                    has_content = f.get("content") is not None or f.get("content_compressed")
                                    content_len = len(f.get("content", "")) if f.get("content") else (len(f.get("content_compressed", "")) if f.get("content_compressed") else 0)
                                    logger.info(f"[SYNC]   - {f.get('path')}: size={f.get('size')}, "
                                               f"has_content={has_content}, content_length={content_len}")
                            else:
                                logger.warning(f"[SYNC] File pull: No files received from master!")

                            # Pre-apply: check import dependencies (JS/TS)
                            missing_deps = file_sync_service.check_import_dependencies(files_list)
                            if missing_deps:
                                file_summary["missing_dependencies"] = [
                                    {"file": d["file"], "imports": d["missing"], "resolved": d["resolved_path"]}
                                    for d in missing_deps
                                ]
                                logger.warning(
                                    f"[SYNC] File pull: {len(missing_deps)} import dependencies missing from batch - "
                                    f"sync may break frontend. Run full sync or ensure referenced files are included."
                                )
                            # Apply files atomically (all succeed or all rollback)
                            logger.info(f"[SYNC] File pull: Processing {len(files_list)} files atomically")
                            success, apply_result = file_sync_service.apply_files_atomic(
                                files_list, conflict_strategy, create_backup=True
                            )
                            asum = apply_result.get("summary", {})
                            file_summary["total_processed"] = asum.get("total_processed", 0)
                            file_summary["total_created"] = asum.get("total_created", 0)
                            file_summary["total_updated"] = asum.get("total_updated", 0)
                            file_summary["total_conflicts"] = asum.get("total_conflicts", 0)
                            file_summary["total_errors"] = asum.get("total_errors", 0)
                            file_summary["total_backed_up"] = asum.get("total_backed_up", 0)
                            file_details.extend(apply_result.get("details", []))
                            
                            invalid_from_master = files_data.get("data", {}).get("invalid_files", [])
                            if invalid_from_master:
                                logger.warning(
                                    f"[SYNC] File pull: Master excluded {len(invalid_from_master)} files without content"
                                )
                            if apply_result.get("invalid_files"):
                                logger.warning(
                                    f"[SYNC] File pull: Skipped {len(apply_result['invalid_files'])} files missing content"
                                )
                            if asum.get("rolled_back"):
                                logger.error("[SYNC] File pull: Atomic apply failed, rolled back all changes")

                            logger.info(f"[SYNC] File pull sync completed: processed={file_summary['total_processed']}, "
                                       f"created={file_summary['total_created']}, updated={file_summary['total_updated']}, "
                                       f"errors={file_summary['total_errors']}, rolled_back={asum.get('rolled_back', False)}")
                        else:
                            logger.error(f"[SYNC] File pull sync failed: HTTP {files_response.status_code}, "
                                       f"response={files_response.text[:500]}")
                            _log_interconnector_alert(
                                "File pull sync failed",
                                {"url": files_pull_url, "status": files_response.status_code},
                            )
                            file_summary["total_errors"] += 1

                    except Exception as e:
                        logger.error(f"Error during file pull sync: {e}")
                        file_summary["total_errors"] += 1

                # Push files
                if direction in ["push", "bidirectional"]:
                    try:
                        # Get local files
                        local_files = file_sync_service.scan_files(file_paths)

                        # Send to master
                        files_push_url = f"{master_url}/api/interconnector/sync/files/push"
                        files_push_data = {
                            "files": local_files,
                            "conflict_strategy": conflict_strategy,
                        }

                        # Build headers - conditionally include API key
                        push_headers = {
                            "Content-Type": "application/json",
                        }
                        if master_api_key:
                            push_headers["X-API-Key"] = master_api_key

                        files_response = requests.post(
                            files_push_url,
                            headers=push_headers,
                            json=files_push_data,
                            timeout=120,
                            verify=False
                        )

                        if files_response.status_code == 200:
                            files_result = files_response.json()
                            push_file_summary = files_result.get("data", {}).get("summary", {})

                            # Merge push results
                            file_summary["total_processed"] += push_file_summary.get("total_processed", 0)
                            file_summary["total_created"] += push_file_summary.get("total_created", 0)
                            file_summary["total_updated"] += push_file_summary.get("total_updated", 0)
                            file_summary["total_conflicts"] += push_file_summary.get("total_conflicts", 0)
                            file_summary["total_errors"] += push_file_summary.get("total_errors", 0)
                            file_summary["total_backed_up"] += push_file_summary.get("total_backed_up", 0)

                            logger.info(f"File push sync completed: {push_file_summary}")
                        else:
                            logger.warning(f"File push sync failed: HTTP {files_response.status_code}")
                            _log_interconnector_alert(
                                "File push sync failed",
                                {"url": files_push_url, "status": files_response.status_code},
                            )
                            file_summary["total_errors"] += 1

                    except Exception as e:
                        logger.error(f"Error during file push sync: {e}")
                        file_summary["total_errors"] += 1

                # Add file sync results to summary (always include, even if empty)
                if sync_files:
                    logger.info(f"[SYNC] Adding file sync results to summary: "
                               f"processed={file_summary['total_processed']}, "
                               f"created={file_summary['total_created']}, "
                               f"updated={file_summary['total_updated']}, "
                               f"errors={file_summary['total_errors']}")
                    summary["files"] = {
                        "summary": file_summary,
                        "details": file_details,
                    }
                else:
                    logger.debug("[SYNC] File sync not enabled, skipping file sync results")

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during sync: {e}")
            return error_response(f"Sync failed: Network error - {str(e)}", 503)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error during sync: {e}")
            return error_response(f"Synchronization failed: {str(e)}", 500)

        # Record sync history
        sync_duration_ms = int((time.time() - sync_start_time) * 1000)
        sync_status = "success" if summary["total_errors"] == 0 else "partial"

        logger.info(f"[SYNC] Recording sync history - node_id={local_node_id}, direction={direction}, "
                   f"status={sync_status}, duration={sync_duration_ms}ms, "
                   f"processed={summary['total_processed']}, errors={summary['total_errors']}")

        try:
            sync_history = InterconnectorSyncHistory(
                node_id=local_node_id,
                sync_direction=direction,
                entities_synced=json.dumps(entities),
                items_processed=summary["total_processed"],
                items_created=summary["total_created"],
                items_updated=summary["total_updated"],
                conflicts_resolved=summary["total_conflicts"],
                sync_duration_ms=sync_duration_ms,
                status=sync_status,
                sync_timestamp=datetime.now(),
            )
            db.session.add(sync_history)
            logger.info(f"[SYNC] Sync history record created: node_id={local_node_id}, "
                       f"sync_timestamp={sync_history.sync_timestamp}")

            # Update node's last sync time if it exists
            logger.info(f"[SYNC] Looking up node record - trying node_id={local_node_id} first")
            node = db.session.query(InterconnectorNode).filter_by(node_id=local_node_id).first()
            if not node:
                node_name = config.get("node_name")
                logger.info(f"[SYNC] Node not found by node_id, trying node_name={node_name}")
                node = db.session.query(InterconnectorNode).filter_by(node_name=node_name).first()
            
            if node:
                logger.info(f"[SYNC] Node found: node_id={node.node_id}, node_name={node.node_name}, "
                           f"updating last_sync_time and heartbeat")
                node.last_sync_time = datetime.now()
                # Also update heartbeat if node exists
                node.last_heartbeat = datetime.now()
                node.status = "active"
            else:
                logger.warning(f"[SYNC] Node record not found for node_id={local_node_id} "
                             f"or node_name={config.get('node_name')} - cannot update last_sync_time")

            db.session.commit()
            logger.info(f"[SYNC] Sync history and node record committed successfully")
            
            # Log sync completion for debugging
            logger.info(f"[SYNC] Sync completed: {summary}, direction={direction}, node_id={local_node_id}")
        except Exception as e:
            logger.error(f"[SYNC] Failed to record sync history: {e}", exc_info=True)
            db.session.rollback()

        result = {
            "success": sync_status == "success",
            "direction": direction,
            "entities": entities,
            "summary": summary,
            "details": details,
            "sync_duration_ms": sync_duration_ms,
            "timestamp": datetime.now().isoformat(),
        }

        return success_response(result, "Synchronization completed")

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error during manual sync: {e}")
        return error_response(f"Synchronization failed: {str(e)}", 500)


@interconnector_bp.route("/test-connection", methods=["POST"])
def test_connection():
    """Test connection to master server (client mode)."""
    try:
        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json()
        master_url = data.get("master_url")
        master_api_key = data.get("master_api_key")

        if not master_url:
            return validation_error_response("Master URL is required")
        
        # API key is optional if server doesn't require it
        # But we can't check server config from here, so we'll allow empty API key
        # The server will accept or reject based on its require_api_key setting

        # Clean up URL
        master_url = master_url.rstrip('/')
        if not master_url.startswith(('http://', 'https://')):
            master_url = f"http://{master_url}"

        # Build headers - conditionally include API key
        test_headers = {}
        if master_api_key:
            test_headers["X-API-Key"] = master_api_key

        # Test connection with latency measurement
        start_time = time.time()
        test_url = f"{master_url}/api/interconnector/status"
        
        try:
            response = requests.get(
                test_url,
                headers=test_headers,
                timeout=10,
                verify=False  # Allow self-signed certs for local networks
            )
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                # Determine connection quality
                if latency_ms < 50:
                    quality = "excellent"
                elif latency_ms < 200:
                    quality = "good"
                elif latency_ms < 1000:
                    quality = "poor"
                else:
                    quality = "very_poor"

                return success_response(
                    {
                        "connection_status": "success",
                        "master_url": master_url,
                        "latency_ms": latency_ms,
                        "quality": quality,
                        "http_status": response.status_code,
                    },
                    "Connection test successful",
                )
            else:
                return error_response(
                    f"Connection test failed: HTTP {response.status_code}",
                    400
                )

        except requests.exceptions.Timeout:
            return error_response("Connection test failed: Timeout", 408)
        except requests.exceptions.ConnectionError as e:
            return error_response(f"Connection test failed: Unable to reach server - {str(e)}", 503)
        except requests.exceptions.RequestException as e:
            return error_response(f"Connection test failed: {str(e)}", 500)

    except Exception as e:
        logger.error(f"Error testing connection: {e}")
        return error_response(f"Connection test failed: {str(e)}", 500)


# =============================================================================
# SIMPLIFIED CLIENT UPDATE ENDPOINTS
# These endpoints provide a streamlined "Updates Available" experience for clients
# =============================================================================

@interconnector_bp.route("/updates/check", methods=["GET"])
def check_for_updates():
    """
    Lightweight endpoint for clients to check if code updates are available.
    This does NOT transfer file contents - only metadata for comparison.
    
    Returns:
        - available: bool - whether updates are available
        - count: int - number of files that differ
        - summary: dict - breakdown by directory
        - master_version: str - timestamp of most recent file on master
        - local_version: str - timestamp of most recent local file
    """
    try:
        logger.info("[UPDATES] Checking for updates...")
        config = _get_config()
        
        if not config.get("is_enabled"):
            logger.warning("[UPDATES] Check rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)
        
        if config.get("node_mode") != "client":
            logger.warning(f"[UPDATES] Check rejected: Not in client mode (mode={config.get('node_mode')})")
            return error_response("Update check is only available on client nodes", 400)
        
        master_url = config.get("master_url", "").rstrip("/")
        master_api_key = config.get("master_api_key", "")
        
        if not master_url:
            logger.warning("[UPDATES] Check rejected: No master URL configured")
            return error_response("Master URL not configured", 400)
        
        logger.info(f"[UPDATES] Fetching manifest from master: {master_url}")
        
        # Build headers
        headers = {}
        if master_api_key:
            headers["X-API-Key"] = master_api_key
        
        # Fetch file metadata from master (without content for speed)
        try:
            # First, get file hashes from master
            manifest_url = f"{master_url}/api/interconnector/updates/manifest"
            logger.debug(f"[UPDATES] Requesting manifest from: {manifest_url}")
            
            response = requests.get(
                manifest_url,
                headers=headers,
                timeout=30,
                verify=False
            )
            
            logger.debug(f"[UPDATES] Master response status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"[UPDATES] Master returned HTTP {response.status_code}: {response.text[:200]}")
                return error_response(f"Failed to fetch update manifest from master: HTTP {response.status_code}", 502)
            
            master_data = response.json()
            if master_data.get("error"):
                logger.error(f"[UPDATES] Master returned error: {master_data.get('error')}")
                return error_response(f"Master returned error: {master_data.get('error')}", 502)
            
            master_files = master_data.get("data", {}).get("files", [])
            master_timestamp = master_data.get("data", {}).get("timestamp")
            
            logger.info(f"[UPDATES] Received {len(master_files)} files from master manifest")
            
            # Debug: Log first few files with hashes
            if master_files:
                for f in master_files[:3]:
                    logger.debug(f"[UPDATES] Master file sample: {f.get('path')} hash={f.get('hash', 'NONE')[:16] if f.get('hash') else 'NULL'}...")
            
        except requests.exceptions.Timeout:
            logger.error("[UPDATES] Timeout connecting to master server")
            return error_response("Timeout connecting to master server", 504)
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[UPDATES] Connection error to master: {e}")
            return error_response(f"Cannot connect to master server: {str(e)}", 503)
        except Exception as e:
            logger.error(f"[UPDATES] Error fetching from master: {e}", exc_info=True)
            return error_response(f"Error fetching from master: {str(e)}", 502)
        
        # Get local file hashes for comparison
        logger.info("[UPDATES] Scanning local files for comparison...")
        file_sync_service = get_file_sync_service()
        local_files = file_sync_service.scan_files(include_content=False)
        
        logger.info(f"[UPDATES] Found {len(local_files)} local files")
        
        # Debug: Log first few local files with hashes
        if local_files:
            for f in local_files[:3]:
                logger.debug(f"[UPDATES] Local file sample: {f.get('path')} hash={f.get('hash', 'NONE')[:16] if f.get('hash') else 'NULL'}...")
        
        # Build lookup dict of local files
        local_lookup = {f["path"]: f for f in local_files}
        
        # Compare files
        updates_needed = []
        new_files = []
        modified_files = []
        
        for master_file in master_files:
            path = master_file.get("path")
            master_hash = master_file.get("hash")
            
            local_file = local_lookup.get(path)
            
            if not local_file:
                # New file on master
                logger.debug(f"[UPDATES] New file on master: {path}")
                new_files.append({
                    "path": path,
                    "action": "create",
                    "size": master_file.get("size", 0)
                })
                updates_needed.append(master_file)
            elif local_file.get("hash") != master_hash:
                # File differs
                logger.debug(f"[UPDATES] File differs: {path} (local={local_file.get('hash', 'NULL')[:16] if local_file.get('hash') else 'NULL'}... vs master={master_hash[:16] if master_hash else 'NULL'}...)")
                modified_files.append({
                    "path": path,
                    "action": "update",
                    "size": master_file.get("size", 0),
                    "local_modified": local_file.get("modified_at"),
                    "master_modified": master_file.get("modified_at")
                })
                updates_needed.append(master_file)
        
        # Build summary by directory
        summary = {"backend": 0, "frontend": 0, "other": 0}
        for f in updates_needed:
            path = f.get("path", "")
            if path.startswith("backend/"):
                summary["backend"] += 1
            elif path.startswith("frontend/"):
                summary["frontend"] += 1
            else:
                summary["other"] += 1
        
        # Get local version (most recent file modification)
        local_version = None
        if local_files:
            local_times = [f.get("modified_at") for f in local_files if f.get("modified_at")]
            if local_times:
                local_version = max(local_times)
        
        logger.info(f"[UPDATES] Check complete: {len(updates_needed)} updates available "
                   f"({len(new_files)} new, {len(modified_files)} modified)")
        
        return success_response({
            "available": len(updates_needed) > 0,
            "count": len(updates_needed),
            "new_files": len(new_files),
            "modified_files": len(modified_files),
            "summary": summary,
            "master_version": master_timestamp,
            "local_version": local_version,
            "last_checked": datetime.now().isoformat()
        }, "Update check complete")
        
    except Exception as e:
        logger.error(f"Error checking for updates: {e}", exc_info=True)
        return error_response(f"Update check failed: {str(e)}", 500)


@interconnector_bp.route("/updates/manifest", methods=["GET"])
def get_update_manifest():
    """
    Master endpoint: Return file manifest (metadata only, no content).
    This is a lightweight endpoint for clients to compare against their local files.
    """
    try:
        logger.info("[UPDATES] Manifest requested")
        config = _get_config()
        
        if not config.get("is_enabled"):
            logger.warning("[UPDATES] Manifest rejected: Interconnector not enabled")
            return error_response("Interconnector is not enabled", 400)
        
        if config.get("node_mode") != "master":
            logger.warning(f"[UPDATES] Manifest rejected: Not in master mode (mode={config.get('node_mode')})")
            return error_response("Manifest endpoint is only available on master nodes", 400)
        
        # Verify API key if required
        api_key = request.headers.get("X-API-Key")
        is_valid, error_msg = _check_api_key(config, api_key)
        if not is_valid:
            logger.warning(f"[UPDATES] Manifest rejected: {error_msg}")
            return error_response(error_msg or "Invalid or missing API key", 401)
        
        # Scan files WITHOUT content (metadata only for speed, but WITH hashes for comparison)
        logger.info("[UPDATES] Scanning files for manifest...")
        file_sync_service = get_file_sync_service()
        files_list = file_sync_service.scan_files(include_content=False)
        
        logger.info(f"[UPDATES] Manifest scan complete: {len(files_list)} files")
        
        # Verify hashes are present
        files_with_hash = sum(1 for f in files_list if f.get("hash"))
        files_without_hash = len(files_list) - files_with_hash
        if files_without_hash > 0:
            logger.warning(f"[UPDATES] Warning: {files_without_hash} files missing hash in manifest")
        
        # Debug: Log sample files
        if files_list:
            for f in files_list[:3]:
                logger.debug(f"[UPDATES] Manifest sample: {f.get('path')} hash={f.get('hash', 'NONE')[:16] if f.get('hash') else 'NULL'}...")
        
        # Get most recent modification time
        timestamps = [f.get("modified_at") for f in files_list if f.get("modified_at")]
        latest_timestamp = max(timestamps) if timestamps else datetime.now().isoformat()
        
        logger.info(f"[UPDATES] Manifest ready: {len(files_list)} files, latest: {latest_timestamp}")
        
        return success_response({
            "files": files_list,
            "count": len(files_list),
            "timestamp": latest_timestamp,
        }, "Manifest retrieved")
        
    except Exception as e:
        logger.error(f"Error getting update manifest: {e}", exc_info=True)
        return error_response(f"Failed to get manifest: {str(e)}", 500)


@interconnector_bp.route("/updates/preview", methods=["GET"])
def preview_updates():
    """
    Client endpoint: Get detailed preview of what would change if updates are applied.
    Returns file paths, actions (create/update), and size information.
    """
    try:
        config = _get_config()
        
        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)
        
        if config.get("node_mode") != "client":
            return error_response("Update preview is only available on client nodes", 400)
        
        master_url = config.get("master_url", "").rstrip("/")
        master_api_key = config.get("master_api_key", "")
        
        if not master_url:
            return error_response("Master URL not configured", 400)
        
        headers = {}
        if master_api_key:
            headers["X-API-Key"] = master_api_key
        
        # Fetch manifest from master
        try:
            response = requests.get(
                f"{master_url}/api/interconnector/updates/manifest",
                headers=headers,
                timeout=30,
                verify=False
            )
            
            if response.status_code != 200:
                return error_response(f"Failed to fetch manifest: HTTP {response.status_code}", 502)
            
            master_data = response.json()
            master_files = master_data.get("data", {}).get("files", [])
            
        except Exception as e:
            return error_response(f"Error fetching from master: {str(e)}", 502)
        
        # Get local files
        file_sync_service = get_file_sync_service()
        local_files = file_sync_service.scan_files(include_content=False)
        local_lookup = {f["path"]: f for f in local_files}
        
        # Build detailed preview
        preview_files = []
        total_size = 0
        
        for master_file in master_files:
            path = master_file.get("path")
            master_hash = master_file.get("hash")
            file_size = master_file.get("size", 0)
            
            local_file = local_lookup.get(path)
            
            if not local_file:
                preview_files.append({
                    "path": path,
                    "action": "create",
                    "size": file_size,
                    "size_display": _format_size(file_size),
                    "master_modified": master_file.get("modified_at")
                })
                total_size += file_size
            elif local_file.get("hash") != master_hash:
                size_diff = file_size - local_file.get("size", 0)
                preview_files.append({
                    "path": path,
                    "action": "update",
                    "size": file_size,
                    "size_display": _format_size(file_size),
                    "size_diff": size_diff,
                    "size_diff_display": f"+{_format_size(size_diff)}" if size_diff >= 0 else f"-{_format_size(abs(size_diff))}",
                    "local_modified": local_file.get("modified_at"),
                    "master_modified": master_file.get("modified_at")
                })
                total_size += file_size
        
        # Sort by path for consistent display
        preview_files.sort(key=lambda x: x["path"])
        
        return success_response({
            "files": preview_files,
            "count": len(preview_files),
            "total_size": total_size,
            "total_size_display": _format_size(total_size),
            "backup_note": "All existing files will be backed up before changes are applied."
        }, "Update preview generated")
        
    except Exception as e:
        logger.error(f"Error previewing updates: {e}", exc_info=True)
        return error_response(f"Preview failed: {str(e)}", 500)


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


@interconnector_bp.route("/updates/apply", methods=["POST"])
def apply_updates():
    """
    Client endpoint: Apply code updates from master.
    This fetches the actual file contents and applies them locally.
    
    Optional body parameters:
        - files: list of specific file paths to update (if empty, updates all)
    """
    try:
        config = _get_config()
        
        if not config.get("is_enabled"):
            return error_response("Interconnector is not enabled", 400)
        
        if config.get("node_mode") != "client":
            return error_response("Apply updates is only available on client nodes", 400)
        
        master_url = config.get("master_url", "").rstrip("/")
        master_api_key = config.get("master_api_key", "")
        
        if not master_url:
            return error_response("Master URL not configured", 400)
        
        headers = {"Content-Type": "application/json"}
        if master_api_key:
            headers["X-API-Key"] = master_api_key
        
        # Get optional file filter from request body
        data = request.get_json() or {}
        requested_files = data.get("files", [])  # Empty = all files
        
        # Step 1: Get manifest from master to know what to update
        try:
            manifest_response = requests.get(
                f"{master_url}/api/interconnector/updates/manifest",
                headers=headers,
                timeout=30,
                verify=False
            )
            
            if manifest_response.status_code != 200:
                return error_response(f"Failed to fetch manifest: HTTP {manifest_response.status_code}", 502)
            
            master_manifest = manifest_response.json()
            master_files = master_manifest.get("data", {}).get("files", [])
            
        except Exception as e:
            return error_response(f"Error fetching manifest: {str(e)}", 502)
        
        # Step 2: Get local files for comparison
        file_sync_service = get_file_sync_service()
        local_files = file_sync_service.scan_files(include_content=False)
        local_lookup = {f["path"]: f for f in local_files}
        
        # Step 3: Determine which files need updating
        files_to_update = []
        for master_file in master_files:
            path = master_file.get("path")
            master_hash = master_file.get("hash")
            
            # If specific files requested, only update those
            if requested_files and path not in requested_files:
                continue
            
            local_file = local_lookup.get(path)
            
            if not local_file or local_file.get("hash") != master_hash:
                files_to_update.append(path)
        
        if not files_to_update:
            return success_response({
                "applied": 0,
                "message": "No updates needed - all files are up to date"
            }, "No updates needed")
        
        # Step 4: Fetch full file contents from master
        logger.info(f"[UPDATES] Fetching {len(files_to_update)} files from master...")
        
        try:
            pull_response = requests.get(
                f"{master_url}/api/interconnector/sync/files/pull",
                headers=headers,
                timeout=120,  # Longer timeout for file transfer
                verify=False
            )
            
            if pull_response.status_code != 200:
                return error_response(f"Failed to pull files: HTTP {pull_response.status_code}", 502)
            
            pull_data = pull_response.json()
            all_master_files = pull_data.get("data", {}).get("files", [])
            
        except Exception as e:
            return error_response(f"Error pulling files: {str(e)}", 502)
        
        # Step 5: Apply updates atomically (filter to files we need, then apply all)
        files_to_apply = [f for f in all_master_files if f.get("path") in files_to_update]
        valid_files, invalid_files = file_sync_service.validate_files_batch(files_to_apply)
        
        if invalid_files:
            logger.warning(
                f"[UPDATES] {len(invalid_files)} files missing content, excluding from update"
            )
        
        success = True
        summary = {
            "total_processed": 0,
            "total_created": 0,
            "total_updated": 0,
            "total_backed_up": 0,
            "total_errors": 0,
            "total_skipped": 0
        }
        details = []
        backup_path = None
        
        if valid_files:
            success, apply_result = file_sync_service.apply_files_atomic(
                valid_files, "last_write_wins", create_backup=True
            )
            asum = apply_result.get("summary", {})
            summary["total_processed"] = asum.get("total_processed", 0)
            summary["total_created"] = asum.get("total_created", 0)
            summary["total_updated"] = asum.get("total_updated", 0)
            summary["total_backed_up"] = asum.get("total_backed_up", 0)
            summary["total_errors"] = asum.get("total_errors", 0)
            summary["total_skipped"] = asum.get("total_skipped", 0)
            for d in apply_result.get("details", []):
                action = "skipped"
                if d.get("created"):
                    action = "created"
                elif d.get("updated"):
                    action = "updated"
                elif d.get("status") == "error":
                    action = "error"
                details.append({
                    "path": d.get("path"),
                    "action": action,
                    "error": d.get("error"),
                })
            if asum.get("rolled_back"):
                logger.error("[UPDATES] Atomic apply failed, rolled back all changes")
        
        # Get backup path for display
        project_root = file_sync_service.get_project_root()
        backup_dir = project_root / "backups" / "file_sync"
        if backup_dir.exists():
            backup_path = str(backup_dir)
        
        logger.info(f"[UPDATES] Update complete: {summary}")
        
        return success_response({
            "applied": summary["total_processed"],
            "created": summary["total_created"],
            "updated": summary["total_updated"],
            "backed_up": summary["total_backed_up"],
            "skipped": summary["total_skipped"],
            "errors": summary["total_errors"],
            "backup_path": backup_path,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }, f"Successfully applied {summary['total_processed']} updates")
        
    except Exception as e:
        logger.error(f"Error applying updates: {e}", exc_info=True)
        return error_response(f"Update failed: {str(e)}", 500)

