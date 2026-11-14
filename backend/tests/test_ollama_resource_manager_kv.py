"""Tests for per-architecture KV sizing and non-sticky num_ctx fallback."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.utils import ollama_resource_manager as orm


# Resources of a 16 GiB card at rest plus 60 GB RAM (~48 GB available).
GPU16_RESOURCES = {
    "gpu_free_mb": 14470.0,
    "gpu_total_mb": 16376.0,
    "ram_free_mb": 48000.0,
    "ram_total_mb": 60000.0,
}

# An 11.9B Q4_K_M model with 7206 MiB of weights and a very large native context.
GEMMA4_12B_INFO = {
    "size_mb": 7206.0,
    "parameter_count": 11_907_350_576,
    "native_context": 1_572_864,
    "architecture": "gemma4",
    "families": ["gemma4"],
    "quantization": "Q4_K_M",
    "is_vision": True,
    "capabilities": ["completion", "vision"],
}

# gemma4:e4b: 8.0B params, a 9163 MiB file (audio + vision towers included).
GEMMA4_E4B_INFO = dict(
    GEMMA4_12B_INFO,
    size_mb=9163.0,
    parameter_count=7_996_157_674,
    native_context=131_072,
)


# qwen3.5:4b as Ollama 0.40.15 describes it: 4.21B params, a 3,170 MiB file.
QWEN35_4B_INFO = {
    "size_mb": 3170.0,
    "parameter_count": 4_205_751_296,
    "native_context": 262_144,
    "architecture": "qwen35",
    "families": ["qwen35"],
    "quantization": "Q4_K_M",
    "is_vision": False,
    "capabilities": ["completion", "tools", "thinking"],
}
QWEN35_08B_INFO = dict(QWEN35_4B_INFO, size_mb=1261.0, parameter_count=752_393_024)
QWEN35_9B_INFO = dict(QWEN35_4B_INFO, size_mb=6297.0, parameter_count=9_197_093_888)

# An 8 GB machine without an NVIDIA card (7,834 MB total), with Guaardvark running.
CPU8_RESOURCES = {
    "gpu_free_mb": 0.0,
    "gpu_total_mb": 0.0,
    "ram_free_mb": 4400.0,
    "ram_total_mb": 7834.0,
}

MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _nothing_loaded(monkeypatch):
    """No model is loaded unless a test says so; sizing never reads the real /api/ps."""
    monkeypatch.setattr(orm, "resident_models", lambda ttl_s=orm.RESIDENT_TTL_S: [])


@pytest.fixture(autouse=True)
def _clean_caches():
    orm.clear_cache()
    orm._provisional_llms.clear()
    yield
    orm.clear_cache()
    orm._provisional_llms.clear()


class _FakeLlm:
    """Stand-in for a llama-index Ollama: assignable context_window, dict additional_kwargs."""

    def __init__(self, model, context_window):
        self.model = model
        self.context_window = context_window
        self.additional_kwargs = {"num_ctx": context_window}


def _show_response(model_info, details, ok=True, status=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.json.return_value = {"model_info": model_info, "details": details, "capabilities": []}
    return resp


GEMMA4_SHOW_INFO = {
    "gemma4.context_length": 1_572_864,
    "gemma4.rope.scaling.original_context_length": 262_144,
    "general.parameter_count": 11_907_350_576,
}
GEMMA4_SHOW_DETAILS = {"family": "gemma4", "families": ["gemma4"], "quantization_level": "Q4_K_M"}


class TestOverheadProfile:
    def test_gemma4_family_uses_measured_profile(self):
        profile = orm.overhead_profile("anything:latest", {"architecture": "gemma4"})
        assert profile is orm.OVERHEAD_PROFILES["gemma4"]

    def test_gemma4_matched_by_name_when_family_unknown(self):
        profile = orm.overhead_profile("vendor/gemma4-12b-custom:latest", {"architecture": "unknown"})
        assert profile is orm.OVERHEAD_PROFILES["gemma4"]

    def test_unknown_family_falls_back(self):
        profile = orm.overhead_profile("llama3:latest", {"architecture": "llama"})
        assert profile is orm.FALLBACK_OVERHEAD
        assert profile.mb_per_b_per_ctx == pytest.approx(0.08)

    def test_gemma4_estimate_is_far_below_flat_fallback(self):
        params = GEMMA4_12B_INFO["parameter_count"]
        gemma = orm._estimate_total_overhead_mb(params, 8192, orm.OVERHEAD_PROFILES["gemma4"])
        flat = orm._estimate_total_overhead_mb(params, 8192)
        assert flat == pytest.approx(11.907 * 8192 * 0.08, rel=1e-3)
        # Measured: ~1450 MiB above the weights at 8192 on this architecture.
        assert 1300 < gemma < 1700
        assert gemma < flat / 4

    def test_gemma4_estimate_tracks_measured_slope(self):
        params = GEMMA4_12B_INFO["parameter_count"]
        profile = orm.OVERHEAD_PROFILES["gemma4"]
        at_8k = orm._estimate_total_overhead_mb(params, 8192, profile)
        at_32k = orm._estimate_total_overhead_mb(params, 32768, profile)
        # Observed growth 8192 -> 32768 was 432 MiB; the profile must not undershoot it.
        assert 432 <= (at_32k - at_8k) < 600

    def test_e4b_estimate_does_not_undershoot_measured_growth(self):
        params = GEMMA4_E4B_INFO["parameter_count"]
        profile = orm.OVERHEAD_PROFILES["gemma4"]
        growth = (orm._estimate_total_overhead_mb(params, 32768, profile)
                  - orm._estimate_total_overhead_mb(params, 8192, profile))
        # Observed growth 8192 -> 32768 on e4b was 278 MiB.
        assert growth >= 278


class TestQwen35Profile:
    """Growth figures are the 2026-11-08 measurements recorded beside the profile."""

    def test_matched_by_family_and_by_name(self):
        profile = orm.OVERHEAD_PROFILES["qwen35"]
        assert orm.overhead_profile("anything:latest", {"architecture": "qwen35"}) is profile
        assert orm.overhead_profile("qwen3.5:4b", {"architecture": "unknown"}) is profile
        assert orm.overhead_profile("qwen3:8b", {"architecture": "qwen3"}) is orm.FALLBACK_OVERHEAD

    def test_4b_does_not_undershoot_measured_growth(self):
        params, profile = QWEN35_4B_INFO["parameter_count"], orm.OVERHEAD_PROFILES["qwen35"]
        est = lambda ctx: orm._estimate_total_overhead_mb(params, ctx, profile)
        assert est(16384) - est(2048) >= 524    # CPU: 3,004 -> 3,528 MB
        assert est(32768) - est(2048) >= 1096   # card: 3,831 -> 4,927 MiB

    def test_small_and_large_sizes_do_not_undershoot(self):
        profile = orm.OVERHEAD_PROFILES["qwen35"]
        for info, measured in ((QWEN35_08B_INFO, 570), (QWEN35_9B_INFO, 1126)):
            params = info["parameter_count"]
            growth = (orm._estimate_total_overhead_mb(params, 32768, profile)
                      - orm._estimate_total_overhead_mb(params, 2048, profile))
            assert growth >= measured

    def test_4b_estimate_is_far_below_flat_fallback(self):
        params = QWEN35_4B_INFO["parameter_count"]
        qwen = orm._estimate_total_overhead_mb(params, 8192, orm.OVERHEAD_PROFILES["qwen35"])
        assert qwen < orm._estimate_total_overhead_mb(params, 8192) / 5


class TestRamReserve:
    @pytest.mark.parametrize("total,reserve", [
        (7834.0, 7834.0 * 0.25),
        (4096.0, orm.RAM_RESERVE_MIN_MB),
        (32768.0, 8192.0),
        (65536.0, orm.RAM_RESERVE_MB),
        (0.0, orm.RAM_RESERVE_MB),
    ])
    def test_a_quarter_of_ram_within_bounds(self, total, reserve):
        assert orm.ram_reserve_mb(total) == pytest.approx(reserve)


class TestNumCtxOverride:
    """GUAARDVARK_NUM_CTX: a size set for a model replaces the sizing."""

    @pytest.fixture(autouse=True)
    def _no_override(self, monkeypatch):
        monkeypatch.delenv(orm.NUM_CTX_ENV, raising=False)

    def test_unset_leaves_the_sizing_alone(self):
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            sized = orm.decide_num_ctx("qwen3.5:4b")
        assert orm.num_ctx_override("qwen3.5:4b") is None
        assert sized.resolved and sized.num_ctx < orm.MAX_NUM_CTX

    def test_named_model_gets_its_size_above_the_ceiling(self, monkeypatch):
        monkeypatch.setenv(orm.NUM_CTX_ENV, "qwen3.5:4b=65536, gemma4:12b=16384")
        with patch.object(orm, "get_model_info", side_effect=AssertionError("not sized")):
            assert orm.decide_num_ctx("qwen3.5:4b") == orm.NumCtxDecision(65536, resolved=True)
            assert orm.decide_num_ctx("gemma4:12b").num_ctx == 16384

    def test_untagged_name_means_latest(self, monkeypatch):
        monkeypatch.setenv(orm.NUM_CTX_ENV, "Qwen3.8=32768")
        assert orm.num_ctx_override("qwen3.8:latest") == 32768
        assert orm.num_ctx_override("qwen3.8") == 32768
        assert orm.num_ctx_override("qwen3.8:27b") is None

    def test_other_models_are_still_sized(self, monkeypatch):
        monkeypatch.setenv(orm.NUM_CTX_ENV, "gemma4:12b=65536")
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx < orm.MAX_NUM_CTX

    def test_pre_load_check_recommends_the_set_size(self, monkeypatch):
        monkeypatch.setenv(orm.NUM_CTX_ENV, "qwen3.5:4b=65536")
        with patch.object(orm, "get_model_info", return_value=None):
            assert orm.validate_model_before_load("qwen3.5:4b")[2] == 65536
        # 3,170 MB of weights against the 8 GB machine's 2,442 MB budget: still
        # reported as not fitting, with the set size kept.
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            safe, _reason, num_ctx = orm.validate_model_before_load("qwen3.5:4b")
        assert not safe and num_ctx == 65536

    @pytest.mark.parametrize("raw", ["qwen3.5:4b", "qwen3.5:4b=", "qwen3.5:4b=big", "qwen3.5:4b=0", "=8192"])
    def test_malformed_entries_are_ignored(self, monkeypatch, raw):
        monkeypatch.setenv(orm.NUM_CTX_ENV, f"{raw},gemma4:12b=16384")
        assert orm.num_ctx_override("qwen3.5:4b") is None
        assert orm.num_ctx_override("gemma4:12b") == 16384


class TestSmallMachine:
    """The web UI's chat prompt measured 2,131-2,431 tokens (2026-11-08)."""

    def _loaded(self, monkeypatch, size_mb, vram_mb, ctx=8192):
        entry = {"name": "qwen3.5:4b", "model": "qwen3.5:4b", "context_length": ctx,
                 "size": int(size_mb * MIB), "size_vram": int(vram_mb * MIB)}
        monkeypatch.setattr(orm, "resident_models", lambda ttl_s=orm.RESIDENT_TTL_S: [entry])

    def test_floor_clears_the_ui_prompt(self):
        assert orm.MIN_NUM_CTX >= 4096

    def test_loaded_model_keeps_its_window_beside_itself(self, monkeypatch):
        self._loaded(monkeypatch, 3004, 320)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == orm.DEFAULT_TEXT_NUM_CTX

    def test_a_model_wholly_on_the_cpu_counts_too(self, monkeypatch):
        self._loaded(monkeypatch, 3004, 0)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == orm.DEFAULT_TEXT_NUM_CTX

    def test_another_loaded_model_is_not_counted(self, monkeypatch):
        entry = {"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest",
                 "size": 414 * MIB, "size_vram": 0}
        monkeypatch.setattr(orm, "resident_models", lambda ttl_s=orm.RESIDENT_TTL_S: [entry])
        monkeypatch.setattr(orm, "model_digests", lambda name, tags=None: frozenset())
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == orm.MIN_NUM_CTX

    def test_unloaded_model_on_8gb_gets_more_than_the_floor(self):
        resources = dict(CPU8_RESOURCES, ram_free_mb=5400.0)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=resources):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == 7168

    def test_its_own_memory_never_grows_a_loaded_model(self, monkeypatch):
        """7,168 unloaded and 8,192 loaded would reload the model on every change."""
        self._loaded(monkeypatch, 3004, 320, ctx=7168)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == 7168

    def test_a_model_loaded_below_the_floor_is_raised_to_it(self, monkeypatch):
        self._loaded(monkeypatch, 3004, 320, ctx=2048)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == orm.MIN_NUM_CTX

    def test_memory_freed_elsewhere_still_grows_a_loaded_model(self, monkeypatch):
        self._loaded(monkeypatch, 3004, 320, ctx=4096)
        roomy = dict(CPU8_RESOURCES, ram_free_mb=6000.0)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=roomy):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == orm.DEFAULT_TEXT_NUM_CTX

    def test_a_shrinking_budget_still_shrinks_a_loaded_model(self, monkeypatch):
        self._loaded(monkeypatch, 3004, 320, ctx=8192)
        tight = dict(CPU8_RESOURCES, ram_free_mb=2600.0)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=tight):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx < 8192

    def test_validation_sizes_from_the_same_memory(self, monkeypatch):
        self._loaded(monkeypatch, 3004, 320)
        with patch.object(orm, "get_model_info", return_value=QWEN35_4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=CPU8_RESOURCES):
            safe, _, num_ctx = orm.validate_model_before_load("qwen3.5:4b")
        assert safe is True
        assert num_ctx == orm.DEFAULT_TEXT_NUM_CTX

    def test_a_short_native_window_is_not_exceeded(self):
        info = dict(QWEN35_4B_INFO, native_context=2048)
        starved = dict(CPU8_RESOURCES, ram_free_mb=500.0)
        with patch.object(orm, "get_model_info", return_value=info), \
             patch.object(orm, "get_system_resources", return_value=starved):
            assert orm.decide_num_ctx("qwen3.5:4b").num_ctx == 2048
            assert orm.validate_model_before_load("qwen3.5:4b")[2] == 2048


