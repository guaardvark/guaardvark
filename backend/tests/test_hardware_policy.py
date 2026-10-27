from backend.services import hardware_policy as hp


# NOTE: blackwell(12.0) and hopper(9.0) both resolve through the single
# `major >= 9` branch in torch_channel — there is no separate >=12 path today.
# If Blackwell ever needs a distinct channel (e.g. cu130), split that branch
# AND give it its own assertion here.
def test_torch_channel_blackwell_is_cu128():
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": "12.0"}) == "cu128"


def test_torch_channel_hopper_is_cu128():
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": "9.0"}) == "cu128"


def test_torch_channel_ampere_ada_is_cu124():
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": "8.9"}) == "cu124"


def test_torch_channel_turing_is_cu118():
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": "7.5"}) == "cu118"


def test_torch_channel_pre_pascal_is_cpu():
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": "5.2"}) == "cpu"


def test_torch_channel_amd_is_rocm():
    assert hp.torch_channel({"vendor": "amd"}) == "rocm6.3"


def test_torch_channel_amd_honors_rocm_override():
    # The rocm_whl override is the function's main value-add over a constant.
    assert hp.torch_channel({"vendor": "amd"}, rocm_whl="rocm6.2") == "rocm6.2"


def test_torch_channel_malformed_compute_cap_is_cpu():
    # Module contract promises safe degradation (never crash) on bad input.
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": "bogus"}) == "cpu"
    assert hp.torch_channel({"vendor": "nvidia", "compute_cap": ""}) == "cpu"


def test_torch_channel_no_gpu_is_cpu():
    assert hp.torch_channel({"vendor": "none"}) == "cpu"


def test_torch_channel_missing_compute_cap_is_cpu():
    # Unknown capability must degrade safely, never crash.
    assert hp.torch_channel({"vendor": "nvidia"}) == "cpu"


def test_ollama_tuning_16gb_is_single_slot_with_two_models_resident():
    t = hp.ollama_tuning({"vendor": "nvidia", "vram_mb": 16311})
    assert t["NUM_PARALLEL"] == 1
    assert t["MAX_LOADED_MODELS"] == 2
    assert t["KV_CACHE_TYPE"] == "f16"
    assert t["FLASH_ATTENTION"] == 1
    assert t["KEEP_ALIVE"] == "15m"


def test_ollama_tuning_24gb_allows_two_slots():
    t = hp.ollama_tuning({"vendor": "nvidia", "vram_mb": 24564})
    assert t["NUM_PARALLEL"] == 2
    assert t["MAX_LOADED_MODELS"] == 2
    assert t["KV_CACHE_TYPE"] == "f16"  # constant must hold on the high-VRAM branch too


def test_ollama_tuning_nvidia_forces_cuda_over_vulkan():
    # Preserve the force-CUDA hardening through the rendered drop-in path.
    assert hp.ollama_tuning({"vendor": "nvidia", "vram_mb": 16311})["VULKAN"] == 0


def test_ollama_tuning_amd_keeps_vulkan():
    assert hp.ollama_tuning({"vendor": "amd", "vram_mb": 24000})["VULKAN"] == 1


def test_ollama_tuning_no_gpu_disables_gpu_knobs():
    t = hp.ollama_tuning({"vendor": "none"})
    assert t["NUM_PARALLEL"] == 1
    assert t["FLASH_ATTENTION"] == 0
    assert t["MAX_LOADED_MODELS"] == 1
    assert t["VULKAN"] == 0


def test_ollama_tuning_nvidia_zero_vram_degrades():
    # A misconfigured detector reporting vram_mb=0 must hit the degraded path.
    t = hp.ollama_tuning({"vendor": "nvidia", "vram_mb": 0})
    assert t["FLASH_ATTENTION"] == 0
    assert t["NUM_PARALLEL"] == 1


def test_model_tier_small_for_low_ram():
    t = hp.model_tier(ram_gb=6, gpu={"vendor": "none"}, arch="x86_64")
    assert t["chat"] == "qwen3.5:4b"
    assert t["embed"] == "nomic-embed-text"


