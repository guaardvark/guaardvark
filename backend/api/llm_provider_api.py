"""REST API for the cloud-models layer: master toggle, provider selection, keys-status, test.

Local Ollama is always available. Cloud providers (currently Mistral) are gated
behind the master `cloud_models_enabled` switch (off by default) AND their API
key. Keys live in .env; only the toggle + selection state live in the DB.
"""
import logging

from flask import Blueprint, request

from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

llm_provider_bp = Blueprint("llm_provider", __name__, url_prefix="/api/llm")


@llm_provider_bp.route("/provider", methods=["GET"])
def get_provider():
    """Full state for the settings UI: master toggle, active provider, whether a
    cloud provider is live (the 'data leaves your machine' indicator), the
    provider list with per-provider key availability, and the active Mistral model."""
    from backend.services import llm_provider as lp
    return success_response(data=lp.provider_state())


@llm_provider_bp.route("/cloud-enabled", methods=["POST"])
def set_cloud_enabled():
    """Master switch. Body: {"enabled": true|false}. When turned off, chat
    immediately reverts to local Ollama regardless of provider selection."""
    from backend.services import llm_provider as lp
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    lp.set_cloud_models_enabled(enabled)
    return success_response(data=lp.provider_state(),
                            message=f"Cloud models {'enabled' if enabled else 'disabled'}")


@llm_provider_bp.route("/provider", methods=["POST"])
def set_provider():
    """Switch the active chat provider. Body: {"provider": "ollama"|"mistral"}.
    Rejects a cloud provider when the master toggle is off or its key is missing."""
    from backend.services import llm_provider as lp
    body = request.get_json(silent=True) or {}
    provider = body.get("provider", "")
    try:
        active = lp.set_active_provider(provider)
    except ValueError as e:
        return error_response(str(e), 400)
    return success_response(data=lp.provider_state(), message=f"LLM provider set to {active}")


@llm_provider_bp.route("/provider/models", methods=["GET"])
def list_provider_models():
    """List models for a provider (?provider=mistral; defaults to the active one)."""
    from backend.services import llm_provider as lp
    provider = (request.args.get("provider") or lp.get_active_provider()).strip().lower()
    if provider == lp.MISTRAL:
        if not lp.mistral_available():
            return error_response("Mistral API key not configured (set MISTRAL_API_KEY in .env).", 400)
        from backend.services import mistral_provider
        return success_response(data={"provider": provider, "models": mistral_provider.list_models()})
    if provider == lp.OPENAI:
        if not lp.provider_available(lp.OPENAI):
            return error_response(
                "OpenAI-compatible provider not configured (set GUAARDVARK_OPENAI_API_KEY / GUAARDVARK_OPENAI_BASE_URL in .env).",
                400,
            )
        from backend.services import openai_provider
        return success_response(data={"provider": provider, "models": openai_provider.list_models()})
    # Ollama listing already has a dedicated endpoint; point callers there.
    return success_response(data={"provider": provider, "models": [], "see": "/api/model/list"})


@llm_provider_bp.route("/provider/mistral-model", methods=["POST"])
def set_mistral_model():
    """Set the active Mistral model. Body: {"model": "mistral-large-latest"}."""
    from backend.services import llm_provider as lp
    body = request.get_json(silent=True) or {}
    try:
        model = lp.set_mistral_model(body.get("model", ""))
    except ValueError as e:
        return error_response(str(e), 400)
    return success_response(data={"mistral_model": model}, message=f"Mistral model set to {model}")


@llm_provider_bp.route("/provider/openai-model", methods=["POST"])
def set_openai_model():
    """Set the active OpenAI-compatible model. Body: {"model": "gpt-4o"}."""
    from backend.services import llm_provider as lp
    body = request.get_json(silent=True) or {}
    try:
        model = lp.set_openai_model(body.get("model", ""))
    except ValueError as e:
        return error_response(str(e), 400)
    return success_response(data={"openai_model": model}, message=f"OpenAI model set to {model}")


@llm_provider_bp.route("/provider/test", methods=["POST"])
def test_mistral():
    """Live round-trip against the ACTIVE cloud provider to confirm key/model work."""
    from backend.services import llm_provider as lp
    provider = lp.get_active_provider()
    if provider == lp.OLLAMA or provider not in lp.CLOUD_PROVIDERS:
        return error_response("No active cloud provider selected to test.", 400)
    if not lp.provider_available(provider):
        env = lp.CLOUD_PROVIDERS[provider]["key_env"]
        return error_response(
            f"{lp.CLOUD_PROVIDERS[provider]['label']} not configured (set {env} in .env).", 400)
    model = lp.get_active_cloud_model()
    if provider == lp.MISTRAL:
        from backend.services import mistral_provider as provider_mod
    else:
        from backend.services import openai_provider as provider_mod
    try:
        text = provider_mod.complete(
            "Reply with exactly: Connection successful",
            model=model,
        )
    except Exception as e:  # noqa: BLE001
        return error_response(f"{provider} request failed: {e}", 503)
    return success_response(data={"connected": True, "response": text, "model": model, "provider": provider})
