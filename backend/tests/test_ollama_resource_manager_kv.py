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
