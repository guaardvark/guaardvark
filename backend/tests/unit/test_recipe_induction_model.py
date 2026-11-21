"""Recipe induction asks the model chat uses, not a saved name Ollama lacks."""
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask


def test_induction_skips_a_saved_model_ollama_does_not_have(monkeypatch):
    from backend.api import agent_control_api

    step = SimpleNamespace(action=SimpleNamespace(action_type="click", target_description="Search",
                                                  text=None, keys=None), failed=False)
    last = SimpleNamespace(success=True, verified=True, task="open search", steps=[step])
    svc_mod = types.ModuleType("backend.services.agent_control_service")
    svc_mod.get_agent_control_service = lambda: SimpleNamespace(_last_result=last)
    monkeypatch.setitem(sys.modules, "backend.services.agent_control_service", svc_mod)

    sent = {}

    def fake_post(url, json=None, **kwargs):
        sent.update(json or {})
        raise RuntimeError("stop after the request is built")

    with patch("backend.utils.llm_service.get_saved_active_model_name", return_value="qwen3.5:4b"), \
            patch("backend.services.ollama_chat_model.installed_chat_tags", return_value={"gemma4:12b"}), \
            patch("requests.post", side_effect=fake_post):
        agent_control_api._induce_candidate_recipe(Flask("t"), "sess", "open search")

    assert sent.get("model") == "gemma4:12b"