def test_model_tier_tiny_for_four_gb_boards():
    assert hp.model_tier(ram_gb=3.8, gpu={"vendor": "none"}, arch="aarch64")["chat"] == "qwen3.5:2b"


def test_model_tier_small_for_aarch64():
    t = hp.model_tier(ram_gb=32, gpu={"vendor": "none"}, arch="aarch64")
    assert t["chat"] == "qwen3.5:4b"


def test_model_tier_arm_wins_even_with_high_ram():
    # ARM forces the small tier regardless of RAM (proves the guard is unconditional).
    t = hp.model_tier(ram_gb=128, gpu={"vendor": "none"}, arch="aarch64")
    assert t["chat"] == "qwen3.5:4b"


GB10 = {"vendor": "nvidia", "vram_mb": None, "compute_cap": "12.1", "unified_memory_gb": 119.7}


def test_model_tier_standard_for_arm_with_nvidia_gpu():
    # GB10 / DGX Spark: aarch64 with an NVIDIA GPU is not a Raspberry Pi.
    assert hp.model_tier(ram_gb=119.7, gpu=GB10, arch="aarch64")["chat"] == "gemma4:12b"


def test_model_tier_small_arm_nvidia_board_stays_small():
    # A Jetson-class board with 8 GB still gets the small model, by its memory.
    jetson = {"vendor": "nvidia", "vram_mb": None, "unified_memory_gb": 7.4}
    assert hp.model_tier(ram_gb=7.4, gpu=jetson, arch="aarch64")["chat"] == "qwen3.5:4b"


def test_ollama_tuning_uses_unified_memory_when_vram_unreported():
    t = hp.ollama_tuning(GB10)
    assert t["FLASH_ATTENTION"] == 1
    assert t["NUM_PARALLEL"] == 2 and t["MAX_LOADED_MODELS"] == 2
    assert hp.gpu_memory_mb(GB10) == int(119.7 * 1024)


def test_model_tier_standard_for_normal_box():
    t = hp.model_tier(ram_gb=125, gpu={"vendor": "nvidia", "vram_mb": 16311}, arch="x86_64")
    assert t["chat"] == "gemma4:12b"
    assert t["embed"] == "nomic-embed-text"


def test_model_tier_small_when_the_card_has_no_room():
    # An 8 GB card, or no GPU, on a big machine: the standard model would not fit.
    assert hp.model_tier(ram_gb=32, gpu={"vendor": "nvidia", "vram_mb": 8188}, arch="x86_64")["chat"] == "qwen3.5:4b"
    assert hp.model_tier(ram_gb=32, gpu={"vendor": "none"}, arch="x86_64")["chat"] == "qwen3.5:4b"


def test_model_tier_apple_silicon_by_memory():
    mac = {"vendor": "apple", "accel": "mps", "vram_mb": None}
    assert hp.model_tier(ram_gb=16, gpu=mac, arch="arm64")["chat"] == "gemma4:12b"
    assert hp.model_tier(ram_gb=8, gpu=mac, arch="arm64")["chat"] == "qwen3.5:4b"


def test_is_stale_profile_flags_nvidia_without_compute_cap():
    # The exact failure on this box: an old cache lacking gpu.compute_cap.
    stale = {"gpu": {"vendor": "nvidia", "vram_mb": 16311}}  # no compute_cap
    assert hp._is_stale_profile(stale) is True


def test_is_stale_profile_accepts_current_nvidia():
    current = {"gpu": {"vendor": "nvidia", "vram_mb": 16311, "compute_cap": "12.0"}}
    assert hp._is_stale_profile(current) is False


def test_is_stale_profile_accepts_non_nvidia():
    # AMD/none profiles don't depend on compute_cap, so they're never stale here.
    assert hp._is_stale_profile({"gpu": {"vendor": "amd", "vram_mb": 24000}}) is False
    assert hp._is_stale_profile({"gpu": {"vendor": "none"}}) is False


