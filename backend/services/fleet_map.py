"""Master-side fleet aggregation singleton.

Holds structured per-node hardware profiles + cached live-state. Feeds the
RoutingTableBuilder and the /api/cluster/fleet endpoint. Lives in process
memory — persistence is via InterconnectorNode rows in the DB; this just
aggregates them for fast lookups.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class FleetMap:
    def __init__(self, live_state_ttl_s: float = 30.0):
        self._profiles: dict[str, dict] = {}
        self._live_state: dict[str, tuple[float, dict]] = {}
        self._flap_history: dict[str, list[float]] = {}
        self._last_seen: dict[str, float] = {}
        self._lock = threading.RLock()
        self._ttl = live_state_ttl_s

    # ---- registration -----------------------------------------------

    def register(self, node_id: str, profile: dict[str, Any]) -> None:
        with self._lock:
            self._profiles[node_id] = profile
            self._last_seen[node_id] = time.time()

    def update_live_state(self, node_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._live_state[node_id] = (time.time(), state)
            self._last_seen[node_id] = time.time()

    def mark_flap(self, node_id: str) -> None:
        """Called by the heartbeat sweeper when a node transitions offline."""
        with self._lock:
            self._flap_history.setdefault(node_id, []).append(time.time())
            # Keep only last 30 min
            cutoff = time.time() - 1800
            self._flap_history[node_id] = [t for t in self._flap_history[node_id] if t > cutoff]

    # ---- queries ----------------------------------------------------

    def get_profile(self, node_id: str) -> dict | None:
        with self._lock:
            return self._profiles.get(node_id)

    def get_live_state(self, node_id: str) -> dict | None:
        with self._lock:
            entry = self._live_state.get(node_id)
            if entry is None:
                return None
            ts, state = entry
            if time.time() - ts > self._ttl:
                return None
            return state

    def get_nodes_with_service(self, service: str) -> list[str]:
        with self._lock:
            return [
                nid for nid, p in self._profiles.items()
                if p.get("services", {}).get(service, {}).get("installed") is True
            ]

    def get_gpu_capable_nodes(self) -> list[str]:
        """Returns nodes whose GPU vendor is 'nvidia'. AMD/Intel excluded v1."""
        with self._lock:
            return sorted(
                nid for nid, p in self._profiles.items()
                if p.get("gpu", {}).get("vendor") == "nvidia"
            )

    def get_nodes_with_model(self, model_name: str) -> list[str]:
        """Reads from live_state.loaded_models (populated by node_api /live-state)."""
        with self._lock:
            out = []
            for nid in self._profiles:
                entry = self._live_state.get(nid)
                if entry is None:
                    continue
                _ts, state = entry
                if model_name in (state.get("loaded_models") or []):
                    out.append(nid)
            return out

    def get_flap_count(self, node_id: str, window_s: float = 1800) -> int:
        with self._lock:
            cutoff = time.time() - window_s
            return sum(1 for t in self._flap_history.get(node_id, []) if t > cutoff)

    def get_all_node_ids(self) -> list[str]:
        with self._lock:
            return list(self._profiles.keys())

    def get_fleet_summary(self) -> dict[str, Any]:
        with self._lock:
            total_vram = sum(
                p.get("gpu", {}).get("vram_mb") or 0
                for p in self._profiles.values()
                if p.get("gpu", {}).get("vendor") == "nvidia"
            )
            total_ram = sum(p.get("ram", {}).get("total_gb") or 0 for p in self._profiles.values())
            service_map: dict[str, list[str]] = {}
            for nid, p in self._profiles.items():
                for svc, meta in (p.get("services") or {}).items():
                    if meta.get("installed"):
                        service_map.setdefault(svc, []).append(nid)
            return {
                "nodes": dict(self._profiles),
                "live_state": {nid: s for nid, (_, s) in self._live_state.items()},
                "node_count": len(self._profiles),
                "total_gpu_vram_mb": total_vram,
                "total_ram_gb": total_ram,
                "gpu_capable_nodes": self.get_gpu_capable_nodes(),
                "service_map": service_map,
            }


_singleton: FleetMap | None = None
_singleton_lock = threading.Lock()


def get_fleet_map() -> FleetMap:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = FleetMap()
        return _singleton
