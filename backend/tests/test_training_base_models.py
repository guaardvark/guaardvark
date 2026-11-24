"""Training base models: a declared list with the limits and VRAM a run is
held to, downloaded only from the modal's Download click without sending a
Hugging Face token, recorded at the commit that arrived, found offline for
training, and jobs on anything else (an Ollama tag, a model not downloaded,
one that does not fit) refused with the reason.

Seams: huggingface_hub.snapshot_download, read by the module at call time;
_hardware, _record_path, _spawn and _download on the module; the HF cache
folder through local_weights.hf_repo_cache_dir, read at call time. Nothing
touches the network or this machine's GPU."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

import huggingface_hub
from flask import Flask

from backend.models import TrainingDataset, TrainingJob, db
from backend.services import training_base_models as tbm
from backend.services import training_libraries as tl
from backend.tests.fixtures.model_files import safetensors_bytes, write_model_file

SMALL, DEFAULT = "Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct"
GEMMA = "google/gemma-4-E4B-it"
GPU_16 = {"practical": True, "reason": "NVIDIA GPU with 16 GB of memory.", "gpu": "test card",
          "vram_mb": 16376, "ram_gb": 64}


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """A 16 GB card, a private record file and HF cache, an idle download."""
    hardware = dict(GPU_16)
    monkeypatch.setattr(tbm, "_hardware", lambda: dict(hardware))
    monkeypatch.setattr(tbm, "_record_path", lambda: tmp_path / "base_models.json")
    monkeypatch.setattr(tbm, "_download", tbm._idle_download())
    spawned = []
    monkeypatch.setattr(tbm, "_spawn", lambda target, *args, name: spawned.append((target, args)))
    monkeypatch.setattr(tl, "_tokens", {})
    cache = tmp_path / "hub"
    monkeypatch.setattr("backend.services.local_weights.hf_repo_cache_dir",
                        lambda repo: cache / f"models--{repo.replace('/', '--')}")
    return SimpleNamespace(hardware=hardware, record=tmp_path / "base_models.json", cache=cache,
                           spawned=spawned, tmp=tmp_path)


def _snapshot(root, name="abc123", files=("config.json", "tokenizer_config.json", "model.safetensors")):
    folder = root / "snapshots" / name
    folder.mkdir(parents=True, exist_ok=True)
    for f in files:
        write_model_file(folder / f)
    return folder


@pytest.fixture
def hub(monkeypatch):
    """snapshot_download: local_files_only answers from `cached`; a download
    records its arguments and lands in `download_to`."""
    state = SimpleNamespace(cached={}, calls=[], download_to=None, fail=None)

    def snapshot_download(**kwargs):
        state.calls.append(kwargs)
        if kwargs.get("local_files_only"):
            if kwargs["repo_id"] not in state.cached:
                raise FileNotFoundError("not in the cache")
            return str(state.cached[kwargs["repo_id"]])
        if state.fail:
            raise state.fail
        state.cached[kwargs["repo_id"]] = state.download_to
        return str(state.download_to)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    return state


# ---- the declared list ------------------------------------------------------------

def test_every_entry_declares_what_the_product_enforces():
    assert [e["id"] for e in tbm.BASE_MODELS] == [SMALL, DEFAULT, GEMMA]
    for entry in tbm.BASE_MODELS:
        assert entry["repo"] == entry["id"] and entry["revision"]
        assert entry["license"] == "Apache-2.0"
        assert entry["size_gb"] > 0 and entry["max_seq_length"] > 0 and entry["max_batch_size"] > 0
        assert entry["vram_mb"] > 0 and entry["vram_measured"]
        assert entry["export"] in ("verified", "unverified")
        assert entry["ollama_tags"] and all(":" in tag for tag in entry["ollama_tags"])
        assert entry["response_markers"]["response"].rstrip("\n") in entry["ollama_template"]
        assert entry["ollama_stop"]
        assert '"""' not in entry["ollama_template"]  # it sits in a Modelfile's triple quotes
        assert set(entry["response_markers"]) == {"instruction", "response"}