def test_load_hardware_redetects_when_cache_stale(monkeypatch, tmp_path):
    import json
    from backend.services import hardware_policy as _hp
    stale = tmp_path / "hardware.json"
    stale.write_text(json.dumps({"gpu": {"vendor": "nvidia", "vram_mb": 16311}}))
    monkeypatch.setenv("GUAARDVARK_HARDWARE_JSON", str(stale))
    sentinel = {"gpu": {"vendor": "nvidia", "vram_mb": 16311, "compute_cap": "12.0"}}
    from backend.services.hardware_detector import HardwareDetector
    monkeypatch.setattr(HardwareDetector, "detect", lambda self: sentinel)
    # Stale cache must be rejected in favor of the live detect() result.
    assert _hp._load_hardware() == sentinel


def test_load_hardware_detects_when_no_cache_file(monkeypatch, tmp_path):
    from backend.services import hardware_policy as _hp
    monkeypatch.setenv("GUAARDVARK_HARDWARE_JSON", str(tmp_path / "does_not_exist.json"))
    sentinel = {"gpu": {"vendor": "nvidia", "vram_mb": 16311, "compute_cap": "12.0"}}
    from backend.services.hardware_detector import HardwareDetector
    monkeypatch.setattr(HardwareDetector, "detect", lambda self: sentinel)
    assert _hp._load_hardware() == sentinel


def test_policy_fingerprint_stable_and_sensitive():
    hw_a = {"arch": "x86_64", "gpu": {"vendor": "nvidia", "compute_cap": "12.0", "vram_mb": 16311}}
    hw_b = {"arch": "x86_64", "gpu": {"vendor": "nvidia", "compute_cap": "8.9", "vram_mb": 16311}}
    fp_a1 = hp.policy_fingerprint(hw_a)
    fp_a2 = hp.policy_fingerprint(dict(hw_a))
    assert fp_a1 == fp_a2                 # stable for identical hardware
    assert hp.policy_fingerprint(hw_b) != fp_a1   # different compute_cap -> different channel -> different fp


def test_ollama_tuning_12gb_holds_one_model():
    t = hp.ollama_tuning({"vendor": "nvidia", "vram_mb": 12288})
    assert t["NUM_PARALLEL"] == 1
    assert t["MAX_LOADED_MODELS"] == 1


# ---- training_fit: Settings > Training libraries ---------------------------------

NVIDIA_16GB = {"vendor": "nvidia", "vram_mb": 16311, "compute_cap": "12.0"}


def test_training_is_practical_on_a_16gb_nvidia_card_with_ram():
    fit = hp.training_fit(64, NVIDIA_16GB, "x86_64")
    assert fit["practical"] is True
    assert "16 GB" in fit["reason"]


def test_training_is_practical_at_the_8gb_floor():
    assert hp.training_fit(32, {"vendor": "nvidia", "vram_mb": 7680}, "x86_64")["practical"] is True


def test_training_is_not_practical_without_a_gpu():
    fit = hp.training_fit(64, {"vendor": "none"}, "x86_64")
    assert fit["practical"] is False
    assert "No GPU" in fit["reason"]


def test_training_is_not_practical_on_a_raspberry_pi():
    assert hp.training_fit(8, {"vendor": "none"}, "aarch64")["practical"] is False


def test_training_is_not_practical_on_a_card_under_8gb():
    fit = hp.training_fit(32, {"vendor": "nvidia", "vram_mb": 6144}, "x86_64")
    assert fit["practical"] is False
    assert "6.0 GB" in fit["reason"]


def test_training_is_not_practical_when_the_gpu_memory_is_unknown():
    assert hp.training_fit(32, {"vendor": "nvidia", "vram_mb": None}, "x86_64")["practical"] is False


def test_training_is_not_practical_with_8gb_of_ram_even_on_a_big_card():
    fit = hp.training_fit(8, NVIDIA_16GB, "x86_64")
    assert fit["practical"] is False
    assert "8 GB of memory" in fit["reason"]


def test_training_names_the_gpu_vendor_it_is_not_set_up_for():
    for vendor, word in (("amd", "AMD"), ("apple", "Apple"), ("intel", "Intel")):
        fit = hp.training_fit(64, {"vendor": vendor, "vram_mb": 24576}, "x86_64")
        assert fit["practical"] is False
        assert word in fit["reason"]


def test_training_fit_tolerates_a_missing_gpu_dict():
    assert hp.training_fit(0, None, "")["practical"] is False
