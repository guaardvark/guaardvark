"""Periodic Celery task — marks stale cluster nodes offline.

Runs only on master (guarded by CLUSTER_ROLE env var). Triggers a routing
table recompute when any node flips offline/online so workloads reroute
within the sweep interval + heartbeat timeout.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from celery import shared_task

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 15  # per spec §6.2


def _run_sweep(timeout_s: int) -> dict:
    """Core sweep logic — separated so it can run with an active app context."""
    from backend.models import db, InterconnectorNode
    from backend.services.fleet_map import get_fleet_map

    threshold = datetime.utcnow() - timedelta(seconds=timeout_s)

    marked_offline: list[str] = []
    marked_online: list[str] = []

    for node in InterconnectorNode.query.all():
        if node.last_heartbeat is None:
            continue
        is_stale = node.last_heartbeat < threshold
        if is_stale and node.online:
            node.online = False
            marked_offline.append(node.node_id)
            get_fleet_map().mark_flap(node.node_id)
            log.warning(
                "[CLUSTER] node %s marked offline (last_heartbeat=%s)",
                node.node_id,
                node.last_heartbeat.isoformat(),
            )
        elif not is_stale and not node.online:
            node.online = True
            marked_online.append(node.node_id)
            log.info("[CLUSTER] node %s back online", node.node_id)

    if marked_offline or marked_online:
        db.session.commit()
        try:
            from backend.services.cluster_routing import recompute_and_broadcast
            recompute_and_broadcast(reason="heartbeat_timeout")
        except ImportError:
            # Task 14 adds this helper; harmless during intermediate state
            pass

    return {"marked_offline": marked_offline, "marked_online": marked_online}


@shared_task(name="cluster.sweep_node_heartbeats")
def sweep_node_heartbeats() -> dict:
    if os.environ.get("CLUSTER_ROLE") != "master":
        return {"skipped": "not_master"}

    timeout_s = int(os.environ.get("CLUSTER_HEARTBEAT_TIMEOUT_S", DEFAULT_TIMEOUT_S))

    # In production, ContextTask wraps us in an app context automatically.
    # When called directly (tests, scripts), push one ourselves if needed.
    try:
        from flask import current_app
        # If this succeeds we already have an active context — run directly.
        current_app._get_current_object()  # raises RuntimeError if no context
        return _run_sweep(timeout_s)
    except RuntimeError:
        pass

    # No active app context — build a minimal one (mirrors celery_app pattern).
    from backend.celery_app import create_minimal_celery_flask_app
    minimal_app = create_minimal_celery_flask_app()
    with minimal_app.app_context():
        return _run_sweep(timeout_s)
