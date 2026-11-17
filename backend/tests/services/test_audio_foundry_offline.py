"""Audio Foundry runs offline and listens on this machine only; Install fetches
everything generation reads.

Generation used to reach huggingface.co on every cold load and download
Kokoro voice packs on first use, and misaki pip-installed spaCy's English
model mid-request. The sidecar now runs with the Hub client offline, the
Manage-models row counts every file generation reads, and its Install fetches
them (plus the spaCy wheel, into the plugin venv). No network, GPU or database
here: snapshot_download, pip and threads are faked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import audio_foundry_models as afm
from backend.services.comfyui_launch_flags import LOCAL_ONLY_ENV

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "audio_foundry"
START_SH = PLUGIN / "scripts" / "start.sh"
CATALOG = PLUGIN / "backends" / "kokoro_voices.json"


def _entry(model_id):
    return next(e for e in afm.AUDIO_FOUNDRY_MODELS if e["id"] == model_id)


@pytest.fixture(autouse=True)
def _clean_state():
    afm.reset_download_state()
    yield
    afm.reset_download_state()


# ---- the sidecar process -------------------------------------------------------------
def test_start_sh_runs_the_sidecar_with_the_hub_client_offline():
    text = START_SH.read_text()
    env_line = text.index('source "$PROJECT_ROOT/.env"')
    launch = text.index("python -m uvicorn service.app:app")
    for key, value in LOCAL_ONLY_ENV.items():
        line = f"export {key}={value}"
        assert line in text, line
        # After .env, so a value there cannot switch it back on; before launch.
        assert env_line < text.index(line) < launch, line


def test_start_sh_listens_on_loopback_unless_opted_in():
    text = START_SH.read_text()
    assert 'BIND_HOST="${GUAARDVARK_AUDIO_FOUNDRY_HOST:-127.0.0.1}"' in text
    assert '--host "$BIND_HOST"' in text
    assert "--host 0.0.0.0" not in text
    assert "http://127.0.0.1:$SERVICE_PORT/health" in text


def test_sidecar_sends_no_cors_headers():
    app_py = (PLUGIN / "service" / "app.py").read_text()
    assert "CORSMiddleware" not in app_py
    assert "allow_origins" not in app_py


# ---- the voice catalog shared by sidecar and backend ---------------------------------
def test_catalog_ids_are_plain_voice_ids():
    cat = json.loads(CATALOG.read_text())
    ids = afm.kokoro_voice_ids()
    assert ids and len(ids) == len(set(ids))
    # A path ('x.pt') or a blend ('a,b') can never be a catalog id.
    assert all(re.fullmatch(r"[a-z]{2}_[a-z]+", v) for v in ids)
    assert cat["default"] in ids
    assert cat["hf_repo"] == _entry("kokoro")["hf_repo"]
    assert cat["voice_file"].format(voice="af_heart") == "voices/af_heart.pt"


def test_spacy_wheel_is_pinned_by_version_and_hash():
    g2p = json.loads(CATALOG.read_text())["english_g2p"]
    url, _, digest = g2p["wheel"].partition("#sha256=")
    assert url.startswith("https://github.com/explosion/spacy-models/releases/download/")
    assert url.split("/")[-1].startswith(g2p["package"] + "-3.8.0-")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


# ---- "installed" means every file generation reads -----------------------------------
def test_kokoro_is_not_installed_while_a_voice_pack_is_missing(monkeypatch):
    entry = _entry("kokoro")
    files = afm.required_hub_files(entry)
    assert {"config.json", "kokoro-v1_0.pth", "voices/af_heart.pt", "voices/bf_emma.pt"} <= set(files)
    have = set(files) - {"voices/bf_emma.pt"}
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: f in have)
    monkeypatch.setattr(afm, "venv_has_package", lambda name: True)

    row = afm._hub_row(entry)
    assert row["installed"] is False
    assert row["missing_files"] == ["voices/bf_emma.pt"]

    have.add("voices/bf_emma.pt")
    assert afm._hub_row(entry)["installed"] is True


def test_kokoro_needs_the_spacy_model_once_the_plugin_venv_exists(monkeypatch):
    entry = _entry("kokoro")
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: True)

    monkeypatch.setattr(afm, "venv_has_package", lambda name: False)
    row = afm._hub_row(entry)
    assert row["installed"] is False and row["missing_files"] == ["en_core_web_sm"]

    # No venv yet (plugin never started): nothing to check or install into.
    monkeypatch.setattr(afm, "venv_has_package", lambda name: None)
    assert afm._hub_row(entry)["installed"] is True


def test_chatterbox_needs_all_five_files(monkeypatch):
    entry = _entry("chatterbox")
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: f != "conds.pt")
    row = afm._hub_row(entry)
    assert row["installed"] is False and row["missing_files"] == ["conds.pt"]
    assert afm.required_venv_packages(entry) == []


def test_venv_has_package_reads_dist_info(tmp_path, monkeypatch):
    monkeypatch.setattr(afm, "PLUGIN_DIR", tmp_path)
    assert afm.venv_has_package("en_core_web_sm") is None
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    assert afm.venv_has_package("en_core_web_sm") is False
    (site / "en_core_web_sm-3.8.0.dist-info").mkdir()
    assert afm.venv_has_package("en_core_web_sm") is True


# ---- Install --------------------------------------------------------------------------
def test_install_starts_when_only_the_spacy_model_is_missing(monkeypatch):
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: True)
    monkeypatch.setattr(afm, "venv_has_package", lambda name: False)
    started = {}
    monkeypatch.setattr(afm.threading, "Thread",
                        lambda *a, **k: (started.update(k), SimpleNamespace(start=lambda: None))[1])

    payload, status = afm.start_download("kokoro")
    assert status == 200 and payload["status"] == "started"
    assert started["target"] is afm._run_install


def test_install_reports_already_installed_only_when_everything_is_there(monkeypatch):
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: True)
    monkeypatch.setattr(afm, "venv_has_package", lambda name: True)
    payload, status = afm.start_download("kokoro")
    assert status == 200 and payload["already_installed"] is True


def _run(monkeypatch, tmp_path, entry, *, cached, pkg_present, pip=None):
    calls = []
    state = {"cached": set(cached), "pkg": pkg_present}
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: f in state["cached"])
    monkeypatch.setattr(afm, "venv_has_package", lambda name: state["pkg"])
    monkeypatch.setattr(afm, "_dir_bytes", lambda d: 0)
    monkeypatch.setattr(afm, "_hf_repo_cache_dir", lambda repo: tmp_path / "hf-cache")
    # The fake snapshot writes no files; the checksum check has its own tests.
    monkeypatch.setattr(afm, "checksum_mismatches", lambda entry: [])

    def fake_snapshot(**kw):
        calls.append(("snapshot", kw["repo_id"]))
        state["cached"] = set(afm.required_hub_files(entry))

    def fake_pip(wheel):
        calls.append(("pip", wheel))
        state["pkg"] = True

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(afm, "pip_install_into_plugin_venv", pip or fake_pip)
    with afm._download_lock:
        afm._download_state.update({"epoch": 7, "is_downloading": True})
    afm._run_install(entry, 7)
    return calls


def test_install_fetches_missing_voice_packs_then_the_spacy_wheel(monkeypatch, tmp_path):
    entry = _entry("kokoro")
    calls = _run(monkeypatch, tmp_path, entry, cached={"config.json", "kokoro-v1_0.pth"}, pkg_present=False)
    assert [c[0] for c in calls] == ["snapshot", "pip"]
    assert calls[1][1] == afm.required_venv_packages(entry)[0]["wheel"]
    assert afm._download_state["status"] == "completed"
    assert afm._download_state["is_downloading"] is False


def test_install_skips_the_snapshot_when_every_file_is_cached(monkeypatch, tmp_path):
    entry = _entry("kokoro")
    calls = _run(monkeypatch, tmp_path, entry, cached=afm.required_hub_files(entry), pkg_present=False)
    assert [c[0] for c in calls] == ["pip"]
    assert afm._download_state["status"] == "completed"


def test_pip_failure_is_reported_as_itself(monkeypatch, tmp_path):
    def failing(wheel):
        raise afm.VenvInstallFailed("pip could not install en_core_web_sm: 404 Not Found")

    entry = _entry("kokoro")
    _run(monkeypatch, tmp_path, entry, cached=afm.required_hub_files(entry), pkg_present=False,
         pip=failing)
    st = afm._download_state
    assert st["status"] == "failed" and st["is_downloading"] is False
    # Not rewritten by the Hugging Face classifier into "No Hugging Face repo".
    assert st["error"] == "pip could not install en_core_web_sm: 404 Not Found"


def test_pip_runs_in_the_plugin_venv_without_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(afm, "PLUGIN_DIR", tmp_path)
    with pytest.raises(afm.VenvInstallFailed, match="start the plugin once"):
        afm.pip_install_into_plugin_venv("https://example.invalid/x.whl")

    py = tmp_path / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(afm.subprocess, "run", fake_run)
    wheel = afm.required_venv_packages(_entry("kokoro"))[0]["wheel"]
    afm.pip_install_into_plugin_venv(wheel)
    assert seen["argv"] == [str(py), "-m", "pip", "install", "--no-deps",
                            "--disable-pip-version-check", wheel]

    monkeypatch.setattr(afm.subprocess, "run",
                        lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="HTTP 404"))
    with pytest.raises(afm.VenvInstallFailed, match="HTTP 404"):
        afm.pip_install_into_plugin_venv(wheel)


# ---- Kokoro's languages ----------------------------------------------------------------
def _pack_names(pack_id):
    return json.loads(CATALOG.read_text())["language_packs"][pack_id]["packages"]


def test_language_packs_are_optional_rows_of_their_own(monkeypatch):
    monkeypatch.setattr(afm, "venv_has_package", lambda name: False)
    for pack_id, language in (("kokoro-japanese", "Japanese"), ("kokoro-mandarin", "Mandarin Chinese")):
        row = afm._hub_row(_entry(pack_id))
        assert row["optional"] is True and row["installed"] is False and row["group"] == "voice"
        assert row["language_pack"] is True
        assert row["missing_files"] == _pack_names(pack_id)
        assert language in row["description"] and "MB download" in row["description"]
    # Not part of Kokoro itself: a stock install is complete without them.
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: True)
    monkeypatch.setattr(afm, "venv_has_package", lambda name: name == "en_core_web_sm")
    assert afm._hub_row(_entry("kokoro"))["installed"] is True


def test_a_language_pack_is_missing_until_it_is_seen_in_the_venv(monkeypatch):
    entry = _entry("kokoro-japanese")
    # No venv yet: unlike spaCy's model, which every start installs, a pack is never assumed.
    monkeypatch.setattr(afm, "venv_has_package", lambda name: None)
    assert afm._hub_row(entry)["installed"] is False
    monkeypatch.setattr(afm, "venv_has_package", lambda name: True)
    assert afm._hub_row(entry)["installed"] is True


def test_voices_are_listed_with_the_language_they_still_need(monkeypatch):
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, f: True)
    have = {"en_core_web_sm", *_pack_names("kokoro-mandarin")}
    monkeypatch.setattr(afm, "venv_has_package", lambda name: name in have)

    groups = {g["label"]: g for g in afm.kokoro_voice_groups()}
    japanese = groups["Japanese Male"]
    assert japanese["language_installed"] is False
    assert japanese["install"] == {"id": "kokoro-japanese", "name": "Kokoro Japanese"}
    assert japanese["voices"] == [{"id": "jm_kumo", "label": "Kumo", "installed": True,
                                   "language_installed": False}]
    for label in ("Mandarin Chinese Female", "French Female", "Hindi Male", "American Female"):
        assert groups[label]["language_installed"] is True and groups[label]["install"] is None

    offered = {c["id"] for c in afm.kokoro_voice_choices(installed_only=True)}
    assert "zf_xiaoxiao" in offered and "ff_siwis" in offered
    assert not any(v.startswith(("jf_", "jm_")) for v in offered)


def test_language_pack_install_is_one_hash_checked_pip_run(tmp_path, monkeypatch):
    monkeypatch.setattr(afm, "PLUGIN_DIR", tmp_path)
    py = tmp_path / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    present = set()
    monkeypatch.setattr(afm, "venv_has_package", lambda name: name in present)
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        present.update(_pack_names("kokoro-mandarin"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(afm.subprocess, "run", fake_run)
    with afm._download_lock:
        afm._download_state.update({"epoch": 3, "is_downloading": True})
    afm._run_install(_entry("kokoro-mandarin"), 3)

    requirements = PLUGIN / "language_packs" / "kokoro-mandarin.txt"
    assert seen == [[str(py), "-m", "pip", "install", "--no-deps", "--require-hashes",
                     "--disable-pip-version-check", "-r", str(requirements)]]
    assert afm._download_state["status"] == "completed"
    assert afm._download_state["is_downloading"] is False


def test_language_pack_install_failure_shows_pips_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(afm, "PLUGIN_DIR", tmp_path)
    py = tmp_path / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(afm, "venv_has_package", lambda name: False)
    monkeypatch.setattr(afm.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="error: command 'cmake' failed"))
    with afm._download_lock:
        afm._download_state.update({"epoch": 4, "is_downloading": True})
    afm._run_install(_entry("kokoro-japanese"), 4)
    st = afm._download_state
    assert st["status"] == "failed" and st["is_downloading"] is False
    assert "cmake" in st["error"] and "kokoro-japanese.txt" in st["error"]


def test_a_downloaded_file_that_differs_from_its_checksum_is_reported(tmp_path, monkeypatch):
    from backend.services import local_weights

    good = tmp_path / "good.pt"
    good.write_bytes(b"voice")
    cat = json.loads(CATALOG.read_text())
    voice = cat["groups"][0]["voices"][0]
    import hashlib
    monkeypatch.setitem(voice, "sha256", hashlib.sha256(b"voice").hexdigest())
    monkeypatch.setattr(afm, "load_voice_catalog", lambda rel: cat)
    wanted = cat["voice_file"].format(voice=voice["id"])
    monkeypatch.setattr(local_weights, "cached_path",
                        lambda repo, name: str(good) if name == wanted else None)
    assert afm.checksum_mismatches(_entry("kokoro")) == []
    good.write_bytes(b"truncated")
    assert afm.checksum_mismatches(_entry("kokoro")) == [wanted]
    assert afm.checksum_mismatches(_entry("chatterbox")) == []
