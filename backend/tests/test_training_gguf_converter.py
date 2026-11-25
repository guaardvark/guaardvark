"""Settings > Training libraries > GGUF converter: llama.cpp's converter is
fetched only on the Install click, file by file from the pinned commit, kept
only when every file matches its declared sha256, checked again before each
conversion, run offline on the CPU, and deleted on Remove.

Seams: install_dir, _spawn and FILES on the service module;
backend.utils.hosts.no_netrc_session, read by _install at call time; subprocess.run on the module for convert.
Nothing touches the network or runs the converter.
"""
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingJob, db
from backend.services import training_base_models as tbm
from backend.services import training_gguf_converter as gc
from backend.services import training_libraries as tl

CONTENT = {
    "convert_hf_to_gguf.py": b"print('convert')\n",
    "LICENSE": b"MIT License\n",
    "conversion/__init__.py": b"# conversion\n",
    "gguf-py/gguf/__init__.py": b"# gguf\n",
}


def _declared(content):
    return tuple((path, len(data), hashlib.sha256(data).hexdigest()) for path, data in content.items())


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """A private install folder, small declared files and an idle run; the
    install runs in the calling thread."""
    folder = tmp_path / "data" / "training" / "gguf_converter"
    monkeypatch.setattr(gc, "install_dir", lambda: folder)
    monkeypatch.setattr(gc, "FILES", _declared(CONTENT))
    # The total is summed from FILES once, at import.
    monkeypatch.setattr(gc, "DOWNLOAD_BYTES", sum(len(data) for data in CONTENT.values()))
    monkeypatch.setattr(gc, "_run", gc._idle_run())
    monkeypatch.setattr(gc, "_verified", {"key": None, "at": 0.0, "state": None})
    monkeypatch.setattr(gc, "_spawn", lambda target, *args, name: target(*args))
    monkeypatch.setattr(tl, "_tokens", {})
    return SimpleNamespace(folder=folder, tmp=tmp_path)


class FakeGitHub:
    """A no_netrc_session() serving `served` (path -> bytes) and recording URLs."""

    def __init__(self, served):
        self.served = dict(served)
        self.urls = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.urls.append(url)
        prefix = f"https://raw.githubusercontent.com/{gc.PIN['repo']}/{gc.PIN['commit']}/"
        path = url[len(prefix):] if url.startswith(prefix) else None
        data = self.served.get(path)
        response = SimpleNamespace(status_code=200 if data is not None else 404,
                                   iter_content=lambda chunk_size: iter([data or b""]))
        return _Context(response)


class _Context:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *exc):
        return False


@pytest.fixture
def github(monkeypatch):
    from backend.utils import hosts

    fake = FakeGitHub(CONTENT)
    monkeypatch.setattr(hosts, "no_netrc_session", fake)
    return fake


def _install(token=None):
    return gc.start_install(token or tl.issue_plan_token())


# ---- the declared pin ---------------------------------------------------------------

def test_the_pin_declares_a_commit_and_every_file_with_its_sha256():
    assert len(gc.PIN["commit"]) == 40 and int(gc.PIN["commit"], 16) >= 0
    assert gc.PIN["tag"] and gc.PIN["license"] == "MIT" and gc.PIN["repo"] == "ggml-org/llama.cpp"
    paths = [path for path, _, _ in gc.FILES]
    assert len(paths) == len(set(paths))
    for required in ("convert_hf_to_gguf.py", "LICENSE", "gguf-py/LICENSE",
                     "conversion/__init__.py", "conversion/base.py", "conversion/qwen.py",
                     "gguf-py/gguf/__init__.py", "gguf-py/gguf/gguf_writer.py"):
        assert required in paths
    for path, size, digest in gc.FILES:
        assert size > 0 or path.endswith("py.typed"), path
        assert len(digest) == 64 and int(digest, 16) >= 0, path
    assert gc.DOWNLOAD_BYTES == sum(size for _, size, _ in gc.FILES)


def test_the_levels_are_ones_the_converter_writes_and_the_default_is_one_of_them():
    assert gc.DEFAULT_LEVEL in gc.LEVELS
    for key, level in gc.LEVELS.items():
        assert level["outtype"] in ("f32", "f16", "bf16", "q8_0"), key
        assert 0 < level["size_share"] <= 1 and level["measured"]
    assert [lvl["value"] for lvl in gc.levels()][0] == gc.DEFAULT_LEVEL


def test_every_checked_family_is_one_a_base_model_declares():
    families = {entry["architecture"] for entry in tbm.BASE_MODELS}
    assert set(gc.ARCHITECTURES) <= families
    assert all(gc.ARCHITECTURES.values())


@pytest.mark.parametrize("asked, level", [(None, "q8_0"), ("", "q8_0"), ("Q8_0", "q8_0"), ("f16", "f16")])
def test_a_level_is_read_case_blind_and_none_is_the_default(asked, level):
    assert gc.level_for(asked) == level