class TestLoadedOnGpu:
    """A model sized while it is loaded on the GPU reads its own share as used:
    gemma4:12b on a 16 GB card got 32,768 before its load and 8,192 after."""

    BESIDE_ITSELF = dict(GPU16_RESOURCES, gpu_free_mb=7772.0)

    def _loaded(self, monkeypatch, ctx, size=1454553168, size_vram=1454553168):
        # size and size_vram as Ollama 0.40's llamacpp runner reports a loaded gemma4:12b.
        entry = {"name": "gemma4:12b", "model": "gemma4:12b", "context_length": ctx,
                 "size": size, "size_vram": size_vram}
        monkeypatch.setattr(orm, "resident_models", lambda ttl_s=orm.RESIDENT_TTL_S: [entry])

    def _decide(self):
        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=self.BESIDE_ITSELF):
            return orm.decide_num_ctx("gemma4:12b").num_ctx

    def test_it_keeps_the_window_it_is_loaded_with(self, monkeypatch):
        self._loaded(monkeypatch, 32768)
        assert self._decide() == 32768

    def test_it_does_not_grow_past_what_the_free_memory_gives(self, monkeypatch):
        self._loaded(monkeypatch, 8192)
        assert self._decide() == orm.DEFAULT_TEXT_NUM_CTX

    def test_an_oversized_load_is_capped(self, monkeypatch):
        self._loaded(monkeypatch, 262144)
        assert self._decide() == orm.MAX_NUM_CTX

    def test_a_model_split_with_the_cpu_is_sized_as_before(self, monkeypatch):
        self._loaded(monkeypatch, 32768, size=9000 * MIB, size_vram=6000 * MIB)
        assert self._decide() == orm.DEFAULT_TEXT_NUM_CTX

    def test_validation_recommends_the_same_window(self, monkeypatch):
        self._loaded(monkeypatch, 32768)
        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=self.BESIDE_ITSELF):
            safe, _, num_ctx = orm.validate_model_before_load("gemma4:12b")
        assert safe is True and num_ctx == 32768



