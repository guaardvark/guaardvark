"""Cancel must stop this process's ComfyUI prompts and nobody else's.

ComfyUI is a shared sidecar: an unscoped ``/interrupt`` stops whatever is
sampling regardless of who queued it, and ``/queue {"clear": true}`` drops
other clients' pending work with it. These tests pin the scoped behaviour and
the one case that still falls back.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator


@pytest.fixture
def gen():
    """A generator with no real ComfyUI behind it and an empty prompt registry."""
    with ComfyUIVideoGenerator._queued_prompts_lock:
        ComfyUIVideoGenerator._queued_prompts.clear()
    instance = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    instance.comfy_url = "http://comfy.test"
    yield instance
    with ComfyUIVideoGenerator._queued_prompts_lock:
        ComfyUIVideoGenerator._queued_prompts.clear()


def _calls(post: MagicMock):
    """(url, json-body) for every POST the code made."""
    return [(c.args[0], c.kwargs.get("json")) for c in post.call_args_list]


def test_interrupt_targets_only_our_prompts(gen):
    ComfyUIVideoGenerator._track_prompt("ours-1")

    with patch("backend.services.comfyui_video_generator.requests.post") as post:
        assert gen.interrupt() is True

    assert _calls(post) == [
        ("http://comfy.test/interrupt", {"prompt_id": "ours-1"}),
        ("http://comfy.test/queue", {"delete": ["ours-1"]}),
    ]


def test_interrupt_never_clears_the_shared_queue(gen):
    ComfyUIVideoGenerator._track_prompt("ours-1")

    with patch("backend.services.comfyui_video_generator.requests.post") as post:
        gen.interrupt()

    bodies = [body for _url, body in _calls(post)]
    assert {"clear": True} not in bodies


def test_interrupt_accepts_an_explicit_prompt_id(gen):
    ComfyUIVideoGenerator._track_prompt("ours-1")
    ComfyUIVideoGenerator._track_prompt("ours-2")

    with patch("backend.services.comfyui_video_generator.requests.post") as post:
        assert gen.interrupt("ours-2") is True

    assert _calls(post) == [
        ("http://comfy.test/interrupt", {"prompt_id": "ours-2"}),
        ("http://comfy.test/queue", {"delete": ["ours-2"]}),
    ]
    # The one we did not name is still ours to cancel later.
    assert ComfyUIVideoGenerator._known_prompts() == ["ours-1"]


def test_interrupt_falls_back_to_unscoped_when_nothing_is_tracked(gen):
    """A cancel raised in Flask for a clip the Celery worker queued."""
    with patch("backend.services.comfyui_video_generator.requests.post") as post:
        assert gen.interrupt() is True

    # Unscoped stop, but still no queue clear — other clients keep their pending work.
    assert _calls(post) == [("http://comfy.test/interrupt", None)]


def test_a_completed_prompt_is_no_longer_a_target(gen):
    ComfyUIVideoGenerator._track_prompt("ours-1")
    ComfyUIVideoGenerator._forget_prompt("ours-1")

    with patch("backend.services.comfyui_video_generator.requests.post") as post:
        gen.interrupt()

    assert _calls(post) == [("http://comfy.test/interrupt", None)]


def test_interrupt_reports_failure_when_comfyui_is_unreachable(gen):
    ComfyUIVideoGenerator._track_prompt("ours-1")

    with patch(
        "backend.services.comfyui_video_generator.requests.post",
        side_effect=OSError("connection refused"),
    ):
        assert gen.interrupt() is False


def test_registry_is_shared_across_instances(gen):
    """The router replaces its generator on every get_active_generator()."""
    ComfyUIVideoGenerator._track_prompt("ours-1")

    other = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    other.comfy_url = "http://comfy.test"

    with patch("backend.services.comfyui_video_generator.requests.post") as post:
        assert other.interrupt() is True

    assert ("http://comfy.test/interrupt", {"prompt_id": "ours-1"}) in _calls(post)


def test_queue_delete_failure_does_not_sink_the_interrupt(gen):
    ComfyUIVideoGenerator._track_prompt("ours-1")

    def post(url, **_kwargs):
        if url.endswith("/queue"):
            raise OSError("queue endpoint angry")
        return MagicMock()

    with patch("backend.services.comfyui_video_generator.requests.post", side_effect=post):
        assert gen.interrupt() is True


def test_queueing_a_workflow_registers_it_for_cancel(gen):
    gen._last_queue_error = None
    response = MagicMock()
    response.json.return_value = {"prompt_id": "fresh-1"}
    response.raise_for_status.return_value = None

    with patch("backend.services.comfyui_video_generator.requests.post", return_value=response):
        assert gen._queue_prompt({"1": {}}) == "fresh-1"

    assert ComfyUIVideoGenerator._known_prompts() == ["fresh-1"]


def test_a_refused_queue_registers_nothing(gen):
    gen._last_queue_error = None
    response = MagicMock()
    response.json.return_value = {}
    response.raise_for_status.return_value = None

    with patch("backend.services.comfyui_video_generator.requests.post", return_value=response):
        gen._queue_prompt({"1": {}})

    assert ComfyUIVideoGenerator._known_prompts() == []