def test_a_model_not_yet_measured_downloads_but_takes_no_job(machine, hub):
    entry = tbm.get(GEMMA)
    hub.cached[GEMMA] = _snapshot(machine.tmp / "gemma")

    assert tbm.trainable(entry) is False and tbm.trainable(tbm.get(DEFAULT)) is True
    assert {m["id"]: m for m in tbm.status()["models"]}[GEMMA]["trainable"] is False
    assert "not offered for training yet" in tbm.refusal_for_job(GEMMA)
    assert tbm.fit(entry)[0] is True  # so the Download is offered on a 16 GB card


def test_export_is_offered_only_where_it_was_measured():
    assert tbm.export_verified(DEFAULT) is True and tbm.get(DEFAULT)["export_measured"]
    assert tbm.export_verified(SMALL) is False
    assert tbm.export_verified(GEMMA) is False
    assert tbm.export_verified("org/unknown") is False


# ---- status -----------------------------------------------------------------------

def test_status_says_which_models_are_downloaded_and_fit(machine, hub):
    hub.cached[DEFAULT] = _snapshot(machine.tmp / "q15")

    models = {m["id"]: m for m in tbm.status()["models"]}

    assert models[SMALL]["installed"] is False and models[DEFAULT]["installed"] is True
    assert all(m["fits"] for m in models.values())
    entry = next(m for m in tbm.BASE_MODELS if m["id"] == DEFAULT)
    assert models[DEFAULT]["vram_measured"] == entry["vram_measured"]
    assert all(call["local_files_only"] and call["token"] is False for call in hub.calls)


def test_a_snapshot_without_its_weights_is_not_downloaded(machine, hub):
    hub.cached[DEFAULT] = _snapshot(machine.tmp / "q15", files=("config.json", "tokenizer_config.json"))
    assert tbm.local_snapshot(tbm.get(DEFAULT)) is None

    sharded = _snapshot(machine.tmp / "sharded", files=("config.json", "tokenizer_config.json", "a.safetensors"))
    (sharded / "model.safetensors.index.json").write_text(json.dumps(
        {"weight_map": {"x": "a.safetensors", "y": "b.safetensors"}}))
    hub.cached[DEFAULT] = sharded
    assert tbm.local_snapshot(tbm.get(DEFAULT)) is None
    (sharded / "b.safetensors").write_bytes(safetensors_bytes(8)[:-4])
    assert tbm.local_snapshot(tbm.get(DEFAULT)) is None
    write_model_file(sharded / "b.safetensors")
    assert tbm.local_snapshot(tbm.get(DEFAULT)) == str(sharded)


def test_a_model_too_big_for_the_gpu_does_not_fit_and_says_why(machine):
    machine.hardware["vram_mb"] = 6144
    assert tbm.fit(tbm.get(SMALL))[0] is True
    fits, why = tbm.fit(tbm.get(DEFAULT))
    assert fits is False and "this GPU has 6.0 GB" in why

    machine.hardware.update(practical=False, reason="No GPU was found.")
    assert tbm.fit(tbm.get(SMALL)) == (False, "No GPU was found.")


# ---- jobs ----------------------------------------------------------------------------

def test_jobs_on_anything_but_a_downloaded_declared_model_are_refused(machine, hub):
    assert "is an Ollama model" in tbm.refusal_for_job("qwen2.5:1.5b")
    assert DEFAULT in tbm.refusal_for_job("qwen2.5:1.5b")
    assert "looks like an Ollama model" in tbm.refusal_for_job("llama3:8b")
    assert "not a base model Guaardvark can fine-tune" in tbm.refusal_for_job("org/other")
    assert "not downloaded yet" in tbm.refusal_for_job(DEFAULT)

    hub.cached[DEFAULT] = _snapshot(machine.tmp / "q15")
    assert tbm.refusal_for_job(DEFAULT) is None
    assert "text model" in tbm.refusal_for_job(DEFAULT, vision=True)


