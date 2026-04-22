"""Cluster routing table — workload-to-node assignments.

The master runs RoutingTableBuilder.build() against a FleetMap snapshot to
produce a RoutingTable. Nodes cache the latest table (in memory + on disk)
and consult it to decide whether to handle a request locally or forward it.

This module is pure — no Flask, no SocketIO. Those integrations live in
socketio_events.py and cluster_api.py. The builder + store are added in
Task 12; this file contains only the data shapes + workload specs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Literal


# ---- workload specs --------------------------------------------------

WORKLOAD_SPECS: dict[str, dict[str, Any]] = {
    "llm_chat": {
        "mode": "singular",
        "services": ["ollama"],
        "min_vram_mb": 4096,
        "cpu_acceptable": False,
        "prefer": "loaded_model_then_most_vram_free",
        "allowed_archs": None,
    },
    "embeddings": {
        "mode": "singular",
        "services": ["ollama"],
        "min_vram_mb": 1024,
        "cpu_acceptable": True,
        "prefer": "cpu_first_then_most_vram_free",
        "allowed_archs": None,
    },
    "rag_search": {
        "mode": "singular",
        "services": ["ollama"],
        "min_vram_mb": 1024,
        "cpu_acceptable": True,
        "prefer": "co_locate_with_embeddings",
        "allowed_archs": None,
    },
    "video_generation": {
        "mode": "parallel",
        "services": ["comfyui"],
        "min_vram_mb": 12288,
        "cpu_acceptable": False,
        "weight_by": "benchmark_score_or_vram",
        "allowed_archs": ["x86_64"],
    },
    "image_generation": {
        "mode": "parallel",
        "services": ["comfyui"],
        "min_vram_mb": 8192,
        "cpu_acceptable": False,
        "weight_by": "benchmark_score_or_vram",
        "allowed_archs": ["x86_64"],
    },
    "voice_stt": {
        "mode": "singular",
        "services": ["whisper"],
        "min_vram_mb": None,
        "cpu_acceptable": True,
        "prefer": "cpu_first_then_any",
        "allowed_archs": None,
    },
    "voice_tts": {
        "mode": "singular",
        "services": ["piper"],
        "min_vram_mb": None,
        "cpu_acceptable": True,
        "prefer": "cpu_first_then_any",
        "allowed_archs": None,
    },
}


# ---- dataclasses ----------------------------------------------------

@dataclass
class WorkerSlot:
    node_id: str
    weight: float
    vram_mb: int | None = None


@dataclass
class WorkloadRoute:
    workload: str
    mode: Literal["singular", "parallel", "local"]
    primary: str | None
    fallback: list[str]
    workers: list[WorkerSlot] = field(default_factory=list)
    required_services: list[str] = field(default_factory=list)
    min_vram_mb: int | None = None
    cpu_acceptable: bool = False


@dataclass
class RoutingTable:
    routes: dict[str, WorkloadRoute]
    computed_at: datetime
    computed_by: str
    node_count: int
    fleet_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "routes": {k: asdict(v) for k, v in self.routes.items()},
            "computed_at": self.computed_at.isoformat(),
            "computed_by": self.computed_by,
            "node_count": self.node_count,
            "fleet_hash": self.fleet_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RoutingTable":
        routes = {}
        for k, v in d["routes"].items():
            workers = [WorkerSlot(**w) for w in v.get("workers", [])]
            route_kwargs = {**v, "workers": workers}
            routes[k] = WorkloadRoute(**route_kwargs)
        return cls(
            routes=routes,
            computed_at=datetime.fromisoformat(d["computed_at"]),
            computed_by=d["computed_by"],
            node_count=d["node_count"],
            fleet_hash=d["fleet_hash"],
        )


# ---- hashing helpers ------------------------------------------------

def stable_hash(obj: Any) -> str:
    """Stable sha1 over an arbitrary JSON-serializable object (sort_keys=True).
    Prevents spurious hash churn from dict-ordering differences."""
    return hashlib.sha1(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def compute_fleet_hash(profiles: dict[str, dict]) -> str:
    """sha1 over sorted (node_id, profile_hash) pairs — recomputes to the same
    value regardless of dict iteration order."""
    parts = [f"{nid}:{stable_hash(profile)}"
             for nid, profile in sorted(profiles.items())]
    return hashlib.sha1(",".join(parts).encode()).hexdigest()


# ---- imports used only by the builder/store section ----
import threading
from pathlib import Path
from backend.services.fleet_map import FleetMap


# ---- builder --------------------------------------------------------

class RoutingTableBuilder:
    FLAP_WINDOW_S = 1800
    FLAP_DEMOTE_THRESHOLD = 3

    def build(self, fleet: FleetMap, master_node_id: str) -> RoutingTable:
        profiles = {nid: fleet.get_profile(nid) for nid in fleet.get_all_node_ids()}
        routes: dict[str, WorkloadRoute] = {}
        singular_assignments: dict[str, str] = {}  # workload -> primary (for spread rule)

        for workload, spec in WORKLOAD_SPECS.items():
            candidates = self._filter_candidates(workload, spec, fleet, profiles)
            if not candidates:
                routes[workload] = WorkloadRoute(
                    workload=workload, mode="local", primary=None, fallback=[],
                    workers=[], required_services=spec["services"],
                    min_vram_mb=spec.get("min_vram_mb"),
                    cpu_acceptable=spec.get("cpu_acceptable", False),
                )
                continue

            stable = self._apply_presence_filter(candidates, fleet)
            demoted = [c for c in candidates if c not in stable]
            ordered = self._order_candidates(stable, spec, fleet, workload, routes)
            spread = self._apply_spread_rule(ordered, singular_assignments, spec, fleet)

            if spec["mode"] == "singular":
                primary = spread[0] if spread else None
                fallback = spread[1:] + demoted  # flappy nodes still usable as fallback
                routes[workload] = WorkloadRoute(
                    workload=workload, mode="singular", primary=primary,
                    fallback=fallback, workers=[],
                    required_services=spec["services"],
                    min_vram_mb=spec.get("min_vram_mb"),
                    cpu_acceptable=spec.get("cpu_acceptable", False),
                )
                if primary:
                    singular_assignments[workload] = primary
            else:  # parallel
                workers = self._weights_for_parallel(ordered, spec.get("weight_by"), profiles)
                routes[workload] = WorkloadRoute(
                    workload=workload, mode="parallel", primary=None,
                    fallback=[], workers=workers,
                    required_services=spec["services"],
                    min_vram_mb=spec.get("min_vram_mb"),
                    cpu_acceptable=spec.get("cpu_acceptable", False),
                )

        return RoutingTable(
            routes=routes,
            computed_at=datetime.utcnow(),
            computed_by=master_node_id,
            node_count=len(profiles),
            fleet_hash=compute_fleet_hash(profiles),
        )

    # ---- filters ---------------------------------------------------

    def _filter_candidates(self, workload, spec, fleet, profiles) -> list[str]:
        out = []
        is_parallel = spec.get("mode") == "parallel"
        for nid, profile in profiles.items():
            if profile is None:
                continue
            # service check
            services = profile.get("services", {}) or {}
            if not all(services.get(s, {}).get("installed") for s in spec["services"]):
                continue
            # arch check
            allowed = spec.get("allowed_archs")
            if allowed and profile.get("arch") not in allowed:
                continue
            # GPU gate — parallel workloads accept any GPU node with the right service;
            # singular workloads hard-filter by min_vram_mb.
            gpu = profile.get("gpu", {}) or {}
            min_vram = spec.get("min_vram_mb")
            cpu_ok = spec.get("cpu_acceptable", False)
            if is_parallel:
                # Must be a GPU node (cpu_acceptable is always False for parallel specs)
                if gpu.get("vendor") != "nvidia":
                    continue
            elif min_vram is not None:
                # needs a real GPU OR cpu_acceptable fallback
                if gpu.get("vendor") == "nvidia" and (gpu.get("vram_mb") or 0) >= min_vram:
                    pass  # GPU-eligible
                elif cpu_ok:
                    pass  # CPU-acceptable, GPU not required
                else:
                    continue  # no GPU and cpu not acceptable
            out.append(nid)
        return out

    def _apply_presence_filter(self, candidates: list[str], fleet: FleetMap) -> list[str]:
        """Returns candidates NOT excluded for primary slot. Flappy nodes are
        handled by the caller (merged into fallback chain)."""
        return [c for c in candidates
                if fleet.get_flap_count(c, self.FLAP_WINDOW_S) < self.FLAP_DEMOTE_THRESHOLD]

    # ---- ordering --------------------------------------------------

    def _order_candidates(self, candidates, spec, fleet, workload, routes_so_far) -> list[str]:
        prefer = spec.get("prefer")
        profiles = {nid: fleet.get_profile(nid) for nid in candidates}
        if prefer == "most_vram_free":
            return self._sort_by_vram_free(candidates, fleet)
        if prefer == "loaded_model_then_most_vram_free":
            # No specific model known at build time — sort by VRAM.
            # route_for_chat handles model-aware re-sort at query time.
            return self._sort_by_vram_free(candidates, fleet)
        if prefer == "cpu_first_then_most_vram_free":
            cpu_nodes = [c for c in candidates
                         if (profiles[c] or {}).get("gpu", {}).get("vendor") == "none"]
            gpu_nodes = [c for c in candidates if c not in cpu_nodes]
            cpu_nodes.sort(key=lambda c: (profiles[c] or {}).get("cpu", {}).get("cores", 0),
                           reverse=True)
            gpu_nodes = self._sort_by_vram_free(gpu_nodes, fleet)
            return cpu_nodes + gpu_nodes
        if prefer == "cpu_first_then_any":
            cpu_nodes = [c for c in candidates
                         if (profiles[c] or {}).get("gpu", {}).get("vendor") == "none"]
            gpu_nodes = [c for c in candidates if c not in cpu_nodes]
            cpu_nodes.sort(key=lambda c: (profiles[c] or {}).get("ram", {}).get("total_gb", 0),
                           reverse=True)
            return cpu_nodes + sorted(gpu_nodes)
        if prefer == "co_locate_with_embeddings":
            emb = routes_so_far.get("embeddings")
            emb_primary = emb.primary if emb else None
            if emb_primary in candidates:
                return [emb_primary] + [c for c in candidates if c != emb_primary]
            return self._sort_by_vram_free(candidates, fleet)
        return sorted(candidates)

    def _sort_by_vram_free(self, candidates, fleet) -> list[str]:
        def key(c):
            live = fleet.get_live_state(c)
            if live and "gpu" in live:
                free = live["gpu"].get("vram_free_mb")
                if free is not None:
                    return -free  # negative for desc sort
            profile = fleet.get_profile(c)
            return -(profile.get("gpu", {}).get("vram_mb") or 0) if profile else 0
        return sorted(candidates, key=key)

    # ---- spread ----------------------------------------------------

    def _apply_spread_rule(self, ordered, singular_assignments, spec, fleet: FleetMap):
        """Avoid stacking two singular workloads' primaries on the same node
        when alternatives exist. Skipped when the top candidate is genuinely a
        CPU node that cpu_first ordering put there intentionally — displacing it
        would sabotage the preference. GPU nodes at the top are fair game."""
        if spec.get("mode") != "singular" or len(ordered) < 2:
            return ordered
        already_primary = set(singular_assignments.values())
        if ordered[0] not in already_primary:
            return ordered
        # The top node is already claimed by another workload. Before swapping,
        # check if it's a CPU-preferred workload whose top pick is a real CPU node —
        # if so, spreading would undo the preference (e.g. voice_stt on a CPU box).
        prefer = spec.get("prefer", "")
        if prefer.startswith("cpu_first"):
            top_profile = fleet.get_profile(ordered[0]) or {}
            if top_profile.get("gpu", {}).get("vendor") == "none":
                return ordered  # intentional CPU placement — don't spread
        return [ordered[1], ordered[0]] + ordered[2:]

    # ---- parallel weights ------------------------------------------

    def _weights_for_parallel(self, candidates, weight_by, profiles) -> list[WorkerSlot]:
        raw = []
        for nid in candidates:
            profile = profiles.get(nid) or {}
            gpu = profile.get("gpu") or {}
            vram = gpu.get("vram_mb") or 0
            bench = profile.get("benchmark_score")
            score = (bench if (weight_by == "benchmark_score_or_vram" and bench)
                     else vram)
            raw.append((nid, float(score), vram))
        total = sum(s for _, s, _ in raw) or 1.0
        return [WorkerSlot(node_id=nid, weight=s / total, vram_mb=v)
                for nid, s, v in raw]


# ---- store ----------------------------------------------------------

_DEFAULT_PERSIST = "data/cluster/routing_table.json"


class RoutingTableStore:
    def __init__(self, persist_path: str = _DEFAULT_PERSIST):
        self._table: RoutingTable | None = None
        self._persist_path = persist_path
        self._lock = threading.RLock()

    def get(self) -> RoutingTable | None:
        with self._lock:
            return self._table

    def set(self, table: RoutingTable, persist: bool = True) -> None:
        with self._lock:
            self._table = table
            if persist:
                p = Path(self._persist_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(table.to_dict(), indent=2, sort_keys=True))

    def load_from_disk(self) -> bool:
        p = Path(self._persist_path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text())
            with self._lock:
                self._table = RoutingTable.from_dict(data)
            return True
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

    def route_for(self, workload: str) -> WorkloadRoute | None:
        with self._lock:
            if self._table is None:
                return None
            return self._table.routes.get(workload)

    def route_for_chat(self, model_name: str | None,
                       fleet: FleetMap | None = None) -> WorkloadRoute | None:
        """Specialization: if a model is specified and fleet is provided, prefer
        nodes that have the model loaded according to live-state."""
        base = self.route_for("llm_chat")
        if base is None or not model_name or fleet is None:
            return base
        has_model = set(fleet.get_nodes_with_model(model_name))
        if not has_model:
            return base  # cold pull required; caller may log
        all_nodes = ([base.primary] if base.primary else []) + base.fallback
        preferred = [n for n in all_nodes if n in has_model]
        others = [n for n in all_nodes if n not in has_model]
        if not preferred:
            return base
        return WorkloadRoute(
            workload=base.workload, mode=base.mode,
            primary=preferred[0],
            fallback=preferred[1:] + others,
            workers=base.workers, required_services=base.required_services,
            min_vram_mb=base.min_vram_mb, cpu_acceptable=base.cpu_acceptable,
        )


_store_singleton: RoutingTableStore | None = None
_store_lock = threading.Lock()


def get_routing_store() -> RoutingTableStore:
    global _store_singleton
    with _store_lock:
        if _store_singleton is None:
            _store_singleton = RoutingTableStore()
            _store_singleton.load_from_disk()
        return _store_singleton


# ---- master-side recompute + broadcast -----------------------------

def recompute_and_broadcast(reason: str = "manual") -> RoutingTable | None:
    """Master-only: rebuild the table from the current FleetMap and emit
    cluster:routing_table to the cluster:masters-broadcast room. No-op on
    workers or when fleet_hash is unchanged."""
    import os as _os
    import logging as _log
    _logger = _log.getLogger(__name__)

    if _os.environ.get("CLUSTER_ROLE") != "master":
        return None

    from backend.services.fleet_map import get_fleet_map
    from backend.socketio_instance import socketio

    master_id = _os.environ.get("CLUSTER_NODE_ID") or "unknown-master"
    table = RoutingTableBuilder().build(get_fleet_map(), master_node_id=master_id)
    store = get_routing_store()
    prev = store.get()
    if prev is not None and prev.fleet_hash == table.fleet_hash:
        _logger.debug("[CLUSTER] recompute skipped — fleet_hash unchanged (%s)",
                      table.fleet_hash)
        return prev

    store.set(table, persist=True)
    try:
        socketio.emit("cluster:routing_table", table.to_dict(),
                      to="cluster:masters-broadcast")
        _logger.info(
            "[CLUSTER] routing_table recomputed (reason=%s, fleet_hash=%s, nodes=%d)",
            reason, table.fleet_hash, table.node_count)
    except Exception as e:
        _logger.warning("[CLUSTER] broadcast failed: %s (table still persisted locally)", e)
    return table
