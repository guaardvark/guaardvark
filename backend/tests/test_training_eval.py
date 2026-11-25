"""Every text training run holds out a slice of its dataset, never trains on
it, and records the held-out loss of the trained adapter and of the base model
on the job."""
import json
import math
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingJob, db
from backend.tasks import training_tasks as tt


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    monkeypatch.setattr(tt, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(tt, "PROCESSED_DIR", tmp_path)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def task_bodies_run_in_this_app(monkeypatch):
    """Calling a Celery task object goes through the worker's ContextTask,
    which pushes the worker's own Flask app and with it DATABASE_URL. The
    tests call each task's .run instead, and the in-process calls the
    pipeline makes resolve to .run too, so every query lands in this file's
    in-memory database."""
    for name in ("parse_transcripts_task", "filter_dataset_task", "finetune_model_task",
                 "export_gguf_task", "import_ollama_task", "full_training_pipeline_task"):
        monkeypatch.setattr(tt, name, getattr(tt, name).run)


@pytest.fixture(autouse=True)
def base_model_on_disk(monkeypatch, tmp_path):
    """The job's base model counts as declared and downloaded; the trainer
    stand-ins receive its snapshot folder."""
    snapshot = tmp_path / "snapshot"
    monkeypatch.setattr("backend.services.training_base_models.resolve_for_training",
                        lambda model_id: ({"id": model_id}, str(snapshot)))
    monkeypatch.setattr("backend.services.training_base_models.refusal_for_job",
                        lambda model_id, vision=False: None)
    return str(snapshot)


@pytest.fixture(autouse=True)
def no_gpu_claim(monkeypatch):
    """finetune_model_task claims the GPU through gpu_session_when_free, which
    calls gpu_session (a real VRAM check, Ollama eviction, a cross-process
    lease); none of that belongs in a unit test."""
    @contextmanager
    def no_gpu_session(*args, **kwargs):
        yield True

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", no_gpu_session)


@pytest.fixture
def progress(monkeypatch):
    sent = []

    def record(job_id, pct, message, status="processing", metrics=None):
        sent.append((pct, message, status))

    monkeypatch.setattr(tt, "_emit_progress", record)
    return sent


@pytest.fixture(autouse=True)
def export_verified(monkeypatch):
    """The pipeline exports only for a base model whose export is verified and
    once the GGUF converter is installed; these tests are about the gate in
    front of that, so both count as ready."""
    monkeypatch.setattr("backend.services.training_base_models.export_verified", lambda model_id: True)
    monkeypatch.setattr("backend.services.training_gguf_converter.refusal_for", lambda entry: None)


@pytest.fixture(autouse=True)
def libraries_ready(monkeypatch):
    """The training libraries count as installed and complete; the trainer
    itself is a stand-in, and the refusal has its own tests."""
    monkeypatch.setattr("backend.services.training_libraries.unavailable_reason", lambda: None)


@pytest.fixture
def trainer(monkeypatch, tmp_path):
    """Stands in for the text trainer process (tt._run_text_trainer) and the
    vision script: records the rows each call trained on and was asked to
    measure, and reports the losses a test sets (or `report`) as the
    trainer's eval event, as the real trainer does."""
    state = SimpleNamespace(calls=[], losses={"adapter_loss": 1.2, "base_loss": 1.5}, report=None)

    def record(call, data_path, eval_path, model_dir):
        call["train_rows"] = Path(data_path).read_text().splitlines()
        call["eval_rows"] = Path(eval_path).read_text().splitlines() if eval_path else []
        state.calls.append(call)
        (Path(model_dir) / "lora").mkdir(parents=True, exist_ok=True)

    def run_text_trainer(spec, *, workdir, on_event, on_start, should_stop):
        call = dict(spec)
        record(call, spec["data_path"], spec["eval_data_path"], spec["output_dir"])
        if call["eval_rows"]:
            on_event({"event": "eval", **(state.report or {**state.losses, "rows": len(call["eval_rows"])})})
        return spec["output_dir"]

    def vision_finetune(**kwargs):
        model_dir = tmp_path / "models" / kwargs["output_name"]
        record(dict(kwargs), kwargs["data_path"], None, model_dir)
        return str(model_dir)

    monkeypatch.setattr(tt, "_run_text_trainer", run_text_trainer)
    module = types.ModuleType("finetune_vision")
    module.finetune = vision_finetune
    monkeypatch.setitem(sys.modules, "finetune_vision", module)
    return state


def _dataset(path, rows):
    lines = [json.dumps({"instruction": f"Question {i} about held-out splits?",
                         "output": f"Answer {i}, long enough to learn from."}) for i in range(rows)]
    path.write_text("\n".join(lines) + "\n")
    return str(path), lines


def _job(data_path, job_id="train-job", **config):
    job = TrainingJob(job_id=job_id, name="train", base_model="org/base-model",
                      output_model_name="test-out", status="pending",
                      config_json=json.dumps({"data_path": data_path, "steps": 5, **config}))
    db.session.add(job)
    db.session.commit()
    return job_id


def _row(job_id):
    db.session.expire_all()
    return db.session.query(TrainingJob).filter_by(job_id=job_id).one()


def _saved_config(job_id):
    def no_nan(token):
        raise AssertionError(f"config_json carries {token}, which the browser cannot parse")

    return json.loads(_row(job_id).config_json, parse_constant=no_nan)


def test_the_held_out_rows_are_left_out_of_training(app, progress, trainer, tmp_path):
    data, lines = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    tt.finetune_model_task(job_id, {})

    call = trainer.calls[0]
    assert call["data_path"] != data
    assert len(call["eval_rows"]) == 12
    assert len(call["train_rows"]) == 108
    assert not set(call["eval_rows"]) & set(call["train_rows"])
    assert sorted(call["eval_rows"] + call["train_rows"]) == sorted(lines)


def test_both_losses_are_stored_on_the_job(app, progress, trainer, tmp_path):
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    result = tt.finetune_model_task(job_id, {})

    saved = _saved_config(job_id)["eval"]
    assert saved["measured"] is True
    assert saved["adapter_loss"] == 1.2
    assert saved["base_loss"] == 1.5
    assert saved["eval_rows"] == 12 and saved["rows"] == 12
    assert result["eval"]["adapter_loss"] == 1.2
    pct, message, status = progress[-1]
    assert status == "complete"
    assert "1.2000" in message and "1.5000" in message
    assert _row(job_id).status == "completed"


def test_a_resumed_run_holds_out_the_same_rows(app, progress, trainer, tmp_path):
    data, _ = _dataset(tmp_path / "data.jsonl", 150)
    job_id = _job(data)

    tt.finetune_model_task(job_id, {})
    tt.finetune_model_task(job_id, {}, resume=True)

    first, resumed = trainer.calls
    assert first["eval_rows"] == resumed["eval_rows"]
    assert resumed["resume"] is True


def test_a_dataset_too_small_to_spare_rows_trains_on_everything_and_says_so(app, progress, trainer, tmp_path):
    data, lines = _dataset(tmp_path / "data.jsonl", 30)
    job_id = _job(data)

    tt.finetune_model_task(job_id, {})

    call = trainer.calls[0]
    assert call["data_path"] == data
    assert call["eval_data_path"] is None
    assert call["train_rows"] == lines
    saved = _saved_config(job_id)["eval"]
    assert saved["measured"] is False
    assert "too small" in saved["reason"]
    assert "Held-out loss not measured" in progress[-1][1]
    assert _row(job_id).status == "completed"


def test_the_large_dataset_share_is_capped(app, progress, trainer, tmp_path):
    data, _ = _dataset(tmp_path / "data.jsonl", 5000)
    job_id = _job(data)

    tt.finetune_model_task(job_id, {})

    assert len(trainer.calls[0]["eval_rows"]) == tt.EVAL_SPLIT["max_rows"]


def test_eval_fraction_zero_in_the_job_config_turns_the_split_off(app, progress, trainer, tmp_path):
    data, _ = _dataset(tmp_path / "data.jsonl", 200)
    job_id = _job(data, eval_fraction=0)

    tt.finetune_model_task(job_id, {})

    assert trainer.calls[0]["eval_data_path"] is None
    assert "turned off" in _saved_config(job_id)["eval"]["reason"]


def test_a_loss_that_is_not_a_number_is_stored_as_json_the_browser_can_read(app, progress, trainer, tmp_path):
    trainer.losses = {"adapter_loss": math.nan, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    tt.finetune_model_task(job_id, {})

    saved = _saved_config(job_id)["eval"]
    assert saved["adapter_loss"] is None
    assert saved["adapter_loss_not_finite"] is True
    assert saved["base_loss"] == 1.5


def test_the_trainer_reporting_no_measurement_is_recorded_with_its_reason(app, progress, trainer, tmp_path):
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)
    trainer.report = {"reason": "held-out evaluation failed: RuntimeError: out of memory"}

    tt.finetune_model_task(job_id, {})

    saved = _saved_config(job_id)["eval"]
    assert saved["measured"] is False
    assert "out of memory" in saved["reason"]
    assert _row(job_id).status == "completed"


def test_a_vision_run_says_its_loss_was_not_measured(app, progress, trainer, tmp_path):
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data, images_path=str(tmp_path / "images"))

    tt.finetune_model_task(job_id, {})

    assert "eval_data_path" not in trainer.calls[0]
    assert "image_folder" in trainer.calls[0]
    saved = _saved_config(job_id)["eval"]
    assert saved["measured"] is False
    assert "vision" in saved["reason"]


