"""Flask @before_request hook — routes workload requests to remote nodes
when the routing table says so, else passes through to local handlers.

Runs for every request. Cheapest rejection paths checked first (cluster
off, ALWAYS_LOCAL prefix, no table). Wraps exceptions so a broken
cluster never 5xx's a solo Guaardvark.
"""
from __future__ import annotations

import logging
import os

from flask import current_app, request

log = logging.getLogger(__name__)


def cluster_proxy_before_request():
    try:
        if not current_app.config.get("CLUSTER_ENABLED", False):
            return None

        # Lazy imports — avoids triggering cluster_* module loading in solo mode
        from backend.services.cluster_proxy import (
            WorkloadClassifier, LoopDetector, ProxyTargetResolver,
            HttpProxyForwarder,
        )
        from backend.services.cluster_routing import get_routing_store

        if LoopDetector().should_force_local(request):
            log.debug("[ROUTE] loop detected at %s, forcing local", request.path)
            return None

        workload = WorkloadClassifier().classify(request.method, request.path)
        if workload is None:
            return None

        table = get_routing_store().get()
        if table is None:
            log.debug("[ROUTE] %s %s → local (no_table)", workload, request.path)
            return None

        local_id = os.environ.get("CLUSTER_NODE_ID", "unknown")
        model_hint = _extract_model_hint(request, workload)

        forwarder = HttpProxyForwarder()
        last_error = None
        for target in ProxyTargetResolver().resolve(
            workload, table, local_node_id=local_id, model_hint=model_hint,
        ):
            if target is None:
                log.info("[ROUTE] %s %s → local (exhausted, last_error=%s)",
                         workload, request.path, last_error)
                return None
            try:
                return forwarder.forward(target, workload, request)
            except Exception as e:
                last_error = str(e)
                log.warning("[ROUTE] %s → %s failed, trying next (%s)",
                            workload, target.node_id, e)
                continue
        return None
    except Exception as e:
        # Cluster middleware must never break the request path.
        log.exception("[CLUSTER] middleware error, falling back to local: %s", e)
        return None


def _extract_model_hint(request, workload: str) -> str | None:
    """For llm_chat, pull the requested model from the JSON body for
    model-aware routing. Best-effort; returns None if unparseable."""
    if workload != "llm_chat":
        return None
    try:
        data = request.get_json(silent=True) or {}
        return data.get("model")
    except Exception:
        return None
