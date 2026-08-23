"""Server-side client heartbeat — keeps a worker node's liveness up to date
WITHOUT depending on a browser tab being open.

The React Interconnector settings page also sends heartbeats, but only while it
is open and focused (browsers throttle background timers). This periodic task
makes a client node self-register (once) and heartbeat to its master on a fixed
cadence from the backend, so a node stays "online" as long as its backend runs
— which is the behaviour every install expects.

Runs on every node; it early-returns cheaply unless this node is a configured,
enabled client with a master URL. Scheduled from celery_app beat_schedule.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time

import requests
from celery import shared_task

log = logging.getLogger(__name__)

# HardwareDetector.detect() probes CPU/GPU/disk (~200ms). Cache it so a 60s
# heartbeat doesn't re-probe every minute.
_profile_cache: dict = {"profile": None, "expires": 0.0}
_PROFILE_TTL_S = 600


def _load_config() -> dict:
    from backend.models import db, Setting
    try:
        setting = db.session.get(Setting, "interconnector_config")
        if setting and setting.value:
            return json.loads(setting.value) if isinstance(setting.value, str) else setting.value
    except Exception as exc:  # never let config read crash the beat task
        log.debug("[SYNC] client-heartbeat: config read failed: %s", exc)
    return {}


def _local_profile() -> dict:
    now = time.time()
    if _profile_cache["profile"] is not None and _profile_cache["expires"] > now:
        return _profile_cache["profile"]
    from backend.services.hardware_detector import HardwareDetector
    profile = HardwareDetector().detect()
    _profile_cache["profile"] = profile
    _profile_cache["expires"] = now + _PROFILE_TTL_S
    return profile


def _local_node_id(profile: dict) -> str:
    return os.environ.get("CLUSTER_NODE_ID") or (profile.get("node_id") if profile else "") or ""


def _register(master_url: str, headers: dict, node_id: str, node_name: str,
              profile: dict, config: dict) -> None:
    """Self-register with the master. Idempotent: the master keys on the
    machine-stable node_id (from hardware_profile), so this updates in place."""
    requests.post(
        f"{master_url}/api/interconnector/nodes/register",
        json={
            "node_id": node_id,
            "node_name": node_name,
            "node_mode": "client",
            "hardware_profile": profile,
            "port": int(os.environ.get("FLASK_PORT", 5000)),
            "sync_entities": config.get("sync_entities", []),
        },
        headers=headers,
        timeout=10,
    )


def _do_client_heartbeat() -> dict:
    config = _load_config()
    if not config.get("is_enabled") or config.get("node_mode") != "client":
        return {"skipped": "not_enabled_client"}
    master_url = (config.get("master_url") or "").rstrip("/")
    if not master_url:
        return {"skipped": "no_master_url"}

    profile = _local_profile()
    node_id = _local_node_id(profile)
    if not node_id:
        return {"skipped": "no_node_id"}

    node_name = config.get("node_name") or socket.gethostname()
    api_key = config.get("master_api_key") or ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    hb_url = f"{master_url}/api/interconnector/nodes/{node_id}/heartbeat"
    payload = {"hardware_profile": profile, "sync_entities": config.get("sync_entities", [])}
    try:
        resp = requests.post(hb_url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 404:
            # Master doesn't know this node yet — self-register, then heartbeat.
            log.info("[SYNC] client-heartbeat: node not registered on master; registering")
            _register(master_url, headers, node_id, node_name, profile, config)
            resp = requests.post(hb_url, json=payload, headers=headers, timeout=10)
        if resp.ok:
            log.debug("[SYNC] client-heartbeat: ok (node %s -> %s)", node_id, master_url)
            return {"ok": True, "node_id": node_id}
        return {"error": f"master_status_{resp.status_code}", "node_id": node_id}
    except requests.RequestException as exc:
        # Master unreachable is normal/transient — don't spam ERROR.
        log.debug("[SYNC] client-heartbeat: master unreachable: %s", exc)
        return {"error": "master_unreachable"}


@shared_task(name="interconnector.client_heartbeat")
def interconnector_client_heartbeat() -> dict:
    # Reuse the active app context in production; build a minimal one when
    # called directly (tests/scripts). Mirrors cluster_heartbeat_sweeper.
    try:
        from flask import current_app
        current_app._get_current_object()  # raises if no context
        return _do_client_heartbeat()
    except RuntimeError:
        pass
    from backend.celery_app import create_minimal_celery_flask_app
    with create_minimal_celery_flask_app().app_context():
        return _do_client_heartbeat()
