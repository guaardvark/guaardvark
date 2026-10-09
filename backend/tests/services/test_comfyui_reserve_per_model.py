"""ComfyUI's --reserve-vram is declared per model and the launch follows the model.

One launch flag served two incompatible needs: MiniMax H3 at 1344x768 wants
5.0 GB reserved, Wan 2.2 14B GGUF wants 1.0 (at 5.0 it loads ~1 GB usable and
offloads 9.6 GB: 43 min per 5 s clip). The registry entry now carries the
number, the video generator relaunches ComfyUI when the running reserve is
another model's, and an explicit GUAARDVARK_COMFYUI_RESERVE_VRAM still wins.
"""

import logging
from types import SimpleNamespace

import pytest
import requests

from backend.plugins import plugin_manager as pm_module
from backend.plugins.plugin_manager import PluginManager, PluginStatus
from backend.services import comfyui_video_generator as cvg
from backend.services.comfyui_launch_flags import (
    RESERVE_VRAM_ENV,
    read_reserve_request,
    reserve_request_path,
    reserve_vram_cli_args,
    reserve_vram_from_argv,
    write_reserve_request,
)
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
from backend.services.video_model_registry import comfyui_reserve_vram_gb_for_model


def test_registry_declares_the_measured_reserves():
    assert comfyui_reserve_vram_gb_for_model("wan22-14b") == 1.0
    assert comfyui_reserve_vram_gb_for_model("wan22-14b-i2v") == 1.0
    assert comfyui_reserve_vram_gb_for_model("minimax-h3-int8") == 5.0
    assert comfyui_reserve_vram_gb_for_model("minimax-h3-ref2va-int8") == 5.0
    # A reference clip's frames ride through every step beside the render's.
    assert comfyui_reserve_vram_gb_for_model("minimax-h3-ref2va-int8", with_clips=True) == 8.0
    assert comfyui_reserve_vram_gb_for_model("minimax-h3-int8", with_clips=True) == 5.0
    assert comfyui_reserve_vram_gb_for_model("wan22-5b") is None
    assert comfyui_reserve_vram_gb_for_model("no-such-model") is None


def test_running_reserve_is_read_from_argv():
    argv = ["main.py", "--listen", "127.0.0.1", "--port", "8188", "--reserve-vram", "5", "--cache-none"]
    assert reserve_vram_from_argv(argv) == 5.0
    assert reserve_vram_from_argv(["main.py", "--reserve-vram=1.0"]) == 1.0
    assert reserve_vram_from_argv(["main.py", "--port", "8188"]) is None
    assert reserve_vram_from_argv(["main.py", "--reserve-vram", "lots"]) is None


def test_explicit_env_beats_the_request_beats_the_default(tmp_path):
    assert reserve_vram_cli_args({}) == ["--reserve-vram", "1"]
    assert reserve_vram_cli_args({}, requested=5.0) == ["--reserve-vram", "5"]
    assert reserve_vram_cli_args({RESERVE_VRAM_ENV: "2.5"}, requested=5.0) == ["--reserve-vram", "2.5"]

    assert read_reserve_request(tmp_path) is None
    write_reserve_request(tmp_path, 5.0)
    assert reserve_request_path(tmp_path).read_text() == "5\n"
    assert read_reserve_request(tmp_path) == 5.0


def test_plugin_start_sh_reads_the_request_file():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    text = (root / "plugins/comfyui/scripts/start.sh").read_text()
    assert 'pids/comfyui.reserve-vram' in text
    assert 'if [ -n "${GUAARDVARK_COMFYUI_RESERVE_VRAM:-}" ]' in text  # explicit env first
    assert '--reserve-vram "$RESERVE_VRAM"' in text


# ── the generator's relaunch decision ────────────────────────────────────

def _generator(tmp_path, monkeypatch, *, running_reserve, queue_busy=False):
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    gen.comfy_url = "http://127.0.0.1:8188"
    gen._project_root = tmp_path
    gen._object_info_cache = {"stale": True}
    gen.service_available = True
    gen._vram_booking = None
    calls = []

    def fake_get(url, timeout=None, **_kw):
        calls.append(url)
        if url.endswith("/system_stats"):
            argv = ["main.py", "--port", "8188"]
            if running_reserve is not None:
                argv += ["--reserve-vram", f"{running_reserve:g}"]
            return SimpleNamespace(status_code=200, json=lambda: {"system": {"argv": argv}}, raise_for_status=lambda: None)
        if url.endswith("/queue"):
            body = {"queue_running": [["0", "p1"]] if queue_busy else [], "queue_pending": []}
            return SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)
        if url == gen.comfy_url:
            return SimpleNamespace(status_code=200)
        raise requests.RequestException(url)

    monkeypatch.setattr(cvg, "requests", SimpleNamespace(
        get=fake_get, RequestException=requests.RequestException,
        exceptions=requests.exceptions,
    ))
    monkeypatch.delenv(RESERVE_VRAM_ENV, raising=False)
    return gen, calls


def _fake_manager(monkeypatch, success=True):
    restarts = []

    def restart_plugin(plugin_id, **kw):
        restarts.append((plugin_id, kw))
        return {"success": success, "error": None if success else "cooling down"}

    monkeypatch.setattr(pm_module, "get_plugin_manager", lambda: SimpleNamespace(restart_plugin=restart_plugin))
    return restarts


