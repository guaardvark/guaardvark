"""A sync never sends what belongs to this install alone.

Private extension folders and anything whose real location is outside the
install stay here, whichever paths a sync request names: the default set, a
custom path list, a by-path pull, or the single-file fallback.
"""
import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services import interconnector_file_sync_service as sync_mod
from backend.services.interconnector_file_sync_service import (
    EXTENSIONS_SCAFFOLD,
    SYNC_EXTENSIONS_ENV,
    InterconnectorFileSyncService,
    private_path_reason,
)


def _write(path: Path, text: str = "x = 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture()
def install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    outside = tmp_path / "elsewhere"
    _write(root / "backend" / "app.py")
    _write(root / "frontend" / "index.html", "<html></html>\n")
    _write(root / "extensions" / ".gitignore", "*\n")
    _write(root / "extensions" / "README.md", "scaffold\n")
    _write(root / "extensions" / "__init__.py")
    _write(root / "extensions" / "_template" / "extension.json", "{}\n")
    _write(root / "extensions" / "client_one" / "extension.json", "{}\n")
    _write(root / "extensions" / "client_one" / "api" / "routes.py")
    _write(outside / "private_ext" / "extension.json", "{}\n")
    _write(outside / "private_ext" / "secret.py", "TOKEN = 'never'\n")
    _write(outside / "loose.py", "LOOSE = 1\n")
    (root / "extensions" / "linked_ext").symlink_to(outside / "private_ext", target_is_directory=True)
    _write(root / "plugins" / "demo" / "plugin.json", "{}\n")
    (root / "plugins" / "demo" / "loose.py").symlink_to(outside / "loose.py")
    (root / "plugins" / "linked_plugin").symlink_to(outside / "private_ext", target_is_directory=True)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    monkeypatch.delenv(SYNC_EXTENSIONS_ENV, raising=False)
    return root


def _paths(files):
    return sorted(f["path"].replace("\\", "/") for f in files)


# extensions/.gitignore is scaffold too, but the ".git" exclude rule keeps it back.
SCAFFOLD_SENT = [
    "extensions/README.md",
    "extensions/__init__.py",
    "extensions/_template/extension.json",
]


def test_extensions_folder_sends_only_the_scaffold(install):
    svc = InterconnectorFileSyncService()
    sent = _paths(svc.scan_files(["extensions/"], include_content=False))
    assert sent == SCAFFOLD_SENT


def test_naming_a_private_folder_sends_nothing(install):
    svc = InterconnectorFileSyncService()
    for path in ("extensions/client_one", "extensions/linked_ext", "plugins/linked_plugin"):
        assert svc.scan_files([path], include_content=True) == [], path


def test_symlinked_file_inside_a_synced_folder_stays(install):
    svc = InterconnectorFileSyncService()
    sent = _paths(svc.scan_files(["plugins/"], include_content=False))
    assert sent == ["plugins/demo/plugin.json"]


def test_by_path_pull_and_local_fallback_refuse_private_files(install):
    svc = InterconnectorFileSyncService()
    private = [
        "extensions/client_one/api/routes.py",
        "extensions/linked_ext/secret.py",
        "plugins/demo/loose.py",
        "plugins/linked_plugin/secret.py",
    ]
    assert svc.fetch_files_by_paths(private) == []
    for path in private:
        assert svc.resolve_local_file(path) is None, path
    assert _paths(svc.fetch_files_by_paths(["extensions/__init__.py"])) == ["extensions/__init__.py"]


def test_opt_in_sends_a_named_folder_but_never_one_outside_the_install(install, monkeypatch):
    monkeypatch.setenv(SYNC_EXTENSIONS_ENV, "client_one, linked_ext")
    svc = InterconnectorFileSyncService()
    sent = _paths(svc.scan_files(["extensions/client_one", "extensions/linked_ext"], include_content=False))
    assert sent == ["extensions/client_one/api/routes.py", "extensions/client_one/extension.json"]


def test_default_sync_set_carries_no_private_path(install, monkeypatch):
    svc = InterconnectorFileSyncService()
    monkeypatch.setattr(svc, "default_sync_paths", svc.default_sync_paths + ["extensions/"])
    sent = _paths(svc.scan_files(None, include_content=False))
    assert sent == sorted(["backend/app.py", "frontend/index.html", "plugins/demo/plugin.json", *SCAFFOLD_SENT])


@pytest.mark.parametrize("rel, reason", [
    ("extensions/__init__.py", None),
    ("extensions/_template/api/x.py", None),
    ("extensions/_template", None),
    ("extensions", None),
    ("backend/app.py", None),
    ("extensions/client_one/extension.json", "a private extension folder"),
    ("extensions/_templatex/a.py", "a private extension folder"),
    ("extensions/linked_ext/secret.py", "its real location is outside the install"),
])
def test_private_path_reason(install, rel, reason):
    assert private_path_reason(install, rel) == reason


def test_scaffold_matches_what_git_tracks():
    """EXTENSIONS_SCAFFOLD must name exactly the tracked part of extensions/."""
    repo = Path(inspect.getfile(sync_mod)).resolve().parents[2]
    if not shutil.which("git") or not (repo / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "extensions"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out, "git lists no tracked file under extensions/"
    uncovered = [p for p in out if private_path_reason(repo, p) is not None and not (repo / p).is_symlink()]
    assert not uncovered, f"tracked under extensions/ but treated as private: {uncovered}"
    for entry in EXTENSIONS_SCAFFOLD:
        prefix = entry.rstrip("/")
        assert any(p == prefix or p.startswith(prefix + "/") for p in out), f"{entry} is not tracked"