class TestLoadInFlight:
    """Mid-load a model holds its memory but is not in /api/ps: sized then,
    gemma4:12b on a 16 GB card got 8,192 while a warm-up loaded it at 32,768."""

    MID_LOAD = dict(GPU16_RESOURCES, gpu_free_mb=6740.0)

    def _decide(self, monkeypatch):
        monkeypatch.setattr(orm, "resident_models", lambda ttl_s=orm.RESIDENT_TTL_S: [])
        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=self.MID_LOAD):
            return orm.decide_num_ctx("gemma4:12b").num_ctx

    def test_sizing_during_a_load_returns_the_window_being_loaded(self, monkeypatch):
        with orm.load_in_flight("gemma4:12b", 32768):
            assert self._decide(monkeypatch) == 32768

    def test_after_the_load_sizing_measures_again(self, monkeypatch):
        with orm.load_in_flight("gemma4:12b", 32768):
            pass
        assert self._decide(monkeypatch) == orm.DEFAULT_TEXT_NUM_CTX

    def test_another_model_is_not_affected(self, monkeypatch):
        with orm.load_in_flight("qwen3.5:9b", 32768):
            assert self._decide(monkeypatch) == orm.DEFAULT_TEXT_NUM_CTX

    def test_the_mark_is_cleared_when_the_request_fails(self, monkeypatch):
        try:
            with orm.load_in_flight("gemma4:12b", 32768):
                raise RuntimeError("load failed")
        except RuntimeError:
            pass
        assert orm._window_in_flight("gemma4:12b") == 0

    def test_validation_recommends_the_window_being_loaded(self, monkeypatch):
        monkeypatch.setattr(orm, "resident_models", lambda ttl_s=orm.RESIDENT_TTL_S: [])
        with orm.load_in_flight("gemma4:12b", 32768), \
             patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=self.MID_LOAD):
            safe, _, num_ctx = orm.validate_model_before_load("gemma4:12b")
        assert safe is True and num_ctx == 32768