# ---- export gate -------------------------------------------------------------


@pytest.fixture
def export_steps(monkeypatch):
    """Spies on the two steps the gate must hold back, and on the ollama CLI
    behind the second one, plus the Ollama unload the pipeline does first."""
    calls = SimpleNamespace(export=[], register=[])

    def export_gguf(job_id, model_dir, quantization, in_pipeline=False):
        calls.export.append(model_dir)
        return {"gguf_path": f"{model_dir}/model.gguf"}

    def import_ollama(job_id, model_dir, model_name, in_pipeline=False):
        calls.register.append(model_name)
        return {"ollama_model_name": model_name}

    def no_cli(*args, **kwargs):
        raise AssertionError(f"ollama CLI called: {args}")

    monkeypatch.setattr(tt, "export_gguf_task", export_gguf)
    monkeypatch.setattr(tt, "import_ollama_task", import_ollama)
    monkeypatch.setattr(tt.subprocess, "run", no_cli)
    monkeypatch.setattr("backend.services.gpu_resource_coordinator.get_available_vram", lambda: {})
    monkeypatch.setattr("backend.services.gpu_resource_coordinator.unload_ollama_models",
                        lambda *a, **k: {"success": True, "models_unloaded": []})
    return calls


def test_a_run_worse_than_base_is_held_with_both_losses_and_its_adapter_kept(app, progress, trainer, tmp_path):
    trainer.losses = {"adapter_loss": 3.0, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    result = tt.finetune_model_task(job_id, {})

    row = _row(job_id)
    assert row.status == "failed: worse than base"
    assert "3.0000" in row.error_message and "1.5000" in row.error_message
    assert Path(row.lora_path).is_dir()
    gate = _saved_config(job_id)["eval"]["gate"]
    assert gate["export_allowed"] is False
    assert gate["margin"] == tt.EVAL_GATE["margin"]
    assert result["export_allowed"] is False
    assert progress[-1][2] == "error"


def test_the_pipeline_skips_export_and_ollama_create_for_a_run_worse_than_base(
        app, progress, trainer, export_steps, tmp_path):
    trainer.losses = {"adapter_loss": 3.0, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    result = tt.full_training_pipeline_task(job_id, {})

    assert export_steps.export == []
    assert export_steps.register == []
    assert result["gguf_path"] is None and result["ollama_model_name"] is None
    row = _row(job_id)
    assert row.status == "failed: worse than base"
    assert row.gguf_path is None and row.ollama_model_name is None
    assert Path(row.lora_path).is_dir()


def test_a_diverged_run_whose_loss_is_not_a_number_is_held(app, progress, trainer, export_steps, tmp_path):
    trainer.losses = {"adapter_loss": math.inf, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    tt.full_training_pipeline_task(job_id, {})

    assert export_steps.export == [] and export_steps.register == []
    row = _row(job_id)
    assert row.status == "failed: worse than base"
    assert "not a finite number" in row.error_message


def test_a_run_within_the_margin_is_exported(app, progress, trainer, export_steps, tmp_path):
    trainer.losses = {"adapter_loss": 1.55, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data)

    tt.full_training_pipeline_task(job_id, {})

    assert len(export_steps.export) == 1 and len(export_steps.register) == 1
    assert _row(job_id).status == "completed"
    assert _saved_config(job_id)["eval"]["gate"]["export_allowed"] is True


def test_export_if_worse_set_by_the_person_still_exports(app, progress, trainer, export_steps, tmp_path):
    trainer.losses = {"adapter_loss": 3.0, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data, export_if_worse=True)

    tt.full_training_pipeline_task(job_id, {})

    assert len(export_steps.export) == 1 and len(export_steps.register) == 1
    assert _row(job_id).status == "completed"
    assert "overridden" in _saved_config(job_id)["eval"]["gate"]["note"]


def test_a_margin_set_in_the_job_config_wins(app, progress, trainer, export_steps, tmp_path):
    trainer.losses = {"adapter_loss": 1.7, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 120)
    job_id = _job(data, eval_gate_margin=0.2)

    tt.full_training_pipeline_task(job_id, {})

    assert len(export_steps.register) == 1
    assert _saved_config(job_id)["eval"]["gate"]["margin"] == 0.2


def test_a_dataset_too_small_to_measure_skips_the_gate_and_says_so(app, progress, trainer, export_steps, tmp_path):
    trainer.losses = {"adapter_loss": 3.0, "base_loss": 1.5}
    data, _ = _dataset(tmp_path / "data.jsonl", 30)
    job_id = _job(data)

    tt.full_training_pipeline_task(job_id, {})

    assert len(export_steps.register) == 1
    assert _row(job_id).status == "completed"
    saved = _saved_config(job_id)["eval"]
    assert "too small" in saved["reason"]
    assert saved["gate"]["note"].startswith("Export gate skipped")
