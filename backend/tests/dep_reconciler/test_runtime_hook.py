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
