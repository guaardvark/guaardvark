# backend/utils/auth_guard.py
"""Lightweight endpoint protection for dangerous operations.

When GUAARDVARK_API_KEY is set in the environment, protected endpoints
require the key in the X-API-Key header. When unset, localhost requests
pass freely but remote hosts are blocked from sensitive endpoints.
"""

import os
import hmac
import logging

from flask import request, jsonify

logger = logging.getLogger(__name__)

# Endpoints that always require protection (any method)
PROTECTED_PREFIXES = (
    '/api/code-execution/',
    '/api/backups/restore',
    '/api/backups/create',
)

# Endpoints protected only on DELETE
PROTECTED_DELETE_PREFIXES = (
    '/api/backups/',
)


def _is_localhost(addr):
    """Check if address is a loopback/localhost address."""
    return addr in ('127.0.0.1', '::1', 'localhost')


def _is_protected():
    """Check if the current request targets a protected endpoint."""
    path = request.path
    for prefix in PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return True
    if request.method == 'DELETE':
        for prefix in PROTECTED_DELETE_PREFIXES:
            if path.startswith(prefix):
                return True
    return False


def check_endpoint_auth():
    """Flask before_request hook: enforce auth on dangerous endpoints.

    Logic:
    - If endpoint is not protected → allow
    - If GUAARDVARK_API_KEY is set → require X-API-Key header (any host)
    - If GUAARDVARK_API_KEY is NOT set → allow localhost, block remote
    """
    if not _is_protected():
        return None

    api_key = os.environ.get('GUAARDVARK_API_KEY')

    if not api_key:
        # No key configured — localhost-only access
        if _is_localhost(request.remote_addr):
            return None
        logger.warning(
            f"[AUTH] Blocked remote access to {request.path} from {request.remote_addr}"
        )
        return jsonify({"error": "Access denied from remote host"}), 403

    # API key is configured — require it
    provided_key = request.headers.get('X-API-Key', '')
    if provided_key and hmac.compare_digest(provided_key, api_key):
        return None

    logger.warning(
        f"[AUTH] Invalid/missing API key for {request.path} from {request.remote_addr}"
    )
    return jsonify({"error": "Invalid or missing API key"}), 401
