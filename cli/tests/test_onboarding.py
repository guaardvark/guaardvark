"""Tests for onboarding flow logic (non-interactive parts)."""
import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def config_dir(tmp_path):
    config_path = tmp_path / ".guaardvark"
    config_path.mkdir()
    return config_path


@pytest.fixture
def patch_config_dir(monkeypatch, config_dir):
    monkeypatch.setattr("llx.launch_config._config_dir", lambda: config_dir)
    return config_dir


class TestSecurityNotice:
    def test_security_notice_text_contains_key_warnings(self):
        from llx.onboarding import SECURITY_NOTICE
        assert "file" in SECURITY_NOTICE.lower() or "files" in SECURITY_NOTICE.lower()
        assert "gpu" in SECURITY_NOTICE.lower() or "model" in SECURITY_NOTICE.lower()

    def test_security_notice_skipped_with_yes_flag(self, patch_config_dir):
        from llx.onboarding import run_onboarding
        with patch("llx.onboarding._confirm_security", return_value=True) as mock_confirm, \
             patch("llx.onboarding._pick_model", return_value="llama3.3"), \
             patch("llx.onboarding._pick_mode", return_value="lite"):
            run_onboarding(auto_yes=True, model="llama3.3")
            mock_confirm.assert_not_called()


class TestModelPicker:
    def test_fetch_available_models(self):
        from llx.onboarding import fetch_ollama_models
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.3:latest", "size": 4_000_000_000},
                {"name": "nomic-embed-text:latest", "size": 500_000_000},
                {"name": "glm-4.7-flash:latest", "size": 25_000_000_000},
            ]
        }
        with patch("httpx.get", return_value=mock_response):
            models = fetch_ollama_models("http://127.0.0.1:11434")

        names = [m["name"] for m in models]
        assert "llama3.3:latest" in names
        assert "glm-4.7-flash:latest" in names
        assert "nomic-embed-text:latest" not in names

    def test_format_model_size(self):
        from llx.onboarding import format_model_size
        assert format_model_size(4_000_000_000) == "3.7 GB"
        assert format_model_size(500_000_000) == "476.8 MB"
        assert format_model_size(25_000_000_000) == "23.3 GB"


class TestOnboardingResult:
    def test_onboarding_saves_config(self, patch_config_dir, config_dir):
        from llx.onboarding import run_onboarding
        from llx.launch_config import load_launch_config
        with patch("llx.onboarding._confirm_security", return_value=True), \
             patch("llx.onboarding._pick_model", return_value="llama3.3"), \
             patch("llx.onboarding._pick_mode", return_value="lite"):
            run_onboarding(auto_yes=False, model=None)

        cfg = load_launch_config()
        assert cfg["onboarded"] is True
        assert cfg["model"] == "llama3.3"
        assert cfg["mode"] == "lite"

    def test_onboarding_uses_model_flag(self, patch_config_dir, config_dir):
        from llx.onboarding import run_onboarding
        from llx.launch_config import load_launch_config
        with patch("llx.onboarding._confirm_security", return_value=True), \
             patch("llx.onboarding._pick_model") as mock_pick, \
             patch("llx.onboarding._pick_mode", return_value="lite"):
            run_onboarding(auto_yes=False, model="glm-4.7-flash")
            mock_pick.assert_not_called()

        cfg = load_launch_config()
        assert cfg["model"] == "glm-4.7-flash"
