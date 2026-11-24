"""A cancel stops the ComfyUI prompts its own job queued and nothing else.

ComfyUI is shared with the Studio, batch runs, scheduled jobs and anyone using
it directly, so a job tags each prompt with an owner and a cancel, from any
process, stops only that owner's prompts.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services import comfyui_prompt_owner as owner_mod
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator

COMFY = "http://comfy.test"


@pytest.fixture
def gen():
    with ComfyUIVideoGenerator._queued_prompts_lock:
        ComfyUIVideoGenerator._queued_prompts.clear()
    instance = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    instance.comfy_url = COMFY
    yield instance
    with ComfyUIVideoGenerator._queued_prompts_lock:
        ComfyUIVideoGenerator._queued_prompts.clear()


def _queue(running=(), pending=()):
    """A /queue reply; each entry is (prompt_id, extra_data)."""
    def items(entries):
        return [[n, pid, {}, extra, []] for n, (pid, extra) in enumerate(entries)]
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"queue_running": items(running), "queue_pending": items(pending)}
    return response


def _posts(post: MagicMock):
    return [(c.args[0], c.kwargs.get("json")) for c in post.call_args_list]


def test_the_owner_tag_rides_in_extra_data_beside_other_settings():
    payload = {"prompt": {}, "extra_data": {"preview_method": "none"}}
    owner_mod.tag_payload(payload, "music_video:7")
    assert payload["extra_data"] == {"preview_method": "none", "guaardvark_owner": "music_video:7"}
    assert owner_mod.tag_payload({"prompt": {}}, None) == {"prompt": {}}


def test_queueing_with_an_owner_tags_the_prompt(gen):
    gen._last_queue_error = None
    response = MagicMock()
    response.json.return_value = {"prompt_id": "p-1"}
    response.raise_for_status.return_value = None

    with patch("requests.post", return_value=response) as post:
        assert gen._queue_prompt({"1": {}}, owner="music_video:7") == "p-1"

    body = post.call_args.kwargs["json"]
    assert body["extra_data"]["guaardvark_owner"] == "music_video:7"


def test_a_cancel_by_owner_stops_only_that_owners_prompts(gen):
    reply = _queue(
        running=[("theirs-running", {"guaardvark_owner": "video_batch:night"})],
        pending=[
            ("ours-pending", {"guaardvark_owner": "music_video:7"}),
            ("untagged", {}),
            ("other-video", {"guaardvark_owner": "music_video:8"}),
        ],
    )
    with patch("requests.get", return_value=reply) as get, patch("requests.post") as post:
        assert gen.interrupt(owner="music_video:7") is True

    assert get.call_args.args[0] == f"{COMFY}/queue"
    assert _posts(post) == [
        (f"{COMFY}/queue", {"delete": ["ours-pending"]}),
        (f"{COMFY}/interrupt", {"prompt_id": "ours-pending"}),
    ]


def test_a_running_prompt_of_ours_is_interrupted_by_id(gen):
    reply = _queue(running=[("ours-running", {"guaardvark_owner": "music_video:7"})])
    with patch("requests.get", return_value=reply), patch("requests.post") as post:
        assert gen.interrupt(owner="music_video:7") is True

    assert (f"{COMFY}/interrupt", {"prompt_id": "ours-running"}) in _posts(post)
    assert all(body is not None for _url, body in _posts(post))


def test_a_cancel_with_nothing_of_ours_queued_sends_nothing(gen):
    """The case that stopped other renders: a cancel while ComfyUI renders someone else's work."""
    ComfyUIVideoGenerator._track_prompt("another-job-in-this-process")
    reply = _queue(running=[("theirs", {"guaardvark_owner": "video_batch:night"})])
    with patch("requests.get", return_value=reply), patch("requests.post") as post:
        assert gen.interrupt(owner="music_video:7") is False

    post.assert_not_called()
    assert ComfyUIVideoGenerator._known_prompts() == ["another-job-in-this-process"]


def test_an_unreadable_queue_sends_nothing(gen):
    with patch("requests.get", side_effect=OSError("refused")), patch("requests.post") as post:
        assert gen.interrupt(owner="music_video:7") is False
    post.assert_not_called()


def test_the_router_passes_the_owner_through():
    from backend.services.video_generation_router import VideoGenerationRouter

    router = VideoGenerationRouter.__new__(VideoGenerationRouter)
    comfy = MagicMock()
    comfy.interrupt.return_value = True
    router._check_comfyui = lambda: True
    router._get_comfyui = lambda: comfy

    assert router.interrupt(owner="music_video:7") is True
    comfy.interrupt.assert_called_once_with(None, owner="music_video:7")


def test_the_job_cancel_for_a_music_video_is_scoped(monkeypatch):
    from backend.services import job_cancel

    mv = MagicMock(current_stage="generating", clips=[])
    session = MagicMock()
    session.get.return_value = mv
    fake_models = MagicMock(MusicVideo=object, db=MagicMock(session=session))
    monkeypatch.setitem(sys.modules, "backend.models", fake_models)
    router = MagicMock()
    monkeypatch.setattr("backend.services.video_generation_router.get_video_router", lambda: router)

    assert job_cancel._cancel_music_video("12") is True
    router.interrupt.assert_called_once_with(owner="music_video:12")


# ---- The test run itself ----

def _root_conftest():
    path = Path(__file__).resolve().parents[3] / "conftest.py"
    for mod in list(sys.modules.values()):
        if getattr(mod, "__file__", None) and Path(mod.__file__).resolve() == path:
            return mod
    raise AssertionError("the root conftest is not loaded")


def test_backend_config_points_at_a_dead_comfyui():
    guard = _root_conftest()
    if guard._SERVICE_GATE.allowed:
        pytest.skip("GUAARDVARK_TEST_LIVE_SERVICES=1 lifts the dead URL")
    from backend.config import COMFYUI_URL

    assert COMFYUI_URL == guard.DEAD_COMFYUI_URL == "http://127.0.0.1:9"


def test_a_connect_to_the_live_comfyui_port_is_refused_before_it_is_made(monkeypatch):
    guard = _root_conftest()
    monkeypatch.setattr(guard._SERVICE_GATE, "allowed", False)
    reached = []
    # Should the guard ever let it through, this stands in for the real connect.
    monkeypatch.setattr(guard, "_real_connect", lambda sock, address: reached.append(address))

    with pytest.raises(ConnectionRefusedError, match=":8188"):
        socket.create_connection(("127.0.0.1", 8188), timeout=1)

    assert reached == []
    assert ("ComfyUI", 8188) in guard._SERVICE_GATE.refused
