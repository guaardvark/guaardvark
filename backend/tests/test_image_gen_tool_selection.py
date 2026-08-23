"""
Tests for image generation tool selection.
Prevents regression: explicit image gen requests MUST include generate_image;
descriptive mentions of images must NOT force generation.
"""
import pytest


class TestImageToolSelection:
    """Verify that image-related messages always get the generate_image tool."""

    def _get_selected_tools(self, message):
        """Helper: run tool selection for a message and return tool names."""
        from backend.services.unified_chat_engine import select_tools_for_context
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
        "render image of space",
        "generate a gif of a bouncing ball",
    ])
    def test_explicit_image_requests_include_generate_image_tool(self, message):
        """Explicit create-intent messages must include generate_image."""
        tools = self._get_selected_tools(message)
        assert "generate_image" in tools, (
            f"generate_image NOT selected for: {message!r}. Got: {tools}"
        )

    @pytest.mark.parametrize("message", [
        "hello",
        "how are you",
        "what's the weather",
        "tell me a joke",
        "Hi, on the client website there is an image of a duck",
        "The system prompt mentions generating images but I want to discuss the copy",
        "What does the image on the homepage show?",
        "photo of a beach",
        "image of a car",
        "picture of a house",
    ])
    def test_descriptive_messages_do_not_force_generate_image(self, message):
        """Descriptive / reference messages must not pin generate_image."""
        tools = self._get_selected_tools(message)
        assert "generate_image" not in tools, (
            f"generate_image wrongly selected for: {message!r}. Got: {tools}"
        )

    def test_system_prompt_contains_image_gen_rule(self):
        """The system prompt must instruct when to use generate_image."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = False
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- generate_image(prompt:str) - Generate an image"
        )
        assert "generate_image" in prompt
        assert "explicitly" in prompt.lower() or "NEW image" in prompt

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

    def test_brain_state_prompt_contains_image_gen_rule(self):
        """BrainState chat prompt must include the shared image generation rule."""
        from backend.services.brain_state import BrainState

        brain = BrainState.__new__(BrainState)
        brain.system_prompts = {"chat": "You are helpful.\n\n{MEMORY_BLOCK}{DESKTOP_STATE}"}
        brain.tool_registry = None
        brain._app = None

        prompt = brain.get_system_prompt(
            role="chat",
            tool_list="- generate_image(prompt:string) - Generate an image",
        )
        assert "generate_image" in prompt
        assert "<prompt>" in prompt
        assert "<param_name>value</param_name>" not in prompt

    def test_pin_image_generation_on_retry_with_pending_session(self):
        from backend.services.unified_chat_engine import (
            _pin_image_generation_tools,
            _SESSION_PENDING_IMAGE_PROMPT,
        )

        sid = "pin-test"
        _SESSION_PENDING_IMAGE_PROMPT[sid] = "a castle"
        all_tools = ["web_search", "generate_image"]
        selected = _pin_image_generation_tools(
            "try again please", [], all_tools, session_id=sid,
        )
        assert "generate_image" in selected
        _SESSION_PENDING_IMAGE_PROMPT.pop(sid, None)

    def test_pin_image_generation_not_on_duck_website_message(self):
        from backend.services.unified_chat_engine import _pin_image_generation_tools

        all_tools = ["web_search", "generate_image"]
        selected = _pin_image_generation_tools(
            "Hi, on the client website there is an image of a duck",
            [],
            all_tools,
        )
        assert "generate_image" not in selected

    def test_try_again_not_agent_control_keyword(self):
        from backend.services.unified_chat_engine import TOOL_CONTEXT_KEYWORDS

        keywords = TOOL_CONTEXT_KEYWORDS["agent_control"][0]
        assert "try again" not in keywords


class TestUserWantsImageGeneration:
    @pytest.mark.parametrize("message", [
        "generate an image of a duck",
        "draw me a duck",
        "make a picture of a sunset",
    ])
    def test_explicit_requests(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is True

    @pytest.mark.parametrize("message", [
        "Hi, on the client website there is an image of a duck",
        "What does the image on the homepage show?",
        "The system prompt mentions generating images but I want to discuss the copy",
    ])
    def test_descriptive_rejected(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is False

    def test_try_image_generate_direct_skips_duck_website(self):
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        result = engine._try_image_generate_direct(
            "Hi, on the client website there is an image of a duck",
            "sess", lambda *a, **k: None, "req", {},
        )
        assert result is None


class TestPastedDescriptionsDoNotGenerate:
    """A pasted prompt or scene description is not a request to render it.

    These all reached generate_image through substring matching: "withdrawal"
    contains "draw", "animated reflections" contains "animate". The direct
    natural-language path bypasses the LLM, so a match here rendered an image
    with nothing to veto it.
    """

    @pytest.mark.parametrize("message", [
        "A cinematic wide shot of a rain-soaked alley, neon signs, "
        "animated reflections on wet asphalt",
        "Here's the prompt I want to save for later: lone astronaut on a red "
        "dune, drawn in ink wash",
        "Can you review this description? Slow push-in on a lighthouse, gulls "
        "wheeling, moving image quality",
        "The client asked for a withdrawal form redesign",
        "The animation industry uses a lot of GPU time",
    ])
    def test_pasted_description_does_not_want_generation(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is False

    @pytest.mark.parametrize("message", [
        "The client asked for a withdrawal form redesign",
        "A cinematic wide shot with animated reflections on wet asphalt",
    ])
    def test_pasted_description_does_not_pin_generate_image(self, message):
        from backend.services.unified_chat_engine import _pin_image_generation_tools
        selected = _pin_image_generation_tools(message, [], ["web_search", "generate_image"])
        assert "generate_image" not in selected


class TestCommandOnlyMode:
    """chat_media_requires_command: only an explicit command may create media."""

    @staticmethod
    def _force(monkeypatch, enabled):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: enabled)
        return uce

    @pytest.mark.parametrize("message", [
        "generate an image of a cat",
        "draw me a duck",
        "make a picture of a sunset",
    ])
    def test_natural_language_suppressed_when_on(self, monkeypatch, message):
        uce = self._force(monkeypatch, True)
        assert uce.user_wants_image_generation(message) is False

    @pytest.mark.parametrize("message", [
        "/imagine a fox in tall grass",
        "  /imagine a fox",
    ])
    def test_slash_command_still_honoured_when_on(self, monkeypatch, message):
        uce = self._force(monkeypatch, True)
        assert uce.user_wants_image_generation(message) is True

    def test_natural_language_works_when_off(self, monkeypatch):
        uce = self._force(monkeypatch, False)
        assert uce.user_wants_image_generation("generate an image of a cat") is True

    def test_video_suppressed_when_on_but_slash_survives(self, monkeypatch):
        uce = self._force(monkeypatch, True)
        assert uce.user_wants_video_generation("generate a video of a fox") is False
        assert uce.user_wants_video_generation("/video a fox") is True

    def test_defaults_off_so_existing_behaviour_is_unchanged(self):
        from backend.services.unified_chat_engine import _media_requires_explicit_command
        assert _media_requires_explicit_command() is False
