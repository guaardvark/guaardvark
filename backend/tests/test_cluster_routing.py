from backend.services.cluster_routing import (
    WorkerSlot, WorkloadRoute, RoutingTable, WORKLOAD_SPECS,
    stable_hash, compute_fleet_hash,
)


def test_workload_specs_keys():
    for key in ("llm_chat", "embeddings", "rag_search", "video_generation",
                "image_generation", "voice_stt", "voice_tts"):
        assert key in WORKLOAD_SPECS
        spec = WORKLOAD_SPECS[key]
        assert "mode" in spec
        assert spec["mode"] in ("singular", "parallel")
        assert "services" in spec
        assert "cpu_acceptable" in spec


def test_llm_chat_is_gpu_only():
    assert WORKLOAD_SPECS["llm_chat"]["cpu_acceptable"] is False
    assert WORKLOAD_SPECS["llm_chat"]["min_vram_mb"] == 4096


def test_embeddings_and_voice_cpu_acceptable():
    for key in ("embeddings", "voice_stt", "voice_tts"):
        assert WORKLOAD_SPECS[key]["cpu_acceptable"] is True


def test_video_image_restricted_to_x86():
    for key in ("video_generation", "image_generation"):
        assert WORKLOAD_SPECS[key]["allowed_archs"] == ["x86_64"]


def test_routing_table_round_trip():
    route = WorkloadRoute(workload="llm_chat", mode="singular",
                          primary="n1", fallback=["n2"], workers=[],
                          required_services=["ollama"], min_vram_mb=4096,
                          cpu_acceptable=False)
    from datetime import datetime
    t = RoutingTable(routes={"llm_chat": route}, computed_at=datetime.utcnow(),
                     computed_by="n1", node_count=2, fleet_hash="abc")
    d = t.to_dict()
    t2 = RoutingTable.from_dict(d)
    assert t2.routes["llm_chat"].primary == "n1"
    assert t2.fleet_hash == "abc"
    assert t2.computed_by == "n1"


def test_worker_slot_in_parallel_route():
    ws = [WorkerSlot(node_id="n1", weight=0.6, vram_mb=16384),
          WorkerSlot(node_id="n2", weight=0.4, vram_mb=12000)]
    r = WorkloadRoute(workload="video_generation", mode="parallel",
                     primary=None, fallback=[], workers=ws,
                     required_services=["comfyui"], min_vram_mb=12288,
                     cpu_acceptable=False)
    from datetime import datetime
    t = RoutingTable(routes={"video_generation": r},
                     computed_at=datetime.utcnow(),
                     computed_by="n1", node_count=2, fleet_hash="x")
    d = t.to_dict()
    t2 = RoutingTable.from_dict(d)
    assert len(t2.routes["video_generation"].workers) == 2
    assert t2.routes["video_generation"].workers[0].weight == 0.6


def test_stable_hash_sort_keys():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_compute_fleet_hash_deterministic():
    p1 = {"cpu": {"cores": 8}, "arch": "x86_64"}
    p2 = {"arch": "x86_64", "cpu": {"cores": 8}}
    h1 = compute_fleet_hash({"n1": p1})
    h2 = compute_fleet_hash({"n1": p2})
    assert h1 == h2


def test_compute_fleet_hash_different_for_different_fleets():
    a = compute_fleet_hash({"n1": {"arch": "x86_64"}})
    b = compute_fleet_hash({"n1": {"arch": "aarch64"}})
    assert a != b


import pytest
from backend.services.cluster_routing import (
    RoutingTableBuilder, RoutingTableStore, get_routing_store,
)
from backend.services.fleet_map import FleetMap


def _nvidia_node(nid, vram_mb, services, arch="x86_64", ram_gb=64):
    return {
        "arch": arch,
        "gpu": {"vendor": "nvidia", "vram_mb": vram_mb},
        "ram": {"total_gb": ram_gb},
        "services": {s: {"installed": True} for s in services},
        "cpu": {"cores": 8},
    }


def _cpu_node(nid, services, arch="x86_64", ram_gb=32):
    return {
        "arch": arch,
        "gpu": {"vendor": "none"},
        "ram": {"total_gb": ram_gb},
        "services": {s: {"installed": True} for s in services},
        "cpu": {"cores": 8},
    }


def test_build_singular_gpu_workload_prefers_most_vram():
    fm = FleetMap()
    fm.register("big",   _nvidia_node("big",   16384, ["ollama"]))
    fm.register("small", _nvidia_node("small", 12000, ["ollama"]))
    fm.update_live_state("big",   {"gpu": {"vram_free_mb": 12000}})
    fm.update_live_state("small", {"gpu": {"vram_free_mb": 8000}})
    t = RoutingTableBuilder().build(fm, master_node_id="big")
    chat = t.routes["llm_chat"]
    assert chat.primary == "big"
    assert chat.fallback == ["small"]


def test_build_cpu_workload_prefers_no_gpu_node():
    fm = FleetMap()
    fm.register("gpu-box", _nvidia_node("gpu-box", 16384, ["ollama", "whisper"]))
    fm.register("dell",    _cpu_node("dell", ["ollama", "whisper"]))
    t = RoutingTableBuilder().build(fm, master_node_id="gpu-box")
    assert t.routes["voice_stt"].primary == "dell"
    # llm_chat is cpu_acceptable=False, so CPU-only dell is excluded
    assert t.routes["llm_chat"].primary == "gpu-box"


