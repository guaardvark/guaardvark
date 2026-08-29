"""VRAM admission tests for OfflineImageGenerator.

Born from the 2026-06-10 chat image-gen OOM: a flat 3500MB estimate let the
free-VRAM check pass while Z-Image actually allocated 9.9GB into a card already
holding ~9.3GB of resident Ollama models. These tests pin the fix:
family-aware estimates + canonical Ollama eviction when the card is too full.
"""
from types import SimpleNamespace

import pytest

import backend.services.offline_image_generator as oig
import backend.services.gpu_resource_policy as grp


GB = 1024 * 1024 * 1024


@pytest.fixture
def gen():
    return oig.OfflineImageGenerator()


@pytest.fixture
def spy(monkeypatch, gen):
    """Record eviction + orchestrator calls; pretend we're on a CUDA box."""
    calls = {"evicted": 0, "requests": []}
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: calls.__setitem__("evicted", calls["evicted"] + 1) or True)

    class _Orch:
        def request_model(self, slot_id, vram_estimate_mb, priority=50, **kw):
            calls["requests"].append((slot_id, vram_estimate_mb, priority))

    import backend.services.gpu_memory_orchestrator as gmo
    monkeypatch.setattr(gmo, "get_orchestrator", lambda: _Orch())
    monkeypatch.setattr(gen, "_device", "cuda")
    return calls


def _set_vram(monkeypatch, free_gb, total_gb=16):
    monkeypatch.setattr(oig.torch.cuda, "mem_get_info", lambda: (int(free_gb * GB), int(total_gb * GB)))


# --- Family-aware estimates (the 3500 flat estimate is dead) -----------------

def test_estimate_zimage(gen):
    assert gen._vram_estimate_mb("Tongyi-MAI/Z-Image-Turbo") == 11000


def test_estimate_krea2_hf_id_model_offload_peak(gen, monkeypatch):
    # High-VRAM path: model CPU offload, ~14GB peak (2026-07-11 measurement)
    monkeypatch.setattr(gen, "_cuda_total_vram_gb", lambda: 24.0)
    monkeypatch.setattr(gen, "_force_sequential_offload", False)
    assert gen._vram_estimate_mb("krea/Krea-2-Turbo") == 14000


def test_estimate_krea2_catalog_key_sequential_16gb(gen, monkeypatch):
    # Consumer cards use sequential offload — lower admission estimate so
    # require_fit (estimate+1GB margin) still admits on a 16GB card.
    monkeypatch.setattr(gen, "_cuda_total_vram_gb", lambda: 16.0)
    monkeypatch.setattr(gen, "_force_sequential_offload", False)
    assert gen._vram_estimate_mb("krea2-turbo") == 10000


def test_model_family_krea2_catalog_key(gen):
    assert gen._model_family("krea2-turbo") == "krea2"
    assert gen._model_family("krea2-raw") == "krea2"


def test_krea2_variant_raw_vs_turbo(gen):
    assert gen._krea2_variant("krea2-raw") == "raw"
    assert gen._krea2_variant("krea/Krea-2-Raw") == "raw"
    assert gen._krea2_variant("krea2-turbo") == "turbo"


def test_krea2_raw_uses_negative_prompt(gen):
    assert gen._skip_negative_prompt("krea2", "krea2-raw", 3.5) is False
    assert gen._skip_negative_prompt("krea2", "krea2-turbo", 0.0) is True


def test_apply_family_sampling_krea2_raw(gen):
    from backend.services.offline_image_generator import ImageGenerationRequest
    req = ImageGenerationRequest(prompt="test", model="krea2-raw")
    gen._apply_family_sampling(req, "krea2")
    assert req.num_inference_steps == 52
    assert req.guidance_scale == 3.5


def test_apply_family_sampling_krea2_turbo(gen):
    from backend.services.offline_image_generator import ImageGenerationRequest
    req = ImageGenerationRequest(prompt="test", model="krea2-turbo")
    gen._apply_family_sampling(req, "krea2")
    assert req.num_inference_steps == 8
    assert req.guidance_scale == 0.0


def test_apply_family_sampling_zimage_hard_defaults(gen):
    """Fallback path hard-applies official HF recipe."""
    from backend.services.offline_image_generator import ImageGenerationRequest
    req = ImageGenerationRequest(
        prompt="test", model="zimage-turbo", num_inference_steps=3, guidance_scale=5.0
    )
    gen._apply_family_sampling(req, "zimage")
    assert req.num_inference_steps == 9
    assert req.guidance_scale == 0.0


