"""Measuring swarm disk usage survives files an agent deletes mid-walk."""

from pathlib import Path

from service.worktree_manager import WorktreeManager


def test_disk_usage_skips_a_file_that_vanished(tmp_path, monkeypatch):
    mgr = WorktreeManager.__new__(WorktreeManager)
    mgr.swarm_dir = tmp_path / "swarm"
    mgr.swarm_dir.mkdir()
    (mgr.swarm_dir / "kept.bin").write_bytes(b"x" * 2048)
    (mgr.swarm_dir / "gone.bin").write_bytes(b"x" * 4096)

    real_stat = Path.stat
    calls = {"gone.bin": 0}

    # The file is there when is_file() looks and gone when stat() is read.
    def flaky_stat(self, *args, **kwargs):
        if self.name == "gone.bin":
            calls["gone.bin"] += 1
            if calls["gone.bin"] > 1:
                raise FileNotFoundError(str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    assert mgr.disk_usage_mb() == 2048 / (1024 * 1024)
