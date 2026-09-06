"""Real-time query detection must match whole words, never substrings.

A creative-writing prompt mentioning a "weathered" table is not a weather
question, and a long writing brief is never a real-time lookup even when a
keyword appears in it.
"""
from unittest.mock import patch

import pytest

from backend.services.unified_chat_engine import UnifiedChatEngine


class TestIsRealtimeQuery:
    def test_weathered_is_not_weather(self):
        assert UnifiedChatEngine._is_realtime_query(
            "The weathered surface of the table caught the light."
        ) is False

    def test_genuine_weather_question(self):
        assert UnifiedChatEngine._is_realtime_query(
            "what's the weather in Boston right now?"
        ) is True

    def test_phrase_keywords_still_match(self):
        assert UnifiedChatEngine._is_realtime_query("how hot is it today?") is True
        assert UnifiedChatEngine._is_realtime_query("any latest news on the merger") is True

    @pytest.mark.parametrize("blocker", [
        "write", "rewrite", "prompt", "story", "describe", "essay",
        "script", "poem", "scene", "dialogue", "lyrics",
    ])
    def test_writing_tasks_are_blocked(self, blocker):
        msg = f"Please {blocker} something about the weather forecast tonight"
        assert UnifiedChatEngine._is_realtime_query(msg) is False

    def test_generation_requests_are_blocked(self):
        assert UnifiedChatEngine._is_realtime_query(
            "generate an image of the weather over the sea"
        ) is False

    def test_long_creative_prompt_is_never_realtime(self):
        body = (
            "Write nothing yet, just consider: a lone traveller watches the temperature "
            "drop as the forecast worsens over the harbour. "
        )
        # Blockers are removed so only the length rule can reject this message.
        long_prompt = body.replace("Write nothing yet, just consider: ", "") * 8
        assert len(long_prompt) > UnifiedChatEngine._REALTIME_MAX_CHARS
        assert UnifiedChatEngine._is_realtime_query(long_prompt) is False

    def test_same_text_under_the_limit_is_realtime(self):
        short = "a lone traveller watches the temperature drop as the forecast worsens"
        assert len(short) <= UnifiedChatEngine._REALTIME_MAX_CHARS
        assert UnifiedChatEngine._is_realtime_query(short) is True

    def test_empty_message(self):
        assert UnifiedChatEngine._is_realtime_query("") is False


class TestShouldUseWebSearch:
    """enhanced_chat_api's detector shares the word-boundary rule."""

    @pytest.fixture
    def manager(self):
        from backend.api.enhanced_chat_api import EnhancedChatManager
        return EnhancedChatManager.__new__(EnhancedChatManager)

    @patch("backend.utils.settings_utils.get_web_access", return_value=False)
    def test_substrings_do_not_trigger(self, _access, manager):
        # "know", "sometimes", "update" and "opposite" contain now/time/date/site.
        assert manager._should_use_web_search(
            "I know sometimes I update the opposite"
        ) is False

    @patch("backend.utils.settings_utils.get_web_access", return_value=False)
    def test_whole_word_indicator_triggers(self, _access, manager):
        assert manager._should_use_web_search("weather in Boston now") is True

    @patch("backend.utils.settings_utils.get_web_access", return_value=False)
    def test_bare_domain_triggers(self, _access, manager):
        assert manager._should_use_web_search("summarise example.com for me") is True
