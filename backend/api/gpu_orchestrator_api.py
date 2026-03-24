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
    """Request preloading a model. Registers intent but actual loading is up to the caller."""
    data = request.get_json(silent=True) or {}
    slot_id = data.get("slot_id", "").strip()
    vram_mb = data.get("vram_mb", 4000)
    priority = data.get("priority", 50)

    if not slot_id:
        return jsonify({"error": "Missing 'slot_id' field"}), 400

    try:
        slot = _get_orch().request_model(slot_id, vram_estimate_mb=vram_mb, priority=priority)
        return jsonify({"success": True, "slot": slot.to_dict()}), 200
    except Exception as e:
        logger.error(f"GPU preload error: {e}")
        return jsonify({"error": str(e)}), 500
