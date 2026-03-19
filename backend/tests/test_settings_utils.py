"""Tests for get_setting / save_setting utility."""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestGetSetting:
    """Test the unified get_setting function."""

    def test_returns_default_when_no_app_context(self):
        """Outside Flask app context, returns default."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=False):
            result = get_setting("enhanced_context_enabled", default=True, cast=bool)
            assert isinstance(result, bool)

    def test_reads_from_settings_table(self):
        """Keys not in SYSTEM_SETTING_KEYS read from Setting model."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        mock_setting.value = "false"
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = mock_setting
                result = get_setting("enhanced_context_enabled", default=True, cast=bool)
                assert result is False

    def test_reads_from_system_settings_table(self):
        """Keys in SYSTEM_SETTING_KEYS read from SystemSetting model."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        mock_setting.value = "always"
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = mock_setting
                result = get_setting("claude_escalation_mode", default="manual")
                assert result == "always"

    def test_falls_back_to_env_var(self):
        """When DB has no value, reads from mapped env var."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                with patch.dict("os.environ", {"GUAARDVARK_ENHANCED_CONTEXT": "false"}):
                    result = get_setting("enhanced_context_enabled", default=True, cast=bool)
                    assert result is False

    def test_falls_back_to_default(self):
        """When DB and env var both missing, returns default."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("GUAARDVARK_RAG_DEBUG", None)
                    result = get_setting("rag_debug_enabled", default=True, cast=bool)
                    assert result is True

    def test_cast_bool_truthy_values(self):
        """Bool cast handles 'true', 'True', '1', 'yes'."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                for truthy in ["true", "True", "1", "yes"]:
                    mock_setting.value = truthy
                    mock_db.session.get.return_value = mock_setting
                    assert get_setting("rag_debug_enabled", default=False, cast=bool) is True

    def test_cast_bool_falsy_values(self):
        """Bool cast handles 'false', 'False', '0', 'no', ''."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                for falsy in ["false", "False", "0", "no", ""]:
                    mock_setting.value = falsy
                    mock_db.session.get.return_value = mock_setting
                    assert get_setting("rag_debug_enabled", default=True, cast=bool) is False

    def test_cast_int(self):
        """Int cast works for budget values."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        mock_setting.value = "500000"
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = mock_setting
                result = get_setting("claude_monthly_budget", default=1000000, cast=int)
                assert result == 500000

    def test_db_exception_falls_back_gracefully(self):
        """DB errors should not crash -- fall back to env/default."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.side_effect = Exception("DB down")
                result = get_setting("enhanced_context_enabled", default=True, cast=bool)
                assert result is True


class TestSaveSetting:
    """Test save_setting function."""

    def test_save_to_settings_table(self):
        """Non-system keys save to Setting model."""
        from backend.utils.settings_utils import save_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                save_setting("rag_debug_enabled", "false")
                mock_db.session.add.assert_called_once()
                mock_db.session.commit.assert_called_once()

    def test_save_to_system_settings_table(self):
        """System keys save to SystemSetting model."""
        from backend.utils.settings_utils import save_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                save_setting("claude_token_usage", '{"usage": {}}')
                mock_db.session.add.assert_called_once()
                mock_db.session.commit.assert_called_once()

    def test_no_crash_outside_app_context(self):
        """save_setting outside app context logs warning, doesn't crash."""
        from backend.utils.settings_utils import save_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=False):
            save_setting("test_key", "test_value")
