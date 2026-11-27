"""The first LLM call of a turn says when the chat model has to be loaded first."""
from unittest.mock import MagicMock, patch

import requests

from backend.services import chat_keep_ready
from backend.services.unified_chat_engine import _chat_model_resident

MODEL = "gemma4:12b"


def _ps(models):
    reply = MagicMock()
    reply.json.return_value = {"models": models}
    return reply


def test_resident_when_ps_lists_the_model():
    with patch.object(chat_keep_ready.requests, "get", return_value=_ps([{"name": MODEL}])) as get:
        assert _chat_model_resident(MODEL) is True
    url = get.call_args.args[0]
    assert url.endswith("/api/ps")
    assert get.call_args.kwargs["timeout"] == 1.0


def test_untagged_name_matches_latest():
    with patch.object(chat_keep_ready.requests, "get", return_value=_ps([{"name": "llama3:latest"}])):
        assert _chat_model_resident("llama3") is True


def test_not_resident_when_nothing_is_loaded():
    with patch.object(chat_keep_ready.requests, "get", return_value=_ps([])):
        assert _chat_model_resident(MODEL) is False


def test_unknown_when_ollama_does_not_answer():
    with patch.object(chat_keep_ready.requests, "get", side_effect=requests.ConnectionError("refused")):
        assert _chat_model_resident(MODEL) is None


def test_unknown_without_a_model_name():
    with patch.object(chat_keep_ready.requests, "get") as get:
        assert _chat_model_resident(None) is None
    get.assert_not_called()
