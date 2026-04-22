import time
from backend.services.fleet_map import FleetMap, get_fleet_map


def test_register_and_get_nodes_with_service():
    fm = FleetMap()
    fm.register("n1", {"arch": "x86_64",
                       "services": {"ollama": {"installed": True},
                                    "comfyui": {"installed": True}},
                       "gpu": {"vendor": "nvidia", "vram_mb": 16384}})
    fm.register("n2", {"arch": "aarch64",
                       "services": {"ollama": {"installed": True},
                                    "comfyui": {"installed": False}},
                       "gpu": {"vendor": "none"}})
    assert set(fm.get_nodes_with_service("ollama")) == {"n1", "n2"}
    assert set(fm.get_nodes_with_service("comfyui")) == {"n1"}


def test_get_gpu_capable_nodes_only_nvidia():
    fm = FleetMap()
    fm.register("n1", {"gpu": {"vendor": "nvidia", "vram_mb": 16384}, "services": {}})
    fm.register("n2", {"gpu": {"vendor": "amd", "vram_mb": 12000}, "services": {}})
    fm.register("n3", {"gpu": {"vendor": "none"}, "services": {}})
    assert fm.get_gpu_capable_nodes() == ["n1"]


def test_live_state_ttl():
    fm = FleetMap(live_state_ttl_s=0.05)
    fm.register("n1", {"services": {}})
    fm.update_live_state("n1", {"gpu": {"vram_free_mb": 10000}})
    assert fm.get_live_state("n1") is not None
    time.sleep(0.1)
    assert fm.get_live_state("n1") is None


def test_loaded_models_reports_from_live_state():
    fm = FleetMap()
    fm.register("n1", {"services": {"ollama": {"installed": True}}})
    fm.update_live_state("n1", {"loaded_models": ["gemma4:e4b", "qwen3-vl:2b"]})
    assert set(fm.get_nodes_with_model("gemma4:e4b")) == {"n1"}


def test_flap_tracking():
    fm = FleetMap()
    fm.register("n1", {"services": {}})
    assert fm.get_flap_count("n1") == 0
    fm.mark_flap("n1")
    fm.mark_flap("n1")
    fm.mark_flap("n1")
    assert fm.get_flap_count("n1") == 3


def test_fleet_summary_shape():
    fm = FleetMap()
    fm.register("n1", {"arch": "x86_64", "gpu": {"vendor": "nvidia", "vram_mb": 16384},
                       "ram": {"total_gb": 64}, "services": {"ollama": {"installed": True}}})
    summary = fm.get_fleet_summary()
    assert summary["node_count"] == 1
    assert summary["total_gpu_vram_mb"] == 16384
    assert summary["gpu_capable_nodes"] == ["n1"]
    assert "n1" in summary["service_map"]["ollama"]


def test_singleton_same_instance():
    assert get_fleet_map() is get_fleet_map()


def test_concurrent_register_safe():
    """Sanity — multiple threads registering don't crash or corrupt state."""
    import threading
    fm = FleetMap()
    errors = []
    def worker(i):
        try:
            fm.register(f"n{i}", {"services": {"ollama": {"installed": True}}})
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    assert len(fm.get_nodes_with_service("ollama")) == 20
