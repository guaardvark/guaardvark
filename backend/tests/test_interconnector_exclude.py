"""Regression tests for the Interconnector file-sync exclude matching.

Bug (2026-06-03): directory exclude patterns like 'build/' / 'data/' / 'env/' were
matched as bare SUBSTRINGS against the whole path, so 'build/' silently dropped
frontend/src/components/videoeditor/buildPlanRequest.js from sync (broke a client
rebuild), 'data/' dropped metadata_service.py, etc. The fix matches directory
patterns on path-segment boundaries. Each case below pins both directions.
"""
import inspect
from pathlib import Path

import pytest

from backend.services.interconnector_file_sync_service import InterconnectorFileSyncService


@pytest.fixture()
def svc():
    return InterconnectorFileSyncService()


# --- files that must NOT be excluded (the false-positives the bug dropped) ---
@pytest.mark.parametrize("path", [
    "frontend/src/components/videoeditor/buildPlanRequest.js",      # 'build/'
    "frontend/src/components/videoeditor/buildPlanRequest.test.js",
    "frontend/src/utils/smartContextBuilder.js",                   # 'build/'
    "backend/services/metadata_service.py",                        # 'data/'
    "backend/handlers/database_handler.py",                        # 'data/'
    "frontend/src/api/backupService.js",                           # 'backups/'
    "plugins/lora_trainer/scripts/setup_venv.sh",                  # 'env/' + 'venv' substring
    "scripts/dep_reconciler/detectors/torch_venv.py",             # 'env/' + 'venv' substring
    "cli/llx/commands/logs.py",                                    # 'logs/'
])
def test_source_files_are_not_excluded(svc, path):
    assert svc.should_exclude_file(path) is False, f"{path} should sync but was excluded"


# --- ALL virtualenv directories must be excluded, including suffixed sidecar
#     venvs that the literal "venv/" segment pattern misses (2026-06-15) ---
@pytest.mark.parametrize("path", [
    "backend/venv/lib/python3.12/site.py",                  # plain venv
    "plugins/video_editor/venv/lib/python3.12/os.py",       # plain venv (plugin)
    "plugins/audio_foundry/venv/bin/activate",              # plain venv (plugin)
    "plugins/lora_trainer/venv-torch/lib/torch/__init__.py",  # suffixed venv
    "plugins/audio_foundry/venv-music/bin/python",          # suffixed venv
    "some/path/.venv/lib/python3.12/site.py",               # dotted venv
    "plugins/x/venv_py311/lib/foo.py",                      # underscore-suffixed venv
])
def test_all_venv_dirs_excluded(svc, path):
    assert svc.should_exclude_file(path) is True, f"{path} is inside a venv and must be excluded"


# --- real directories that MUST still be excluded (no regression) ---
@pytest.mark.parametrize("path", [
    "frontend/dist/assets/index-abc.js",       # dist/
    "backend/venv/lib/python3.12/site.py",     # backend/venv/
    "data/training/loras/Serenity_Kane_v1.json",  # data/
    "logs/backend.log",                        # logs/ (and *.log)
    "frontend/node_modules/react/index.js",    # node_modules
    "backend/__pycache__/app.cpython-312.pyc", # __pycache__ + .pyc
    "plugins/comfyui/ComfyUI/main.py",         # multi-segment dir pattern
    "backups/old/backend/app.py",              # backups/
    # install stamps from plugins/comfyui/scripts/install_deps.sh — a synced
    # master stamp makes clients SKIP installing deps their venv never got
    "plugins/comfyui/.requirements_installed",
    "plugins/comfyui/.custom_nodes_installed",
    # trees the ComfyUI updater keeps beside the live one
    "plugins/comfyui/ComfyUI.next/main.py",
    "plugins/comfyui/ComfyUI.next.stale-1760000000/models/x.safetensors",
    "plugins/comfyui/ComfyUI.prev-0.33.0/comfy/float.py",
    "plugins/comfyui/.update_job.json",
    # runtime caches; the code-search plugin keeps a private request key in its own
    "plugins/zvec_grep/cache/mcp-request-state.key",
])
def test_real_artifacts_still_excluded(svc, path):
    assert svc.should_exclude_file(path) is True, f"{path} should be excluded but was not"


# --- patterns describe the tree inside the install (2026-10-24) ---
# Matched against the absolute path, an install that lives under a folder named
# like a pattern (/data, ~/venv, /srv/build) excluded every one of its files.
@pytest.mark.parametrize("root", ["/data/guaardvark", "/home/u/venv/gx", "/srv/build/gx", "/opt/logs/gx"])
def test_install_location_does_not_exclude_its_files(svc, monkeypatch, root):
    monkeypatch.setenv("GUAARDVARK_ROOT", root)
    assert svc.should_exclude_file(f"{root}/backend/app.py") is False
    assert svc.should_exclude_file(f"{root}/frontend/node_modules/react/index.js") is True


# --- dependency-pin manifests MUST ride the sync allowlist (2026-08-04) ---
# default_sync_paths enumerates backend/ files INDIVIDUALLY, so new manifests
# are silently left behind unless listed: backend/constraints.txt (numpy<2
# convergence via PIP_CONSTRAINT) and backend/requirements-cv.txt (jax pins)
# were both missing — clients kept the numpy churn no matter what master fixed.
@pytest.mark.parametrize("path", [
    "backend/requirements.txt",
    "backend/requirements-base.txt",
    "backend/requirements-cv.txt",
    "backend/constraints.txt",
])
def test_dependency_manifests_are_synced(svc, path):
    assert path in svc.default_sync_paths, f"{path} missing from default_sync_paths"
    assert svc.should_exclude_file(path) is False, f"{path} is in sync paths but exclude-filtered"


# --- every top-level entry under backend/ must be on the allowlist (2026-08-31) ---
# default_sync_paths names backend/ packages one by one. backend/profiles and
# backend/extensions were added without a line here, so clients pulled a
# config.py / app.py that import them and nothing to import: every client boot
# died with "cannot import name 'extensions' from 'backend'". This walks the
# real backend/ directory so the next new package fails here, not on a client.
_BACKEND_DIR = Path(inspect.getfile(InterconnectorFileSyncService)).resolve().parents[1]


def test_every_backend_entry_is_synced(svc):
    listed = {p.rstrip("/") for p in svc.default_sync_paths}
    missing = []
    for entry in sorted(_BACKEND_DIR.iterdir()):
        if entry.name.startswith("."):
            continue
        rel = f"backend/{entry.name}"
        probe = f"{rel}/probe.py" if entry.is_dir() else rel
        if svc.should_exclude_file(probe):
            continue  # venv, data, __pycache__, logs, pids: excluded by policy
        if rel not in listed:
            missing.append(rel)
    assert not missing, (
        f"backend/ entries not in default_sync_paths (clients will never receive them): {missing}"
    )
