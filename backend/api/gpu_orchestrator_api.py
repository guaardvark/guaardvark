"""
GPU Orchestrator API — REST endpoints for GPU memory management.

Provides:
    GET  /api/gpu/memory/status   — VRAM usage, loaded models, tier, eviction queue
    POST /api/gpu/memory/intent   — Frontend signals route navigation intent
    GET  /api/gpu/memory/tier     — Get current quality tier
    POST /api/gpu/memory/tier     — Set quality tier (speed/balanced/quality)
    POST /api/gpu/memory/evict    — Force-evict a specific model
    POST /api/gpu/memory/preload  — Manually preload a model

Auto-discovered by blueprint_discovery.py.
Note: /api/gpu is already used by gpu_api.py (coordinator), so this uses /api/gpu/memory.
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

gpu_orchestrator_bp = Blueprint("gpu_orchestrator_bp", __name__, url_prefix="/api/gpu/memory")


def _get_orch():
    from backend.services.gpu_memory_orchestrator import get_orchestrator
    return get_orchestrator()


@gpu_orchestrator_bp.route("/status", methods=["GET"])
def gpu_status():
    """Full GPU memory status snapshot."""
    try:
        snapshot = _get_orch().get_registry_snapshot()
        return jsonify(snapshot), 200
    except Exception as e:
        logger.error(f"GPU status error: {e}")
        return jsonify({"error": str(e)}), 500


@gpu_orchestrator_bp.route("/intent", methods=["POST"])
def gpu_intent():
    """Receive navigation intent from frontend. Triggers predictive model management."""
    data = request.get_json(silent=True) or {}
    route = data.get("route", "/")

    try:
        result = _get_orch().prepare_for_route(route)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"GPU intent error: {e}")
        return jsonify({"error": str(e)}), 500


@gpu_orchestrator_bp.route("/tier", methods=["GET"])
def get_tier():
    """Get the current quality tier and its config."""
    orch = _get_orch()
    return jsonify({
        "tier": orch.get_quality_tier(),
        "config": orch.get_tier_config(),
    }), 200


@gpu_orchestrator_bp.route("/tier", methods=["POST"])
def set_tier():
    """Set the quality tier (speed / balanced / quality)."""
    data = request.get_json(silent=True) or {}
    tier = data.get("tier", "").strip().lower()

    if not tier:
        return jsonify({"error": "Missing 'tier' field"}), 400

    result = _get_orch().set_quality_tier(tier)
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@gpu_orchestrator_bp.route("/evict", methods=["POST"])
def gpu_evict():
    """Force-evict a specific model from GPU memory."""
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id", "").strip()

    if not slot_id:
        return jsonify({"error": "Missing 'slot_id' field"}), 400

    success = _get_orch().force_evict(slot_id)
    if success:
        return jsonify({"success": True, "slot_id": slot_id}), 200
    else:
        return jsonify({"success": False, "error": f"Could not evict {slot_id} (not loaded or eviction failed)"}), 404


@gpu_orchestrator_bp.route("/preload", methods=["POST"])
def gpu_preload():
    """Request preloading a model. Registers intent but actual loading is up to the caller.

    Accepts `exclusive: bool` in the body — when true, evicts ALL other
    registered models AND force-unloads any Ollama models the orchestrator
    doesn't know about (the polish flow warms Ollama via direct HTTP, which
    bypasses the orchestrator's registry, so a registry-only eviction can
    leave a hot Ollama model camping 8-10 GB of VRAM).
    """
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id", "").strip()
    vram_mb = data.get("vram_mb", 4000)
    priority = data.get("priority", 50)
    exclusive = bool(data.get("exclusive", False))

    if not slot_id:
        return jsonify({"error": "Missing 'slot_id' field"}), 400

    try:
        # When the caller wants exclusive VRAM, drop any unregistered Ollama
        # models off the GPU first. Best-effort — failures here just log and
        # we proceed to request_model, which handles its own registry.
        if exclusive:
            _force_unload_ollama_models()

        slot = _get_orch().request_model(
            slot_id,
            vram_estimate_mb=vram_mb,
            priority=priority,
            exclusive=exclusive,
        )
        return jsonify({"success": True, "slot": slot.to_dict()}), 200
    except Exception as e:
        logger.error(f"GPU preload error: {e}")
        return jsonify({"error": str(e)}), 500


def _force_unload_ollama_models() -> None:
    """Unload every model Ollama is currently holding.

    Ollama's `/api/ps` lists loaded models; sending a `keep_alive=0` chat
    request to each is its documented way to drop a model immediately. The
    orchestrator's normal eviction path can't do this because Ollama models
    warmed via direct HTTP (e.g. our music-prompt rewriter) don't get
    registered in the orchestrator's slot registry.

    All errors are swallowed and logged — this is a best-effort assist for
    callers asking for exclusive VRAM, not a critical correctness path.
    """
    try:
        import requests
        from backend.config import OLLAMA_BASE_URL
    except Exception as e:
        logger.warning("Force-unload Ollama: imports failed (%s); skipping", e)
        return

    try:
        ps = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=3)
        ps.raise_for_status()
        loaded = [m.get("name") for m in (ps.json() or {}).get("models", []) if m.get("name")]
    except Exception as e:
        logger.warning("Force-unload Ollama: /api/ps failed (%s); nothing to unload", e)
        return

    if not loaded:
        logger.info("Force-unload Ollama: no models currently loaded")
        return

    logger.info("Force-unload Ollama: dropping %d model(s): %s", len(loaded), ", ".join(loaded))
    for name in loaded:
        try:
            # `keep_alive: 0` evicts immediately. Empty messages keeps the call
            # cheap (no actual generation work), and stream=false makes the
            # call synchronous so we know the eviction has registered before
            # we move on to load the heavy model.
            requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={"model": name, "messages": [], "keep_alive": 0, "stream": False},
                timeout=10,
            )
        except Exception as e:
            logger.warning("Force-unload Ollama: %s drop failed (%s)", name, e)


@gpu_orchestrator_bp.route("/mark-loaded", methods=["POST"])
def gpu_mark_loaded():
    """Transition a slot from LOADING to LOADED — call after the model finished loading.

    Out-of-process callers (audio_foundry, future plugin services) need this
    because they ran request_model() over HTTP, ran their own load() locally,
    and now have to tell the orchestrator the load is complete.
    """
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id", "").strip()

    if not slot_id:
        return jsonify({"error": "Missing 'slot_id' field"}), 400

    _get_orch().mark_model_loaded(slot_id)
    return jsonify({"success": True, "slot_id": slot_id}), 200


@gpu_orchestrator_bp.route("/release", methods=["POST"])
def gpu_release():
    """Mark a model as no longer in active use. Does not unload — starts the eviction timer."""
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id", "").strip()

    if not slot_id:
        return jsonify({"error": "Missing 'slot_id' field"}), 400

    _get_orch().release_model(slot_id)
    return jsonify({"success": True, "slot_id": slot_id}), 200