def test_training_loads_the_recorded_commit_offline(machine, hub):
    with pytest.raises(tbm.BaseModelUnavailable, match="not on this machine"):
        tbm.resolve_for_training(DEFAULT)

    machine.record.write_text(json.dumps({DEFAULT: {"revision": "abc123"}}))
    hub.cached[DEFAULT] = _snapshot(machine.tmp / "q15")
    entry, path = tbm.resolve_for_training(DEFAULT)

    assert entry["id"] == DEFAULT and path == str(hub.cached[DEFAULT])
    assert hub.calls[-1] == {"repo_id": DEFAULT, "revision": "abc123", "local_files_only": True, "token": False}


# ---- download ------------------------------------------------------------------------

def test_download_starts_only_with_a_plan_token(machine, hub):
    for token in (None, "", "made-up"):
        with pytest.raises(tbm.Refused) as refused:
            tbm.start_install(token, DEFAULT)
        assert refused.value.code == "NEEDS_CLICK"
    assert machine.spawned == []

    tbm.start_install(tl.issue_plan_token(), DEFAULT)
    assert len(machine.spawned) == 1
    assert tbm._download["state"] == "running" and tbm._download["model"] == DEFAULT


def test_a_download_sends_no_token_and_records_the_commit_that_arrived(machine, hub, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_should_never_be_sent")
    hub.download_to = _snapshot(machine.tmp / "q15", name="0f1e2d3c")
    tbm.start_install(tl.issue_plan_token(), DEFAULT)
    target, args = machine.spawned[0]

    target(*args)

    download = hub.calls[-1]
    assert download == {"repo_id": DEFAULT, "revision": "main", "token": False}
    assert json.loads(machine.record.read_text())[DEFAULT]["revision"] == "0f1e2d3c"
    state = tbm._download_snapshot()
    assert state["state"] == "completed" and state["progress"] == 100
    assert tbm.local_snapshot(tbm.get(DEFAULT)) == str(hub.download_to)


@pytest.mark.parametrize("error, says", [
    (RuntimeError("429 Client Error: Too Many Requests"), "rate-limited"),
    (RuntimeError("Cannot reach the Hub: offline mode is enabled"), "HF_HUB_OFFLINE"),
])
def test_a_failed_download_says_why(machine, hub, error, says):
    hub.fail = error
    tbm.start_install(tl.issue_plan_token(), DEFAULT)
    target, args = machine.spawned[0]

    target(*args)

    state = tbm._download_snapshot()
    assert state["state"] == "failed" and says in state["error"]
    assert not machine.record.exists()


def test_download_refusals_name_the_reason(machine, hub):
    with pytest.raises(tbm.Refused) as unknown:
        tbm.start_install(tl.issue_plan_token(), "org/other")
    assert unknown.value.code == "UNKNOWN_MODEL"

    machine.hardware["vram_mb"] = 6144
    with pytest.raises(tbm.Refused) as too_big:
        tbm.start_install(tl.issue_plan_token(), DEFAULT)
    assert too_big.value.code == "DOES_NOT_FIT"
    machine.hardware["vram_mb"] = 16376

    hub.cached[SMALL] = _snapshot(machine.tmp / "q05")
    with pytest.raises(tbm.Refused) as already:
        tbm.start_install(tl.issue_plan_token(), SMALL)
    assert already.value.code == "ALREADY_INSTALLED"

    tbm.start_install(tl.issue_plan_token(), DEFAULT)
    with pytest.raises(tbm.Refused) as busy:
        tbm.start_install(tl.issue_plan_token(), DEFAULT)
    assert busy.value.code == "BUSY"


def test_remove_deletes_the_weights_and_the_record(machine, hub):
    folder = machine.cache / "models--Qwen--Qwen2.5-1.5B-Instruct"
    _snapshot(folder)
    machine.record.write_text(json.dumps({DEFAULT: {"revision": "abc123"}}))

    with pytest.raises(tbm.Refused):
        tbm.start_remove("made-up", DEFAULT)
    tbm.start_remove(tl.issue_plan_token(), DEFAULT)

    assert not folder.exists()
    assert json.loads(machine.record.read_text()) == {}
    with pytest.raises(tbm.Refused) as nothing:
        tbm.start_remove(tl.issue_plan_token(), DEFAULT)
    assert nothing.value.code == "NOTHING_TO_REMOVE"


# ---- routes --------------------------------------------------------------------------

@pytest.fixture
def client(machine, monkeypatch):
    from backend.api.training.routes import training_bp

    monkeypatch.setattr("backend.services.training_libraries.unavailable_reason", lambda: None)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(training_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _error(response):
    return response.get_json()["error"]


def test_the_list_route_hands_out_a_token_only_with_plan(client, hub):
    plain = client.get("/api/training/base-models").get_json()["data"]
    planned = client.get("/api/training/base-models?plan=1").get_json()["data"]
    assert [m["id"] for m in plain["models"]] == [SMALL, DEFAULT, GEMMA]
    assert "plan_token" not in plain and planned["plan_token"] in tl._tokens


def test_the_install_route_needs_the_confirm_and_the_token(client, hub, machine):
    refused = client.post("/api/training/base-models/install", json={"model": DEFAULT})
    assert refused.status_code == 403 and _error(refused)["code"] == "NEEDS_CLICK"

    token = client.get("/api/training/base-models?plan=1").get_json()["data"]["plan_token"]
    started = client.post("/api/training/base-models/install",
                          json={"confirm": "download", "model": DEFAULT, "plan_token": token})
    assert started.status_code == 202
    assert started.get_json()["data"]["download"]["state"] == "running"


def test_remove_waits_for_a_running_job_on_the_model(client, hub):
    db.session.add(TrainingJob(job_id="busy", name="busy", base_model=DEFAULT, status="running"))
    db.session.commit()
    token = client.get("/api/training/base-models?plan=1").get_json()["data"]["plan_token"]
    response = client.post("/api/training/base-models/remove",
                           json={"confirm": "remove", "model": DEFAULT, "plan_token": token})
    assert response.status_code == 409 and _error(response)["code"] == "TRAINING_RUNNING"


def _dataset(tmp_path):
    data = tmp_path / "d.jsonl"
    data.write_text(json.dumps({"instruction": "Name a colour.", "output": "Blue."}) + "\n")
    ds = TrainingDataset(name="d", path=str(data))
    db.session.add(ds)
    db.session.commit()
    return ds.id


def test_a_job_on_an_ollama_tag_is_refused_with_the_model_to_use(client, hub, machine):
    response = client.post("/api/training/jobs", json={
        "name": "job", "base_model": "qwen2.5:1.5b", "dataset_id": _dataset(machine.tmp)})
    assert response.status_code == 400
    assert _error(response)["code"] == "BASE_MODEL_UNAVAILABLE"
    assert DEFAULT in _error(response)["message"]
    assert db.session.query(TrainingJob).count() == 0


def test_a_job_past_the_model_limits_is_refused(client, hub, machine):
    hub.cached[DEFAULT] = _snapshot(machine.tmp / "q15")
    ds = _dataset(machine.tmp)
    over = client.post("/api/training/jobs", json={
        "name": "job", "base_model": DEFAULT, "dataset_id": ds, "config": {"batch_size": 64}})
    assert over.status_code == 400 and "Batch size 64 is above the 4" in _error(over)["message"]

    created = client.post("/api/training/jobs", json={
        "name": "job", "base_model": DEFAULT, "dataset_id": ds, "config": {"batch_size": 2}})
    assert created.status_code == 201


def test_other_hosts_cannot_download_or_remove_base_models():
    from backend.utils.auth_guard import _is_protected

    app = Flask(__name__)
    for path in ("/api/training/base-models/install", "/api/training/base-models/remove"):
        with app.test_request_context(path, method="POST"):
            assert _is_protected() is True, path
    with app.test_request_context("/api/training/base-models", method="GET"):
        assert _is_protected() is False