def test_build_excludes_arch_mismatched_nodes():
    fm = FleetMap()
    fm.register("pi",  _cpu_node("pi", ["comfyui"], arch="aarch64"))
    fm.register("x86", _nvidia_node("x86", 16384, ["comfyui"]))
    t = RoutingTableBuilder().build(fm, master_node_id="x86")
    workers = [w.node_id for w in t.routes["video_generation"].workers]
    assert "pi" not in workers  # allowed_archs = ["x86_64"]
    assert "x86" in workers


def test_build_emits_local_when_no_capable_nodes():
    fm = FleetMap()
    fm.register("pi", _cpu_node("pi", ["whisper"]))  # no comfyui
    t = RoutingTableBuilder().build(fm, master_node_id="pi")
    assert t.routes["video_generation"].mode == "local"
    assert t.routes["video_generation"].primary is None


def test_parallel_weights_by_vram():
    fm = FleetMap()
    fm.register("big",   _nvidia_node("big",   16384, ["comfyui"]))
    fm.register("small", _nvidia_node("small", 12000, ["comfyui"]))
    t = RoutingTableBuilder().build(fm, master_node_id="big")
    weights = {w.node_id: w.weight for w in t.routes["video_generation"].workers}
    assert weights["big"] > weights["small"]
    assert abs(sum(weights.values()) - 1.0) < 0.001  # normalized


def test_presence_stability_excludes_flappy_from_primary():
    fm = FleetMap()
    fm.register("stable", _nvidia_node("stable", 16384, ["ollama"]))
    fm.register("flappy", _nvidia_node("flappy", 24576, ["ollama"]))
    for _ in range(4):
        fm.mark_flap("flappy")
    t = RoutingTableBuilder().build(fm, master_node_id="stable")
    assert t.routes["llm_chat"].primary == "stable"
    assert "flappy" in t.routes["llm_chat"].fallback


def test_spread_rule_avoids_stacking():
    fm = FleetMap()
    fm.register("big",   _nvidia_node("big",   24576, ["ollama"]))
    fm.register("small", _nvidia_node("small", 12000, ["ollama"]))
    t = RoutingTableBuilder().build(fm, master_node_id="big")
    assert t.routes["llm_chat"].primary == "big"
    assert t.routes["embeddings"].primary == "small"


def test_route_for_chat_prefers_loaded_model_node():
    from backend.services.cluster_routing import RoutingTable, WorkloadRoute
    from datetime import datetime
    route = WorkloadRoute(
        workload="llm_chat", mode="singular",
        primary="n1", fallback=["n2"], workers=[],
        required_services=["ollama"], min_vram_mb=4096, cpu_acceptable=False,
    )
    table = RoutingTable(routes={"llm_chat": route},
                         computed_at=datetime.utcnow(), computed_by="n1",
                         node_count=2, fleet_hash="x")
    store = RoutingTableStore()
    store.set(table, persist=False)

    fm = FleetMap()
    fm.register("n1", _nvidia_node("n1", 16384, ["ollama"]))
    fm.register("n2", _nvidia_node("n2", 24576, ["ollama"]))
    fm.update_live_state("n1", {"loaded_models": []})
    fm.update_live_state("n2", {"loaded_models": ["gemma4:e4b"]})

    chat_route = store.route_for_chat("gemma4:e4b", fleet=fm)
    assert chat_route.primary == "n2"
    assert chat_route.fallback == ["n1"]


def test_route_for_chat_without_model_returns_base():
    from backend.services.cluster_routing import RoutingTable, WorkloadRoute
    from datetime import datetime
    route = WorkloadRoute(
        workload="llm_chat", mode="singular", primary="n1", fallback=["n2"],
        workers=[], required_services=["ollama"], min_vram_mb=4096,
        cpu_acceptable=False,
    )
    table = RoutingTable(routes={"llm_chat": route}, computed_at=datetime.utcnow(),
                         computed_by="n1", node_count=2, fleet_hash="x")
    store = RoutingTableStore()
    store.set(table, persist=False)
    assert store.route_for_chat(None).primary == "n1"
    assert store.route_for_chat("").primary == "n1"


def test_store_persists_and_reloads(tmp_path):
    from backend.services.cluster_routing import RoutingTable, WorkloadRoute
    from datetime import datetime
    route = WorkloadRoute(workload="llm_chat", mode="singular", primary="n1",
                          fallback=[], workers=[], required_services=["ollama"],
                          min_vram_mb=4096, cpu_acceptable=False)
    table = RoutingTable(routes={"llm_chat": route},
                         computed_at=datetime.utcnow(), computed_by="n1",
                         node_count=1, fleet_hash="abc")
    store = RoutingTableStore(persist_path=str(tmp_path / "table.json"))
    store.set(table, persist=True)
    store2 = RoutingTableStore(persist_path=str(tmp_path / "table.json"))
    assert store2.load_from_disk() is True
    assert store2.get().routes["llm_chat"].primary == "n1"


def test_store_load_returns_false_when_missing(tmp_path):
    store = RoutingTableStore(persist_path=str(tmp_path / "missing.json"))
    assert store.load_from_disk() is False
    assert store.get() is None


def test_get_routing_store_singleton():
    assert get_routing_store() is get_routing_store()
