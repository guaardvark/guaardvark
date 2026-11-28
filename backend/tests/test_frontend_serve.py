"""A served build shows a frontend change only after dist is rebuilt."""

import importlib.util
import json
from pathlib import Path

import pytest

from backend.utils import frontend_serve, update_state

REPO = Path(__file__).resolve().parents[2]


def _serve(root, mode):
    (root / ".start_cache").mkdir(exist_ok=True)
    (root / ".start_cache" / "frontend_serve").write_text(mode + "\n")


def _build_record(root, **fields):
    (root / ".start_cache" / "frontend_build.json").write_text(json.dumps(fields))


@pytest.mark.parametrize("content, expected", [("build\n", "build"), ("dev", "dev"), ("nginx", None)])
def test_serve_mode_reads_start_record(tmp_path, content, expected):
    _serve(tmp_path, content)
    assert frontend_serve.serve_mode(tmp_path) == expected


def test_no_record_means_no_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_serve.subprocess, "Popen", lambda *a, **k: pytest.fail("started a rebuild"))
    assert frontend_serve.serve_mode(tmp_path) is None
    assert frontend_serve.request_rebuild(tmp_path) is False
    _serve(tmp_path, "dev")
    assert frontend_serve.request_rebuild(tmp_path) is False


def test_dev_server_change_is_ready_at_once(tmp_path):
    _serve(tmp_path, "dev")
    assert frontend_serve.interface_state(tmp_path, 100.0) == {"ready_ts": 100.0, "failed": None}


def test_served_build_is_ready_when_a_rebuild_ended_after_the_change(tmp_path):
    _serve(tmp_path, "build")
    assert frontend_serve.interface_state(tmp_path, 100.0)["ready_ts"] is None  # no record
    _build_record(tmp_path, state="ok", exit_code=0)  # start.sh's own record carries no ts
    assert frontend_serve.interface_state(tmp_path, 100.0)["ready_ts"] is None
    _build_record(tmp_path, state="ok", ts=90.0)
    assert frontend_serve.interface_state(tmp_path, 100.0)["ready_ts"] is None
    _build_record(tmp_path, state="running", ts=120.0)
    assert frontend_serve.interface_state(tmp_path, 100.0) == {"ready_ts": None, "failed": None}
    _build_record(tmp_path, state="ok", ts=130.0)
    assert frontend_serve.interface_state(tmp_path, 100.0) == {"ready_ts": 130.0, "failed": None}


def test_failed_rebuild_asks_for_a_restart(tmp_path):
    _serve(tmp_path, "build")
    _build_record(tmp_path, state="failed", exit_code=137, ts=130.0)
    update_state.record_update_applied(tmp_path, ["frontend/src/App.jsx"], now=100.0, boot_time=50.0)
    state = update_state.restart_state(tmp_path, None, boot_time=50.0)
    assert state["restart_required"] is True
    assert "out of memory" in state["restart_reason"]
    assert state["update"]["ui_ts"] is None


