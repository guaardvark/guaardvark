"""Unit tests for the cluster hardening pass (liveness-aware routing,
model-aware HTTP proxy resolution, FleetMap address cache, AMD GPU gating).

Pure-logic only — no DB/app fixtures, so these run with the lightweight
dependency set (no postgres/celery needed).
"""
import backend.services.fleet_map as fm_mod
from backend.services.fleet_map import FleetMap
from backend.services.cluster_routing import (
    RoutingTableBuilder, RoutingTableStore, compute_fleet_hash,
)
from backend.services.cluster_proxy import ProxyTargetResolver


def _nvidia(node_id, vram, services, arch="x86_64"):
    return {"arch": arch, "gpu": {"vendor": "nvidia", "vram_mb": vram},
            "services": {s: {"installed": True} for s in services}}


def _amd(node_id, vram, services, arch="x86_64"):
    return {"arch": arch, "gpu": {"vendor": "amd", "vram_mb": vram},
            "services": {s: {"installed": True} for s in services}}


# ---- FleetMap liveness + address -----------------------------------

def test_fleetmap_online_defaults_true_for_unknown():
    fm = FleetMap()
    assert fm.is_online("never-seen") is True


def test_fleetmap_register_and_set_online():
    fm = FleetMap()
    fm.register("n1", {"services": {}})
    assert fm.is_online("n1") is True
    fm.set_online("n1", False)
    assert fm.is_online("n1") is False
    fm.register("n2", {"services": {}}, online=False)
    assert fm.is_online("n2") is False


def test_fleetmap_address_roundtrip():
    fm = FleetMap()
    assert fm.get_address("n1") is None
    fm.set_address("n1", "10.0.0.5", 5000)
    assert fm.get_address("n1") == ("10.0.0.5", 5000)


# ---- liveness-aware routing builder --------------------------------

def test_offline_node_demoted_from_primary():
    fm = FleetMap()
    fm.register("up", _nvidia("up", 16384, ["ollama"]))
    fm.register("down", _nvidia("down", 24576, ["ollama"]))  # bigger, would win
    fm.set_online("down", False)
    table = RoutingTableBuilder().build(fm, master_node_id="m")
    chat = table.routes["llm_chat"]
    assert chat.primary == "up"            # online node wins despite less VRAM
    assert "down" in chat.fallback         # offline node kept as last resort


def test_all_offline_yields_no_primary():
    fm = FleetMap()
    fm.register("a", _nvidia("a", 16384, ["ollama"]))
    fm.set_online("a", False)
    table = RoutingTableBuilder().build(fm, master_node_id="m")
    assert table.routes["llm_chat"].primary is None


def test_fleet_hash_changes_on_liveness_flip():
    fm = FleetMap()
    fm.register("a", _nvidia("a", 16384, ["ollama"]))
    fm.register("b", _nvidia("b", 24576, ["ollama"]))
    h1 = RoutingTableBuilder().build(fm, master_node_id="m").fleet_hash
    fm.set_online("b", False)
    h2 = RoutingTableBuilder().build(fm, master_node_id="m").fleet_hash
    assert h1 != h2  # offline transition must change the hash → triggers rebroadcast


def test_compute_fleet_hash_backcompat_without_online():
    profiles = {"a": {"x": 1}}
    # old call form still works and is stable
    assert compute_fleet_hash(profiles) == compute_fleet_hash(profiles)


def test_offline_node_excluded_from_parallel_workers():
    fm = FleetMap()
    fm.register("g1", _nvidia("g1", 16384, ["comfyui"]))
    fm.register("g2", _nvidia("g2", 24576, ["comfyui"]))
    fm.set_online("g2", False)
    table = RoutingTableBuilder().build(fm, master_node_id="m")
    worker_ids = {w.node_id for w in table.routes["image_generation"].workers}
    assert worker_ids == {"g1"}


# ---- AMD GPU gating -------------------------------------------------

def test_amd_gpu_with_vram_is_chat_eligible():
    fm = FleetMap()
    fm.register("amd", _amd("amd", 16384, ["ollama"]))
    table = RoutingTableBuilder().build(fm, master_node_id="m")
    assert table.routes["llm_chat"].primary == "amd"


def test_amd_gpu_without_vram_not_chat_eligible():
    fm = FleetMap()
    prof = _amd("amd", None, ["ollama"])
    fm.register("amd", prof)
    table = RoutingTableBuilder().build(fm, master_node_id="m")
    # llm_chat is not cpu_acceptable and AMD reports no VRAM → not GPU-eligible
    assert table.routes["llm_chat"].primary is None


# ---- model-aware HTTP proxy resolution (P2) -------------------------

def _install_singletons(fm, store):
    fm_mod._singleton = fm
    import backend.services.cluster_routing as cr_mod
    cr_mod._store_singleton = store


def test_resolver_prefers_node_with_model_loaded():
    fm = FleetMap()
    fm.register("a", _nvidia("a", 24576, ["ollama"]))  # more VRAM → static primary
    fm.register("b", _nvidia("b", 16384, ["ollama"]))
    fm.set_address("a", "10.0.0.1", 5000)
    fm.set_address("b", "10.0.0.2", 5000)
    fm.update_live_state("b", {"loaded_models": ["gemma4:e4b"]})

    store = RoutingTableStore(persist_path="/tmp/_test_routing_hardening.json")
    store.set(RoutingTableBuilder().build(fm, master_node_id="m"), persist=False)
    _install_singletons(fm, store)

    # Static table primary is "a" (most VRAM); model hint should flip it to "b".
    targets = list(ProxyTargetResolver().resolve(
        "llm_chat", store.get(), local_node_id="m", model_hint="gemma4:e4b"))
    first = targets[0]
    assert first is not None and first.node_id == "b"


def test_resolver_falls_back_to_static_route_without_hint():
    fm = FleetMap()
    fm.register("a", _nvidia("a", 24576, ["ollama"]))
    fm.register("b", _nvidia("b", 16384, ["ollama"]))
    fm.set_address("a", "10.0.0.1", 5000)
    fm.set_address("b", "10.0.0.2", 5000)
    store = RoutingTableStore(persist_path="/tmp/_test_routing_hardening2.json")
    store.set(RoutingTableBuilder().build(fm, master_node_id="m"), persist=False)
    _install_singletons(fm, store)
    targets = list(ProxyTargetResolver().resolve(
        "llm_chat", store.get(), local_node_id="m", model_hint=None))
    assert targets[0].node_id == "a"  # most-VRAM static primary


# ---- FleetMap-backed target resolution (no DB on hot path) ----------

def test_get_target_uses_fleetmap_address_and_online():
    fm = FleetMap()
    fm.register("a", _nvidia("a", 16384, ["ollama"]))
    fm.set_address("a", "10.0.0.9", 5050)
    fm_mod._singleton = fm
    t = ProxyTargetResolver()._get_target("a")
    assert t is not None and t.host == "10.0.0.9" and t.port == 5050
    fm.set_online("a", False)
    assert ProxyTargetResolver()._get_target("a") is None
