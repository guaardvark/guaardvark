"""The ComfyUI updater rehearsed against a fake tree: git and pip are a fake
runner, the plugin controller is a recorder, HTTP answers come from the tree on
disk. Nothing here touches a venv, a service, or the network."""

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services import comfyui_update_service as cus
from backend.services.comfyui_update_service import (
    ComfyUIUpdateService,
    UpdateRefused,
    manifest_marker_nodes,
    requirement_names,
    requirements_stamp,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD = "0.33.0"
NEW = "0.39.0"
TAG = "v0.39.0"
COMMIT = "b0b743566f65daafc423b4fea8a2fbda94b3384a"
OLD_REQS = "comfyui-frontend-package==1.49.6\ncomfy-kitchen==0.2.31\ntorch\nav>=16.0.0\n"
NEW_REQS = "comfyui-frontend-package==1.53.10\ncomfy-kitchen==0.2.37\ntorch\nav>=17.0.0\n#non essential\nspandrel\n"
BASE_NODES = ["UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple", "VHS_VideoCombine", "RIFE VFI"]
NEW_NODES = ["TextEncodeQwenImage21", "QwenImage21Cache"]


def make_tree(path: Path, version: str, reqs: str, *, state=()):
    (path / "comfy" / "ldm" / "models").mkdir(parents=True)
    (path / "main.py").write_text("")
    (path / "comfy" / "ldm" / "models" / "autoencoder.py").write_text("")
    (path / "comfyui_version.py").write_text(f'__version__ = "{version}"\n')
    (path / "requirements.txt").write_text(reqs)
    for name in state:
        d = path / name
        d.mkdir()
        (d / f"{name}.marker").write_text(name)
    return path


class FakeRunner:
    """git clone / rev-parse, the installed-packages probe, and pip install."""

    def __init__(self, packages, *, clone_version=NEW, clone_commit=COMMIT, clone_reqs=NEW_REQS):
        self.packages = dict(packages)
        self.clone_version = clone_version
        self.clone_commit = clone_commit
        self.clone_reqs = clone_reqs
        self.pip_fail = False
        self.clone_fail = False
        self.calls = []

    @staticmethod
    def _ok(stdout=""):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    @staticmethod
    def _bad(stderr):
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    def __call__(self, argv, *, cwd=None, timeout=None, env=None):
        argv = [str(a) for a in argv]
        self.calls.append((argv, env))
        if argv[:2] == ["git", "clone"]:
            if self.clone_fail:
                return self._bad("fatal: could not read from remote")
            dest = Path(argv[-1])
            make_tree(dest, self.clone_version, self.clone_reqs, state=("input", "models", "custom_nodes"))
            (dest / ".git").mkdir()
            return self._ok()
        if argv[:2] == ["git", "-C"] and argv[3:5] == ["rev-parse", "HEAD"]:
            return self._ok(self.clone_commit + "\n")
        if argv[1] == "-c":
            names = argv[3:]
            return self._ok(json.dumps({n: self.packages.get(n) for n in names}) + "\n")
        if argv[1:4] == ["-m", "pip", "install"]:
            if self.pip_fail:
                return self._bad("ERROR: No matching distribution found for comfy-kitchen==0.2.37")
            specs = argv[4:]
            if "-r" in specs:
                specs = Path(specs[specs.index("-r") + 1]).read_text().splitlines()
            for spec in specs:
                m = re.match(r"([A-Za-z0-9_.-]+)==(\S+)", spec.strip())
                if m:
                    self.packages[m.group(1)] = m.group(2)
            return self._ok()
        raise AssertionError(f"unexpected command: {argv}")

    def pip_calls(self):
        return [a for a, _ in self.calls if a[1:4] == ["-m", "pip", "install"]]


class FakeController:
    def __init__(self):
        self.running = True
        self.calls = []
        self.start_fail = False
        self.start_fail_once = False
        self.stop_fail_after = None  # fail every stop after this many stops

    def stop(self):
        self.calls.append("stop")
        if self.stop_fail_after is not None and self.calls.count("stop") > self.stop_fail_after:
            return {"success": False, "error": "process still running"}
        self.running = False
        return {"success": True}

    def start(self):
        self.calls.append("start")
        if self.start_fail or self.start_fail_once:
            self.start_fail_once = False
            return {"success": False, "error": "start.sh exited 1"}
        self.running = True
        return {"success": True}


class FakeHttp:
    """Answers come from the tree at the live path, like a real server would."""

    def __init__(self, svc_root: Path, controller: FakeController):
        self.root = svc_root
        self.controller = controller
        self.queue = {"queue_running": [], "queue_pending": []}
        self.missing_nodes = set()

    def __call__(self, url, timeout):
        path = url.split("8188", 1)[-1] or "/"
        if path == "/queue":
            return 200, self.queue
        if not self.controller.running:
            return None, None
        if path == "/":
            return 200, {}
        if path == "/system_stats":
            return 200, {"system": {"comfyui_version": cus.read_tree_version(self.root / "ComfyUI")}}
        if path == "/object_info":
            nodes = list(BASE_NODES)
            if cus.read_tree_version(self.root / "ComfyUI") == NEW:
                nodes += NEW_NODES
            return 200, {n: {} for n in nodes if n not in self.missing_nodes}
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture(autouse=True)
def _fresh_status():
    cus.reset_status()
    yield
    cus.reset_status()


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "plugins" / "comfyui"
    root.mkdir(parents=True)
    (root / "releases.json").write_text(json.dumps({
        "repo": "https://github.com/Comfy-Org/ComfyUI",
        "target": TAG,
        "min_free_gb": 2,
        "state_dirs": ["models", "custom_nodes", "user", "input", "output", "temp"],
        "releases": {TAG: {
            "version": NEW, "commit": COMMIT,
            "packages": {"comfyui-frontend-package": "1.53.10", "comfy-kitchen": "0.2.37"},
            "post_start_nodes": NEW_NODES, "notes": "test",
        }},
    }))
    (root / "plugin.json").write_text(json.dumps({
        "instance_check": {"paths": ["/object_info/UNETLoader", "/object_info/UnetLoaderGGUF"]},
    }))
    (root / "custom_nodes.manifest").write_text(
        "# header\n#   VHS_VideoCombine            -> ComfyUI-VideoHelperSuite\n"
        "#   RIFE VFI                    -> ComfyUI-Frame-Interpolation\n"
        "#   CogVideo* / DownloadAndLoad -> wrapper\n"
        "ComfyUI-VideoHelperSuite|https://example.invalid/x|abc\n"
    )
    make_tree(root / "ComfyUI", OLD, OLD_REQS, state=("models", "custom_nodes", "user", "output"))
    (root / ".requirements_installed").write_text(requirements_stamp(OLD_REQS.encode()))
    packages = {"comfyui-frontend-package": "1.49.6", "comfy-kitchen": "0.2.31", "torch": "2.6.0", "av": "18.1.0"}
    runner = FakeRunner(packages)
    controller = FakeController()
    http = FakeHttp(root, controller)
    w = SimpleNamespace(root=root, runner=runner, controller=controller, http=http,
                        busy=[None], free_gb=[100.0], invalidated=[], log=tmp_path / "logs" / "comfyui-update.log")
    w.svc = ComfyUIUpdateService(
        root, python=Path("/fake/venv/bin/python"), controller=controller,
        base_url="http://127.0.0.1:8188", runner=runner, http_get=http,
        busy_probe=lambda: w.busy[0],
        disk_usage=lambda p: SimpleNamespace(free=w.free_gb[0] * 1e9),
        log_path=w.log, invalidate_caches=lambda: w.invalidated.append(True),
        sleep=lambda s: None, health_timeout_s=0, health_poll_s=0,
    )
    return w


def phases_logged(status):
    return [m.group(1) for line in status["log_tail"] for m in [re.search(r" \[([a-z-]+)\] ", line)] if m]


def assert_live_is_old(w):
    """The live tree is the old one again, with every state dir and the old stamp."""
    live = w.root / "ComfyUI"
    assert cus.read_tree_version(live) == OLD
    for d in ("models", "custom_nodes", "user", "output"):
        assert (live / d / f"{d}.marker").read_text() == d
    assert not list(w.root.glob("ComfyUI.prev-*"))
    assert (w.root / ".requirements_installed").read_text() == requirements_stamp(OLD_REQS.encode())
    assert w.runner.packages["comfy-kitchen"] == "0.2.31"
    assert w.runner.packages["comfyui-frontend-package"] == "1.49.6"


# ── declared data ──────────────────────────────────────────────────────

def test_releases_json_declares_the_target():
    data = json.loads((REPO_ROOT / "plugins" / "comfyui" / "releases.json").read_text())
    assert data["repo"] == "https://github.com/Comfy-Org/ComfyUI"
    assert data["target"] in data["releases"]
    assert data["min_free_gb"] > 0
    assert data["state_dirs"] == ["models", "custom_nodes", "user", "input", "output", "temp"]
    rel = data["releases"][data["target"]]
    for key in ("version", "commit", "packages", "post_start_nodes", "notes"):
        assert key in rel, key
    assert re.fullmatch(r"[0-9a-f]{40}", rel["commit"])
    assert rel["version"] == data["target"].lstrip("v")
    assert all(re.fullmatch(r"\d+(\.\d+)*", v) for v in rel["packages"].values())


def test_start_sh_declares_the_float_patch():
    src = (REPO_ROOT / "plugins" / "comfyui" / "scripts" / "start.sh").read_text()
    assert "comfy/float.py" in src
    assert "_CK_STOCHASTIC_ROUNDING_AVAILABLE = True\\nexcept (AttributeError, ImportError):" in src
    assert "_CK_STOCHASTIC_ROUNDING_AVAILABLE = True\\nexcept Exception:" in src


def test_manifest_marker_nodes_from_the_shipped_manifest():
    nodes = manifest_marker_nodes(REPO_ROOT / "plugins" / "comfyui" / "custom_nodes.manifest")
    assert {"VHS_VideoCombine", "UnetLoaderGGUF", "RIFE VFI", "ImageResizeKJ"} <= set(nodes)
    assert not any("*" in n or n == "DownloadAndLoad" for n in nodes)


def test_requirement_names_and_stamp():
    assert requirement_names(NEW_REQS) == ["comfyui-frontend-package", "comfy-kitchen", "torch", "av", "spandrel"]
    assert requirements_stamp(b"x") == "9dd4e461268c8034f5c8564e155c67a6\n"


# ── check() ────────────────────────────────────────────────────────────

def test_check_reports_an_available_update(world):
    c = world.svc.check()
    assert c["installed"] == OLD and c["target"] == NEW and c["target_tag"] == TAG
    assert c["available"] is True and c["busy"] is None
    assert c["disk_free_gb"] == 100.0 and c["min_free_gb"] == 2
    assert c["package_changes"] == [
        {"name": "comfyui-frontend-package", "from": "1.49.6", "to": "1.53.10"},
        {"name": "comfy-kitchen", "from": "0.2.31", "to": "0.2.37"},
    ]
    assert c["previous"] is None
    assert c["keeps"] == ["models", "custom_nodes", "user", "input", "output", "temp"]
    assert "GitHub" in c["download"] and "PyPI" in c["download"]


def test_check_busy_from_the_queue_and_from_the_job_probe(world):
    world.http.queue = {"queue_running": [1], "queue_pending": [2, 3]}
    assert world.svc.check()["busy"] == "ComfyUI is working (1 running, 2 queued)"
    world.http.queue = {"queue_running": [], "queue_pending": []}
    world.busy[0] = "1 active video render job(s) in progress"
    assert world.svc.check()["busy"] == "1 active video render job(s) in progress"


def test_check_sees_a_kept_previous_tree_and_nothing_to_update(world):
    make_tree(world.root / f"ComfyUI.prev-{OLD}", OLD, OLD_REQS)
    (world.root / "ComfyUI" / "comfyui_version.py").write_text(f'__version__ = "{NEW}"\n')
    c = world.svc.check()
    assert c["previous"] == OLD and c["available"] is False


# ── refusals ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("setup, needle", [
    (lambda w: w.busy.__setitem__(0, "job holds GPU"), "busy"),
    (lambda w: w.free_gb.__setitem__(0, 1.5), "1.5 GB free"),
    (lambda w: (w.root / "ComfyUI" / "comfyui_version.py").write_text(f'__version__ = "{NEW}"\n'), "already at or past"),
    (lambda w: cus._status.__setitem__("state", "running"), "already running"),
])
def test_start_refuses(world, setup, needle):
    setup(world)
    with pytest.raises(UpdateRefused, match=needle):
        world.svc.start(background=False)
    assert world.controller.calls == []
    assert not (world.root / "ComfyUI.next").exists()


