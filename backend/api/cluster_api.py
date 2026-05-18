# backend/api/cluster_api.py
"""Master-only cluster management endpoints.

GET  /api/cluster/routing-table            — current table as JSON
POST /api/cluster/routing-table/recompute  — force rebuild + rebroadcast
GET  /api/cluster/metrics                  — per-workload route counts (stub)
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

cluster_api_bp = Blueprint("cluster_api", __name__)


def _master_only():
    """Return a 403 tuple when not running as master, else None."""
    if os.environ.get("CLUSTER_ROLE") != "master":
        return jsonify({"error": "master-only endpoint"}), 403
    return None


@cluster_api_bp.route("/api/cluster/routing-table", methods=["GET"])
def get_routing_table():
    guard = _master_only()
    if guard:
        return guard
    from backend.services.cluster_routing import get_routing_store
    table = get_routing_store().get()
    if table is None:
        return jsonify({"error": "no table yet"}), 204
    return jsonify(table.to_dict())


@cluster_api_bp.route("/api/cluster/routing-table/recompute", methods=["POST"])
def post_recompute():
    guard = _master_only()
    if guard:
        return guard
    reason = (request.get_json(silent=True) or {}).get("reason", "manual")
    from backend.services.cluster_routing import recompute_and_broadcast
    recompute_and_broadcast(reason=reason)
    return jsonify({"ok": True, "reason": reason}), 200


@cluster_api_bp.route("/api/cluster/metrics", methods=["GET"])
def get_cluster_metrics():
    guard = _master_only()
    if guard:
        return guard
    # Stub for now — Phase 5 dashboard wires this up with real counters.
    # Returning the empty shape so clients can code against the contract today.
    return jsonify({
        "per_workload": {},
        "fallback_rate": 0.0,
        "median_proxy_latency_ms": 0,
    })
