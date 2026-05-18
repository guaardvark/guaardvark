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

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_URL = "http://localhost:5002"

# Hard kill switch — overrides config.yaml's orchestrator_enabled. Set by the
# pytest conftest so test runs never accidentally talk to a real backend.
_ENV_DISABLE = "AUDIO_FOUNDRY_DISABLE_ORCHESTRATOR"


class OrchestratorClient:
    """Thin HTTP wrapper around backend.api.gpu_orchestrator_api endpoints."""

    def __init__(
        self,
        backend_url: str = DEFAULT_BACKEND_URL,
        timeout_s: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._url = backend_url.rstrip("/")
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
        )

    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any], op: str) -> bool:
        try:
            response = httpx.post(f"{self._url}{path}", json=payload, timeout=self._timeout)
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.warning("Orchestrator %s failed (non-fatal): %s", op, e)
            return False
