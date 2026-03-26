"""Tests for guaardvark-guardian."""

import json
from unittest.mock import MagicMock, patch

import pytest

from guaardvark_guardian import Guardian, ReviewResult, Directive, RiskLevel, EscalationResult


class TestGuardianOffline:
    """Tests for Guardian behavior when Claude API is unavailable."""

    def test_init_without_api_key(self):
        g = Guardian()
        assert not g.available

    def test_init_with_empty_api_key(self):
        g = Guardian(api_key="")
        assert not g.available

    def test_escalation_offline(self):
        g = Guardian()
        result = g.escalate("What is this?")
        assert isinstance(result, EscalationResult)
        assert not result.available
        assert "not configured" in result.reason

    def test_review_change_offline(self):
        g = Guardian()
        result = g.review_change(
            file_path="app.py",
            current_content="print('hello')",
            proposed_diff="+print('world')",
            reasoning="Add greeting",
        )
        assert isinstance(result, ReviewResult)
        assert result.approved is True
        assert result.directive == Directive.PROCEED_WITH_CAUTION
        assert result.offline_fallback is True

    def test_advise_offline(self):
        g = Guardian()
        result = g.advise({"gpu_usage": 0.5})
        assert not result.available

    def test_usage_starts_at_zero(self):
        g = Guardian()
        assert g.usage["total_tokens"] == 0
        assert g.usage["monthly_budget"] == 1_000_000


class TestReviewResult:
    """Tests for ReviewResult type."""

    def test_from_dict_valid(self):
        data = {
            "approved": False,
            "directive": "reject",
            "risk_level": "high",
            "reason": "Dangerous change",
            "suggestions": ["Revert this"],
        }
        result = ReviewResult.from_dict(data)
        assert result.approved is False
        assert result.directive == Directive.REJECT
        assert result.risk_level == RiskLevel.HIGH
        assert result.reason == "Dangerous change"
        assert result.suggestions == ["Revert this"]
        assert result.offline_fallback is False

    def test_from_dict_invalid_directive_defaults(self):
        data = {"approved": True, "directive": "invalid_value", "risk_level": "low", "reason": "ok"}
        result = ReviewResult.from_dict(data)
        assert result.directive == Directive.PROCEED_WITH_CAUTION

    def test_from_dict_invalid_risk_defaults(self):
        data = {"approved": True, "directive": "proceed", "risk_level": "extreme", "reason": "ok"}
        result = ReviewResult.from_dict(data)
        assert result.risk_level == RiskLevel.UNKNOWN

    def test_offline_default(self):
        result = ReviewResult.offline_default("No connection")
        assert result.approved is True
        assert result.directive == Directive.PROCEED_WITH_CAUTION
        assert result.offline_fallback is True
        assert "No connection" in result.reason


class TestDirective:
    """Tests for Directive enum."""

    def test_emergency_directives(self):
        assert Directive.HALT_SELF_IMPROVEMENT.is_emergency
        assert Directive.LOCK_CODEBASE.is_emergency
        assert Directive.HALT_FAMILY.is_emergency

    def test_non_emergency_directives(self):
        assert not Directive.PROCEED.is_emergency
        assert not Directive.PROCEED_WITH_CAUTION.is_emergency
        assert not Directive.REJECT.is_emergency

    def test_string_values(self):
        assert Directive.PROCEED.value == "proceed"
        assert Directive.HALT_FAMILY.value == "halt_family"


class TestGuardianWithMockAPI:
    """Tests for Guardian with mocked Anthropic client."""

    def _make_guardian(self, **kwargs):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            g = Guardian(api_key="sk-ant-test-key", **kwargs)
        return g, mock_client

    def test_escalation_success(self):
        g, client = self._make_guardian()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="The answer is 42.")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        client.messages.create.return_value = mock_response

        result = g.escalate("What is the meaning of life?")
        assert result.available
        assert result.response == "The answer is 42."
        assert result.usage["input_tokens"] == 100
        assert g.usage["total_tokens"] == 150

    def test_review_change_approved(self):
        g, client = self._make_guardian()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "approved": True,
            "directive": "proceed",
            "risk_level": "low",
            "reason": "Looks good",
            "suggestions": [],
        }))]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 80
        client.messages.create.return_value = mock_response

        result = g.review_change(
            file_path="utils.py",
            current_content="def old(): pass",
            proposed_diff="+def new(): pass",
            reasoning="Rename function",
        )
        assert result.approved
        assert result.directive == Directive.PROCEED
        assert result.risk_level == RiskLevel.LOW

    def test_review_change_emergency_fires_callback(self):
        callback_calls = []

        def on_directive(directive, review):
            callback_calls.append((directive, review))

        g, client = self._make_guardian(on_directive=on_directive)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "approved": False,
            "directive": "halt_self_improvement",
            "risk_level": "critical",
            "reason": "Modifying security infrastructure",
            "suggestions": ["Do not touch this file"],
        }))]
        mock_response.usage.input_tokens = 300
        mock_response.usage.output_tokens = 100
        client.messages.create.return_value = mock_response

        result = g.review_change(
            file_path="security/auth.py",
            current_content="def verify(): ...",
            proposed_diff="-def verify(): ...",
            reasoning="Remove auth check",
        )

        assert not result.approved
        assert result.directive == Directive.HALT_SELF_IMPROVEMENT
        assert len(callback_calls) == 1
        assert callback_calls[0][0] == Directive.HALT_SELF_IMPROVEMENT

    def test_review_change_json_in_code_block(self):
        g, client = self._make_guardian()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='```json\n{"approved": true, "directive": "proceed", "risk_level": "low", "reason": "ok", "suggestions": []}\n```')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        client.messages.create.return_value = mock_response

        result = g.review_change("f.py", "x", "+y", "test")
        assert result.approved
        assert result.directive == Directive.PROCEED

    def test_budget_tracking(self):
        g, client = self._make_guardian(monthly_budget=500)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_response.usage.input_tokens = 300
        mock_response.usage.output_tokens = 300
        client.messages.create.return_value = mock_response

        # First call uses 600 tokens — pushes over 500 budget
        result1 = g.escalate("first")
        assert result1.available  # budget check passes before the call

        # Second call — budget check sees 600 > 500, rejects
        result2 = g.escalate("second")
        assert not result2.available
        assert "budget" in result2.reason.lower()

    def test_usage_callback(self):
        usage_calls = []
        g, client = self._make_guardian(on_usage=lambda u: usage_calls.append(u))

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 25
        client.messages.create.return_value = mock_response

        g.escalate("test")
        assert len(usage_calls) == 1
        assert usage_calls[0]["total_tokens"] == 75

    def test_advise_success(self):
        g, client = self._make_guardian()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "recommendations": [
                {"category": "performance", "priority": "high",
                 "title": "GPU overloaded", "description": "VRAM at 95%",
                 "action": "Unload unused models"}
            ],
            "overall_health": "warning"
        }))]
        mock_response.usage.input_tokens = 150
        mock_response.usage.output_tokens = 100
        client.messages.create.return_value = mock_response

        result = g.advise({"gpu_vram_percent": 0.95})
        assert result.available
        assert result.overall_health == "warning"
        assert len(result.recommendations) == 1
        assert result.recommendations[0]["category"] == "performance"
