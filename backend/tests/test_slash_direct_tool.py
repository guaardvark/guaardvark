"""Tests for slash command → direct tool execution."""
import pytest


class TestSlashCommandResolver:
    def test_imagine_resolves_to_generate_image(self):
        from backend.services.slash_command_executor import resolve_slash_direct_tool

        tool, params = resolve_slash_direct_tool({
            "slash_command": "imagine",
            "slash_args": "a cat on a motorcycle",
            "direct_tool_params": {"model": "auto"},
        })
        assert tool == "generate_image"
        assert params["prompt"] == "a cat on a motorcycle"
        assert params["model"] == "auto"

    def test_imagine_uses_persisted_model_when_omitted(self, monkeypatch):
        from backend.services.slash_command_executor import resolve_slash_direct_tool

        monkeypatch.setattr(
            "backend.utils.settings_utils.get_chat_image_model",
            lambda: "sd-xl",
        )
        tool, params = resolve_slash_direct_tool({
            "slash_command": "imagine",
            "slash_args": "a cat on a motorcycle",
        })
        assert tool == "generate_image"
        assert params["model"] == "sd-xl"

    def test_websearch_resolves_to_web_search(self):
        from backend.services.slash_command_executor import resolve_slash_direct_tool

        tool, params = resolve_slash_direct_tool({
            "direct_tool": "web_search",
            "direct_tool_params": {"query": "weather Cleveland"},
        })
        assert tool == "web_search"
        assert params["query"] == "weather Cleveland"

    def test_explicit_direct_tool_wins(self):
        from backend.services.slash_command_executor import resolve_slash_direct_tool

        tool, params = resolve_slash_direct_tool({
            "direct_tool": "generate_image",
            "direct_tool_params": {"prompt": "sunset", "model": "sdxl"},
        })
        assert tool == "generate_image"
        assert params["prompt"] == "sunset"

    def test_video_resolves_to_generate_video(self):
        from backend.services.slash_command_executor import resolve_slash_direct_tool

        tool, params = resolve_slash_direct_tool({
            "slash_command": "video",
            "slash_args": "a cat playing piano",
        })
        assert tool == "generate_video"
        assert params["prompt"] == "a cat playing piano"
        assert tool != "generate_animation"


class TestDirectToolIntercept:
    def test_try_direct_tool_calls_registry_without_llm(self):
        from backend.services.unified_chat_engine import UnifiedChatEngine
        from backend.services.agent_tools import ToolResult

        class FakeRegistry:
            def get_tool(self, name):
                return object() if name == "generate_image" else None

            def execute_tool(self, tool_name, **kwargs):
                return ToolResult(
                    success=True,
                    output="ok",
                    metadata={"image_url": "/api/outputs/generated_images/test.png", "prompt": kwargs.get("prompt")},
                )

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = FakeRegistry()
        engine._save_message = lambda *a, **k: None

        events = []

        def emit_fn(event, payload):
            events.append((event, payload))

        result = engine._try_direct_tool(
            "/imagine a cat",
            "sess-1",
            {
                "direct_tool": "generate_image",
                "direct_tool_params": {"prompt": "a cat", "model": "auto"},
                "slash_command": "imagine",
            },
            emit_fn,
            "req-1",
        )

        assert result is not None
        assert result["success"] is True
        tool_calls = [e for e in events if e[0] == "chat:tool_call"]
        assert tool_calls
        assert tool_calls[0][1]["tool"] == "generate_image"
        assert "chat:image" in [e[0] for e in events]

    def test_image_retry_uses_pending_prompt(self):
        from backend.services.unified_chat_engine import (
            UnifiedChatEngine,
            _SESSION_PENDING_IMAGE_PROMPT,
        )
        from backend.services.agent_tools import ToolResult

        sid = "retry-sess"
        _SESSION_PENDING_IMAGE_PROMPT[sid] = "a dog on a bike"

        class FakeRegistry:
            def get_tool(self, name):
                return object() if name == "generate_image" else None

            def execute_tool(self, tool_name, **kwargs):
                return ToolResult(success=False, error="GPU is busy — try again in a moment.")

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = FakeRegistry()
        engine._save_message = lambda *a, **k: None

        events = []
        result = engine._try_image_generate_retry(
            "try again please",
            sid,
            {},
            lambda e, p: events.append((e, p)),
            "req-2",
        )

        assert result is not None
        tool_calls = [e for e in events if e[0] == "chat:tool_call"]
        assert tool_calls[0][1]["params"]["prompt"] == "a dog on a bike"
        _SESSION_PENDING_IMAGE_PROMPT.pop(sid, None)

    def test_image_edit_retry_uses_pending_edit(self):
        from backend.services.unified_chat_engine import (
            UnifiedChatEngine,
            _SESSION_PENDING_IMAGE_EDIT,
        )
        from backend.services.agent_tools import ToolResult
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"png")
            img_path = tmp.name

        sid = "edit-retry-sess"
        _SESSION_PENDING_IMAGE_EDIT[sid] = {
            "instruction": "make the sky orange",
            "image": img_path,
        }

        class FakeRegistry:
            def get_tool(self, name):
                return object() if name == "edit_image" else None

            def execute_tool(self, tool_name, **kwargs):
                return ToolResult(success=False, error="GPU is busy — try again in a moment.")

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = FakeRegistry()
        engine._save_message = lambda *a, **k: None

        events = []
        result = engine._try_image_edit_retry(
            "try again",
            sid,
            {},
            lambda e, p: events.append((e, p)),
            "req-edit",
        )

        try:
            assert result is not None
            tool_calls = [e for e in events if e[0] == "chat:tool_call"]
            assert tool_calls[0][1]["tool"] == "edit_image"
            assert tool_calls[0][1]["params"]["instruction"] == "make the sky orange"
            assert tool_calls[0][1]["params"]["image"] == img_path
        finally:
            _SESSION_PENDING_IMAGE_EDIT.pop(sid, None)
            os.unlink(img_path)


class TestParamRecovery:
    def test_literal_prompt_placeholder_rejected(self):
        from backend.services.agent_tools import ToolRegistry

        class FakeTool:
            name = "generate_image"
            parameters = {
                "prompt": type("P", (), {"required": True, "type": "string", "default": None})(),
            }
            requires_approval = False

            def can_execute(self, **kwargs):
                return bool(kwargs.get("prompt"))

            def execute(self, **kwargs):
                from backend.services.agent_tools import ToolResult
                return ToolResult(success=True, output=kwargs.get("prompt"))

            def set_context(self, _ctx):
                pass

        registry = ToolRegistry.__new__(ToolRegistry)
        registry.tools = {"generate_image": FakeTool()}
        registry.get_tool = lambda name: registry.tools.get(name)

        result = registry.execute_tool(
            "generate_image",
            agent_context={
                "user_message": "generate image: a red car",
                "pending_image_prompt": "a red car",
            },
            param_name="prompt",
        )
        assert result.success
        assert result.output == "a red car"


class TestSlashCommandMap:
    def test_map_includes_imagine_and_websearch(self):
        from backend.services.slash_command_executor import SLASH_COMMAND_TOOL_MAP

        assert SLASH_COMMAND_TOOL_MAP["imagine"] == "generate_image"
        assert SLASH_COMMAND_TOOL_MAP["websearch"] == "web_search"

    def test_generate_image_registry_category(self):
        from backend.tools.tool_registry_init import get_tool_categories, initialize_all_tools

        initialize_all_tools()
        cats = get_tool_categories()
        assert cats.get("generate_image") == "image"
