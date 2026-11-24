"""Orphan cleanup only kills processes that run from this install.

Two checkouts on one machine share plugin ports but not plugin state, so to
the second one the first one's ComfyUI looks like an orphan of a disabled
plugin. PluginManager._runs_outside_install tells them apart by the process's
working directory: plugins start in their own plugin folder.
"""
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.plugins.plugin_manager import PluginManager

pytestmark = pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs /proc")


def _manager(root: Path) -> PluginManager:
    pm = object.__new__(PluginManager)
    pm.registry = SimpleNamespace(
        plugins_dir=root / "plugins",
        get_all_plugins=lambda: [],
        get_plugin_dir=lambda _plugin_id: None,
    )
    return pm


def _sleeper(cwd: Path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(cwd))
    time.sleep(0.2)
    return p


def test_a_process_in_a_plugin_folder_belongs_to_the_install(tmp_path):
    root = tmp_path / "install"
    (root / "plugins" / "comfyui").mkdir(parents=True)
    p = _sleeper(root / "plugins" / "comfyui")
    try:
        assert _manager(root)._runs_outside_install(p.pid) is False
    finally:
        p.kill()


def test_a_process_from_another_checkout_does_not(tmp_path):
    root = tmp_path / "install"
    (root / "plugins").mkdir(parents=True)
    other = tmp_path / "install-2" / "plugins" / "comfyui"
    other.mkdir(parents=True)
    p = _sleeper(other)
    try:
        assert _manager(root)._runs_outside_install(p.pid) is True
    finally:
        p.kill()


def test_a_sibling_whose_name_starts_with_the_root_does_not(tmp_path):
    root = tmp_path / "guaardvark"
    (root / "plugins").mkdir(parents=True)
    sibling = tmp_path / "guaardvark-own"
    sibling.mkdir()
    p = _sleeper(sibling)
    try:
        assert _manager(root)._runs_outside_install(p.pid) is True
    finally:
        p.kill()


def test_an_unreadable_process_keeps_the_old_behaviour(tmp_path):
    assert _manager(tmp_path)._runs_outside_install(2 ** 22 + 12345) is False