class TestDecideNumCtx:
    def test_gemma4_12b_reaches_max_on_16gb_card(self):
        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            decision = orm.decide_num_ctx("gemma4:12b")
        assert decision.resolved is True
        assert decision.num_ctx == orm.MAX_NUM_CTX == 32768

    def test_gemma4_e4b_reaches_max_on_16gb_card(self):
        # Measured at 32768: 6826 MiB of 16376 in use, far inside the 2 GiB reserve.
        with patch.object(orm, "get_model_info", return_value=GEMMA4_E4B_INFO), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            decision = orm.decide_num_ctx("gemma4:e4b")
        assert decision.resolved is True
        assert decision.num_ctx == orm.MAX_NUM_CTX

    def test_unmeasured_family_same_size_stays_capped(self):
        info = dict(GEMMA4_12B_INFO, architecture="llama", families=["llama"])
        with patch.object(orm, "get_model_info", return_value=info), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            decision = orm.decide_num_ctx("someone/dense-12b:latest")
        assert decision.resolved is True
        assert decision.num_ctx == orm.DEFAULT_TEXT_NUM_CTX

    def test_compute_optimal_num_ctx_returns_the_decision_number(self):
        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            assert orm.compute_optimal_num_ctx("gemma4:12b") == orm.decide_num_ctx("gemma4:12b").num_ctx

    def test_native_context_still_caps(self):
        info = dict(GEMMA4_12B_INFO, native_context=16384)
        with patch.object(orm, "get_model_info", return_value=info), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            assert orm.decide_num_ctx("gemma4:12b").num_ctx == 16384


