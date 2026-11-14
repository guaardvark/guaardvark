"""get_llm_instance(model=...) forwards its per-call knobs to build_ollama."""
from unittest.mock import MagicMock, patch

from backend.utils import llm_service


def _capture():
    calls = []

    def fake_build_ollama(model_name, **kwargs):
        calls.append((model_name, kwargs))
        return object()

    return calls, fake_build_ollama


def test_default_call_leaves_thinking_to_build_ollama():
    calls, fake = _capture()
    with patch("backend.utils.ollama_resource_manager.build_ollama", fake):
        llm = llm_service.get_llm_instance(model="gemma4:12b")
    assert llm is not None
    model, kwargs = calls[0]
    assert model == "gemma4:12b"
    assert "thinking" not in kwargs
    assert kwargs["json_mode"] is False
    assert kwargs["request_timeout"] == 180.0
    assert "context_window" not in kwargs


def test_explicit_knobs_are_forwarded():
    calls, fake = _capture()
    with patch("backend.utils.ollama_resource_manager.build_ollama", fake):
        llm_service.get_llm_instance(
            model="qwen3.5:9b", thinking=True, request_timeout=30,
            json_mode=True, num_ctx=4096, num_predict=512,
        )
    _, kwargs = calls[0]
    assert kwargs["thinking"] is True
    assert kwargs["request_timeout"] == 30.0
    assert kwargs["json_mode"] is True
    assert kwargs["context_window"] == 4096
    assert kwargs["additional_kwargs"] == {"num_predict": 512}


def test_thinking_false_is_forwarded_not_dropped():
    calls, fake = _capture()
    with patch("backend.utils.ollama_resource_manager.build_ollama", fake):
        llm_service.get_llm_instance(model="gemma4:12b", thinking=False)
    assert calls[0][1]["thinking"] is False


def test_construction_failure_returns_none():
    def boom(model_name, **kwargs):
        raise RuntimeError("no ollama")

    with patch("backend.utils.ollama_resource_manager.build_ollama", boom):
        assert llm_service.get_llm_instance(model="gemma4:12b") is None


class TestWorkerStartupLlm:
    """The Celery worker's startup LLM follows the window its model is loaded with."""

    ENTRY = {"name": "gemma4:12b", "model": "gemma4:12b", "context_length": 32768,
             "size": 1454553168, "size_vram": 1454553168}
    INFO = {"size_mb": 7206.0, "parameter_count": 11_907_350_576, "native_context": 1_572_864}

    def _llm(self, cls):
        return cls(model="gemma4:12b", base_url="http://127.0.0.1:11434", context_window=8192,
                   additional_kwargs={"num_ctx": 8192})

    def _options(self, llm):
        from backend.utils import ollama_resource_manager as orm
        with patch.object(orm, "resident_models", return_value=[self.ENTRY]), \
             patch.object(orm, "get_model_info", return_value=self.INFO):
            return llm._model_kwargs["num_ctx"], llm.metadata.context_window

    def test_requests_use_the_loaded_window(self):
        llm = self._llm(llm_service.LoadedWindowOllama)
        assert self._options(llm) == (32768, 32768)

    def test_a_copy_keeps_following(self):
        copy = self._llm(llm_service.LoadedWindowOllama).model_copy(update={"thinking": False})
        assert isinstance(copy, llm_service.LoadedWindowOllama)
        assert self._options(copy) == (32768, 32768)

    def test_the_plain_class_keeps_the_window_it_was_built_with(self):
        llm = self._llm(llm_service.Ollama)
        assert self._options(llm) == (8192, 8192)

    def _startup_llm(self, monkeypatch):
        from backend.utils import ollama_resource_manager as orm
        tags = MagicMock(status_code=200)
        tags.json.return_value = {"models": [{"name": "gemma4:12b"}]}
        monkeypatch.setattr(llm_service, "get_saved_active_model_name", lambda: "gemma4:12b")
        with patch("requests.get", return_value=tags), \
             patch.object(orm, "validate_model_before_load", return_value=(True, "OK", 8192)), \
             patch.object(orm, "model_info_available", return_value=True), \
             patch.object(orm, "thinking_kwargs", return_value={}):
            return llm_service.get_llm_for_startup()

    def test_the_worker_gets_the_following_class(self, monkeypatch):
        monkeypatch.setenv("CELERY_WORKER_MODE", "true")
        assert isinstance(self._startup_llm(monkeypatch), llm_service.LoadedWindowOllama)

    def test_the_server_gets_the_plain_class(self, monkeypatch):
        monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
        assert type(self._startup_llm(monkeypatch)) is llm_service.Ollama
