"""HTTP client for the main backend's GPU memory orchestrator.

The orchestrator is a singleton living in the backend Flask process — its
in-process API can't be reached directly from this plugin (different Python
interpreter, different memory). The backend exposes the relevant operations
under /api/gpu/memory/*; this client wraps them so the dispatcher can reserve
VRAM, signal load completion, release on idle, and evict on load failure.

Every method is non-fatal: if the orchestrator is unreachable we log and
return False/None so the plugin still works (just without coordinated VRAM
arbitration). That mirrors the contract used by service/registration.py.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from service.config_loader import API_KEY_HEADER, backend_api_key, backend_url_is_own, resolve_backend_url

logger = logging.getLogger(__name__)

# Hard kill switch — overrides config.yaml's orchestrator_enabled. Set by the
# pytest conftest so test runs never accidentally talk to a real backend.
_ENV_DISABLE = "AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR"


class OrchestratorClient:
    """Thin HTTP wrapper around backend.api.gpu_orchestrator_api endpoints.

    `backend_url=None` resolves via config_loader.resolve_backend_url().
    """

    def __init__(
        self,
        backend_url: str | None = None,
        timeout_s: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._url = resolve_backend_url(backend_url).rstrip("/")
        self._own_backend = backend_url_is_own(backend_url)
        self._timeout = float(timeout_s)
        # Env var hard-disables regardless of constructor arg — for tests.
        if os.environ.get(_ENV_DISABLE) == "1":
            enabled = False
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def request_vram(
        self,
        slot_id: str,
        vram_mb: int,
        priority: int = 70,
        exclusive: bool = False,
    ) -> bool:
        """Reserve VRAM for slot_id; the orchestrator may evict other models first.

        When `exclusive=True`, the orchestrator will evict ALL other registered
        models AND force-unload any loaded Ollama models (via /api/ps +
        keep_alive=0) — required for big models like ACE-Step that can't share
        the GPU with anything heavy.

        Returns True on success, False if the orchestrator is unreachable or
        rejected the request. Caller should still attempt load() in the False
        case — best-effort degradation.
        """
        if not self._enabled:
            return True
        return self._post(
            "/api/gpu/memory/preload",
            {
                "slot_id": slot_id,
                "vram_mb": int(vram_mb),
                "priority": int(priority),
                "exclusive": bool(exclusive),
            },
            op="request_vram",
        )

    def mark_loaded(self, slot_id: str) -> bool:
        """Tell the orchestrator the model finished loading (LOADING -> LOADED)."""
        if not self._enabled:
            return True
        return self._post(
            "/api/gpu/memory/mark-loaded",
            {"slot_id": slot_id},
            op="mark_loaded",
        )

    def release(self, slot_id: str) -> bool:
        """Tell the orchestrator we're done with this model — eviction timer can start."""
        if not self._enabled:
            return True
        return self._post(
            "/api/gpu/memory/release",
            {"slot_id": slot_id},
            op="release",
        )

    def evict(self, slot_id: str) -> bool:
        """Force-remove a slot. Used in the load() failure path to clean up a LOADING slot."""
        if not self._enabled:
            return True
        return self._post(
            "/api/gpu/memory/evict",
            {"slot_id": slot_id},
            op="evict",
            missing_ok=True,
        )

    def gpu_holder(self) -> str | None:
        """The job holding the GPU exclusively (e.g. "video_render:VideoBatch_x"),
        or None when the card is free, the orchestrator is off, or the backend
        cannot say. An older backend without the route answers 404: None."""
        if not self._enabled:
            return None
        try:
            key = backend_api_key(self._own_backend)
            response = httpx.get(
                f"{self._url}/api/gpu/memory/holder",
                headers={API_KEY_HEADER: key} if key else None,
                timeout=self._timeout,
            )
            if response.status_code != 200:
                return None
            body = response.json()
            if body.get("held"):
                return str(body.get("holder") or "another job")
            return None
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("Orchestrator gpu_holder failed (non-fatal): %s", e)
            return None

    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any], op: str, missing_ok: bool = False) -> bool:
        """missing_ok: a 404 means the orchestrator does not track the slot,
        which is the outcome an evict wants, so it is not a failure."""
        try:
            key = backend_api_key(self._own_backend)
            response = httpx.post(
                f"{self._url}{path}",
                json=payload,
                headers={API_KEY_HEADER: key} if key else None,
                timeout=self._timeout,
            )
            if missing_ok and response.status_code == 404:
                logger.debug("Orchestrator %s: slot not tracked", op)
                return True
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.warning("Orchestrator %s failed (non-fatal): %s", op, e)
            return False
