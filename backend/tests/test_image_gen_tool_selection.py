"""
Tests for image generation tool selection.
Prevents regression: Image gen requests MUST include generate_image tool.
The LLM should never be left without the tool when the user asks for images.
"""
import pytest


class TestImageToolSelection:
    """Verify that image-related messages always get the generate_image tool."""

    def _get_selected_tools(self, message):
        """Helper: run tool selection for a message and return tool names."""
        from backend.services.unified_chat_engine import select_tools_for_context
        # Simulate a full tool registry with all tool names
        all_tools = [
            "web_search", "analyze_website", "generate_image", "generate_animation",
            "browse_files", "read_file", "write_file", "execute_code",
            "agent_screen_capture", "agent_mode_start", "media_play",
        ]
        return select_tools_for_context(message, all_tools)

    @pytest.mark.parametrize("message", [
        "generate an image of a cat",
        "draw me a chicken",
        "create an image of a sunset",
        "make a picture of a dog",
        "make an image of a mountain",
        "photo of a beach",
        "image of a car",
        "picture of a house",
        "visualize a graph",
        "illustration of a dragon",
        "render image of space",
    ])
    def test_image_requests_include_generate_image_tool(self, message):
        """Every image-related message must include generate_image in selected tools."""
        tools = self._get_selected_tools(message)
        assert "generate_image" in tools, (
            f"generate_image NOT selected for: {message!r}. Got: {tools}"
        )

    @pytest.mark.parametrize("message", [
        "hello",
        "how are you",
        "what's the weather",
        "tell me a joke",
    ])
    def test_non_image_requests_may_omit_tool(self, message):
        """Non-image messages don't need generate_image (but may have it via core tools)."""
        tools = self._get_selected_tools(message)
        # Just verify it doesn't crash — non-image messages may or may not have the tool
        assert isinstance(tools, list)

    def test_system_prompt_contains_image_gen_rule(self):
        """The system prompt must contain the CRITICAL image gen instruction."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = False
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- generate_image(prompt:str) - Generate an image"
        )
        assert "CRITICAL" in prompt
        assert "generate_image" in prompt
        assert "CALL THE TOOL" in prompt

    def test_voice_mode_appends_voice_instruction(self):
        """When is_voice_message=True, voice instruction should be appended."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = True
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- generate_image(prompt:str) - Generate an image"
        )
        assert "VOICE MODE" in prompt
        assert "spoken" in prompt.lower()