def test_a_level_the_converter_does_not_write_is_refused_with_the_choices():
    with pytest.raises(ValueError, match="offers Q8_0, F16; q4_k_m is not one of them"):
        gc.level_for("q4_k_m")


# ---- status -------------------------------------------------------------------------

def test_a_stock_install_lists_it_missing_with_what_would_download(machine):
    status = gc.status()
    assert status["state"] == "missing"
    assert status["tag"] == gc.PIN["tag"] and status["commit"] == gc.PIN["commit"]
    assert status["download_host"] == "raw.githubusercontent.com"
    assert status["files"] == len(CONTENT)
    assert status["download_bytes"] == sum(len(d) for d in CONTENT.values())
    assert status["license"] == "MIT" and status["python_packages"].startswith("None")
    assert status["location"] == str(machine.folder)
    assert "plan_token" not in status
    assert gc.status(with_plan_token=True)["plan_token"] in tl._tokens


def test_missing_refuses_export_and_names_where_to_install(machine):
    code, reason = gc.refusal_for(tbm.get("Qwen/Qwen2.5-1.5B-Instruct"))
    assert code == "GGUF_CONVERTER_MISSING"
    assert "Settings > Training libraries > GGUF converter" in reason and "from GitHub" in reason


# ---- install ------------------------------------------------------------------------

def test_install_refuses_without_a_plan_token_and_fetches_nothing(machine, github):
    for token in (None, "", "made-up"):
        with pytest.raises(gc.Refused) as refused:
            gc.start_install(token)
        assert refused.value.code == "NEEDS_CLICK"
    assert github.urls == [] and not machine.folder.exists()


def test_install_fetches_each_pinned_file_at_the_commit_and_checks_it(machine, github):
    _install()

    prefix = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{gc.PIN['commit']}/"
    assert github.urls == [prefix + path for path in CONTENT]
    for path, data in CONTENT.items():
        assert (machine.folder / path).read_bytes() == data
    record = json.loads((machine.folder / "guaardvark-install.json").read_text())
    assert record["commit"] == gc.PIN["commit"] and record["tag"] == gc.PIN["tag"]
    assert gc._run["state"] == "completed" and gc._run["progress"] == 100
    assert gc.status()["state"] == "installed"
    assert gc.refusal_for(tbm.get("Qwen/Qwen2.5-1.5B-Instruct")) is None
    assert [p.name for p in machine.folder.parent.iterdir()] == ["gguf_converter"]


def test_a_file_that_differs_from_its_sha256_installs_nothing(machine, github):
    github.served["conversion/__init__.py"] = b"# something else\n"

    _install()

    assert gc._run["state"] == "failed"
    assert "conversion/__init__.py from GitHub does not match its pinned sha256" in gc._run["error"]
    assert not machine.folder.exists()
    assert list(machine.folder.parent.iterdir()) == []  # the staging folder is gone too


def test_a_file_github_does_not_serve_installs_nothing(machine, github):
    del github.served["LICENSE"]

    _install()

    assert gc._run["state"] == "failed" and "GitHub answered 404 for LICENSE" in gc._run["error"]
    assert not machine.folder.exists()


def test_a_second_install_of_an_intact_converter_is_refused(machine, github):
    _install()
    with pytest.raises(gc.Refused) as refused:
        _install()
    assert refused.value.code == "ALREADY_INSTALLED"


def test_a_changed_file_or_another_commit_is_not_used_and_install_replaces_it(machine, github):
    _install()
    (machine.folder / "conversion/__init__.py").write_bytes(b"# edited\n")
    state = gc.installed_state(fresh=True)
    assert state["state"] == "damaged" and "conversion/__init__.py" in state["problem"]
    assert gc.refusal_for(tbm.get("Qwen/Qwen2.5-1.5B-Instruct"))[0] == "GGUF_CONVERTER_NOT_READY"

    record = machine.folder / "guaardvark-install.json"
    record.write_text(json.dumps({"tag": "b1", "commit": "0" * 40}))
    assert gc.installed_state(fresh=True)["state"] == "other_version"

    _install()
    assert gc.installed_state(fresh=True)["state"] == "installed"
    assert (machine.folder / "conversion/__init__.py").read_bytes() == CONTENT["conversion/__init__.py"]


def test_a_family_not_checked_with_this_pin_is_refused(machine, github):
    _install()
    code, reason = gc.refusal_for(tbm.get("google/gemma-4-E4B-it"))
    assert code == "GGUF_FAMILY_UNCHECKED" and "Gemma 4 E4B Instruct" in reason


# ---- remove -------------------------------------------------------------------------

def test_remove_needs_a_token_and_deletes_the_folder(machine, github):
    _install()
    with pytest.raises(gc.Refused):
        gc.start_remove(None)
    assert machine.folder.exists()

    gc.start_remove(tl.issue_plan_token())

    assert not machine.folder.exists() and gc.status()["state"] == "missing"
    with pytest.raises(gc.Refused) as refused:
        gc.start_remove(tl.issue_plan_token())
    assert refused.value.code == "NOTHING_TO_REMOVE"