def test_soft_clamp_zimage_preserves_user_steps(gen):
    """Primary path must not clobber user/preset steps (e.g. High or slider)."""
    from backend.services.offline_image_generator import ImageGenerationRequest
    req = ImageGenerationRequest(
        prompt="test", model="zimage-turbo", num_inference_steps=12, guidance_scale=0.0
    )
    gen._soft_clamp_family_sampling(req, "zimage")
    assert req.num_inference_steps == 12
    assert req.guidance_scale == 0.0


def test_soft_clamp_zimage_fixes_out_of_range(gen):
    from backend.services.offline_image_generator import ImageGenerationRequest
    req = ImageGenerationRequest(
        prompt="test", model="zimage-turbo", num_inference_steps=99, guidance_scale=9.0
    )
    gen._soft_clamp_family_sampling(req, "zimage")
    assert req.num_inference_steps == 9
    assert req.guidance_scale == 0.0


def test_ram_estimate_krea2(gen):
    assert gen._ram_estimate_gb("krea2-turbo") == 24.0


def test_auto_vram_uses_offload_turbo_default(gen, monkeypatch):
    # Consumer auto-router prefers zimage → 11GB estimate
    monkeypatch.setattr(gen, "_prefer_krea2_for_auto", lambda: False)
    assert gen._vram_estimate_mb("auto") == 11000


def test_estimate_sdxl(gen):
    assert gen._vram_estimate_mb("stabilityai/stable-diffusion-xl-base-1.0") == 8000


def test_estimate_sd_family_default(gen):
    # SD-class models fall through _FAMILY_VRAM_MB to the 4000 MB default.
    # (Was SD 1.5 before it was removed from the system, 2026-08-07.)
    assert gen._vram_estimate_mb("SG161222/Realistic_Vision_V5.1_noVAE") == 4000


def test_ram_estimate_zimage(gen):
    # The base is the family constant, not a number copied into the test:
    # 2f0f522 lowered it 24 -> 21 (measured after the ladder/unload leak fixes)
    # and this assertion kept saying 24 for weeks.
    assert gen._ram_estimate_gb("Tongyi-MAI/Z-Image-Turbo") == oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]


def test_ram_estimate_sdxl(gen):
    assert gen._ram_estimate_gb("stabilityai/stable-diffusion-xl-base-1.0") == 10.0


def test_ram_estimate_sd_family_default(gen):
    assert gen._ram_estimate_gb("SG161222/Realistic_Vision_V5.1_noVAE") == 6.0


# --- Eviction decision --------------------------------------------------------

def test_tight_vram_evicts_ollama_and_registers_real_estimate(gen, spy, monkeypatch):
    # The exact 2026-06-10 scenario: ~6GB free, Z-Image needs ~11GB.
    _set_vram(monkeypatch, free_gb=6)
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert spy["evicted"] == 1
    assert spy["requests"] == [("sd:pipeline", 11000, 85)]


def test_roomy_vram_does_not_evict(gen, spy, monkeypatch):
    # Negative case: 15GB free fits SDXL + margin — chat model must survive.
    _set_vram(monkeypatch, free_gb=15)
    gen._ensure_vram_for_pipeline("stabilityai/stable-diffusion-xl-base-1.0")
    assert spy["evicted"] == 0
    assert spy["requests"] == [("sd:pipeline", 8000, 85)]


def test_margin_tips_the_decision(gen, spy, monkeypatch):
    # 12GB free fits 11000MB raw but NOT with the 1.6GB margin (10% of 16GB).
    _set_vram(monkeypatch, free_gb=12)
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert spy["evicted"] == 1


def test_already_resident_pipeline_skips_admission(gen, spy, monkeypatch):
    _set_vram(monkeypatch, free_gb=1)
    gen._pipeline = object()
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert spy["evicted"] == 0
    assert spy["requests"] == []


def test_cpu_device_skips_vram_check_but_still_registers(gen, spy, monkeypatch):
    monkeypatch.setattr(gen, "_device", "cpu")
    monkeypatch.setattr(
        oig.torch.cuda, "mem_get_info",
        lambda: pytest.fail("mem_get_info must not be called on a CPU box"),
    )
    gen._ensure_vram_for_pipeline("SG161222/Realistic_Vision_V5.1_noVAE")
    assert spy["evicted"] == 0
    assert spy["requests"] == [("sd:pipeline", 4000, 85)]


