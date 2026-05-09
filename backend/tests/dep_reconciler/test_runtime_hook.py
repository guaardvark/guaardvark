"""Test the PluginManager.enable_plugin runtime hook into the reconciler.

The hook spawns `python scripts/dep_reconciler.py --only=plugin_bundle`
synchronously. On non-zero exit, the plugin enable is reverted and the
error is surfaced.
"""
from unittest.mock import MagicMock, patch

import pytest

# We import the helper directly so we don't need a full Flask app.
# Note: actual location is backend.plugins.plugin_manager, not services.
from backend.plugins.plugin_manager import _run_dep_reconciler_for_plugin


def test_returns_true_when_reconciler_exits_zero():
    fake_proc = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("backend.plugins.plugin_manager.subprocess.run", return_value=fake_proc):
        ok, err = _run_dep_reconciler_for_plugin("discord")
    assert ok is True
    assert err is None


def test_returns_false_with_stderr_when_reconciler_fails():
    fake_proc = MagicMock(returncode=1, stdout="", stderr="pip install failed: ...")
    with patch("backend.plugins.plugin_manager.subprocess.run", return_value=fake_proc):
        ok, err = _run_dep_reconciler_for_plugin("discord")
    assert ok is False
    assert "pip install failed" in err


def test_returns_false_when_subprocess_times_out():
    import subprocess
    with patch(
        "backend.plugins.plugin_manager.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=120),
    ):
        ok, err = _run_dep_reconciler_for_plugin("discord")
    assert ok is False
    assert "timed out" in err.lower()


def test_interconnector_writes_sync_sentinel(tmp_path, monkeypatch):
    """apply_files_atomic must write .sync_in_progress before applying files
    and clear it after, so the dep_reconciler refuses to run mid-sync.
    """
    from backend.services import interconnector_file_sync_service as svc_mod
    from backend.services.interconnector_file_sync_service import (
        InterconnectorFileSyncService,
    )

    sentinel_dir = tmp_path / "data" / "dep_reconciler"
    sentinel_file = sentinel_dir / ".sync_in_progress"
    monkeypatch.setattr(svc_mod, "_SENTINEL_DIR", sentinel_dir)
    monkeypatch.setattr(svc_mod, "_SENTINEL_FILE", sentinel_file)

    svc = InterconnectorFileSyncService()

    seen_during_apply: list[bool] = []

    def fake_inner(*a, **kw):
        seen_during_apply.append(sentinel_file.exists())
        return {"ok": True, "applied": [], "rolled_back": []}

    monkeypatch.setattr(svc, "_apply_files_atomic_inner", fake_inner, raising=False)

    svc.apply_files_atomic([])

    assert seen_during_apply == [True], "sentinel must exist during apply"
    assert not sentinel_file.exists(), "sentinel must be cleared after apply"