class TestNoModelInfoFallback:
    def test_none_info_is_not_resolved(self):
        with patch.object(orm, "get_model_info", return_value=None), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            decision = orm.decide_num_ctx("gemma4:12b")
        assert decision.resolved is False

    def test_none_info_does_not_probe_hardware(self):
        with patch.object(orm, "get_model_info", return_value=None), \
             patch.object(orm, "get_system_resources") as resources:
            orm.decide_num_ctx("gemma4:12b")
            orm.validate_model_before_load("gemma4:12b")
        resources.assert_not_called()

    def test_gemma4_placeholder_is_text_default_not_vision(self):
        with patch.object(orm, "get_model_info", return_value=None):
            decision = orm.decide_num_ctx("satgeze/gemma4-12b-uncensored-1.5m:latest")
        assert decision.num_ctx == orm.DEFAULT_TEXT_NUM_CTX

    def test_vision_only_placeholder_keeps_vision_default(self):
        with patch.object(orm, "get_model_info", return_value=None):
            decision = orm.decide_num_ctx("llava:13b")
        assert decision.num_ctx == orm.DEFAULT_VISION_NUM_CTX

    def test_validate_uses_the_same_placeholder(self):
        with patch.object(orm, "get_model_info", return_value=None):
            _, _, text_ctx = orm.validate_model_before_load("gemma4:12b")
            _, _, vision_ctx = orm.validate_model_before_load("llava:13b")
        assert text_ctx == orm.DEFAULT_TEXT_NUM_CTX
        assert vision_ctx == orm.DEFAULT_VISION_NUM_CTX

    def test_resolve_decision_swallows_errors_as_unresolved(self):
        with patch.object(orm, "decide_num_ctx", side_effect=RuntimeError("boom")):
            decision = orm.resolve_num_ctx_decision("gemma4:12b")
        assert decision == orm.NumCtxDecision(orm.DEFAULT_TEXT_NUM_CTX, resolved=False)

    def test_unreachable_ollama_is_not_hammered_but_retries_after_ttl(self):
        now = [1_000_000.0]
        with patch.object(orm.requests, "post", side_effect=requests.ConnectionError("refused")) as post, \
             patch.object(orm.time, "time", side_effect=lambda: now[0]):
            assert orm.get_model_info("gemma4:12b") is None
            assert orm.get_model_info("gemma4:12b") is None
            assert post.call_count == 1
            now[0] += orm._unreachable_ttl + 1
            assert orm.get_model_info("gemma4:12b") is None
            assert post.call_count == 2

    def test_missing_model_is_negative_cached_too(self):
        with patch.object(orm.requests, "post", return_value=_show_response({}, {}, ok=False, status=404)) as post:
            assert orm.get_model_info("not-pulled:latest") is None
            assert orm.get_model_info("not-pulled:latest") is None
        assert post.call_count == 1

    def test_clear_cache_resets_unreachable_backoff(self):
        with patch.object(orm.requests, "post", side_effect=requests.ConnectionError("refused")) as post:
            orm.get_model_info("gemma4:12b")
            orm.clear_cache()
            orm.get_model_info("gemma4:12b")
            assert post.call_count == 2


