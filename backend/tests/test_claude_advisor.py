"""Tests for ClaudeAdvisorService."""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestClaudeAdvisorService:
    """Test the Claude Advisor Service."""

    def test_init_without_api_key(self):
        """Service should initialize gracefully without API key."""
        with patch.dict("os.environ", {}, clear=False):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                from backend.services.claude_advisor_service import ClaudeAdvisorService
                service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
                service._api_key = None
                service._client = None
                assert service.is_available() is False

    def test_init_with_api_key(self):
        """Service should initialize with API key."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        assert service.is_available() is True

    def test_escalate_returns_unavailable_without_key(self):
        """Escalation should return unavailable when no API key."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = None
        service._client = None
        result = service.escalate("test message", [])
        assert result["available"] is False

    def test_review_change_approved(self):
        """Guardian should return approval with directive."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        from datetime import datetime
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        service._model = "claude-sonnet-4-20250514"
        service._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        service._usage_reset_date = datetime.now().replace(day=1)
        service._monthly_budget = 1000000

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "approved": True,
            "suggestions": [],
            "risk_level": "low",
            "directive": "proceed",
            "reason": "Change looks safe"
        }))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        service._client.messages.create.return_value = mock_response

        result = service.review_change(
            file_path="backend/services/indexing_service.py",
            current_content="def foo():\n    return 1",
            proposed_diff="- return 1\n+ return 2",
            reasoning="Fix off-by-one error"
        )
        assert result["approved"] is True
        assert result["directive"] == "proceed"

    def test_review_change_halt_directive(self):
        """Guardian should handle halt_self_improvement directive."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        from datetime import datetime
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        service._model = "claude-sonnet-4-20250514"
        service._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        service._usage_reset_date = datetime.now().replace(day=1)
        service._monthly_budget = 1000000

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "approved": False,
            "suggestions": ["Do not modify security files"],
            "risk_level": "critical",
            "directive": "halt_self_improvement",
            "reason": "Attempted to modify protected security infrastructure"
        }))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        service._client.messages.create.return_value = mock_response

        result = service.review_change(
            file_path="backend/services/tool_execution_guard.py",
            current_content="class Guard: pass",
            proposed_diff="- class Guard: pass\n+ class Guard: pass  # modified",
            reasoning="Optimize guard"
        )
        assert result["approved"] is False
        assert result["directive"] == "halt_self_improvement"

    def test_review_change_offline_fallback(self):
        """Guardian should return fallback when Claude is unavailable."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = None
        service._client = None

        result = service.review_change(
            file_path="backend/services/indexing_service.py",
            current_content="x = 1",
            proposed_diff="- x = 1\n+ x = 2",
            reasoning="test"
        )
        assert result["approved"] is True
        assert result["directive"] == "proceed_with_caution"
        assert result["offline_fallback"] is True

    def test_token_usage_tracking(self):
        """Service should track token usage."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        service._track_usage(input_tokens=100, output_tokens=50)
        assert service._usage["total_tokens"] == 150

    def test_token_budget_exceeded(self):
        """Service should refuse calls when budget is exceeded."""
        from backend.services.claude_advisor_service import ClaudeAdvisorService
        from datetime import datetime
        service = ClaudeAdvisorService.__new__(ClaudeAdvisorService)
        service._api_key = "test-key"
        service._client = MagicMock()
        service._monthly_budget = 1000
        service._usage = {"input_tokens": 500, "output_tokens": 501, "total_tokens": 1001}
        service._usage_reset_date = datetime.now().replace(day=1)

        result = service.escalate("test", [])
        assert result["available"] is False
        assert "budget" in result.get("reason", "").lower()
