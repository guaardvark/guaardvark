"""
LLM provider selection — local-first with an opt-in cloud layer.

Guaardvark defaults to local Ollama and stays 100% offline unless the operator
explicitly opts in. Two gates, in order:

1. A MASTER switch ``cloud_models_enabled`` (DB setting, default OFF) — the
   single "airplane mode" flip that allows ANY cloud provider at all. While it
   is off, ``get_active_provider()`` always returns Ollama regardless of what
   else is configured, so a fresh install is fully local until the operator
   turns this on.
2. A per-provider selection (currently Ollama | Mistral) plus that provider's
   API key being present.

Adding a new cloud provider later is a matter of extending ``CLOUD_PROVIDERS``
and giving it a client module shaped like ``mistral_provider`` — the master
toggle, the UI indicator, and the API all key off this registry.

Embeddings are deliberately NOT covered here — they ALWAYS stay on Ollama so the
RAG vector store stays consistent with what indexed it, even when chat is cloud.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from backend import config

logger = logging.getLogger(__name__)

OLLAMA = "ollama"
MISTRAL = "mistral"
OPENAI = "openai"

_PROVIDER_KEY = "llm_provider"
_MISTRAL_MODEL_KEY = "mistral_active_model"
_OPENAI_MODEL_KEY = "openai_active_model"
_CLOUD_ENABLED_KEY = "cloud_models_enabled"


# ---------------------------------------------------------------------------
# Cloud provider registry. id -> metadata. `available` is computed live from
# whether the provider's API key is configured. Add a provider here (+ a client
# module) and the master toggle / UI indicator / API pick it up automatically.
# ---------------------------------------------------------------------------
def _mistral_available() -> bool:
    return bool(config.MISTRAL_API_KEY)


def _openai_available() -> bool:
    if config.OPENAI_API_KEY:
        return True
    # Allow keyless OpenAI-compatible endpoints (e.g. local Ollama / vLLM on /v1).
    return bool(
        config.OPENAI_BASE_URL
        and config.OPENAI_BASE_URL != "https://api.openai.com/v1"
    )


CLOUD_PROVIDERS: Dict[str, Dict] = {
    MISTRAL: {
        "label": "Mistral (cloud)",
        "key_env": "MISTRAL_API_KEY",
        "available_fn": _mistral_available,
    },
    OPENAI: {
        "label": "OpenAI-compatible (cloud)",
        "key_env": "GUAARDVARK_OPENAI_API_KEY",
        "available_fn": _openai_available,
    },
}


# ---------------------------------------------------------------------------
# Generic settings access (mirrors llm_service's Setting usage)
# ---------------------------------------------------------------------------
def _get_setting(key: str) -> Optional[str]:
    try:
        from backend.models import Setting, db

        if db and Setting:
            row = db.session.get(Setting, key)
            if row and row.value:
                return row.value
    except Exception as e:  # noqa: BLE001 - best effort, no Flask ctx etc.
        logger.debug("llm_provider: could not read setting %s: %s", key, e)
    return None


def _set_setting(key: str, value: str) -> bool:
    try:
        from backend.models import Setting, db

        if not (db and Setting):
            return False
        row = db.session.get(Setting, key)
        if row:
            row.value = value
        else:
            db.session.add(Setting(key=key, value=value))
        db.session.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_provider: could not write setting %s: %s", key, e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Master cloud switch (the airplane-mode gate)
# ---------------------------------------------------------------------------
def cloud_models_enabled() -> bool:
    """Master switch. Default OFF — a fresh install is fully local until the
    operator opts in. Everything cloud is gated behind this."""
    return (_get_setting(_CLOUD_ENABLED_KEY) or "").strip().lower() in ("1", "true", "yes", "on")


def set_cloud_models_enabled(enabled: bool) -> bool:
    _set_setting(_CLOUD_ENABLED_KEY, "true" if enabled else "false")
    logger.info("Cloud models %s", "ENABLED" if enabled else "disabled")
    return bool(enabled)


def provider_available(provider: str) -> bool:
    """True when the provider's key is configured (independent of the master switch)."""
    meta = CLOUD_PROVIDERS.get((provider or "").strip().lower())
    return bool(meta and meta["available_fn"]())