def test_start_refuses_an_undeclared_target(world):
    with pytest.raises(UpdateRefused, match="not a declared"):
        world.svc.start("v9.9.9", background=False)


# ── the happy path ─────────────────────────────────────────────────────

def test_update_succeeds_end_to_end(world):
    status = world.svc.start(background=False)
    assert status["state"] == "succeeded", status
    assert status["from"] == OLD and status["to"] == NEW and status["percent"] == 100
    assert status["message"] == f"ComfyUI updated from {OLD} to {NEW}."
    assert phases_logged(status) == ["fetch", "snapshot", "stop", "swap", "install", "start", "post-check", "done"]
    assert world.controller.calls == ["stop", "start"]

    live = world.root / "ComfyUI"
    assert cus.read_tree_version(live) == NEW
    for d in ("models", "custom_nodes", "user", "output"):
        assert (live / d / f"{d}.marker").read_text() == d, d
    # The clone's placeholder state dirs were stripped; missing ones are recreated empty.
    assert (live / "input").is_dir() and not (live / "input" / "input.marker").exists()
    assert (live / "temp").is_dir()
    assert not (world.root / ".update_job.json").exists()
    prev = world.root / f"ComfyUI.prev-{OLD}"
    assert cus.read_tree_version(prev) == OLD and not (prev / "models").exists()
    assert not (world.root / "ComfyUI.next").exists()

    assert (world.root / ".requirements_installed").read_text() == requirements_stamp(NEW_REQS.encode())
    assert world.runner.packages["comfy-kitchen"] == "0.2.37"
    pip = world.runner.pip_calls()
    assert pip == [["/fake/venv/bin/python", "-m", "pip", "install", "--quiet", "-r", str(live / "requirements.txt")]]
    assert world.invalidated == [True]
    assert world.log.read_text().count("[done]") == 1
    assert world.svc.check()["previous"] == OLD


