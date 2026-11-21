"""The inbound hooks, installed by the real installer into a scratch repository.

Covers what the hooks promise: nothing happens while off, a held merge is
refused in enforce mode until that exact change is approved, a fault in the
guard never stops git, and a fetch is recorded after the fact.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GUARD_FILES = ["check_inbound.py", "install_hooks.sh", "check_portable.sh", "pre-commit", "commit-msg", "pre-push"]


def _env():
    env = dict(os.environ)
    env.pop("GUAARDVARK_INBOUND_GUARD", None)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return env


def _run(cwd, *args, check=True):
    proc = subprocess.run(list(args), cwd=cwd, env=_env(), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"{args} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


def _git(cwd, *args, check=True):
    return _run(cwd, "git", *args, check=check)


@pytest.fixture
def clone(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "commit.gpgsign", "false")
    (work / "scripts").mkdir()
    for name in GUARD_FILES:
        shutil.copy2(ROOT / "scripts" / name, work / "scripts" / name)
    shutil.copytree(ROOT / "scripts" / "inbound_guard", work / "scripts" / "inbound_guard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "scripts" / "hooks", work / "scripts" / "hooks")
    (work / "README.md").write_text("scratch\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base", "--no-verify")
    _git(work, "push", "-q", "origin", "main")
    _run(work, "bash", "scripts/install_hooks.sh")
    return work


def _risky_branch(work, name="incoming"):
    _git(work, "checkout", "-q", "-b", name)
    (work / "risky.py").write_text("import subprocess\nsubprocess.run(cmd, shell" + "=True)\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "risky", "--no-verify")
    _git(work, "checkout", "-q", "main")


def _head(work):
    return _git(work, "rev-parse", "HEAD").stdout.strip()


def test_installer_check_passes_after_install(clone):
    assert _run(clone, "bash", "scripts/install_hooks.sh", "--check").returncode == 0


def test_off_does_nothing(clone):
    _risky_branch(clone)
    before = _head(clone)
    proc = _git(clone, "merge", "--no-ff", "incoming", "-m", "merge")
    assert _head(clone) != before
    assert "inbound guard" not in proc.stderr


def test_enforce_refuses_until_this_change_is_approved(clone):
    _git(clone, "config", "inboundguard.mode", "enforce")
    _risky_branch(clone)
    before = _head(clone)

    proc = _git(clone, "merge", "--no-ff", "incoming", "-m", "merge", check=False)
    assert proc.returncode != 0
    assert "Refusing the merge commit" in proc.stderr
    assert _head(clone) == before
    assert (clone / ".git" / "MERGE_HEAD").exists()

    # Concluding by hand goes through pre-commit, which asks the same question.
    assert _git(clone, "commit", "--no-edit", check=False).returncode != 0
    assert _head(clone) == before

    log = json.loads(_run(clone, "python3", "scripts/check_inbound.py", "log", "--json").stdout)
    digest = log[-1]["digest"]
    _run(clone, "python3", "scripts/check_inbound.py", "approve", digest, "--note", "read it")
    _git(clone, "commit", "--no-edit")
    assert _head(clone) != before


def test_a_broken_install_never_stops_git(clone):
    _git(clone, "config", "inboundguard.mode", "enforce")
    _risky_branch(clone)
    engine = clone / ".git" / "inbound-guard" / "engine"
    engine.rename(engine.with_name("engine.away"))
    before = _head(clone)
    proc = _git(clone, "merge", "--no-ff", "incoming", "-m", "merge")
    assert _head(clone) != before
    assert "merge not read" in proc.stderr


def test_fetch_is_recorded(clone, tmp_path):
    _git(clone, "config", "inboundguard.mode", "observe")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    (other / "risky.py").write_text("import subprocess\nsubprocess.run(cmd, shell" + "=True)\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "upstream", "--no-verify")
    _git(other, "push", "-q", "origin", "main")

    proc = _git(clone, "fetch", "-q", "origin")
    assert "origin/main" in proc.stderr and "HOLD" in proc.stderr
    log = json.loads(_run(clone, "python3", "scripts/check_inbound.py", "log", "--json").stdout)
    assert log[-1]["source"] == "fetch" and log[-1]["verdict"] == "hold"


def _log(work):
    return json.loads(_run(work, "python3", "scripts/check_inbound.py", "log", "-n", "50", "--json").stdout)


def test_records_name_the_commits_that_arrived(clone, tmp_path):
    _git(clone, "config", "inboundguard.mode", "observe")
    _risky_branch(clone)
    incoming, before = _git(clone, "rev-parse", "incoming").stdout.strip(), _head(clone)
    _git(clone, "merge", "--no-ff", "incoming", "-m", "merge")
    merge = next(r for r in reversed(_log(clone)) if r.get("source") == "git-merge")
    assert (merge["old_sha"], merge["new_sha"]) == (before, incoming)
    assert merge["ts"][-3] == ":"

    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    (other / "risky2.py").write_text("import subprocess\nsubprocess.run(cmd, shell" + "=True)\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "upstream", "--no-verify")
    _git(other, "push", "-q", "origin", "main")
    old_remote = _git(clone, "rev-parse", "origin/main").stdout.strip()
    _git(clone, "fetch", "-q", "origin")
    fetch = next(r for r in reversed(_log(clone)) if r.get("source") == "fetch")
    assert (fetch["old_sha"], fetch["new_sha"]) == (old_remote, _git(other, "rev-parse", "HEAD").stdout.strip())

    _run(clone, "python3", "scripts/check_inbound.py", "scan", "--range", "main...incoming", "--record",
         "--exit-zero")
    scan = _log(clone)[-1]
    assert scan["source"] == "cli"
    assert (scan["old_sha"], scan["new_sha"]) == (_head(clone), incoming)
