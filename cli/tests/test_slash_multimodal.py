"""Tests for multi-modal slash commands."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def router():
    from llx.slash import SlashRouter
    state = {
        "server": "http://localhost:5000",
        "session_id": "test-session",
        "message_count": 0,
        "agent_mode": False,
    }
    return SlashRouter(state)


class TestImagineCommand:
    def test_imagine_registered(self, router):
        assert "imagine" in router.get_command_names()

    def test_imagine_calls_api(self, router):
        with patch("llx.client.get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.post.return_value = {
                "success": True,
                "data": {"batch_id": "batch-123", "prompt_count": 1},
            }
            mock_client_fn.return_value = mock_client
            router.dispatch("/imagine a sunset over mountains")
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "/api/batch-image/generate/prompts" in call_args[0][0]

    def test_imagine_no_prompt_shows_usage(self, router):
        result = router.dispatch("/imagine")
        assert result is True


class TestVideoCommand:
    def test_video_registered(self, router):
        assert "video" in router.get_command_names()

    def test_video_calls_api(self, router):
        with patch("llx.client.get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.post.return_value = {
                "success": True,
                "data": {"batch_id": "batch-456", "status": "pending"},
            }
            mock_client_fn.return_value = mock_client
            router.dispatch("/video a cat playing piano")
            mock_client.post.assert_called_once()


class TestVoiceCommand:
    def test_voice_registered(self, router):
        assert "voice" in router.get_command_names()


class TestIndexCommand:
    def test_index_registered(self, router):
        assert "index" in router.get_command_names()


class TestAgentCommand:
    def test_agent_registered(self, router):
        assert "agent" in router.get_command_names()

    def test_agent_toggles_mode(self, router):
        assert router._state.get("agent_mode") is False
        router.dispatch("/agent")
        assert router._state.get("agent_mode") is True
        router.dispatch("/agent")
        assert router._state.get("agent_mode") is False


class TestWebCommand:
    def test_web_registered(self, router):
        assert "web" in router.get_command_names()

    def test_web_opens_browser(self, router):
        with patch("webbrowser.open") as mock_open:
            router.dispatch("/web")
            mock_open.assert_called_once_with("http://localhost:5173")