def test_pip_runs_with_the_backend_constraints(world, monkeypatch):
    monkeypatch.delenv("PIP_CONSTRAINT", raising=False)
    world.svc.start(background=False)
    env = next(env for argv, env in world.runner.calls if argv[1:4] == ["-m", "pip", "install"])
    assert env["PIP_CONSTRAINT"].endswith(os.path.join("backend", "constraints.txt"))


def test_update_keeps_only_one_previous_tree(world):
    make_tree(world.root / "ComfyUI.prev-0.31.0", "0.31.0", OLD_REQS)
    world.svc.start(background=False)
    assert [p.name for p in world.root.glob("ComfyUI.prev-*")] == [f"ComfyUI.prev-{OLD}"]


def test_stale_bare_clone_is_replaced(world):
    make_tree(world.root / "ComfyUI.next", "0.38.0", NEW_REQS)
    (world.root / "ComfyUI.next" / "junk").write_text("")
    status = world.svc.start(background=False)
    assert status["state"] == "succeeded"
    assert any("removing stale bare clone ComfyUI.next" in line for line in status["log_tail"])
    assert not (world.root / "ComfyUI.next").exists()
    assert cus.read_tree_version(world.root / "ComfyUI") == NEW


def test_next_tree_holding_state_is_set_aside_never_deleted(world):
    stale = make_tree(world.root / "ComfyUI.next", "0.38.0", NEW_REQS, state=("models",))
    (stale / "models" / "big.safetensors").write_text("weights")
    with pytest.raises(UpdateRefused, match=r"ComfyUI.next held models and was set aside as .*ComfyUI.next.stale-"):
        world.svc.start(background=False)
    parked = [p for p in world.root.iterdir() if p.name.startswith("ComfyUI.next.stale-")]
    assert len(parked) == 1
    assert (parked[0] / "models" / "big.safetensors").read_text() == "weights"
    assert not (world.root / "ComfyUI.next").exists()
    assert world.controller.calls == []
    assert_live_is_old(world)
    # A second attempt proceeds and leaves the parked tree alone.
    assert world.svc.start(background=False)["state"] == "succeeded"
    assert (parked[0] / "models" / "big.safetensors").exists()


