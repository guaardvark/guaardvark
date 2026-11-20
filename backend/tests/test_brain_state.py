#!/usr/bin/env python3
"""Tests for BrainState singleton — initialization, refresh, degradation, warm-up."""

import re
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from backend.services.brain_state import (
    BrainHealth,
    BrainState,
    ModelCapabilities,
    ReflexAction,
    ReflexResult,
    WarmUpStatus,
    _build_default_reflexes,
)
from backend.services.step_budget import StepBudget, TierTelemetry
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset BrainState singleton between tests."""
    BrainState.reset()
    yield
    BrainState.reset()


# ---------------------------------------------------------------------------
# Singleton behavior
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_instance_returns_same_object(self):
        a = BrainState.get_instance()
        b = BrainState.get_instance()
        assert a is b

    def test_reset_clears_instance(self):
        a = BrainState.get_instance()
        BrainState.reset()
        b = BrainState.get_instance()
        assert a is not b

    def test_thread_safety(self):
        """Multiple threads should get the same instance."""
        instances = []

        def grab():
            instances.append(id(BrainState.get_instance()))

        threads = [threading.Thread(target=grab) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(instances)) == 1


# ---------------------------------------------------------------------------
# ModelCapabilities
# ---------------------------------------------------------------------------

class TestModelCapabilities:
    def test_defaults(self):
        caps = ModelCapabilities()
        assert caps.name == ""
        assert caps.supports_native_tools is False
        assert caps.is_thinking_model is False
        assert caps.is_vision_model is False
        assert caps.context_window == 8192

    def test_custom_values(self):
        caps = ModelCapabilities(
            name="gemma4:12b",
            supports_native_tools=True,
            is_thinking_model=True,
            context_window=32768,
        )
        assert caps.supports_native_tools is True
        assert caps.is_thinking_model is True


# ---------------------------------------------------------------------------
# BrainHealth
# ---------------------------------------------------------------------------

class TestBrainHealth:
    def test_to_dict(self):
        health = BrainHealth(
            llm_available=True,
            tools_available=True,
            reflexes_loaded=True,
            warm_up_status=WarmUpStatus.READY,
        )
        d = health.to_dict()
        assert d["llm_available"] is True
        assert d["warm_up_status"] == "ready"
        assert d["degradation_reason"] is None

    def test_degradation_reason(self):
        health = BrainHealth(degradation_reason="Ollama is down")
        assert health.degradation_reason == "Ollama is down"
        assert health.llm_available is False


# ---------------------------------------------------------------------------
# WarmUpStatus
# ---------------------------------------------------------------------------

class TestWarmUpStatus:
    def test_all_states(self):
        assert WarmUpStatus.PENDING.value == "pending"
        assert WarmUpStatus.WARMING.value == "warming"
        assert WarmUpStatus.READY.value == "ready"
        assert WarmUpStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# TierTelemetry
# ---------------------------------------------------------------------------

class TestTierTelemetry:
    def test_hash_message_deterministic(self):
        h1 = TierTelemetry.hash_message("Hello World")
        h2 = TierTelemetry.hash_message("hello world")
        assert h1 == h2  # case-insensitive normalization

    def test_hash_message_strips_whitespace(self):
        h1 = TierTelemetry.hash_message("  hello  ")
        h2 = TierTelemetry.hash_message("hello")
        assert h1 == h2

    def test_hash_is_truncated(self):
        h = TierTelemetry.hash_message("test message")
        assert len(h) == 16

    def test_to_dict_includes_intent_when_set(self):
        t = TierTelemetry(
            tier=2, latency_ms=150, intent="social",
            success=True, model="llama3.1",
            timestamp="2026-04-05T12:00:00Z",
        )
        assert t.to_dict()["intent"] == "social"


# ---------------------------------------------------------------------------
# Reflex table building
# ---------------------------------------------------------------------------

class TestReflexTable:
    def test_no_social_reflexes_without_tools(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        names = [r.name for r in reflexes]
        assert names == []

    def test_sorted_by_priority(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        priorities = [r.priority for r in reflexes]
        assert priorities == sorted(priorities)

    def test_media_reflexes_with_mock_registry(self):
        registry = MagicMock()
        registry.get_tool.return_value = MagicMock()
        registry.execute_tool.return_value = MagicMock(
            success=True, output={"status": "playing"}
        )
        reflexes = _build_default_reflexes(tool_registry=registry)
        names = [r.name for r in reflexes]
        assert "media_play" in names
        assert "media_control" in names
        assert "media_volume" in names
        assert "media_status" in names


# ---------------------------------------------------------------------------
# BrainState.match_reflex
# ---------------------------------------------------------------------------

class TestMatchReflex:
    def test_hello_not_a_reflex(self):
        state = BrainState.get_instance()
        state.reflexes = _build_default_reflexes(tool_registry=None)
        state.health.reflexes_loaded = True
        assert state.match_reflex("hello") is None

    def test_no_match_for_complex_message(self):
        state = BrainState.get_instance()
        state.reflexes = _build_default_reflexes(tool_registry=None)
        state.health.reflexes_loaded = True

        result = state.match_reflex("Can you analyze this website for me?")
        assert result is None

    def test_no_match_for_empty(self):
        state = BrainState.get_instance()
        state.reflexes = _build_default_reflexes(tool_registry=None)

        result = state.match_reflex("")
        assert result is None

    def test_no_match_for_ambiguous_affirmation(self):
        """Bare 'yes' should NOT match a reflex -- it's context-dependent."""
        state = BrainState.get_instance()
        state.reflexes = _build_default_reflexes(tool_registry=None)

        result = state.match_reflex("yes")
        assert result is None


