"""A hard-coded chat model tag is a preference, not a requirement.

The installer's hardware policy pulls gemma4:e2b on most machines while Film
Crew was written against gemma4:e4b; on a clean install every production
failed with a 404 until the operator edited the constant (reported by a
tester on 2026-08-29). The resolver must land on what is installed.
"""
from __future__ import annotations

from unittest.mock import patch

from backend.services.ollama_chat_model import is_embedding_model, resolve_chat_model

MOD = "backend.services.ollama_chat_model"


def test_preferred_wins_when_installed():
    assert resolve_chat_model("gemma4:e4b", installed={"gemma4:e4b", "gemma4:e2b"}) == "gemma4:e4b"


def test_same_family_beats_everything_else():
    with patch(f"{MOD}._saved_active_model", return_value="qwen3:8b"):
        assert resolve_chat_model("gemma4:e4b", installed={"gemma4:e2b", "qwen3:8b"}) == "gemma4:e2b"


def test_saved_active_model_when_family_absent():
    with patch(f"{MOD}._saved_active_model", return_value="qwen3:8b"), \
         patch(f"{MOD}._policy_model", return_value="llama3.2:1b"):
        assert resolve_chat_model("gemma4:e4b", installed={"qwen3:8b", "llama3.2:1b"}) == "qwen3:8b"


def test_policy_tier_when_no_active_model():
    with patch(f"{MOD}._saved_active_model", return_value=None), \
         patch(f"{MOD}._policy_model", return_value="llama3.2:1b"):
        assert resolve_chat_model("gemma4:e4b", installed={"mistral:7b", "llama3.2:1b"}) == "llama3.2:1b"


def test_any_gemma_then_first_installed():
    with patch(f"{MOD}._saved_active_model", return_value=None), \
         patch(f"{MOD}._policy_model", return_value=None):
        assert resolve_chat_model("qwen3:8b", installed={"mistral:7b", "gemma3:4b"}) == "gemma3:4b"
        assert resolve_chat_model("qwen3:8b", installed={"mistral:7b", "llama3.2:1b"}) == "llama3.2:1b"


def test_embedding_models_are_never_chosen():
    with patch(f"{MOD}._saved_active_model", return_value="nomic-embed-text:latest"), \
         patch(f"{MOD}._policy_model", return_value=None):
        # Only an embedding model is installed: keep the preference so Ollama's
        # own "not found" error surfaces instead of a 400 from the embed model.
        assert resolve_chat_model("gemma4:e4b", installed={"nomic-embed-text:latest"}) == "gemma4:e4b"
    assert is_embedding_model("nomic-embed-text:latest")
    assert not is_embedding_model("gemma4:e2b")


def test_nothing_installed_keeps_preference():
    assert resolve_chat_model("gemma4:e4b", installed=set()) == "gemma4:e4b"


def test_production_reads_ollama_when_not_injected():
    with patch(f"{MOD}.installed_chat_tags", return_value={"gemma4:e2b"}):
        assert resolve_chat_model("gemma4:e4b") == "gemma4:e2b"
