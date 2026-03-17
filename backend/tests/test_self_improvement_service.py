"""Tests for SelfImprovementService."""

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestSelfImprovementService:
    """Test the Self-Improvement Service."""

    def test_check_enabled_returns_false_when_disabled(self):
        """Service should not run when disabled."""
        from backend.services.self_improvement_service import SelfImprovementService

        service = SelfImprovementService.__new__(SelfImprovementService)
        service._check_enabled = lambda: False
        assert service._check_enabled() is False

    def test_check_enabled_returns_false_when_locked(self):
        """Service should not run when codebase is locked."""
        from backend.services.self_improvement_service import SelfImprovementService

        service = SelfImprovementService.__new__(SelfImprovementService)
        with patch(
            "backend.services.self_improvement_service._is_codebase_locked",
            return_value=True,
        ):
            service._initialized = True
            assert service._is_safe_to_run() is False

    def test_parse_test_results(self):
        """Should parse pytest output into structured results."""
        from backend.services.self_improvement_service import SelfImprovementService

        service = SelfImprovementService.__new__(SelfImprovementService)

        pytest_output = """
FAILED backend/tests/test_code_tools.py::test_edit_code - AssertionError: expected 'hello'
PASSED backend/tests/test_code_tools.py::test_read_code
FAILED backend/tests/test_self_improvement.py::test_planted_bug_fix - RuntimeError: model unavailable
2 failed, 1 passed
"""
        failures = service._parse_test_failures(pytest_output)
        assert len(failures) == 2
        assert failures[0]["test_name"] == "test_edit_code"
        assert "test_code_tools.py" in failures[0]["file"]

    def test_error_fingerprint(self):
        """Should generate consistent fingerprints for same errors."""
        from backend.services.self_improvement_service import SelfImprovementService

        service = SelfImprovementService.__new__(SelfImprovementService)

        fp1 = service._error_fingerprint("backend/api/foo.py", 42, "ValueError")
        fp2 = service._error_fingerprint("backend/api/foo.py", 42, "ValueError")
        fp3 = service._error_fingerprint("backend/api/bar.py", 42, "ValueError")
        assert fp1 == fp2
        assert fp1 != fp3
