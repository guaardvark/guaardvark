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
