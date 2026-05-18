from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.dep_reconciler.reconcilers.plugin_bundle import PluginBundle


def _make_plugin(root: Path, pid: str, reqs: str = "requests\n") -> Path:
    p = root / "plugins" / pid
    p.mkdir(parents=True)
    (p / "requirements.txt").write_text(reqs)
    return p


@pytest.fixture
def fake_repo(tmp_path):
    _make_plugin(tmp_path, "discord", "aiohttp\n")
    _make_plugin(tmp_path, "swarm", "ray\n")
    return tmp_path


def test_id(fake_repo):
    assert PluginBundle(fake_repo, ["discord", "swarm"]).id == "plugin_bundle"


def test_inactive_when_no_plugins(fake_repo):
    assert not PluginBundle(fake_repo, []).is_active()


def test_active_when_plugins_have_reqs(fake_repo):
    assert PluginBundle(fake_repo, ["discord", "swarm"]).is_active()


def test_compute_hash_changes_when_member_set_changes(fake_repo):
    h1 = PluginBundle(fake_repo, ["discord"]).compute_hash()
    h2 = PluginBundle(fake_repo, ["discord", "swarm"]).compute_hash()
    assert h1 != h2


def test_compute_hash_changes_when_member_reqs_change(fake_repo):
    r = PluginBundle(fake_repo, ["discord"])
    h1 = r.compute_hash()
    (fake_repo / "plugins" / "discord" / "requirements.txt").write_text("aiohttp\nyarl\n")
    h2 = r.compute_hash()
    assert h1 != h2


def test_per_member_hashes_exposed_for_state(fake_repo):
    r = PluginBundle(fake_repo, ["discord", "swarm"])
    members = r.member_hashes()
    assert set(members.keys()) == {"discord", "swarm"}
    assert all(v.startswith("sha256:") for v in members.values())


def test_install_runs_one_pip_invocation_with_all_reqs(fake_repo, tmp_path):
    r = PluginBundle(fake_repo, ["discord", "swarm"])
    with patch.object(r, "_run_subprocess", return_value=0) as m:
        rc = r.install(tmp_path / "log.txt")
    assert rc == 0
    assert m.call_count == 1, "should aggregate into one pip call"
    args = m.call_args.args[0]
    # Should have -r for both plugins
    assert args.count("-r") == 2


def test_install_skipped_when_no_members(fake_repo, tmp_path):
    r = PluginBundle(fake_repo, [])
    with patch.object(r, "_run_subprocess") as m:
        rc = r.install(tmp_path / "log.txt")
    assert rc == 0
    m.assert_not_called()