def test_older_prev_tree_holding_state_is_kept(world):
    kept = make_tree(world.root / "ComfyUI.prev-0.31.0", "0.31.0", OLD_REQS, state=("user",))
    status = world.svc.start(background=False)
    assert status["state"] == "succeeded"
    assert (kept / "user" / "user.marker").exists()
    assert any("keeping ComfyUI.prev-0.31.0: it holds user" in line for line in status["log_tail"])


# ── rollback at each phase ─────────────────────────────────────────────

def test_fetch_commit_mismatch_touches_nothing(world):
    world.runner.clone_commit = "0" * 40
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert status["message"].startswith("Update failed at fetch: fetched commit 000000000000 is not the declared")
    assert status["message"].endswith(f"ComfyUI {OLD} was not touched and is still running.")
    assert world.controller.calls == []
    assert not (world.root / "ComfyUI.next").exists()
    assert_live_is_old(world)


def test_fetch_wrong_version_touches_nothing(world):
    world.runner.clone_version = "0.38.0"
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert "reports version 0.38.0" in status["message"]
    assert world.controller.calls == []
    assert_live_is_old(world)


def test_work_appearing_before_stop_is_refused(world):
    calls = []

    def probe():
        calls.append(1)
        return "render started" if len(calls) > 1 else None

    world.svc._busy_probe = probe
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back" and "Update failed at stop: ComfyUI became busy" in status["message"]
    assert world.controller.calls == []
    assert_live_is_old(world)


