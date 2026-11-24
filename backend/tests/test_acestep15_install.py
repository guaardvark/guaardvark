"""ACE-Step 1.5 is optional: installed only by its Install, refused until then.

It counts as installed only when its own Python environment is built from the
pinned source commit and every checkpoint file is in data/models/ace-step-1.5.
Install builds the environment first, then downloads the pinned revision into
that folder (not the shared Hugging Face cache, whose blobs ACE-Step's loader
would rewrite). The generate route refuses a model it does not have rather than
answering with another. No network, GPU or subprocess: the build, the download
and the sidecar are faked.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from backend.services import audio_foundry_models as afm
from backend.tests.fixtures.model_files import write_model_file

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "audio_foundry"
CATALOG = json.loads((PLUGIN / "backends" / "acestep15_files.json").read_text())


def _entry():
    return next(e for e in afm.AUDIO_FOUNDRY_MODELS if e["id"] == "ace-step-1.5")


@pytest.fixture(autouse=True)
def _clean_state():
    afm.reset_download_state()
    yield
    afm.reset_download_state()


@pytest.fixture
def install_root(tmp_path, monkeypatch):
    """A repo root and plugin dir with nothing installed."""
    monkeypatch.setattr(afm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(afm, "PLUGIN_DIR", tmp_path / "plugin")
    return tmp_path


def _build_env(root: Path, commit: str = CATALOG["source"]["commit"]) -> None:
    venv = root / "plugin" / CATALOG["environment"]
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("")
    (venv / ".acestep15-source").write_text(commit + "\n")


def _place_weights(root: Path) -> None:
    ckpt = root / CATALOG["local_dir"] / "checkpoints"
    for name in CATALOG["files"]:
        write_model_file(ckpt / name)


def test_the_catalog_pins_what_install_and_the_runner_read():
    entry = _entry()
    assert entry["hf_repo"] == CATALOG["hf_repo"]
    assert entry["license"] == "MIT" and entry["group"] == "music"
    assert re.fullmatch(r"[0-9a-f]{40}", CATALOG["hf_revision"])
    assert re.fullmatch(r"[0-9a-f]{40}", CATALOG["source"]["commit"])
    assert CATALOG["local_dir"].startswith("data/models/")
    # plugins/*/venv-*/ is ignored by git, backups and Interconnector sync.
    assert CATALOG["environment"].startswith("venv-")
    files = afm.required_hub_files(entry)
    assert files == CATALOG["files"] and not any(f.endswith(".py") for f in files)
    for folder in (CATALOG["dit"], CATALOG["planner"], "vae", "Qwen3-Embedding-0.6B"):
        assert any(f.startswith(folder + "/") for f in files)
    assert (PLUGIN / entry["environment_script"]).is_file()


def test_installed_only_with_the_environment_and_every_weight(install_root):
    entry = _entry()
    row = afm._hub_row(entry)
    assert row["installed"] is False and row["missing_files"][0].startswith(CATALOG["environment"])

    _place_weights(install_root)
    assert afm._hub_row(entry)["installed"] is False

    _build_env(install_root, commit="0" * 40)
    assert afm.missing_environment(entry), "an environment from another commit is not this release"

    _build_env(install_root)
    row = afm._hub_row(entry)
    assert row["installed"] is True and row["missing_files"] == []
    assert row["license"] == "MIT" and row["vram_note"]


def test_the_vram_note_states_the_catalogs_measured_reserve():
    vram = CATALOG["vram"]
    assert isinstance(vram["reserve_mb"], int) and vram["reserve_mb"] > 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", vram["measured"]) and vram["method"]
    note = afm._hub_row(_entry())["vram_note"]
    assert f"{vram['reserve_mb'] / 1000:.1f} GB" in note and vram["card"] in note


def test_v1_is_unaffected_by_the_15_helpers():
    v1 = next(e for e in afm.AUDIO_FOUNDRY_MODELS if e["id"] == "ace-step")
    assert afm.local_weights_dir(v1) is None
    assert afm.missing_environment(v1) == []
    assert afm.environment_supported(v1) is None


def test_install_builds_the_environment_then_downloads_the_pinned_revision(install_root, monkeypatch):
    entry = _entry()
    calls = []
    monkeypatch.setattr(afm, "_dir_bytes", lambda d: 0)

    def fake_build(e):
        calls.append(("env", e["id"]))
        _build_env(install_root)

    def fake_snapshot(**kw):
        calls.append(("snapshot", kw))
        _place_weights(install_root)

    import huggingface_hub
    monkeypatch.setattr(afm, "build_model_environment", fake_build)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    with afm._download_lock:
        afm._download_state.update({"epoch": 3, "is_downloading": True})
    afm._run_install(entry, 3)

    assert [c[0] for c in calls] == ["env", "snapshot"]
    kw = calls[1][1]
    assert kw["repo_id"] == CATALOG["hf_repo"]
    assert kw["revision"] == CATALOG["hf_revision"]
    assert kw["local_dir"] == str(install_root / CATALOG["local_dir"] / "checkpoints")
    assert afm._download_state["status"] == "completed"
    assert afm._hub_row(entry)["installed"] is True


def test_an_environment_failure_is_reported_as_itself(install_root, monkeypatch):
    def failing(_entry):
        raise afm.EnvironmentBuildFailed("Building the ACE-Step 1.5 environment failed: git not found")

    import huggingface_hub
    monkeypatch.setattr(afm, "build_model_environment", failing)
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda **kw: pytest.fail("no download after a failed build"))
    with afm._download_lock:
        afm._download_state.update({"epoch": 4, "is_downloading": True})
    afm._run_install(_entry(), 4)
    st = afm._download_state
    assert st["status"] == "failed" and st["is_downloading"] is False
    assert st["error"] == "Building the ACE-Step 1.5 environment failed: git not found"


def test_the_build_failure_names_the_scripts_failed_line(install_root, monkeypatch):
    def fake_run(argv, stdout, **kw):
        stdout.write("  [audio_foundry/setup_music15] FAILED: git not found\n")
        stdout.flush()
        return type("P", (), {"returncode": 1})()

    monkeypatch.setattr(afm.subprocess, "run", fake_run)
    with pytest.raises(afm.EnvironmentBuildFailed, match="git not found"):
        afm.build_model_environment(_entry())
    assert (install_root / "logs" / "ace-step-1.5_install.log").is_file()


def test_install_is_refused_where_the_environment_cannot_be_built(install_root, monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    payload, status = afm.start_download("ace-step-1.5")
    assert status == 400 and "Linux x86_64" in payload["error"]


def test_install_reports_already_installed_only_when_both_parts_are_there(install_root):
    _place_weights(install_root)
    _build_env(install_root)
    payload, status = afm.start_download("ace-step-1.5")
    assert status == 200 and payload["already_installed"] is True


def _runner():
    spec = importlib.util.spec_from_file_location("run_acestep15_under_test",
                                                  PLUGIN / "scripts" / "run_acestep15.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_runner_refuses_missing_weights_before_ace_step_can_download(tmp_path, monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner, "_REPO_ROOT", tmp_path)
    reply = runner._do_load()
    assert reply["ok"] is False
    assert "not on this machine" in reply["error"] and "Manage models" in reply["error"]


# ---- the generate route ------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from flask import Flask
    from backend.api import audio_foundry_api as api

    forwarded = []
    monkeypatch.setattr(api, "_proxy_generate",
                        lambda path, data: (forwarded.append((path, data)) or ({"ok": True}, 202)))
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(api.audio_foundry_bp)
    return app.test_client(), forwarded


def test_an_unknown_music_model_is_refused(client):
    c, forwarded = client
    res = c.post("/api/audio-foundry/generate/music", json={"style_prompt": "x", "model": "ace-step-2"})
    assert res.status_code == 400 and "Unknown music model" in res.get_json()["error"]
    assert forwarded == []


def test_15_is_refused_until_installed_then_forwarded(client, monkeypatch):
    c, forwarded = client
    monkeypatch.setattr(afm, "missing_parts", lambda model_id: ["venv-music15 (Python environment)"])
    res = c.post("/api/audio-foundry/generate/music", json={"style_prompt": "x", "model": "ace-step-1.5"})
    assert res.status_code == 400
    body = res.get_json()
    assert body["needs_install"] == "ace-step-1.5" and "Manage models" in body["error"]
    assert forwarded == []

    monkeypatch.setattr(afm, "missing_parts", lambda model_id: [])
    res = c.post("/api/audio-foundry/generate/music", json={"style_prompt": "x", "model": "ace-step-1.5"})
    assert res.status_code == 202
    assert forwarded == [("/generate/music", {"style_prompt": "x", "model": "ace-step-1.5"})]


def test_no_model_still_goes_straight_to_the_sidecar(client):
    c, forwarded = client
    res = c.post("/api/audio-foundry/generate/music", json={"style_prompt": "x"})
    assert res.status_code == 202 and forwarded == [("/generate/music", {"style_prompt": "x"})]
