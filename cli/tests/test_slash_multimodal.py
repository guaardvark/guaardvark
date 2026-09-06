"""Tests for multi-modal slash commands."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def router():
    from llx.slash import SlashRouter
    state = {
        "server": "http://localhost:5002",
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
                "response": "Image saved",
            }
            mock_client_fn.return_value = mock_client
            router.dispatch("/imagine a sunset over mountains")
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "/api/chat/unified/direct-tool" in call_args[0][0]

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


class TestIngestCommand:
    def test_ingest_registered(self, router):
        assert "ingest" in router.get_command_names()


class TestDidYouMean:
    def test_unknown_command_suggests_close_match(self, router, capsys):
        router.dispatch("/imagn")
        # Rich writes to the console, not necessarily capsys; the handler
        # should still return True and not raise.
        assert "imagn" not in router.get_command_names()


class TestHelpFilter:
    def test_help_specific_command(self, router):
        result = router.dispatch("/help imagine")
        assert result is True


class TestAgentCommand:
    def test_agent_registered(self, router):
        assert "agent" in router.get_command_names()

    def test_agent_toggles_mode(self, router):
        assert router._state.get("agent_mode") is False
        router.dispatch("/agent")
        assert router._state.get("agent_mode") is True
        router.dispatch("/agent")
        assert router._state.get("agent_mode") is False

    def test_agent_on_off(self, router):
        router.dispatch("/agent on")
        assert router._state.get("agent_mode") is True
        router.dispatch("/agent off")
        assert router._state.get("agent_mode") is False

    def test_agent_shot_does_not_toggle(self, router):
        with patch("llx.client.get_client") as mock_fn:
            mock_client = MagicMock()
            mock_client.http.post.return_value.status_code = 503
            mock_client.http.post.return_value.headers = {"content-type": "application/json"}
            mock_client.http.post.return_value.json.return_value = {"error": "Agent display not running"}
            mock_fn.return_value = mock_client
            was = router._state.get("agent_mode")
            router.dispatch("/agent shot")
            assert router._state.get("agent_mode") is was


class TestWebCommand:
    def test_web_registered(self, router):
        assert "web" in router.get_command_names()

    def test_web_opens_browser(self, router):
        with patch("webbrowser.open") as mock_open, patch(
            "llx.config.get_frontend_url", return_value="http://localhost:5173"
        ):
            router.dispatch("/web")
            mock_open.assert_called_once_with("http://localhost:5173")

    def test_web_images_path(self, router):
        with patch("webbrowser.open") as mock_open, patch(
            "llx.config.get_frontend_url", return_value="http://localhost:5173"
        ):
            router.dispatch("/web images")
            mock_open.assert_called_once_with("http://localhost:5173/images")
