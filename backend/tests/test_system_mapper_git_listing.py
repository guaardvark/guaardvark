"""The System Mapper surveys what git lists, and walks the tree only when git cannot.

Builds small trees in a temp folder; the ones that need a repository run
`git init` there. No backend, GPU or network.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services.system_mapper import core, dependency_graph, dispatch_graph, reachability
from backend.services.system_mapper.core import codebase_map, source_files


def _write(path: Path, text: str = "x = 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _tree(root: Path) -> Path:
    """Source, a scratch folder holding a copy of it, and a folder the mapper skips by name."""
    _write(root / "backend" / "__init__.py", "")
    _write(root / "backend" / "util.py", "def helper():\n    return 1\n")
    _write(root / "backend" / "new_module.py")
    _write(root / "backend" / "tools" / "echo_tool.py", "class Echo:\n    name = 'echo'\n")
    _write(root / "backend" / "debug.log", "noise\n")
    _write(root / "frontend" / "src" / "api.js", "fetch('/api/things/list');\n")
    _write(root / "scratch" / "copy" / "backend" / "util.py")
    _write(root / "scratch" / "copy" / "frontend" / "src" / "api.js", "fetch('/api/ghost');\n")
    _write(root / "data" / "tracked_but_skipped.py")
    _write(root / "plugins" / "demo" / "plugin.json", "{}\n")
    return root


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """_tree as a git checkout: scratch/ and *.log ignored, new_module.py untracked."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    root = _tree(tmp_path / "checkout")
    _write(root / ".gitignore", "scratch/\n*.log\n")
    _git(root, "init", "-q")
    _git(root, "add", ".gitignore", "backend/__init__.py", "backend/util.py", "backend/tools",
         "frontend", "data", "plugins")
    return root


def _rel(root: Path, paths) -> list[str]:
    return [p.relative_to(root).as_posix() for p in paths]


def test_a_checkout_lists_tracked_and_unignored_files_only(repo):
    assert _rel(repo, source_files(repo, pattern="*.py")) == [
        "backend/__init__.py",
        "backend/new_module.py",  # untracked, not ignored
        "backend/tools/echo_tool.py",
        "backend/util.py",
    ]


def test_a_folder_skipped_by_name_stays_skipped_even_when_tracked(repo):
    listed = _rel(repo, source_files(repo))
    assert "data/tracked_but_skipped.py" not in listed
    assert "backend/debug.log" not in listed  # ignored by pattern
    assert "plugins/demo/plugin.json" in listed


def test_extra_excludes_and_exclude_dirs(repo):
    assert "backend/util.py" not in _rel(repo, source_files(repo, frozenset({"backend"}), pattern="*.py"))
    # exclude_dirs replaces the default set, so data/ is listed again.
    assert "data/tracked_but_skipped.py" in _rel(repo, source_files(repo, exclude_dirs=frozenset()))


def test_under_narrows_the_listing_and_keeps_the_root_relative_rules(repo):
    assert _rel(repo, source_files(repo, pattern="*.py", under=repo / "backend" / "tools")) == [
        "backend/tools/echo_tool.py"]
    assert source_files(repo, under=repo / "data") == []


def test_deleted_files_nested_repositories_and_symlinked_folders_are_left_out(repo, tmp_path):
    (repo / "backend" / "util.py").unlink()  # still in the index
    nested = repo / "vendor_checkout"
    _write(nested / "inner.py")
    _git(nested, "init", "-q")
    outside = tmp_path / "outside"
    _write(outside / "elsewhere.py")
    os.symlink(outside, repo / "linked")
    os.symlink(outside / "elsewhere.py", repo / "backend" / "linked_file.py")
    listed = _rel(repo, source_files(repo, pattern="*.py"))
    assert "backend/util.py" not in listed
    assert not any(p.startswith(("vendor_checkout/", "linked/")) for p in listed)
    assert "backend/linked_file.py" in listed


def test_a_root_git_ignores_maps_as_empty(repo):
    assert source_files(repo / "scratch" / "copy") == []
    assert dependency_graph.analyze(repo / "scratch" / "copy", frozenset())["file_count"] == 0


def test_outside_a_checkout_the_tree_is_walked(tmp_path):
    root = _tree(tmp_path / "plain")
    assert _rel(root, source_files(root, pattern="*.py")) == [
        "backend/__init__.py",
        "backend/new_module.py",
        "backend/tools/echo_tool.py",
        "backend/util.py",
        "scratch/copy/backend/util.py",
    ]
    assert "backend/debug.log" in _rel(root, source_files(root))


@pytest.mark.parametrize("failure", [
    FileNotFoundError("git"),
    subprocess.TimeoutExpired(cmd="git", timeout=core.GIT_LIST_TIMEOUT_S),
])
def test_a_checkout_is_walked_when_git_cannot_be_asked(repo, monkeypatch, failure):
    def no_git(*args, **kwargs):
        raise failure

    monkeypatch.setattr(core.subprocess, "run", no_git)
    assert "scratch/copy/backend/util.py" in _rel(repo, source_files(repo, pattern="*.py"))


def test_git_location_variables_do_not_redirect_the_listing(repo, tmp_path, monkeypatch):
    plain = _tree(tmp_path / "plain")
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo))
    assert "scratch/copy/backend/util.py" in _rel(plain, source_files(plain, pattern="*.py"))


def test_the_listing_runs_without_a_shell(repo, monkeypatch):
    calls = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", spy)
    source_files(repo)
    (cmd, kwargs), = calls
    assert cmd[:3] == ["git", "-C", str(repo)] and "--exclude-standard" in cmd
    assert not kwargs.get("shell") and kwargs["timeout"] == core.GIT_LIST_TIMEOUT_S


def test_the_analyzers_do_not_count_ignored_copies(repo):
    # Four Python modules and frontend/src/api.js; the scratch copies are ignored.
    assert dependency_graph.analyze(repo, frozenset())["file_count"] == 5
    assert [c["file"] for c in reachability._frontend_callers(repo, frozenset())] == ["frontend/src/api.js"]
    assert dispatch_graph._tool_modules(repo, frozenset()) == {"backend.tools.echo_tool"}
    smap = codebase_map(repo)
    assert smap.file_count == 5
    assert not any("scratch" in path for finding in smap.findings for path in finding.paths)