def test_pip_failure_rolls_back(world):
    world.runner.pip_fail = True
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert status["message"].startswith("Update failed at install: pip install failed")
    assert status["message"].endswith(f"ComfyUI {OLD} is running again.")
    assert world.controller.calls == ["stop", "start"]
    assert world.controller.running is True
    assert_live_is_old(world)
    nxt = world.root / "ComfyUI.next"
    assert cus.read_tree_version(nxt) == NEW and not (nxt / "models").exists()


def test_start_failure_rolls_back_packages_and_stamp(world):
    world.controller.start_fail_once = True
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert status["message"].startswith("Update failed at start: could not start ComfyUI: start.sh exited 1")
    assert world.controller.calls == ["stop", "start", "stop", "start"]
    pip = world.runner.pip_calls()
    assert pip[-1][5:] == ["comfyui-frontend-package==1.49.6", "comfy-kitchen==0.2.31"]
    assert_live_is_old(world)


def test_missing_node_after_start_rolls_back(world):
    world.http.missing_nodes = {"QwenImage21Cache"}
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert "Update failed at post-check: the new ComfyUI lacks node(s): QwenImage21Cache" in status["message"]
    assert world.controller.calls == ["stop", "start", "stop", "start"]
    assert world.invalidated == []
    assert_live_is_old(world)


