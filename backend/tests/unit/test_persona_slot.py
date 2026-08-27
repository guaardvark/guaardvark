"""The persona slot in the chat system prompt.

`brain_state._build_system_prompts` imports `get_active_system_prompt` from
`chat_utils` inside a bare `except Exception: pass`. When the name is missing the
import raises, the exception is swallowed, and the persona is silently empty on
every install — which is how it sat undetected. These tests assert the seam
exists and degrades deliberately rather than by accident.
"""
from __future__ import annotations

import inspect

from backend.utils import chat_utils


def test_brain_state_import_target_exists():
    """The exact symbol brain_state imports must be present and callable."""
    fn = getattr(chat_utils, "get_active_system_prompt", None)
    assert callable(fn), (
        "brain_state._build_system_prompts imports this under a bare except; "
        "removing it empties the persona silently"
    )


def test_callable_with_no_arguments():
    """brain_state calls it with no arguments; a required parameter would raise."""
    sig = inspect.signature(chat_utils.get_active_system_prompt)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    assert not required, f"must be callable with no arguments, needs {required}"


def test_returns_none_outside_app_context():
    """No Flask app: return None rather than raising, so startup keeps working."""
    assert chat_utils.get_active_system_prompt() is None


def test_accepts_a_model_name_for_per_model_personas():
    """Rules carry target_models_json; the seam must be able to pass a model."""
    assert "model_name" in inspect.signature(chat_utils.get_active_system_prompt).parameters
    assert chat_utils.get_active_system_prompt("tinyllama:latest") is None
