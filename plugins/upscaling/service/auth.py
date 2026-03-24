"""Bearer token authentication for Upscaling plugin."""
import secrets
from fastapi import Request, HTTPException

# Generated once at module import (service startup).
AUTH_TOKEN = secrets.token_urlsafe(32)


def verify_token(request: Request):
    """Check Authorization: Bearer <token> header.

    Raise 401 if missing or invalid.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")