def test_wrong_running_version_rolls_back(world):
    real = world.http

    def http(url, timeout):
        if url.endswith("/system_stats"):
            return 200, {"system": {"comfyui_version": "0.0.1"}}
        return real(url, timeout)

    world.svc.http_get = http
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert "reports version 0.0.1, expected 0.39.0" in status["message"]
    assert_live_is_old(world)


def test_failed_rollback_reports_manual_steps(world):
    world.controller.start_fail = True
    status = world.svc.start(background=False)
    assert status["state"] == "failed"
    msg = status["message"]
    assert msg.startswith("Update failed at start:")
    assert "Rolling back also failed" in msg
    assert "ComfyUI is stopped" in msg
    assert "comfy-kitchen==0.2.31" in msg and str(world.root / "ComfyUI") in msg
    assert str(world.log) in msg
    # The trees and state dirs were still put back; only the restart is missing.
    assert cus.read_tree_version(world.root / "ComfyUI") == OLD
    assert (world.root / "ComfyUI" / "models" / "models.marker").exists()
    assert "ROLLBACK FAILED" in world.log.read_text()


# ── restore_previous ───────────────────────────────────────────────────

def test_restore_previous_swaps_back(world):
    assert world.svc.start(background=False)["state"] == "succeeded"
    world.controller.calls.clear()
    status = world.svc.restore_previous(background=False)
    assert status["state"] == "succeeded", status
    assert status["from"] == NEW and status["to"] == OLD
    assert phases_logged(status) == ["snapshot", "stop", "swap", "install", "start", "post-check", "done"]
    live = world.root / "ComfyUI"
    assert cus.read_tree_version(live) == OLD
    for d in ("models", "custom_nodes", "user", "output"):
        assert (live / d / f"{d}.marker").read_text() == d, d
    assert [p.name for p in world.root.glob("ComfyUI.prev-*")] == [f"ComfyUI.prev-{NEW}"]
    assert world.runner.packages["comfy-kitchen"] == "0.2.31"
    assert (world.root / ".requirements_installed").read_text() == requirements_stamp(OLD_REQS.encode())
    assert world.svc.check()["previous"] == NEW


def test_restore_previous_refuses_without_a_kept_tree(world):
    with pytest.raises(UpdateRefused, match="no previous ComfyUI tree"):
        world.svc.restore_previous(background=False)


def test_restore_previous_rolls_back_on_pip_failure(world):
    assert world.svc.start(background=False)["state"] == "succeeded"
    world.runner.pip_fail = True
    status = world.svc.restore_previous(background=False)
    assert status["state"] == "rolled_back"
    assert status["message"].endswith(f"ComfyUI {NEW} is running again.")
    live = world.root / "ComfyUI"
    assert cus.read_tree_version(live) == NEW
    assert (live / "models" / "models.marker").exists()
    assert cus.read_tree_version(world.root / f"ComfyUI.prev-{OLD}") == OLD
    assert world.runner.packages["comfy-kitchen"] == "0.2.37"


def test_state_dir_move_failing_mid_loop_rolls_back(world, monkeypatch):
    real_rename = os.rename

    def flaky(src, dst):
        if Path(src).name == "custom_nodes" and Path(dst).parent.name == "ComfyUI.next":
            raise OSError(28, "No space left on device")
        real_rename(src, dst)

    monkeypatch.setattr(cus.os, "rename", flaky)
    status = world.svc.start(background=False)
    assert status["state"] == "rolled_back"
    assert status["message"].startswith("Update failed at swap: [Errno 28]")
    assert world.controller.calls == ["stop", "start"]
    assert_live_is_old(world)
    assert not (world.root / ".update_job.json").exists()


def test_stop_failing_during_rollback_leaves_trees_alone(world):
    world.http.missing_nodes = {"QwenImage21Cache"}
    world.controller.stop_fail_after = 1
    status = world.svc.start(background=False)
    assert status["state"] == "failed"
    msg = status["message"]
    assert msg.startswith("Update failed at post-check:")
    assert "could not stop the new ComfyUI: process still running" in msg
    assert "The new ComfyUI may still be running: stop it from the Plugins page first." in msg
    assert cus.read_tree_version(world.root / "ComfyUI") == NEW  # nothing renamed under a running server
    assert (world.root / "ComfyUI" / "models" / "models.marker").exists()
    assert cus.read_tree_version(world.root / f"ComfyUI.prev-{OLD}") == OLD
    assert (world.root / ".update_job.json").exists()  # repair can pick it up
    assert world.svc.status()["state"] == "needs_repair"