def test_mismatched_reserve_restarts_comfyui_with_the_models_value(tmp_path, monkeypatch, caplog):
    gen, _ = _generator(tmp_path, monkeypatch, running_reserve=5.0)
    restarts = _fake_manager(monkeypatch)

    with caplog.at_level(logging.INFO, logger=cvg.logger.name):
        assert gen._ensure_comfyui_reserve_for("wan22-14b-i2v") is None

    assert read_reserve_request(tmp_path) == 1.0
    assert restarts == [("comfyui", {"cancel_video_jobs": False})]
    assert gen._object_info_cache is None, "custom nodes are re-read after a relaunch"
    lines = [r.getMessage() for r in caplog.records if "Restarting ComfyUI" in r.getMessage()]
    assert lines == ["Restarting ComfyUI: wan22-14b-i2v needs --reserve-vram 1, it is running with 5"]


def test_matching_reserve_leaves_comfyui_alone(tmp_path, monkeypatch):
    gen, _ = _generator(tmp_path, monkeypatch, running_reserve=1.0)
    restarts = _fake_manager(monkeypatch)

    assert gen._ensure_comfyui_reserve_for("wan22-14b") is None

    assert restarts == [] and not reserve_request_path(tmp_path).exists()


def test_explicit_env_override_wins_without_a_restart(tmp_path, monkeypatch):
    gen, calls = _generator(tmp_path, monkeypatch, running_reserve=5.0)
    restarts = _fake_manager(monkeypatch)
    monkeypatch.setenv(RESERVE_VRAM_ENV, "5.0")

    assert gen._ensure_comfyui_reserve_for("wan22-14b") is None

    assert restarts == [] and calls == []


def test_model_without_a_declared_reserve_is_not_probed(tmp_path, monkeypatch):
    gen, calls = _generator(tmp_path, monkeypatch, running_reserve=5.0)
    restarts = _fake_manager(monkeypatch)

    assert gen._ensure_comfyui_reserve_for("wan22-5b") is None

    assert restarts == [] and calls == []


def test_unreadable_running_reserve_means_no_restart(tmp_path, monkeypatch):
    gen, _ = _generator(tmp_path, monkeypatch, running_reserve=None)
    restarts = _fake_manager(monkeypatch)

    assert gen._ensure_comfyui_reserve_for("minimax-h3-int8") is None
    assert restarts == []


def test_failed_restart_is_reported_not_rendered(tmp_path, monkeypatch):
    gen, _ = _generator(tmp_path, monkeypatch, running_reserve=1.0)
    _fake_manager(monkeypatch, success=False)

    error = gen._ensure_comfyui_reserve_for("minimax-h3-int8")

    assert error == "ComfyUI could not be restarted with --reserve-vram 5 for minimax-h3-int8: cooling down"


def test_busy_queue_is_never_pulled_out_from_under_a_render(tmp_path, monkeypatch, caplog):
    gen, _ = _generator(tmp_path, monkeypatch, running_reserve=1.0, queue_busy=True)
    restarts = _fake_manager(monkeypatch)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(cvg, "time", SimpleNamespace(
        time=lambda: clock.now, sleep=lambda s: setattr(clock, "now", clock.now + 400),
    ))

    with caplog.at_level(logging.WARNING, logger=cvg.logger.name):
        assert gen._ensure_comfyui_reserve_for("minimax-h3-int8") is None

    assert restarts == [] and not reserve_request_path(tmp_path).exists()
    assert any("still busy" in r.getMessage() for r in caplog.records)


# ── the manager side: a relaunch for flags keeps the batch alive ─────────

def _stop_stub(tmp_path):
    metadata = SimpleNamespace(type="service", port=8188, config=SimpleNamespace(service_url=None), endpoints={})
    stub = SimpleNamespace(
        registry=SimpleNamespace(get_plugin=lambda pid: metadata, get_plugin_dir=lambda pid: tmp_path),
        _plugin_status={"comfyui": PluginStatus.RUNNING},
        _broadcast_plugins_status=lambda reason: None,
        _check_service_running=lambda meta: False,
        _save_running=lambda: None,
        _kill_by_port=lambda port: None,
        _gate=SimpleNamespace(try_acquire=lambda pid: (True, 0.0, ""), release=lambda pid: None),
    )
    return stub


@pytest.mark.parametrize("cancel, expected", [(True, 1), (False, 0)])
def test_stop_cancels_video_batches_only_when_asked(tmp_path, monkeypatch, cancel, expected):
    from backend.services import batch_video_generator as bvg
    cancelled = []
    monkeypatch.setattr(bvg, "get_batch_video_generator", lambda: SimpleNamespace(
        cancel_all_active=lambda reason: cancelled.append(reason) or [],
    ))
    monkeypatch.setattr(pm_module.time, "sleep", lambda s: None)

    result = PluginManager.stop_plugin(_stop_stub(tmp_path), "comfyui", cancel_video_jobs=cancel)

    assert result["success"] is True
    assert len(cancelled) == expected