class TestModelInfoParsing:
    def test_native_context_prefers_exact_key_over_rope_original(self):
        with patch.object(orm.requests, "post", return_value=_show_response(GEMMA4_SHOW_INFO, GEMMA4_SHOW_DETAILS)), \
             patch.object(orm.requests, "get", side_effect=requests.ConnectionError("no tags")):
            info = orm.get_model_info("gemma4:12b")
        assert info["native_context"] == 1_572_864
        assert info["architecture"] == "gemma4"

    def test_native_context_falls_back_to_loose_key(self):
        show = {"llama.rope.scaling.original_context_length": 8192, "general.parameter_count": 8_000_000_000}
        with patch.object(orm.requests, "post", return_value=_show_response(show, {"family": "llama"})), \
             patch.object(orm.requests, "get", side_effect=requests.ConnectionError("no tags")):
            info = orm.get_model_info("llama3:8b")
        assert info["native_context"] == 8192


class TestRefreshContextWindow:
    def test_provisional_instance_is_re_resolved_once_info_arrives(self):
        llm = _FakeLlm("gemma4:12b", orm.DEFAULT_TEXT_NUM_CTX)
        orm.mark_provisional(llm, resolved=False)
        assert orm.is_provisional(llm)

        with patch.object(orm, "get_model_info", return_value=None):
            assert orm.refresh_context_window(llm) == orm.DEFAULT_TEXT_NUM_CTX
        assert orm.is_provisional(llm)

        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            assert orm.refresh_context_window(llm) == 32768
        assert llm.context_window == 32768
        assert llm.additional_kwargs["num_ctx"] == 32768
        assert not orm.is_provisional(llm)

    def test_first_successful_show_refreshes_waiting_instances(self):
        waiting = _FakeLlm("gemma4:12b", orm.DEFAULT_TEXT_NUM_CTX)
        other = _FakeLlm("llama3:8b", orm.DEFAULT_TEXT_NUM_CTX)
        orm.mark_provisional(waiting)
        orm.mark_provisional(other)

        with patch.object(orm.requests, "post", return_value=_show_response(GEMMA4_SHOW_INFO, GEMMA4_SHOW_DETAILS)) as post, \
             patch.object(orm.requests, "get", side_effect=requests.ConnectionError("no tags")), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            assert orm.get_model_info("gemma4:12b") is not None
        # The refresh sized from the cache: one /api/show request in total.
        assert post.call_count == 1
        assert waiting.context_window == 32768
        assert waiting.additional_kwargs["num_ctx"] == 32768
        assert not orm.is_provisional(waiting)
        assert other.context_window == orm.DEFAULT_TEXT_NUM_CTX
        assert orm.is_provisional(other)

    def test_resolved_instance_is_left_alone(self):
        llm = _FakeLlm("gemma4:12b", 8192)
        orm.mark_provisional(llm, resolved=True)
        assert not orm.is_provisional(llm)
        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            assert orm.refresh_context_window(llm) == 8192
        assert llm.context_window == 8192
        assert llm.additional_kwargs["num_ctx"] == 8192

    def test_refresh_never_raises_on_hot_path(self):
        llm = _FakeLlm("gemma4:12b", 8192)
        orm.mark_provisional(llm)
        with patch.object(orm, "decide_num_ctx", side_effect=RuntimeError("boom")):
            assert orm.refresh_context_window(llm) == 8192
        assert orm.is_provisional(llm)

    def test_untrackable_stand_in_is_tolerated(self):
        stand_in = object()
        orm.mark_provisional(stand_in)
        assert not orm.is_provisional(stand_in)
        assert orm.refresh_context_window(stand_in) == 0

    def test_build_ollama_marks_placeholder_and_refreshes(self):
        with patch.object(orm, "get_model_info", return_value=None):
            llm = orm.build_ollama("gemma4:12b", base_url="http://localhost:11434")
        assert llm.context_window == orm.DEFAULT_TEXT_NUM_CTX
        assert llm.additional_kwargs["num_ctx"] == orm.DEFAULT_TEXT_NUM_CTX
        assert orm.is_provisional(llm)

        with patch.object(orm, "get_model_info", return_value=GEMMA4_12B_INFO), \
             patch.object(orm, "get_system_resources", return_value=GPU16_RESOURCES):
            assert orm.refresh_context_window(llm) == 32768
        assert llm.context_window == 32768
        assert llm.additional_kwargs["num_ctx"] == 32768

    def test_build_ollama_explicit_context_window_is_final(self):
        with patch.object(orm, "get_model_info", return_value=None):
            llm = orm.build_ollama("gemma4:12b", base_url="http://localhost:11434", context_window=4096)
        assert llm.context_window == 4096
        assert not orm.is_provisional(llm)