def test_dangling_state_symlink_travels(world):
    live = world.root / "ComfyUI"
    (live / "temp").symlink_to(world.root / "nowhere")
    assert world.svc.start(background=False)["state"] == "succeeded"
    assert os.path.islink(live / "temp") and os.readlink(live / "temp") == str(world.root / "nowhere")


class _Crash(BaseException):
    """Escapes every except Exception, like a kill or power loss would."""


def crash_between_tree_renames(world, monkeypatch):
    real_rename = os.rename

    def crashing(src, dst):
        if Path(src) == world.root / "ComfyUI.next" and Path(dst) == world.root / "ComfyUI":
            raise _Crash()
        real_rename(src, dst)

    monkeypatch.setattr(cus.os, "rename", crashing)
    with pytest.raises(_Crash):
        world.svc.start(background=False)
    monkeypatch.setattr(cus.os, "rename", real_rename)
    cus.reset_status()  # the process died; a new one starts with an idle status
    assert not (world.root / "ComfyUI").exists()
    assert (world.root / f"ComfyUI.prev-{OLD}" / "main.py").exists()
    assert (world.root / "ComfyUI.next" / "models" / "models.marker").exists()


def test_crash_between_renames_needs_repair_and_repair_restores(world, monkeypatch):
    crash_between_tree_renames(world, monkeypatch)
    journal = json.loads((world.root / ".update_job.json").read_text())
    assert journal["phase"] == "swap" and journal["stopped"] is True
    assert journal["moved"] == ["models", "custom_nodes", "user", "output"]
    assert journal["old_tree_at"].endswith(f"ComfyUI.prev-{OLD}")
    assert journal["snapshot"]["comfy-kitchen"] == "0.2.31"

    fresh = ComfyUIUpdateService(
        world.root, python=Path("/fake/venv/bin/python"), controller=world.controller,
        base_url="http://127.0.0.1:8188", runner=world.runner, http_get=world.http,
        busy_probe=lambda: None, disk_usage=lambda p: SimpleNamespace(free=100e9),
        log_path=world.log, invalidate_caches=lambda: None, sleep=lambda s: None,
        health_timeout_s=0, health_poll_s=0,
    )
    needs = fresh.needs_repair()
    assert needs.startswith("A ComfyUI update was interrupted at swap: old tree at ")
    assert f"ComfyUI.prev-{OLD}" in needs and "ComfyUI.next" in needs
    assert "models, custom_nodes, user, output" in needs
    assert fresh.status()["state"] == "needs_repair" and fresh.status()["message"] == needs
    assert fresh.check()["needs_repair"] == needs
    with pytest.raises(UpdateRefused, match="Repair the interrupted job first"):
        fresh.start(background=False)
    with pytest.raises(UpdateRefused, match="Repair the interrupted job first"):
        fresh.restore_previous(background=False)

    world.controller.calls.clear()
    status = fresh.repair(background=False)
    assert status["state"] == "rolled_back", status
    assert status["message"] == f"Repaired an interrupted update (it stopped at swap); ComfyUI {OLD} is running again."
    assert world.controller.calls == ["start"]
    assert_live_is_old(world)
    assert not (world.root / ".update_job.json").exists()
    assert fresh.status()["state"] == "rolled_back"
    assert cus.read_tree_version(world.root / "ComfyUI.next") == NEW
    with pytest.raises(UpdateRefused, match="nothing to repair"):
        fresh.repair(background=False)