def test_frontend_update_requests_a_rebuild_only_for_frontend_paths(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(frontend_serve, "request_rebuild", lambda root: asked.append(root) or True)
    update_state.record_update_applied(tmp_path, ["backend/app.py"], now=100.0, boot_time=50.0)
    assert asked == []
    update_state.record_update_applied(tmp_path, ["frontend/src/App.jsx"], now=101.0, boot_time=50.0)
    assert asked == [tmp_path]
    # A later backend-only apply does not rebuild again for the earlier files.
    update_state.record_update_applied(tmp_path, ["backend/config.py"], now=102.0, boot_time=50.0)
    assert asked == [tmp_path]


def _rebuild_module():
    spec = importlib.util.spec_from_file_location("frontend_rebuild", REPO / "scripts" / "frontend_rebuild.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_build(marker):
    def build(frontend, fresh, log_path):
        fresh.mkdir()
        (fresh / "index.html").write_text(marker)
        return 0
    return build


def test_rebuild_swaps_in_a_finished_build_and_stamps_it(tmp_path):
    rebuild = _rebuild_module()
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("old")
    (dist / ".build_fp").write_text("fp-old\n")

    rc = rebuild.rebuild(tmp_path, build=_fake_build("new"), wait_for_memory=lambda: True,
                         fingerprint=lambda root: "fp-new")

    assert rc == 0
    assert (dist / "index.html").read_text() == "new"
    assert (dist / ".build_fp").read_text().strip() == "fp-new"
    assert not (tmp_path / "frontend" / "dist.next").exists()
    assert not (tmp_path / "frontend" / "dist.old").exists()
    record = json.loads((tmp_path / ".start_cache" / "frontend_build.json").read_text())
    assert record["state"] == "ok" and record["fingerprint"] == "fp-new" and record["ts"] > 0


def test_rebuild_keeps_the_served_build_when_the_new_one_fails(tmp_path):
    rebuild = _rebuild_module()
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("old")

    def failing(frontend, fresh, log_path):
        fresh.mkdir()
        return 1

    rc = rebuild.rebuild(tmp_path, build=failing, wait_for_memory=lambda: True, fingerprint=lambda root: "fp-new")

    assert rc == 1
    assert (dist / "index.html").read_text() == "old"
    assert not (tmp_path / "frontend" / "dist.next").exists()
    record = json.loads((tmp_path / ".start_cache" / "frontend_build.json").read_text())
    assert record["state"] == "failed" and record["exit_code"] == 1


def test_rebuild_skips_the_build_when_dist_already_matches(tmp_path):
    rebuild = _rebuild_module()
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("current")
    (dist / ".build_fp").write_text("fp\n")

    rc = rebuild.rebuild(tmp_path, build=lambda *a: pytest.fail("built"), wait_for_memory=lambda: True,
                         fingerprint=lambda root: "fp")

    assert rc == 0
    assert json.loads((tmp_path / ".start_cache" / "frontend_build.json").read_text())["state"] == "ok"


def test_rebuild_does_not_start_without_free_memory(tmp_path):
    rebuild = _rebuild_module()
    rc = rebuild.rebuild(tmp_path, build=lambda *a: pytest.fail("built"), wait_for_memory=lambda: False,
                         fingerprint=lambda root: "fp")
    assert rc == 1
    record = json.loads((tmp_path / ".start_cache" / "frontend_build.json").read_text())
    assert record["state"] == "failed" and "memory" in record["reason"]


def test_memory_wait_gives_up_after_the_deadline():
    rebuild = _rebuild_module()
    assert rebuild._wait_for_memory(needed=10, wait_s=0, available=lambda: 5, sleep=lambda s: None) is False
    assert rebuild._wait_for_memory(needed=10, wait_s=0, available=lambda: 10, sleep=lambda s: None) is True
    assert rebuild._wait_for_memory(needed=10, wait_s=0, available=lambda: None, sleep=lambda s: None) is True


@pytest.fixture
def own_checkout(tmp_path, monkeypatch):
    """A stand-in install root that records what a guarded write reports."""
    from backend.services import guarded_code_service

    reported = []
    monkeypatch.setattr(guarded_code_service, "default_repo_root", lambda: tmp_path)
    monkeypatch.setattr(update_state, "record_update_applied", lambda root, paths: reported.append(list(paths)))
    return guarded_code_service, tmp_path, reported


def test_guarded_write_to_the_interface_reports_it_when_a_build_is_served(own_checkout):
    service, root, reported = own_checkout
    _serve(root, "build")
    service._interface_file_changed(root / "frontend" / "src" / "App.jsx")
    assert reported == [["frontend/src/App.jsx"]]


def test_guarded_write_reports_nothing_under_the_dev_server(own_checkout):
    service, root, reported = own_checkout
    _serve(root, "dev")
    service._interface_file_changed(root / "frontend" / "src" / "App.jsx")
    assert reported == []


def test_guarded_write_outside_the_interface_or_the_install_reports_nothing(own_checkout, tmp_path_factory):
    service, root, reported = own_checkout
    _serve(root, "build")
    service._interface_file_changed(root / "backend" / "app.py")
    service._interface_file_changed(tmp_path_factory.mktemp("worktree") / "frontend" / "src" / "App.jsx")
    assert reported == []
