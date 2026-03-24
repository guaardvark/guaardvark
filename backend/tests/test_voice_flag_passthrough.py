"""
Tests for voice message flag passthrough.
Prevents regression: is_voice_message must flow from API → engine → system prompt.
"""
import pytest
from unittest.mock import MagicMock


class TestVoiceFlagEngine:
    """Test that the engine uses the voice flag in system prompt."""

    def test_chat_method_accepts_voice_flag(self):
        """UnifiedChatEngine.chat() should accept is_voice_message parameter."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        # Verify the parameter exists in the method signature
        import inspect
        sig = inspect.signature(engine.chat)
        assert "is_voice_message" in sig.parameters

    def test_voice_flag_adds_instruction_to_system_prompt_with_tools(self):
        """When is_voice_message=True, system prompt with tools must include voice instruction."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = True
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- generate_image(prompt:str) - Generate an image"
        )
        assert "VOICE MODE" in prompt
        assert "meta-commentary" in prompt
        assert "spoken" in prompt.lower()

    def test_voice_flag_adds_instruction_to_lean_prompt(self):
        """When is_voice_message=True, lean prompt (no tools) must also include voice instruction."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = True
        prompt = engine._build_system_prompt("You are helpful.", "")
        assert "VOICE MODE" in prompt
        assert "meta-commentary" in prompt

    def test_no_voice_flag_omits_instruction(self):
        """When is_voice_message=False, system prompt must NOT include voice instruction."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = False
        prompt = engine._build_system_prompt("You are helpful.", "")
        assert "VOICE MODE" not in prompt

    def test_no_voice_flag_omits_instruction_with_tools(self):
        """When is_voice_message=False, tools prompt must NOT include voice instruction."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = False
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- web_search(query:str) - Search the web"
        )
        assert "VOICE MODE" not in prompt

    def test_voice_flag_default_false(self):
        """is_voice_message should default to False in chat() signature."""
        from backend.services.unified_chat_engine import UnifiedChatEngine
        import inspect

        sig = inspect.signature(UnifiedChatEngine.chat)
        param = sig.parameters["is_voice_message"]
        assert param.default is False

    def test_api_extracts_voice_flag(self):
        """Verify the unified_chat_api.py reads is_voice_message from request body."""
        import ast
        with open("backend/api/unified_chat_api.py", "r") as f:
            source = f.read()
        # Check that the API extracts the flag
        assert "is_voice_message" in source
        assert 'data.get("is_voice_message"' in source or "data.get('is_voice_message'" in source