def test_repair_without_a_journal_reads_the_disk(world, monkeypatch):
    crash_between_tree_renames(world, monkeypatch)
    (world.root / ".update_job.json").unlink()
    needs = world.svc.needs_repair()
    assert needs.startswith(f"{world.root / 'ComfyUI'} is missing while ComfyUI.prev-{OLD}, ComfyUI.next exist(s)")
    world.controller.calls.clear()
    status = world.svc.repair(background=False)
    assert status["state"] == "rolled_back", status
    assert world.controller.calls == ["start"]
    assert cus.read_tree_version(world.root / "ComfyUI") == OLD
    for d in ("models", "custom_nodes", "user", "output"):
        assert (world.root / "ComfyUI" / d / f"{d}.marker").exists(), d
    assert world.svc.needs_repair() is None


def test_repair_is_refused_when_the_disk_is_ambiguous(world):
    live = world.root / "ComfyUI"
    live.rename(world.root / f"ComfyUI.prev-{OLD}")
    make_tree(world.root / "ComfyUI.prev-0.31.0", "0.31.0", OLD_REQS)
    assert world.svc.needs_repair()
    with pytest.raises(UpdateRefused, match="Cannot repair without a journal.*2 kept tree"):
        world.svc.repair(background=False)


# ── the plugin-manager controller's gate handling ─────────────────────

class FakeManager:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def start_plugin(self, plugin_id):
        self.calls.append(("start", plugin_id))
        return self.answers.pop(0) if self.answers else {"success": True}

    def stop_plugin(self, plugin_id, *, cancel_video_jobs=True):
        self.calls.append(("stop", plugin_id, cancel_video_jobs))
        return self.answers.pop(0) if self.answers else {"success": True}


def test_controller_waits_for_a_gpu_holder_to_release():
    busy = {"success": False, "gated": True, "error": "GPU is busy with 'audio_foundry'", "cooldown_remaining": 8.0}
    slept = []
    manager = FakeManager([busy, busy, {"success": True, "message": "started"}])
    ctl = cus.PluginManagerController(sleep=slept.append, gpu_poll_s=5.0, gpu_wait_s=600.0, manager=manager)
    assert ctl.start() == {"success": True, "message": "started"}
    assert slept == [5.0, 5.0]
    assert manager.calls == [("start", "comfyui")] * 3


def test_controller_reports_a_gpu_holder_that_never_releases():
    busy = {"success": False, "gated": True, "error": "GPU is busy with 'lora_trainer'", "cooldown_remaining": 8.0}
    slept = []
    manager = FakeManager([busy] * 50)
    ctl = cus.PluginManagerController(sleep=slept.append, gpu_poll_s=5.0, gpu_wait_s=20.0, manager=manager)
    result = ctl.start()
    assert result == {"success": False, "error": "the GPU is still held by 'lora_trainer' after 20 s"}
    assert slept == [5.0] * 4


def test_controller_sleeps_through_a_cooldown_and_stops_without_cancelling_jobs():
    cooling = {"success": False, "gated": True, "error": "Plugin 'comfyui' cooling down", "cooldown_remaining": 2.5}
    slept = []
    manager = FakeManager([cooling, {"success": True}])
    ctl = cus.PluginManagerController(sleep=slept.append, manager=manager)
    assert ctl.stop() == {"success": True}
    assert slept == [3.0]
    assert manager.calls == [("stop", "comfyui", False)] * 2


# ── status ─────────────────────────────────────────────────────────────

def test_status_shape(world):
    idle = cus.get_status()
    assert set(idle) == {"state", "phase", "phase_label", "percent", "from", "to",
                         "started_at", "finished_at", "message", "log_tail"}
    assert idle["state"] == "idle"
    done = world.svc.start(background=False)
    assert set(done) == set(idle) | {"kind"}
    assert done["kind"] == "update"
    assert done["phase"] == "done" and done["phase_label"] == "Done"
    assert done["started_at"] and done["finished_at"]
    assert len(done["log_tail"]) <= cus.LOG_TAIL_LINES
    assert all(re.match(r"\d{4}-\d{2}-\d{2}T", line) for line in done["log_tail"])
