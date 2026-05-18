#!/usr/bin/env python3
"""Tests for today's brain_state change: `get_system_prompt()` now substitutes
`{MEMORY_BLOCK}` with a live DB read so newly-saved memories affect the very
next chat turn (was previously frozen at startup)."""

import os
import sys

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.brain_state import BrainState


@pytest.fixture(autouse=True)
def reset_singleton():
    BrainState.reset()
    yield
    BrainState.reset()


class TestMemoryBlockSubstitution:
    def test_substitutes_memory_block_token_with_live_text(self):
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "PREFIX\n\n{MEMORY_BLOCK}SUFFIX"}
        state._app = None  # bypass app_context path

        with patch(
            "backend.api.memory_api.get_memories_for_context",
            return_value="User's saved memories:\n- be polite",
        ):
            out = state.get_system_prompt("chat")

        assert "{MEMORY_BLOCK}" not in out
        assert "- be polite" in out
        assert "PREFIX" in out and "SUFFIX" in out

    def test_empty_memory_text_collapses_to_nothing(self):
        """No memories saved → token replaced by empty string (no stray blank lines from the token itself)."""
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "A{MEMORY_BLOCK}B"}
        state._app = None

        with patch("backend.api.memory_api.get_memories_for_context", return_value=""):
            out = state.get_system_prompt("chat")
        assert out == "AB"

    def test_exception_in_memory_read_does_not_leak_token(self):
        """Memory subsystem failure must not return a prompt containing the raw placeholder."""
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "HEAD{MEMORY_BLOCK}TAIL"}
        state._app = None

        with patch(
            "backend.api.memory_api.get_memories_for_context",
            side_effect=RuntimeError("db down"),
        ):
            out = state.get_system_prompt("chat")

        assert "{MEMORY_BLOCK}" not in out
        assert out == "HEADTAIL"

    def test_memory_text_separated_from_template_by_blank_line(self):
        """Memory text gets a trailing blank line so it never collides with the next prompt section."""
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "{MEMORY_BLOCK}RULES:"}
        state._app = None

        with patch("backend.api.memory_api.get_memories_for_context", return_value="some memories"):
            out = state.get_system_prompt("chat")
        # "some memories" + "\n\n" + "RULES:"
        assert out == "some memories\n\nRULES:"

    def test_uses_app_context_when_app_captured(self):
        """If `_app` is set, memory read happens inside an `app.app_context()`."""
        from flask import Flask

        state = BrainState.get_instance()
        state.system_prompts = {"chat": "P{MEMORY_BLOCK}"}

        captured_app = Flask(__name__)
        captured_app.config.update({"TESTING": True})
        state._app = captured_app

        pushed_contexts = []

        def fake_get_mem(*_args, **_kwargs):
            from flask import current_app
            pushed_contexts.append(current_app._get_current_object())
            return "mem"

        with patch("backend.api.memory_api.get_memories_for_context", side_effect=fake_get_mem):
            out = state.get_system_prompt("chat")

        assert pushed_contexts == [captured_app]
        assert "mem" in out

    def test_template_without_memory_token_untouched(self):
        """Contexts whose prompt doesn't use {MEMORY_BLOCK} must render verbatim."""
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "plain prompt, no tokens"}
        state._app = None

        with patch("backend.api.memory_api.get_memories_for_context", return_value="will not show"):
            out = state.get_system_prompt("chat")

        assert out == "plain prompt, no tokens"

    def test_fallback_to_chat_context_still_substitutes(self):
        """Unknown context falls back to chat template; substitution still runs on that template."""
        state = BrainState.get_instance()
        state.system_prompts = {"chat": "chat:{MEMORY_BLOCK}end"}
        state._app = None

        with patch("backend.api.memory_api.get_memories_for_context", return_value="m"):
            out = state.get_system_prompt("unknown-context")

        assert out == "chat:m\n\nend"