class TestThinkingDefault:
    """Thinking is off unless a caller asks for it, and only for models that reason."""

    def test_name_pattern_answers_without_a_show_request(self):
        with patch.object(orm, "get_model_info", side_effect=AssertionError("no I/O")):
            assert orm.model_supports_thinking("gemma4:12b")
            assert orm.model_supports_thinking("qwen3.5:9b")
            assert orm.model_supports_thinking("deepseek-r1:8b")

    def test_capabilities_decide_for_an_unknown_name(self):
        with patch.object(orm, "get_model_info", return_value={"capabilities": ["completion", "thinking"]}):
            assert orm.model_supports_thinking("newfamily:7b")
        with patch.object(orm, "get_model_info", return_value={"capabilities": ["completion"]}):
            assert not orm.model_supports_thinking("newfamily:7b")

    def test_unreachable_ollama_means_no_flag(self):
        with patch.object(orm, "get_model_info", return_value=None):
            assert not orm.model_supports_thinking("llama3:latest")
            assert orm.thinking_kwargs("llama3:latest") == {}
            assert orm.think_payload("llama3:latest") == {}
        assert not orm.model_supports_thinking("")

    def test_kwarg_shapes_for_a_thinking_model(self):
        assert orm.thinking_kwargs("gemma4:12b") == {"thinking": False}
        assert orm.think_payload("gemma4:12b") == {"think": False}

    def test_build_ollama_turns_thinking_off_by_default(self):
        with patch.object(orm, "get_model_info", return_value=None):
            llm = orm.build_ollama("gemma4:12b", base_url="http://localhost:11434")
        assert llm.thinking is False

    def test_build_ollama_leaves_other_models_alone(self):
        with patch.object(orm, "get_model_info", return_value=None):
            llm = orm.build_ollama("llama3:latest", base_url="http://localhost:11434")
        assert llm.thinking is None

    def test_explicit_thinking_wins(self):
        with patch.object(orm, "get_model_info", return_value=None):
            on = orm.build_ollama("gemma4:12b", base_url="http://localhost:11434", thinking=True)
            model_default = orm.build_ollama("gemma4:12b", base_url="http://localhost:11434", thinking=None)
        assert on.thinking is True
        assert model_default.thinking is None
