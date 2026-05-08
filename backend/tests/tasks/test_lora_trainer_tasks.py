import json
import pytest
from pathlib import Path

from flask import Flask
from backend.models import db, Subject
from backend.tasks.lora_trainer_tasks import train_subject_lora_for_subject

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

def test_mock_trainer_writes_safetensors_and_sidecar(tmp_path):
    from plugins.lora_trainer.mock_trainer import train_subject_lora
    out_dir = tmp_path / "loras"
    res = train_subject_lora(
        subject_id=42,
        subject_name="Test Subject",
        ref_image_paths=["/tmp/a.jpg", "/tmp/b.jpg"],
        output_dir=str(out_dir),
        sleep_s=0,
    )
    assert res["status"] == "ok"
    assert "lora_path" in res
    
    lora_path = Path(res["lora_path"])
    assert lora_path.exists()
    assert lora_path.read_bytes().startswith(b"\x10\x00\x00\x00\x00\x00\x00\x00")
    
    sidecar_path = lora_path.with_suffix(".json")
    assert sidecar_path.exists()
    sidecar = json.loads(sidecar_path.read_text())
    assert sidecar["subject_id"] == 42
    assert sidecar["subject_name"] == "Test Subject"
    assert sidecar["ref_count"] == 2
    assert sidecar["mock"] is True


def test_mock_trainer_fails_with_no_refs(tmp_path):
    from plugins.lora_trainer.mock_trainer import train_subject_lora
    res = train_subject_lora(
        subject_id=42,
        subject_name="Test",
        ref_image_paths=[],
        output_dir=str(tmp_path),
        sleep_s=0,
    )
    assert res["status"] == "failed"
    assert "no reference images" in res["error"]


def test_train_lora_for_subject_updates_subject_row(app, tmp_path, monkeypatch):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training", ref_image_paths=["/tmp/1.jpg"])
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    # Pin mock — without this, auto-detect picks real once venv-torch exists
    # on disk, which would actually try to load SDXL on a shared GPU and fail.
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "mock")

    with app.app_context():
        train_subject_lora_for_subject(s_id)

    with app.app_context():
        s = db.session.get(Subject, s_id)
        assert s.training_status == "trained"
        assert s.lora_path is not None
        assert s.lora_version == 1
        assert Path(s.lora_path).exists()


def test_train_lora_skips_when_status_not_training(app, tmp_path, monkeypatch):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="trained", lora_path="/already/done.safetensors")
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))

    with app.app_context():
        train_subject_lora_for_subject(s_id)

    with app.app_context():
        s = db.session.get(Subject, s_id)
        assert s.training_status == "trained"
        assert s.lora_path == "/already/done.safetensors"
        # No files should be written
        assert not list(tmp_path.iterdir())


def test_train_lora_marks_failed_on_trainer_failure(app, monkeypatch):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training", ref_image_paths=["/tmp/1.jpg"])
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    def fake_train_impl(subject_id):
        return {"status": "failed", "error": "mock failure"}
    
    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._train_impl", fake_train_impl)

    with app.app_context():
        train_subject_lora_for_subject(s_id)

    with app.app_context():
        s = db.session.get(Subject, s_id)
        assert s.training_status == "failed"
        assert s.lora_path is None

def test_backend_selector_uses_mock_when_env_set_to_mock(app, tmp_path, monkeypatch):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training", ref_image_paths=["/tmp/1.jpg"])
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "mock")
    
    # Even if real is available, it should use mock
    monkeypatch.setattr("plugins.lora_trainer.real_trainer.RealLoraTrainer.is_available", lambda: True)
    
    called_mock = False
    def fake_mock(*args, **kwargs):
        nonlocal called_mock
        called_mock = True
        return {"status": "ok", "lora_path": "/tmp/mock.safetensors", "lora_version": 1}
        
    monkeypatch.setattr("plugins.lora_trainer.mock_trainer.train_subject_lora", fake_mock)

    with app.app_context():
        train_subject_lora_for_subject(s_id)
        
    assert called_mock

def test_backend_selector_uses_mock_when_real_unavailable_in_auto(app, tmp_path, monkeypatch):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training", ref_image_paths=["/tmp/1.jpg"])
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "auto")
    
    monkeypatch.setattr("plugins.lora_trainer.real_trainer.RealLoraTrainer.is_available", lambda: False)
    
    called_mock = False
    def fake_mock(*args, **kwargs):
        nonlocal called_mock
        called_mock = True
        return {"status": "ok", "lora_path": "/tmp/mock.safetensors", "lora_version": 1}
        
    monkeypatch.setattr("plugins.lora_trainer.mock_trainer.train_subject_lora", fake_mock)

    with app.app_context():
        train_subject_lora_for_subject(s_id)
        
    assert called_mock

def test_backend_selector_uses_real_when_real_available_in_auto(app, tmp_path, monkeypatch):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training", ref_image_paths=["/tmp/1.jpg"])
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "auto")
    
    monkeypatch.setattr("plugins.lora_trainer.real_trainer.RealLoraTrainer.is_available", lambda: True)
    
    called_real = False
    def fake_real(*args, **kwargs):
        nonlocal called_real
        called_real = True
        return {"status": "ok", "lora_path": "/tmp/real.safetensors", "lora_version": 1}
        
    monkeypatch.setattr("plugins.lora_trainer.real_trainer.RealLoraTrainer.train_subject_lora", fake_real)

    with app.app_context():
        train_subject_lora_for_subject(s_id)
        
    assert called_real