# ---- converting ---------------------------------------------------------------------

@pytest.fixture
def converter_run(monkeypatch):
    """subprocess.run for the converter: records the call and writes the
    file it was asked for (or fails with `code`)."""
    import subprocess

    calls = []
    outcome = {"code": 0}

    def run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        if outcome["code"] == 0:
            Path(cmd[cmd.index("--outfile") + 1]).write_bytes(b"GGUF")
            return subprocess.CompletedProcess(cmd, 0, stdout="INFO:hf-to-gguf:Model successfully exported\n")
        return subprocess.CompletedProcess(cmd, outcome["code"], stdout="ERROR: Model X is not supported\n")

    monkeypatch.setattr(gc.subprocess, "run", run)
    return SimpleNamespace(calls=calls, outcome=outcome)


def test_convert_runs_the_installed_script_offline_on_the_cpu(machine, github, converter_run, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "secret")
    _install()
    merged, out = machine.tmp / "run" / "merged", machine.tmp / "run" / "run-q8_0.gguf"
    merged.mkdir(parents=True)

    result = gc.convert(merged, out, "q8_0", log_path=machine.tmp / "run" / "train.log")

    (call,) = converter_run.calls
    assert call["cmd"][:3] == [sys.executable, str(machine.folder / "convert_hf_to_gguf.py"), str(merged)]
    assert call["cmd"][3:] == ["--outtype", "q8_0", "--outfile", str(out) + ".partial"]
    assert call["env"]["HF_HUB_OFFLINE"] == "1" and call["env"]["CUDA_VISIBLE_DEVICES"] == ""
    assert "HF_TOKEN" not in call["env"]
    assert result == out and out.read_bytes() == b"GGUF"
    assert not Path(str(out) + ".partial").exists()
    assert "convert to GGUF (q8_0)" in (machine.tmp / "run" / "train.log").read_text()


def test_a_failed_conversion_leaves_no_file_and_says_why(machine, github, converter_run):
    _install()
    converter_run.outcome["code"] = 1
    out = machine.tmp / "run-f16.gguf"

    with pytest.raises(gc.ConverterFailed, match="exited with code 1: ERROR: Model X is not supported"):
        gc.convert(machine.tmp, out, "f16", log_path=machine.tmp / "train.log")

    assert not out.exists() and not Path(str(out) + ".partial").exists()


def test_convert_refuses_files_changed_since_the_install(machine, github, converter_run):
    _install()
    gc.installed_state()  # a cached verdict does not stand in for the fresh check
    (machine.folder / "convert_hf_to_gguf.py").write_bytes(b"print('other')\n")

    with pytest.raises(gc.ConverterFailed, match="convert_hf_to_gguf.py does not match"):
        gc.convert(machine.tmp, machine.tmp / "x.gguf", "q8_0", log_path=machine.tmp / "train.log")
    assert converter_run.calls == []


# ---- routes -------------------------------------------------------------------------

@pytest.fixture
def client(machine):
    from backend.api.training.routes import training_bp

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(training_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_the_routes_install_only_with_the_confirm_and_the_token(client, github):
    no_confirm = client.post("/api/training/libraries/gguf-converter/install", json={})
    assert no_confirm.status_code == 403 and no_confirm.get_json()["error"]["code"] == "NEEDS_CLICK"
    assert github.urls == []

    token = client.get("/api/training/libraries/gguf-converter?plan=1").get_json()["data"]["plan_token"]
    started = client.post("/api/training/libraries/gguf-converter/install",
                          json={"confirm": "install", "plan_token": token})
    assert started.status_code == 202
    assert client.get("/api/training/libraries/gguf-converter").get_json()["data"]["state"] == "installed"


def test_remove_waits_for_a_running_export(client, github):
    _install()
    db.session.add(TrainingJob(job_id="exp", name="exp", base_model="org/base", status="running",
                               pipeline_stage="exporting"))
    db.session.commit()
    token = client.get("/api/training/libraries/gguf-converter?plan=1").get_json()["data"]["plan_token"]

    response = client.post("/api/training/libraries/gguf-converter/remove",
                           json={"confirm": "remove", "plan_token": token})

    assert response.status_code == 409 and response.get_json()["error"]["code"] == "EXPORT_RUNNING"
    assert gc.status()["state"] == "installed"


def test_other_hosts_cannot_install_or_remove():
    from backend.utils.auth_guard import _is_protected

    app = Flask(__name__)
    for path in ("/api/training/libraries/gguf-converter/install",
                 "/api/training/libraries/gguf-converter/remove"):
        with app.test_request_context(path, method="POST"):
            assert _is_protected() is True, path
    with app.test_request_context("/api/training/libraries/gguf-converter", method="GET"):
        assert _is_protected() is False
