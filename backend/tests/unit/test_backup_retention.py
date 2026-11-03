"""Pinning, retention and the data-ignore rules of backup_service."""
import os
import shutil
import time
from pathlib import Path

import pytest

from backend import config
from backend.services import backup_service


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    d.mkdir()
    monkeypatch.setattr(config, "BACKUP_DIR", str(d))
    return d


def _zip(backup_dir: Path, name: str, age_days: float, size: int = 10) -> Path:
    p = backup_dir / name
    p.write_bytes(b"x" * size)
    mtime = time.time() - age_days * 86400
    os.utime(p, (mtime, mtime))
    return p


def test_pin_marks_listing_and_survives_unpin(backup_dir):
    _zip(backup_dir, "auto_daily_20260101_000000.zip", 1)
    assert backup_service.set_pinned("auto_daily_20260101_000000.zip", True)
    assert backup_service.list_backups()[0]["pinned"] is True
    assert backup_service.set_pinned("auto_daily_20260101_000000.zip", False)
    assert backup_service.list_backups()[0]["pinned"] is False


def test_pin_refuses_names_outside_the_folder(backup_dir):
    assert backup_service.set_pinned("../etc/passwd", True) is False
    assert backup_service.set_pinned("missing.zip", True) is False


def test_delete_refuses_a_pinned_backup(backup_dir):
    _zip(backup_dir, "auto_daily_20260101_000000.zip", 1)
    backup_service.set_pinned("auto_daily_20260101_000000.zip", True)
    with pytest.raises(backup_service.BackupPinned):
        backup_service.delete_backup("auto_daily_20260101_000000.zip")
    assert (backup_dir / "auto_daily_20260101_000000.zip").exists()
    assert backup_service.delete_backup("auto_daily_20260101_000000.zip", force=True)
    assert "auto_daily_20260101_000000.zip" not in backup_service.list_pinned()


def test_prune_keeps_newest_pinned_and_named_backups(backup_dir):
    for i in range(10):                       # 10 automatic backups, 0..9 days old
        _zip(backup_dir, f"auto_daily_2026010{i}_000000.zip", i + 0.5)
    _zip(backup_dir, "auto_daily_20251001_000000.zip", 60)   # old, pinned
    _zip(backup_dir, "my own backup_20250901_000000.zip", 90)  # named, never touched
    backup_service.set_pinned("auto_daily_20251001_000000.zip", True)

    removed = backup_service.prune_auto_backups(keep_last=3, max_age_days=5)

    names = {e["name"] for e in backup_service.list_backups()}
    assert "my own backup_20250901_000000.zip" in names
    assert "auto_daily_20251001_000000.zip" in names
    # 0.5..2.5 days old stay as the newest three; 3.5 and 4.5 stay under the age limit
    for i in range(5):
        assert f"auto_daily_2026010{i}_000000.zip" in names
    for i in range(5, 10):
        assert f"auto_daily_2026010{i}_000000.zip" in removed


def test_prune_size_cap_removes_oldest_first(backup_dir):
    for i in range(5):
        _zip(backup_dir, f"auto_daily_2026010{i}_000000.zip", i + 0.5, size=100)
    removed = backup_service.prune_auto_backups(keep_last=2, max_age_days=0, max_total_bytes=250)
    assert removed == ["auto_daily_20260104_000000.zip", "auto_daily_20260103_000000.zip", "auto_daily_20260102_000000.zip"]


def test_prune_never_goes_below_keep_last(backup_dir):
    for i in range(3):
        _zip(backup_dir, f"auto_daily_2026010{i}_000000.zip", 100 + i)
    assert backup_service.prune_auto_backups(keep_last=3, max_age_days=1, max_total_bytes=1) == []


def test_disk_check_sizes_the_next_backup_from_the_last_one(backup_dir, monkeypatch):
    _zip(backup_dir, "auto_daily_20260101_000000.zip", 1, size=1000)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("Usage", (), {"free": 5000, "total": 1, "used": 1})())
    assert backup_service.backup_disk_check(3000)["ok"] is True
    assert backup_service.backup_disk_check(4500)["ok"] is False


def test_staging_dir_lives_beside_the_zips(backup_dir):
    staging = Path(backup_service._staging_dir())
    assert staging.parent == backup_dir
    assert oct(staging.stat().st_mode & 0o777) == "0o700"


def test_data_ignore_keeps_trained_loras_and_drops_other_weights(tmp_path):
    root = tmp_path / "root"
    loras = root / "data" / "training" / "loras"
    other = root / "data" / "uploads"
    loras.mkdir(parents=True)
    other.mkdir(parents=True)
    ignore = backup_service._data_ignore(root)
    assert "Ivy_v1.safetensors" not in ignore(str(loras), ["Ivy_v1.safetensors", "notes.tmp"])
    assert "notes.tmp" in ignore(str(loras), ["Ivy_v1.safetensors", "notes.tmp"])
    assert "model.safetensors" in ignore(str(other), ["model.safetensors", "a.txt"])