def test_admission_failure_never_raises(gen, monkeypatch):
    # Orchestrator down, CUDA query exploding — generation must still proceed.
    monkeypatch.setattr(gen, "_device", "cuda")
    monkeypatch.setattr(oig.torch.cuda, "mem_get_info", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")  # must not raise


# --- img2img pipeline family routing (Z-Image uses transformer, not unet) ---

def _fake_txt2img_pipeline(**components):
    return SimpleNamespace(**components)


def test_build_img2img_zimage_uses_transformer(gen, monkeypatch):
    if oig.ZImageImg2ImgPipeline is None:
        pytest.skip("ZImageImg2ImgPipeline not available")

    captured = {}

    class _FakeZImg2Img:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(oig, "ZImageImg2ImgPipeline", _FakeZImg2Img)
    gen._pipeline = _fake_txt2img_pipeline(
        transformer="tr",
        vae="v",
        text_encoder="te",
        tokenizer="tok",
        scheduler="sch",
    )
    gen._build_img2img_pipeline("zimage")
    assert captured["transformer"] == "tr"
    assert "unet" not in captured


def test_build_img2img_sdxl_uses_dual_encoders(gen, monkeypatch):
    captured = {}

    class _FakeSdxlImg2Img:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(oig, "StableDiffusionXLImg2ImgPipeline", _FakeSdxlImg2Img)
    gen._pipeline = _fake_txt2img_pipeline(
        vae="v",
        text_encoder="te",
        text_encoder_2="te2",
        tokenizer="tok",
        tokenizer_2="tok2",
        unet="u",
        scheduler="sch",
    )
    gen._build_img2img_pipeline("sdxl")
    assert captured["unet"] == "u"
    assert captured["text_encoder_2"] == "te2"


def test_build_img2img_sd_uses_unet(gen, monkeypatch):
    captured = {}

    class _FakeSdImg2Img:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(oig, "StableDiffusionImg2ImgPipeline", _FakeSdImg2Img)
    gen._pipeline = _fake_txt2img_pipeline(
        vae="v",
        text_encoder="te",
        tokenizer="tok",
        unet="u",
        scheduler="sch",
    )
    gen._build_img2img_pipeline("sd")
    assert captured["unet"] == "u"


# --- Resolution-scaled estimates (2026-08-04 client box 2048² incident) ----------
# Flat constants were calibrated at ~1024²; admission approved 2048² (4× the
# pixels) under the same numbers. Estimates now price extra megapixels above
# the 1MP calibration point; dims omitted ⇒ exactly the old constants.

def test_estimate_dims_none_matches_constants(gen):
    assert gen._vram_estimate_mb("Tongyi-MAI/Z-Image-Turbo") == 11000
    # The base is the family constant, not a number copied into the test:
    # 2f0f522 lowered it 24 -> 21 (measured after the ladder/unload leak fixes)
    # and this assertion kept saying 24 for weeks.
    assert gen._ram_estimate_gb("Tongyi-MAI/Z-Image-Turbo") == oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]


def test_estimate_1mp_matches_constants(gen):
    assert gen._vram_estimate_mb("Tongyi-MAI/Z-Image-Turbo", 1024, 1024) == 11000
    assert gen._ram_estimate_gb("Tongyi-MAI/Z-Image-Turbo", 1024, 1024) == oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]


def test_estimate_2048_adds_slope(gen):
    # 2048² = 4MP ⇒ 3 extra MP above the calibration point.
    assert gen._vram_estimate_mb("Tongyi-MAI/Z-Image-Turbo", 2048, 2048) == 11000 + 3 * 500
    base = oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]
    slope = oig.OfflineImageGenerator._FAMILY_RAM_SLOPE_GB_PER_MP.get("zimage", 1.0)
    assert gen._ram_estimate_gb("Tongyi-MAI/Z-Image-Turbo", 2048, 2048) == base + 3 * slope


def test_estimate_monotonic_in_resolution(gen):
    sizes = [(512, 512), (1024, 1024), (1448, 1448), (2048, 2048)]
    vram = [gen._vram_estimate_mb("Tongyi-MAI/Z-Image-Turbo", w, h) for w, h in sizes]
    ram = [gen._ram_estimate_gb("Tongyi-MAI/Z-Image-Turbo", w, h) for w, h in sizes]
    assert vram == sorted(vram)
    assert ram == sorted(ram)
    assert vram[0] == vram[1] == 11000  # sub-1MP never dips below the constant