# ---------------------------------------------------------------------------
# Initialization with degradation
# ---------------------------------------------------------------------------

class TestInitializationDegradation:
    @patch("backend.services.brain_state.BrainState._detect_model_capabilities")
    @patch("backend.services.brain_state.BrainState._build_system_prompts")
    def test_initializes_without_tools(self, mock_prompts, mock_caps):
        """Should degrade gracefully if tool registry import fails."""
        state = BrainState.get_instance()

        with patch(
            "backend.tools.tool_registry_init.initialize_all_tools",
            side_effect=ImportError("no tools"),
        ):
            state.initialize(lite_mode=False)

        assert state.health.tools_available is False
        assert state.health.reflexes_loaded is True  # reflex table compiles (may be empty)
        assert state._initialized is True

    def test_lite_mode_skips_tools(self):
        state = BrainState.get_instance()

        with patch.object(state, "_detect_model_capabilities_lite"):
            with patch.object(state, "_build_system_prompts"):
                state.initialize(lite_mode=True)

        assert state.lite_mode is True
        assert state.health.tools_available is False
        assert state.health.reflexes_loaded is True

    def test_is_ready_false_before_init(self):
        state = BrainState.get_instance()
        assert state.is_ready is False

    def test_get_system_prompt_skip_tools(self):
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "BASE"}
        out = state.get_system_prompt(role="chat", skip_tools=True)
        assert "BASE" in out
        assert "TOOLS:" not in out


class TestWarmUpSkipped:
    """Processes that must not start loading the chat model."""

    def test_a_one_shot_flask_command_starts_no_warm_up(self, monkeypatch):
        monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
        state = BrainState.get_instance()
        with patch("backend.utils.process_role.one_shot_command", return_value=True), \
             patch("backend.services.brain_state.threading.Thread") as thread:
            state._start_warmup()
        thread.assert_not_called()
        assert state.health.warm_up_status == WarmUpStatus.READY

    def test_the_celery_worker_starts_no_warm_up(self, monkeypatch):
        monkeypatch.setenv("CELERY_WORKER_MODE", "true")
        state = BrainState.get_instance()
        with patch("backend.services.brain_state.threading.Thread") as thread:
            state._start_warmup()
        thread.assert_not_called()
        assert state.health.warm_up_status == WarmUpStatus.READY

    def test_the_server_starts_the_warm_up(self, monkeypatch):
        monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
        state = BrainState.get_instance()
        with patch("backend.utils.process_role.one_shot_command", return_value=False), \
             patch("backend.services.brain_state.threading.Thread") as thread:
            state._start_warmup()
        thread.return_value.start.assert_called_once()
        assert state.health.warm_up_status == WarmUpStatus.WARMING


# ---------------------------------------------------------------------------
# StepBudget (pure accounting)
# ---------------------------------------------------------------------------

class TestStepBudgetAccounting:
    def test_consume_and_is_exhausted(self):
        budget = StepBudget.from_total(3)
        assert budget.consume(1, 2, "test")
        assert budget.remaining == 2
        assert not budget.is_exhausted()
        budget.consume(2, 2, "finish")
        assert budget.is_exhausted()

    def test_from_hw_policy(self):
        hw = {"gpu": {"vram_mb": 12000}, "ram": {"total_gb": 8}, "arch": "x86_64"}
        budget = StepBudget.from_hw_policy(hw)
        assert budget.total <= 10  # low vram tight cap


class TestBudgetHWDerivedCap:
    def test_low_vram_tight_cap(self):
        hw = {"gpu": {"vram_mb": 8000, "compute_cap": "6.1"}, "ram": {"total_gb": 16}, "arch": "x86_64"}
        budget = StepBudget.from_hw_policy(hw)
        assert budget.total == 10  # from code logic

    def test_high_vram_loose_cap(self):
        hw = {"gpu": {"vram_mb": 32000}, "ram": {"total_gb": 32}, "arch": "x86_64"}
        budget = StepBudget.from_hw_policy(hw)
        assert budget.total >= 20

    def test_uses_model_tier_and_ollama_real(self):
        # Real call with hw dict (no mock for ollama/model_tier - they are internal)
        hw = {"gpu": {"vram_mb": 16000}, "ram": {"total_gb": 16}, "arch": "x86_64"}
        budget = StepBudget.from_hw_policy(hw)
        # should use real logic, expect reduced for small model or vram
        assert budget.total <= 20  # real behavior
        assert budget.total > 0

