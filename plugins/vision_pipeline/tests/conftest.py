"""Shared fixtures and PYTHONPATH setup for vision pipeline tests."""
import sys
import os
from unittest.mock import MagicMock

# Ensure service package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ollama replies the app-level tests share: /api/tags lists one seeing model,
# /api/show describes it, /api/chat answers with a scene.
FAKE_TAGS = {"models": [{"name": "qwen3.5:4b"}]}
FAKE_SHOW = {"capabilities": ["completion", "vision", "tools", "thinking"]}
FAKE_CHAT = {"message": {"content": "A test scene"}, "done": True}


def fake_ollama_get(url, *args, **kwargs):
    return MagicMock(status_code=200, json=MagicMock(return_value=FAKE_TAGS))


def fake_ollama_post(url, *args, **kwargs):
    if url.endswith("/api/show"):
        return MagicMock(status_code=200, json=MagicMock(return_value=FAKE_SHOW))
    return MagicMock(status_code=200, json=MagicMock(return_value=FAKE_CHAT))
