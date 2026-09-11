"""``python -m backend.mcp install --skills`` links .agents/skills into the
Claude Code skill folders. Not run by CI; run by hand with
``pytest backend/mcp/tests/test_installer_skills.py``."""
from pathlib import Path

import pytest

from backend.mcp import installer


@pytest.fixture
def skills_tree(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    src = root / ".agents" / "skills"
    for name in ("guaardvark-alpha", "guaardvark-beta"):
        (src / name).mkdir(parents=True)
        (src / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n")
    (src / "_template").mkdir()  # no SKILL.md, must be ignored
    home = tmp_path / "home"
    monkeypatch.setattr(installer, "_project_root", lambda: root)
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: home))
    return root, home


def test_dry_run_touches_nothing(skills_tree):
    root, home = skills_tree
    results = installer.install_skills(dry_run=True)
    assert {r.status for r in results} == {"dry-run"}
    assert not (home / ".claude").exists()
    assert not (root / ".claude").exists()


def test_links_every_skill_into_both_targets(skills_tree):
    root, home = skills_tree
    results = installer.install_skills()
    assert all(r.status == "installed" for r in results)
    for target in (home / ".claude" / "skills", root / ".claude" / "skills"):
        for name in ("guaardvark-alpha", "guaardvark-beta"):
            link = target / name
            assert link.is_symlink() or (link / "SKILL.md").is_file()
            assert (link / "SKILL.md").read_text().startswith("---")
        assert not (target / "_template").exists()


def test_second_run_is_idempotent_and_repoints_stale_link(skills_tree):
    root, home = skills_tree
    installer.install_skills()
    stale = home / ".claude" / "skills" / "guaardvark-alpha"
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    stale.unlink()
    stale.symlink_to(elsewhere, target_is_directory=True)
    results = installer.install_skills()
    assert stale.resolve() == (root / ".agents" / "skills" / "guaardvark-alpha").resolve()
    assert any("already linked" in r.detail for r in results)


def test_real_directory_is_never_overwritten(skills_tree):
    root, home = skills_tree
    mine = home / ".claude" / "skills" / "guaardvark-alpha"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("user's own\n")
    results = installer.install_skills()
    assert (mine / "SKILL.md").read_text() == "user's own\n"
    assert any(r.status == "skipped" and "guaardvark-alpha" in r.client for r in results)


def test_run_install_with_skills_only_does_not_require_a_client(skills_tree, monkeypatch):
    monkeypatch.setattr(installer, "_detect", lambda client: False)
    assert installer.run_install(None, dry_run=True, skills=True) == 0
    assert installer.run_install(None, dry_run=True, skills=False) == 1
