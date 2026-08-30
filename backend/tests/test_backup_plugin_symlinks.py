"""Backups must not follow plugin symlinks out of the project.

A plugin symlinked into plugins/ from elsewhere on disk is not part of the
checkout. shutil.copytree follows symlinks by default, so without the guard
the external tree (and whatever captures, databases and packages it holds)
ends up in every full backup and code release.
"""
import os
import shutil

import pytest

from backend.services import backup_service as bs


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    external = tmp_path / "elsewhere" / "ext_plugin"
    (root / "plugins" / "local_plugin").mkdir(parents=True)
    (root / "plugins" / "local_plugin" / "plugin.py").write_text("print('hi')\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "tool.sh").write_text("#!/bin/sh\n")
    # A symlink that stays inside the project is fine and must be kept.
    os.symlink(root / "scripts", root / "plugins" / "shared_scripts")

    external.mkdir(parents=True)
    (external / "plugin.py").write_text("print('external')\n")
    (external / "data").mkdir()
    (external / "data" / "session.pcap").write_bytes(b"\x00" * 64)
    os.symlink(external, root / "plugins" / "ext_plugin")
    return root


def test_external_symlink_detected(project):
    names = os.listdir(project / "plugins")
    skipped = bs._external_symlinks(str(project / "plugins"), names, project)
    assert skipped == {"ext_plugin"}


def test_plugin_ignore_skips_external_symlink(project, tmp_path):
    dest = tmp_path / "out"
    shutil.copytree(project / "plugins", dest, ignore=bs._create_plugin_ignore_function(project))
    assert (dest / "local_plugin" / "plugin.py").exists()
    assert (dest / "shared_scripts" / "tool.sh").exists()
    assert not (dest / "ext_plugin").exists()


def test_capture_and_package_files_are_ignored(tmp_path):
    src = tmp_path / "plug"
    src.mkdir()
    for name in ("a.pcap", "b.pcapng", "city.mmdb", "city.mmdb.gz", "chrome.deb", "x.rpm"):
        (src / name).write_bytes(b"x")
    (src / "keep.py").write_text("pass\n")
    dest = tmp_path / "out"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*bs.GLOBAL_IGNORE_PATTERNS))
    assert os.listdir(dest) == ["keep.py"]
