"""Every request that loads the chat model ahead of a chat asks for the chat's own window."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services import brain_state as bs
from backend.services import chat_keep_ready
from backend.services.brain_state import BrainState, WarmUpStatus
from backend.services.unified_chat_engine import UnifiedChatEngine
from backend.utils import ollama_resource_manager as orm

MODEL = "gemma4:12b"
# What sizing the model afresh would return; the instances below carry another window.
FRESH_DECISION = 32768


class _LLM:
    def __init__(self, window):
        self.model = MODEL
        self.context_window = window
        self.additional_kwargs = {"num_ctx": window}


@pytest.fixture(autouse=True)
def _clean_state():
    BrainState.reset()
    with orm._provisional_lock:
        orm._provisional_llms.clear()
    yield
    BrainState.reset()
    with orm._provisional_lock:
        orm._provisional_llms.clear()


@pytest.fixture
def ollama_post():
    """Capture the bodies posted to Ollama; sizing afresh returns FRESH_DECISION."""
    reply = MagicMock(status_code=200)
    reply.json.return_value = {"load_duration": 1}
    with patch.object(orm.requests, "post", return_value=reply) as post, \
         patch.object(chat_keep_ready.requests, "post", return_value=reply), \
         patch.object(orm, "decide_num_ctx", return_value=orm.NumCtxDecision(FRESH_DECISION, True)):
        chat_keep_ready.requests.post = post
        yield post


def _chat_window(llm) -> int:
    return UnifiedChatEngine._chat_context_window(SimpleNamespace(llm=llm))


def _sent_num_ctx(post) -> int:
    assert post.call_count == 1
    return post.call_args.kwargs["json"]["options"]["num_ctx"]


def test_the_warm_up_helper_asks_for_the_chat_window(ollama_post):
    llm = _LLM(8192)
    orm.warm_chat_model(llm, keep_alive="30m")
    body = ollama_post.call_args.kwargs["json"]
    assert body["model"] == MODEL and body["keep_alive"] == "30m"
    assert _sent_num_ctx(ollama_post) == _chat_window(llm) == 8192


def test_the_startup_warm_up_asks_for_the_chat_window(ollama_post, monkeypatch):
    monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
    llm = _LLM(8192)
    state = BrainState.get_instance()
    state.llm, state.active_model = llm, MODEL
    with patch("backend.utils.process_role.one_shot_command", return_value=False), \
         patch.object(orm, "model_installed", return_value=True):
        state._start_warmup()
        state._warm_up_thread.join(5)
    assert state.health.warm_up_status == WarmUpStatus.READY
    assert _sent_num_ctx(ollama_post) == _chat_window(llm) == 8192


def test_keep_ready_asks_for_the_chat_window(ollama_post):
    llm = _LLM(8192)
    state = BrainState.get_instance()
    state.llm = llm
    chat_keep_ready._load(MODEL)
    body = ollama_post.call_args.kwargs["json"]
    assert body["keep_alive"] == -1
    assert _sent_num_ctx(ollama_post) == _chat_window(llm) == 8192


def test_a_placeholder_window_is_resolved_once_for_warm_up_and_chat(ollama_post):
    llm = _LLM(8192)
    orm.mark_provisional(llm, resolved=False)
    orm.warm_chat_model(llm, keep_alive="30m")
    assert _sent_num_ctx(ollama_post) == _chat_window(llm) == FRESH_DECISION
    assert not orm.is_provisional(llm)


def test_the_model_switch_warms_through_the_helper():
    """The route's warm-up is the helper, on the instance it installs for chat."""
    import inspect
    from backend.api import model_api
    source = inspect.getsource(model_api)
    assert 'warm_chat_model(new_llm, keep_alive="30m")' in source
    assert '"options": {"num_predict": 1}' not in source


class TestWaitingForTheModel:
    @pytest.fixture(autouse=True)
    def _fast_poll(self, monkeypatch):
        monkeypatch.setattr(bs, "MODEL_POLL_S", 0.01)

    def test_an_installed_model_loads_at_once_whatever_the_gpu_is_doing(self):
        state = BrainState.get_instance()
        with patch.object(orm, "model_installed", return_value=True), \
             patch.object(chat_keep_ready, "_gpu_busy_reason", return_value="ComfyUI is rendering") as busy:
            assert state._wait_for_model(MODEL, state._warm_up_generation) is True
        busy.assert_not_called()

    def test_a_model_pulled_later_is_picked_up(self):
        state = BrainState.get_instance()
        with patch.object(orm, "model_installed", side_effect=[False, False, True]) as installed, \
             patch.object(chat_keep_ready, "_gpu_busy_reason", return_value=None):
            assert state._wait_for_model(MODEL, state._warm_up_generation) is True
        assert installed.call_count == 3
        assert state.health.warm_up_status == WarmUpStatus.WARMING

    def test_a_model_pulled_later_waits_for_a_free_gpu(self):
        state = BrainState.get_instance()
        with patch.object(orm, "model_installed", side_effect=[False, True, True, True]), \
             patch.object(chat_keep_ready, "_gpu_busy_reason",
                          side_effect=["ComfyUI is rendering", "GPU work just finished", None]) as busy:
            assert state._wait_for_model(MODEL, state._warm_up_generation) is True
        assert busy.call_count == 3

    def test_a_newer_warm_up_ends_the_wait(self):
        state = BrainState.get_instance()
        generation = state._warm_up_generation
        done = []
        with patch.object(orm, "model_installed", return_value=False):
            waiter = threading.Thread(target=lambda: done.append(state._wait_for_model(MODEL, generation)))
            waiter.start()
            state._warm_up_generation += 1
            waiter.join(5)
        assert done == [False]

    def test_a_missing_model_is_not_requested(self, ollama_post, monkeypatch):
        monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
        state = BrainState.get_instance()
        state.llm, state.active_model = _LLM(8192), MODEL
        with patch("backend.utils.process_role.one_shot_command", return_value=False), \
             patch.object(orm, "model_installed", return_value=False):
            state._start_warmup()
            state._warm_up_thread.join(0.2)
            assert state.health.warm_up_status == WarmUpStatus.PENDING
            ollama_post.assert_not_called()
            state._warm_up_generation += 1
            state._warm_up_thread.join(5)
        assert not state._warm_up_thread.is_alive()


def test_a_listed_model_drops_its_no_info_answer():
    orm._unreachable_at[MODEL] = 1e18
    try:
        with patch.object(orm, "_ollama_tags", return_value=[{"name": MODEL}]):
            assert orm.model_installed(MODEL) is True
        assert MODEL not in orm._unreachable_at
        with patch.object(orm, "_ollama_tags", return_value=[]):
            assert orm.model_installed(MODEL) is False
    finally:
        orm._unreachable_at.pop(MODEL, None)
