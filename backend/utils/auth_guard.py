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
    '/api/self-code/',
    # Social outreach has kill switches, draft approval, and fetch-meta — none of
    # which should be reachable from another machine on the LAN without an API key.
    '/api/social-outreach/',
)

# File APIs include both the document library and the live repository editor.
# Keep read-only document browser GETs public for the local UI, but protect
# server filesystem reads and every mutation-capable file route.
PROTECTED_FILE_PREFIXES = (
    '/api/files/read',
    '/api/files/list',
    '/api/files/write',
    '/api/files/create',
    '/api/files/delete',
    '/api/files/mkdir',
    '/api/files/rename',
    '/api/files/browse-server',
)

# Endpoints protected only on DELETE
PROTECTED_DELETE_PREFIXES = (
    '/api/backups/',
    # Cast subjects, reference images and samples; generate/train stay LAN-usable.
    '/api/cast-library/',
    # Clearing the audio job history; generation and cancel stay LAN-usable.
    '/api/audio-foundry/jobs',
)

# Explicitly safe operations that are exempt from the host check even though they
# live under an otherwise-protected prefix. /api/meta is shared by many blueprints
# (jobs, index management, diagnostics) that MUST stay protected, but clearing
# __pycache__ is a non-destructive maintenance op (regenerable .pyc only; the
# module-purge that could destabilize the server is disabled) that the operator
# wants reachable from the LAN UI. Keep this list tiny and genuinely harmless.
SAFE_EXEMPT_PREFIXES = (
    '/api/meta/clear-pycache',
)

# Mutation-only protection: GET/HEAD/OPTIONS stay public for the local UI, but any
# non-GET (create/cancel/delete/run) requires auth/localhost — same model as the
# /api/memory hardening. Stops a random LAN host from wiping jobs/tasks/schedules.
MUTATION_PROTECTED_PREFIXES = (
    # Persists the product profile into .env.
    '/api/settings/profile',
    '/api/memory',
    '/api/tasks',
    '/api/scheduler',
    '/api/jobs',
    '/api/meta',
    '/api/progress-test',
    # Destructive batch-image file operations (delete/rename/move). Generation
    # and reads stay reachable from LAN browsers, which have no API-key field.
    '/api/batch-image/image/',
    '/api/batch-image/delete/',
    '/api/batch-image/rename/',
    '/api/batch-image/move/',
    '/api/batch-image/folder',
    # GPU control (stop Ollama, force-release leases, evict) and upscaling jobs.
    '/api/gpu',
    '/api/upscaling',
)


def _normalize_ip(addr: str) -> str:
    """Normalize IP for localhost checks (handles IPv4-mapped IPv6 like ::ffff:127.0.0.1 and zone IDs)."""
    if not addr:
        return ""
    a = addr.strip()
    if a.lower().startswith("::ffff:"):
        a = a[7:]
    if "%" in a:
        a = a.split("%", 1)[0]
    return a.lower()


_local_ips_cache: set[str] | None = None


def _get_local_ips() -> set[str]:
    """Return IPs that belong to this machine.

    Used so that when the operator loads the UI via a LAN IP/hostname (instead of
    pure localhost), requests proxied through our Vite server are still treated as
    "the person at the console" for no-API-key sensitive endpoints (backups/create,
    self-code, etc.). Different LAN devices get their own IPs in X-Forwarded-For and
    remain blocked.
    """
    global _local_ips_cache
    if _local_ips_cache is not None:
        return _local_ips_cache

    ips: set[str] = {"127.0.0.1", "::1", "localhost"}
    try:
        import socket

        # Primary outbound IP (the one the box uses to talk to the world / LAN gateway)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            src = s.getsockname()[0]
            if src:
                ips.add(src.lower())
        finally:
            s.close()
    except Exception:
        pass

    _local_ips_cache = ips
    return ips


def _is_localhost(addr: str) -> bool:
    """Check if address is loopback or one of this machine's own IPs (incl. LAN IP).

    This makes "access via LAN URL on the operator's own machine" work for no-key
    sensitive ops while still blocking *other* LAN hosts.
    """
    if not addr:
        return False
    a = _normalize_ip(addr)
    if a in ("127.0.0.1", "::1", "localhost"):
        return True
    return a in _get_local_ips()


def _effective_client_ip():
    """Real client IP, accounting for the trusted local Vite proxy.

    `start.sh` serves the production UI via `vite preview`, whose proxy forwards
    /api and /socket.io to Flask from 127.0.0.1 — so a LAN device's request would
    otherwise look local and bypass this guard entirely. The Vite proxy sets
    X-Forwarded-For (xfwd) with the originating client. We trust that header ONLY
    when the direct TCP peer is loopback (i.e. it came through our own local
    proxy). A LAN attacker connecting straight to the backend port has a
    non-loopback peer, so a forged X-Forwarded-For from them is ignored.

    We now also use a robust _is_localhost check (with own-machine-IP detection)
    so an operator reaching the UI via their own LAN IP/hostname still gets
    full localhost-like access for sensitive ops when no GUAARDVARK_API_KEY.
    """
    peer = request.remote_addr or ""
    if _is_localhost(peer):
        xff = request.headers.get('X-Forwarded-For', '')
        if xff:
            # Leftmost entry is the original client.
            return xff.split(',')[0].strip()
    return peer


def _is_protected():
    """Check if the current request targets a protected endpoint."""
    path = request.path
    for prefix in SAFE_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return False
    for prefix in PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return True
    for prefix in PROTECTED_FILE_PREFIXES:
        if path.startswith(prefix):
            return True
    if path.startswith('/api/files/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return True
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        for prefix in MUTATION_PROTECTED_PREFIXES:
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
        # No key configured — localhost-only access. Use the effective client IP
        # so a LAN device proxied through the local Vite preview is still treated
        # as remote (the proxy makes request.remote_addr loopback otherwise).
        client_ip = _effective_client_ip()
        if _is_localhost(client_ip):
            return None
        logger.warning(
            f"[AUTH] Blocked remote access to {request.path} from {client_ip}"
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
