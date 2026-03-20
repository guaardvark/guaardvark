"""Vision Pipeline context utilities.

Fetches and formats vision context from the Vision Pipeline plugin
for injection into chat messages alongside RAG context.

Handles bearer token exchange: on first call, fetches token from
the plugin's /health endpoint and caches it for subsequent requests.
"""
import logging
import requests

logger = logging.getLogger(__name__)

VISION_PIPELINE_URL = "http://localhost:8201"
VISION_CONTEXT_TIMEOUT = 2  # seconds
VISION_ANALYZE_TIMEOUT = 30  # seconds

# Cached bearer token — fetched from plugin /health on first use
_cached_token: str | None = None


def _get_auth_token() -> str | None:
    """Fetch and cache the bearer token from the vision pipeline plugin."""
    global _cached_token
    if _cached_token:
        return _cached_token
    try:
        resp = requests.get(f"{VISION_PIPELINE_URL}/health", timeout=2)
        if resp.status_code == 200:
            _cached_token = resp.json().get("token")
            return _cached_token
    except Exception:
        pass
    return None


def _auth_headers() -> dict:
    """Return Authorization header if token is available."""
    token = _get_auth_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def get_vision_context() -> dict | None:
    """Fetch current vision context from the Vision Pipeline plugin.

    Returns None if plugin isn't running or no active stream.
    Safe to call on every chat message — fast timeout, silent failure.
    GET /context does not require auth (read-only, no sensitive data).
    """
    try:
        resp = requests.get(
            f"{VISION_PIPELINE_URL}/context",
            timeout=VISION_CONTEXT_TIMEOUT
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_active"):
                return data
    except Exception:
        pass
    return None


def format_vision_context(ctx: dict) -> str:
    """Format vision context for injection into context_parts."""
    confidence = ctx.get("confidence", "unknown").upper()
    parts = [f"[LIVE VISION FEED — {confidence}]"]
    parts.append(f"Current scene: {ctx.get('current_scene', 'unknown')}")

    recent = ctx.get("recent_changes", [])
    if recent:
        parts.append(f"Recent activity: {'; '.join(recent[-3:])}")

    summary = ctx.get("summary", "")
    if summary:
        parts.append(f"Earlier context: {summary}")

    parts.append(
        "(You can see the user's camera feed. "
        "Respond naturally to what you observe when relevant.)"
    )
    return "\n".join(parts)


def get_latest_frame() -> str | None:
    """Get the latest raw frame from the vision pipeline.

    Returns base64-encoded JPEG or None.
    """
    try:
        resp = requests.get(
            f"{VISION_PIPELINE_URL}/frame/latest",
            timeout=VISION_CONTEXT_TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json().get("frame")
    except Exception:
        pass
    return None


def get_direct_frame_analysis(frame_base64: str, prompt: str) -> str | None:
    """Synchronous analysis of a frame with a custom prompt.

    Uses the escalation model. 30s timeout — full inference.
    Requires bearer token (POST /analyze is authenticated).
    """
    try:
        resp = requests.post(
            f"{VISION_PIPELINE_URL}/analyze",
            json={"frame": frame_base64, "prompt": prompt},
            headers=_auth_headers(),
            timeout=VISION_ANALYZE_TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json().get("description")
    except Exception:
        pass
    return None