# Back-compat alias used by the API/older callers.
def mistral_available() -> bool:
    return provider_available(MISTRAL)


# ---------------------------------------------------------------------------
# Active provider
# ---------------------------------------------------------------------------
def get_active_provider() -> str:
    """Return the active chat provider.

    Hard gate: if the master cloud switch is OFF, ALWAYS Ollama. Otherwise the
    stored choice, degrading back to Ollama if its key was removed — so a missing
    key or a flipped-off master switch can never wedge chat into a dead provider.
    """
    if not cloud_models_enabled():
        return OLLAMA
    provider = (_get_setting(_PROVIDER_KEY) or OLLAMA).strip().lower()
    if provider == OLLAMA:
        return OLLAMA
    if provider in CLOUD_PROVIDERS and provider_available(provider):
        return provider
    if provider in CLOUD_PROVIDERS:
        logger.warning("Provider '%s' selected but no API key set; using Ollama.", provider)
    return OLLAMA


def set_active_provider(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider != OLLAMA and provider not in CLOUD_PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'.")
    if provider != OLLAMA:
        if not cloud_models_enabled():
            raise ValueError("Cloud models are disabled. Enable them first (master toggle).")
        if not provider_available(provider):
            env = CLOUD_PROVIDERS[provider]["key_env"]
            raise ValueError(f"Cannot select {provider}: {env} is not configured.")
    _set_setting(_PROVIDER_KEY, provider)
    logger.info("LLM provider set to '%s'", provider)
    return provider


def cloud_active() -> bool:
    """True when a cloud (non-Ollama) provider is the active chat model. The UI
    uses this to show the 'data leaves your machine' indicator."""
    return get_active_provider() != OLLAMA


# ---------------------------------------------------------------------------
# Mistral model selection
# ---------------------------------------------------------------------------
def get_mistral_model() -> str:
    return _get_setting(_MISTRAL_MODEL_KEY) or config.MISTRAL_DEFAULT_MODEL


def set_mistral_model(model: str) -> str:
    model = (model or "").strip()
    if not model:
        raise ValueError("Mistral model name cannot be empty.")
    _set_setting(_MISTRAL_MODEL_KEY, model)
    return model


def is_mistral_active() -> bool:
    return get_active_provider() == MISTRAL


# ---------------------------------------------------------------------------
# OpenAI-compatible model selection
# ---------------------------------------------------------------------------
def get_openai_model() -> str:
    return _get_setting(_OPENAI_MODEL_KEY) or config.OPENAI_DEFAULT_MODEL


def set_openai_model(model: str) -> str:
    model = (model or "").strip()
    if not model:
        raise ValueError("OpenAI model name cannot be empty.")
    _set_setting(_OPENAI_MODEL_KEY, model)
    return model


def is_openai_active() -> bool:
    return get_active_provider() == OPENAI


def get_active_cloud_model() -> Optional[str]:
    """Return the model name for the active cloud provider, or None on Ollama."""
    p = get_active_provider()
    if p == MISTRAL:
        return get_mistral_model()
    if p == OPENAI:
        return get_openai_model()
    return None


# ---------------------------------------------------------------------------
# UI/state snapshot
# ---------------------------------------------------------------------------
def provider_state() -> Dict:
    """Everything the settings UI needs in one call."""
    enabled = cloud_models_enabled()
    providers: List[Dict] = [{"id": OLLAMA, "label": "Ollama (local)", "available": True, "cloud": False}]
    for pid, meta in CLOUD_PROVIDERS.items():
        providers.append({
            "id": pid,
            "label": meta["label"],
            "available": meta["available_fn"](),
            "key_env": meta["key_env"],
            "cloud": True,
        })
    return {
        "cloud_models_enabled": enabled,
        "provider": get_active_provider(),
        "cloud_active": cloud_active(),
        "mistral_model": get_mistral_model(),
        "openai_model": get_openai_model(),
        "providers": providers,
    }
