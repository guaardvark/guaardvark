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
