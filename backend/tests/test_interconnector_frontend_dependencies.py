"""Frontend source profiles include the dependency manifests and locale JSON."""

import json

from backend.services.interconnector_file_sync_service import (
    InterconnectorFileSyncService,
    complete_frontend_sync_paths,
)


def test_legacy_code_profile_keeps_frontend_runtime_inputs(tmp_path, monkeypatch):
    files = {
        "frontend/src/i18n/index.js": "import i18next from 'i18next';",
        "frontend/src/locales/en.json": json.dumps({"language": "Language"}),
        "frontend/src/locales/de.json": json.dumps({"language": "Sprache"}),
        "frontend/package.json": json.dumps({"dependencies": {"i18next": "^26"}}),
        "frontend/package-lock.json": json.dumps({"lockfileVersion": 3}),
        "frontend/node_modules/i18next/package.json": "{}",
        "frontend/.env": "PRIVATE_SETTING=example",
    }
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    service = InterconnectorFileSyncService()
    monkeypatch.setattr(service, "get_project_root", lambda: tmp_path)
    scanned = service.scan_files(
        ["frontend/src/"], include_patterns=["**/*.js", "**/*.jsx"]
    )
    assert {entry["path"] for entry in scanned} == {
        name for name in files if "node_modules" not in name and not name.endswith(".env")
    }


def test_completing_paths_preserves_callers_filters_and_does_not_duplicate():
    paths = ["frontend/src/", "frontend/package.json", "frontend/package-lock.json"]
    patterns = ["**/*.js"]
    result, includes = complete_frontend_sync_paths(paths, patterns)
    assert result == paths
    assert patterns == ["**/*.js"]
    assert "frontend/src/**/*.json" in includes
    assert complete_frontend_sync_paths(result, includes) == (result, includes)


def test_backend_only_profile_does_not_pull_frontend():
    assert complete_frontend_sync_paths(["backend/api/"], ["**/*.py"]) == (
        ["backend/api/"], ["**/*.py"]
    )


def test_whole_frontend_profile_does_not_scan_manifests_twice():
    paths, patterns = complete_frontend_sync_paths(["frontend/"], ["**/*.js"])
    assert paths == ["frontend/"]
    assert "frontend/package-lock.json" in patterns
