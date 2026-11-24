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


@pytest.fixture(autouse=True)
def _no_live_gpu(monkeypatch):
    """Keep the real-trainer path off this machine's GPU and services.

    Under it, train_subject_lora_for_subject starts the Cast plugins, claims
    the GPU through gpu_session (a real VRAM check, Ollama eviction, a free on
    the running ComfyUI, a cross-process lease) and shuts the trainer daemon
    down. A test that reaches it then passes or fails on whatever else holds
    the card, and evicts models the person is using.
    """
    from contextlib import contextmanager

    from plugins.lora_trainer import real_trainer

    @contextmanager
    def no_gpu_session(*args, **kwargs):
        yield

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", no_gpu_session)
    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session_when_free", no_gpu_session)
    monkeypatch.setattr(
        "backend.services.gpu_resource_policy.compositor_vram_reserve_mb", lambda: 0
    )
    monkeypatch.setattr(
        "backend.services.plugin_bridge.ensure_plugins_for_stage", lambda *a, **k: None
    )
    monkeypatch.setattr(real_trainer._TRAINER, "shutdown", lambda: None)


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


def _seed_trainable_subject(app, tmp_path, name="Hero"):
    """A subject that passes the real pre-train gate: four actual image files,
    each with a rich, class-anchored caption sidecar so the captioner has
    nothing to send to the VLM. The gate exists so no LoRA is trained on a
    phantom dataset; the tests used to hand it one path that did not exist."""
    from PIL import Image
    refs = []
    for i in range(4):
        img = tmp_path / f"ref_{i}.png"
        Image.new("RGB", (64, 64), (i * 40, 80, 120)).save(img)
        img.with_suffix(".txt").write_text(
            f"a photo of {name}, man with a red beard and round glasses, "
            f"full body standing in a park, looking at the camera, outfit {i}"
        )
        refs.append(str(img))
    with app.app_context():
        s = Subject(name=name, kind="character", training_status="training", ref_image_paths=refs)
        db.session.add(s)
        db.session.commit()
        return s.id


def test_train_lora_for_subject_updates_subject_row(app, tmp_path, monkeypatch):
    s_id = _seed_trainable_subject(app, tmp_path)

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

    def fake_train_impl(subject_id, job_id=None):
        return {"status": "failed", "error": "mock failure"}
    
    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._train_impl", fake_train_impl)

    with app.app_context():
        train_subject_lora_for_subject(s_id)

    with app.app_context():
        s = db.session.get(Subject, s_id)
        assert s.training_status == "failed"
        assert s.lora_path is None

def test_backend_selector_uses_mock_when_env_set_to_mock(app, tmp_path, monkeypatch):
    s_id = _seed_trainable_subject(app, tmp_path)

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

def test_backend_selector_fails_loud_when_real_unavailable_in_auto(app, tmp_path, monkeypatch):
    """NO-MOCKS policy: in 'auto', if the real trainer probe says unavailable, we
    FAIL LOUD — we must NOT silently substitute a fake 27-byte mock LoRA (the bug
    that polluted subject 16). The Subject is marked 'failed', no LoRA is written."""
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training", ref_image_paths=["/tmp/1.jpg"])
        db.session.add(s)
        db.session.commit()
        s_id = s.id

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "auto")

    monkeypatch.setattr("plugins.lora_trainer.real_trainer.RealLoraTrainer.is_available", lambda: False)

    # The mock must NOT be invoked even though real is "unavailable".
    called_mock = False
    def fake_mock(*args, **kwargs):
        nonlocal called_mock
        called_mock = True
        return {"status": "ok", "lora_path": "/tmp/mock.safetensors", "lora_version": 1}
    monkeypatch.setattr("plugins.lora_trainer.mock_trainer.train_subject_lora", fake_mock)

    with app.app_context():
        train_subject_lora_for_subject(s_id)

    assert called_mock is False, "mock must never run as a production auto fallback"
    with app.app_context():
        s = db.session.get(Subject, s_id)
        assert s.training_status == "failed"
        assert s.lora_path is None
    # No fake LoRA file written.
    assert not list(tmp_path.iterdir())

def test_backend_selector_uses_real_when_real_available_in_auto(app, tmp_path, monkeypatch):
    s_id = _seed_trainable_subject(app, tmp_path)

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


@pytest.mark.parametrize("base_id", ["zimage-turbo", "sdxl-legacy"])
def test_gpu_claim_uses_the_bases_measured_training_vram(app, tmp_path, monkeypatch, base_id):
    """The claim asks for the training base's own vram_train_mb from the media
    registry. A single figure for every base refused every 12 GB card, since
    the fit check adds a 1 GB margin and the compositor reserve on top."""
    from contextlib import contextmanager
    from backend.services.media_model_registry import get_profile

    s_id = _seed_trainable_subject(app, tmp_path)
    with app.app_context():
        s = db.session.get(Subject, s_id)
        s.training_settings_json = {"base_model_id": base_id}
        db.session.commit()

    seen = {}

    @contextmanager
    def capture_gpu_session(*args, **kwargs):
        seen.update(kwargs)
        yield

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session_when_free", capture_gpu_session)
    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "real")
    monkeypatch.setattr(
        "plugins.lora_trainer.real_trainer.RealLoraTrainer.train_subject_lora",
        lambda *a, **k: {"status": "ok", "lora_path": str(tmp_path / "x.safetensors"), "lora_version": 1},
    )

    with app.app_context():
        train_subject_lora_for_subject(s_id)

    assert seen["require_fit"] is True
    from backend.services.lora_training_settings import normalize_training_settings
    from backend.services.media_model_registry import train_vram_mb
    storage = normalize_training_settings({"base_model_id": base_id})["base_storage"]
    assert seen["vram_estimate_mb"] == train_vram_mb(get_profile(base_id), storage)
    # A run queued behind another waits out the release cooldown instead of failing.
    from backend.tasks.lora_trainer_tasks import CAST_TRAIN_GPU_WAIT_S
    assert seen["wait_s"] == CAST_TRAIN_GPU_WAIT_S


def test_training_vram_follows_the_base_storage_format():
    from backend.services.media_model_registry import get_profile, train_vram_mb

    zimage = get_profile("zimage-turbo")
    assert train_vram_mb(zimage, "bf16") == zimage["vram_train_mb"]
    assert train_vram_mb(zimage, "float8") == zimage["vram_train_mb_by_storage"]["float8"]
    assert train_vram_mb(zimage, "float8") < train_vram_mb(zimage, "bf16")
    sdxl = get_profile("sdxl-legacy")
    assert train_vram_mb(sdxl, "float8") == sdxl["vram_train_mb"]


# --- 'trained' waits for the post-train smoke render -------------------------
#
# 'trained' is the signal the Cast page, the job handoff ("cast_lora" reads it
# as completed) and the night shift act on, so it must not be committed while
# the smoke render still holds the card.


class _Progress:
    """Records the unified progress calls the task makes, with the member's
    committed status at the moment each one was made."""

    def __init__(self, subject_id):
        self.subject_id = subject_id
        self.calls = []

    def _status(self):
        from sqlalchemy import text
        row = db.session.execute(
            text("SELECT training_status FROM subjects WHERE id = :id"), {"id": self.subject_id}
        ).first()
        return row[0] if row else None

    def update_process(self, job_id, pct, msg, *a, **k):
        self.calls.append(("update", pct, msg, self._status()))

    def complete_process(self, job_id, msg, additional_data=None, **k):
        self.calls.append(("complete", msg, additional_data, self._status()))

    def error_process(self, job_id, msg, *a, **k):
        self.calls.append(("error", msg, self._status()))


@pytest.fixture
def real_train_result(app, tmp_path, monkeypatch):
    """A member in 'training' whose (stubbed) trainer returns a verified-real
    LoRA, so the task goes on to the smoke render. Returns (subject_id, progress)."""
    with app.app_context():
        # dispatch_lora_train commits the run's job id before the task starts.
        s = Subject(name="Hero", kind="character", training_status="training",
                    current_training_job_id="job-1", ref_image_paths=["/tmp/ref1.jpg"])
        db.session.add(s)
        db.session.commit()
        sid = s.id

    lora_path = tmp_path / "hero.safetensors"
    lora_path.write_bytes(b"x" * 200)
    lora_path.with_suffix(".json").write_text(json.dumps({"mock": False, "subject_id": sid}))
    monkeypatch.setattr(
        "backend.tasks.lora_trainer_tasks._train_impl",
        lambda subject_id, job_id=None: {
            "status": "ok", "lora_path": str(lora_path), "lora_version": 1,
            "used_images": ["/tmp/ref1.jpg"],
        },
    )
    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ensure_lora_in_comfyui", lambda p: None
    )
    progress = _Progress(sid)
    monkeypatch.setattr(
        "backend.utils.unified_progress_system.get_unified_progress", lambda: progress
    )
    return sid, progress


def _committed(sid):
    from sqlalchemy import text
    return db.session.execute(
        text("SELECT training_status, lora_path FROM subjects WHERE id = :id"), {"id": sid}
    ).first()


def _stub_smoke(monkeypatch, fn):
    monkeypatch.setattr("backend.services.lora_posttrain_smoke.run_lora_smoke_test", fn)


def test_status_stays_training_while_the_smoke_render_runs(app, monkeypatch, real_train_result):
    sid, progress = real_train_result
    seen = {}

    def smoke(**kw):
        seen["during"] = tuple(_committed(sid))
        return {"ok": True, "path": "/x/smoke.png"}

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        status, lora = _committed(sid)

    # The LoRA is already saved when the check starts, but the member is not
    # yet 'trained'; it becomes so only after the render returned.
    assert seen["during"][0] == "training"
    assert seen["during"][1] and seen["during"][1].endswith("hero.safetensors")
    assert status == "trained"
    kinds = [c[0] for c in progress.calls]
    assert kinds[-1] == "complete"
    # The checking step is shown while still 'training'; completion follows 'trained'.
    checking = [c for c in progress.calls if c[0] == "update" and "test render" in c[2]]
    assert checking and checking[0][3] == "training"
    assert progress.calls[-1][3] == "trained"
    assert progress.calls[-1][2]["smoke_test"]["ok"] is True


def test_smoke_render_failure_still_ends_trained(app, monkeypatch, real_train_result):
    sid, progress = real_train_result
    _stub_smoke(monkeypatch, lambda **kw: {"ok": False, "error": "GPU busy: render in progress"})
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        assert _committed(sid)[0] == "trained"
    assert progress.calls[-1][0] == "complete"
    assert "smoke render skipped" in progress.calls[-1][1]


@pytest.mark.parametrize("exc", [
    RuntimeError("render pipeline crashed"),
    TimeoutError("smoke render timed out"),
])
def test_smoke_render_raising_still_ends_trained(app, monkeypatch, real_train_result, exc):
    sid, progress = real_train_result

    def smoke(**kw):
        raise exc

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        status, lora = _committed(sid)
    assert status == "trained"
    assert lora.endswith("hero.safetensors")
    final = progress.calls[-1]
    assert final[0] == "complete" and final[3] == "trained"
    assert final[2]["smoke_test"]["ok"] is False
    assert type(exc).__name__ in final[2]["smoke_test"]["error"]


def test_soft_time_limit_during_smoke_ends_trained(app, monkeypatch, real_train_result):
    from celery.exceptions import SoftTimeLimitExceeded
    sid, progress = real_train_result

    def smoke(**kw):
        raise SoftTimeLimitExceeded()

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        assert _committed(sid)[0] == "trained"
    assert progress.calls[-1][0] == "complete"


def test_smoke_render_leaving_a_failed_session_still_ends_trained(app, monkeypatch, real_train_result):
    """The smoke render commits on the task's session; a commit that fails there
    must not stop the member from leaving 'training'."""
    sid, progress = real_train_result

    def smoke(**kw):
        db.session.add(Subject(name=None, kind="character"))
        try:
            db.session.commit()
        except Exception:
            pass  # as _run_smoke_once does: logged, not rolled back
        return {"ok": False, "error": "could not persist"}

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        assert _committed(sid)[0] == "trained"
    assert progress.calls[-1][0] == "complete"


def test_cancel_during_smoke_is_not_overwritten(app, monkeypatch, real_train_result):
    sid, progress = real_train_result

    def smoke(**kw):
        from backend.services.lora_train_dispatch import cancel_lora_train
        assert cancel_lora_train(sid)["cancelled"] is True
        return {"ok": True}

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        status, _ = _committed(sid)
        s = db.session.get(Subject, sid)
        assert status == "failed"
        assert s.training_error == "Cancelled by user"
    assert progress.calls[-1][0] == "error"
    assert "superseded" in progress.calls[-1][1]
    assert not any(c[0] == "complete" for c in progress.calls)


def test_delete_during_smoke_ends_quietly(app, monkeypatch, real_train_result):
    sid, progress = real_train_result

    def smoke(**kw):
        db.session.delete(db.session.get(Subject, sid))
        db.session.commit()
        return {"ok": True}

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        assert _committed(sid) is None
    assert progress.calls[-1][0] == "error"
    assert "deleted" in progress.calls[-1][1]


def test_unverified_train_skips_smoke_and_ends_trained(app, tmp_path, monkeypatch):
    """No smoke render runs for an unverified adapter; the member goes straight
    to 'trained' as before."""
    s_id = _seed_trainable_subject(app, tmp_path)
    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._output_dir", lambda: str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_LORA_BACKEND", "mock")
    called = []
    _stub_smoke(monkeypatch, lambda **kw: called.append(kw) or {"ok": True})
    with app.app_context():
        train_subject_lora_for_subject(s_id)
        assert _committed(s_id)[0] == "trained"
    assert called == []


def test_handoff_keeps_following_while_training():
    from backend.services.job_handoff import _read_cast_lora
    assert _read_cast_lora({"training_status": "training"})["ended"] is None
    assert _read_cast_lora({"training_status": "trained"})["ended"] == "completed"


def test_real_smoke_refused_by_the_gpu_gate_ends_trained(app, monkeypatch, real_train_result):
    """The real smoke helper, with its GPU claim refused the way the gate
    refuses it: the member still ends 'trained' and the job completes."""
    from contextlib import contextmanager
    from backend.services.job_operation_gate import GpuBusyError
    sid, progress = real_train_result

    @contextmanager
    def busy_gpu_session(*a, **k):
        raise GpuBusyError("GPU busy: another render holds the card")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", busy_gpu_session)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        assert _committed(sid)[0] == "trained"
    final = progress.calls[-1]
    assert final[0] == "complete" and final[3] == "trained"
    assert final[2]["smoke_test"]["ok"] is False
    assert "busy" in final[2]["smoke_test"]["error"].lower()


def _cancel_and_train_again(sid, new_job_id="job-2"):
    """What Cancel, then Train, commit from the web process: the old run is
    cancelled and a new run owns 'training' under its own job id."""
    from backend.services.lora_train_dispatch import cancel_lora_train
    assert cancel_lora_train(sid)["cancelled"] is True
    s = db.session.get(Subject, sid)
    s.training_status = "training"
    s.current_training_job_id = new_job_id
    db.session.commit()


def test_cancel_then_retrain_during_smoke_leaves_the_new_run_alone(app, monkeypatch, real_train_result):
    sid, progress = real_train_result

    def smoke(**kw):
        _cancel_and_train_again(sid)
        return {"ok": True}

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        status, _ = _committed(sid)
        assert status == "training"
        assert db.session.get(Subject, sid).current_training_job_id == "job-2"
    assert progress.calls[-1][0] == "error"
    assert "superseded" in progress.calls[-1][1]
    assert not any(c[0] == "complete" for c in progress.calls)

    # The new run's task then starts and is not skipped.
    progress.calls.clear()
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-2")
        assert _committed(sid)[0] == "trained"
    assert not any(c[0] == "error" and "skipped" in c[1] for c in progress.calls)
    assert progress.calls[-1][0] == "complete"


def test_cancel_then_retrain_during_training_is_not_recorded(app, monkeypatch, real_train_result):
    """The same race before the smoke render: the old run's result is dropped
    and the new run keeps its 'training' and its own LoRA path."""
    sid, progress = real_train_result
    from backend.tasks import lora_trainer_tasks
    finish = lora_trainer_tasks._train_impl

    def train_then_race(subject_id, job_id=None):
        result = finish(subject_id, job_id=job_id)
        _cancel_and_train_again(subject_id)
        return result

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._train_impl", train_then_race)
    smoke_calls = []
    _stub_smoke(monkeypatch, lambda **kw: smoke_calls.append(kw) or {"ok": True})
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        status, lora = _committed(sid)
    assert status == "training"
    assert lora is None
    assert smoke_calls == []
    assert progress.calls[-1][0] == "error"


# --- the reaper keeps a LoRA its run already saved ----------------------------
#
# A worker that dies during the smoke render (or a failed write of 'trained')
# leaves the member 'training' with its new LoRA saved as the default. The
# reaper marks such a run 'trained' instead of 'failed'. Its train-cap query is
# Postgres SQL, so these tests stand in for it.


class _WorkerDied(BaseException):
    """A kill: nothing after it in the task runs, finally blocks included."""


def _write_valid_lora(path):
    import struct
    header = json.dumps({"w": {"dtype": "F32", "shape": [32], "data_offsets": [0, 128]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 128)


def _die_during_smoke(app, monkeypatch, sid):
    """Run the task until the smoke render, then kill it there."""
    def smoke(**kw):
        raise _WorkerDied()

    def killed(*a, **k):
        raise _WorkerDied()

    _stub_smoke(monkeypatch, smoke)
    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._promote_to_trained", killed)
    with app.app_context():
        with pytest.raises(_WorkerDied):
            train_subject_lora_for_subject(sid, job_id="job-1")
        db.session.rollback()


def _reap(monkeypatch, after_s, past_cap=False):
    """Run the reaper ``after_s`` seconds from now; ``past_cap`` stands in for
    the train-cap query matching every member still in 'training'."""
    from datetime import timedelta
    from backend.tasks import lora_trainer_tasks as t
    from backend.utils.clock import utcnow
    later = utcnow() + timedelta(seconds=after_s)
    monkeypatch.setattr(t, "utcnow", lambda: later)
    monkeypatch.setattr(
        t, "_past_train_cap",
        lambda: Subject.query.filter(Subject.training_status == "training").all() if past_cap else [],
    )
    db.session.expire_all()
    return t.reap_stuck_training_subjects()


@pytest.fixture
def saved_lora(real_train_result, tmp_path):
    _write_valid_lora(tmp_path / "hero.safetensors")
    return real_train_result


def _member(sid):
    db.session.expire_all()
    return db.session.get(Subject, sid)


def test_worker_died_after_save_is_marked_trained(app, monkeypatch, saved_lora):
    from backend.tasks.lora_trainer_tasks import (
        LORA_SAVED_FOR_JOB_KEY, LORA_SAVED_NOTE, LORA_SMOKE_REAP_AFTER_S,
    )
    sid, _ = saved_lora
    _die_during_smoke(app, monkeypatch, sid)
    with app.app_context():
        s = _member(sid)
        assert s.training_status == "training"
        assert s.training_settings_json[LORA_SAVED_FOR_JOB_KEY]["job_id"] == "job-1"

        # Still inside the render's window: left alone.
        assert _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S - 60)["reaped"] == 0
        assert _member(sid).training_status == "training"

        out = _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S + 60)
        assert out == {"reaped": 1, "failed": 0, "trained": 1}
        s = _member(sid)
        assert s.training_status == "trained"
        assert s.training_error == LORA_SAVED_NOTE
        assert s.lora_path.endswith("hero.safetensors")
        assert LORA_SAVED_FOR_JOB_KEY not in (s.training_settings_json or {})


def test_worker_died_after_save_past_the_train_cap_is_marked_trained(app, monkeypatch, saved_lora):
    sid, _ = saved_lora
    _die_during_smoke(app, monkeypatch, sid)
    with app.app_context():
        # The marker's time is unreadable, so only the train-cap rule sees it.
        s = _member(sid)
        from backend.tasks.lora_trainer_tasks import LORA_SAVED_FOR_JOB_KEY
        cfg = dict(s.training_settings_json)
        cfg[LORA_SAVED_FOR_JOB_KEY] = dict(cfg[LORA_SAVED_FOR_JOB_KEY], saved_at="?")
        s.training_settings_json = cfg
        db.session.commit()
        assert _reap(monkeypatch, 0, past_cap=True) == {"reaped": 1, "failed": 0, "trained": 1}
        assert _member(sid).training_status == "trained"


def test_failed_write_of_trained_is_recovered_by_the_reaper(app, monkeypatch, saved_lora):
    from sqlalchemy.exc import OperationalError
    from backend.tasks.lora_trainer_tasks import LORA_SMOKE_REAP_AFTER_S
    sid, progress = saved_lora
    _stub_smoke(monkeypatch, lambda **kw: {"ok": True})

    def db_gone(*a, **k):
        raise OperationalError("UPDATE subjects", {}, Exception("server closed the connection"))

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._promote_to_trained", db_gone)
    with app.app_context():
        with pytest.raises(OperationalError):
            train_subject_lora_for_subject(sid, job_id="job-1")
        db.session.rollback()
        assert _member(sid).training_status == "training"
        assert _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S + 60)["trained"] == 1
        assert _member(sid).training_status == "trained"
    assert not any(c[0] == "complete" for c in progress.calls)


def test_worker_died_before_save_fails_as_before(app, monkeypatch, saved_lora):
    from backend.tasks.lora_trainer_tasks import LORA_SMOKE_REAP_AFTER_S
    sid, _ = saved_lora

    def killed(subject_id, job_id=None):
        raise _WorkerDied()

    monkeypatch.setattr("backend.tasks.lora_trainer_tasks._train_impl", killed)
    with app.app_context():
        with pytest.raises(_WorkerDied):
            train_subject_lora_for_subject(sid, job_id="job-1")
        db.session.rollback()
        assert _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S + 60)["reaped"] == 0
        assert _member(sid).training_status == "training"

        assert _reap(monkeypatch, 0, past_cap=True) == {"reaped": 1, "failed": 1, "trained": 0}
        s = _member(sid)
        assert s.training_status == "failed"
        assert s.training_error == "Training did not finish (worker stopped or timed out). Safe to retry."
        assert s.lora_path is None


def test_newer_run_is_left_alone(app, monkeypatch, saved_lora):
    """The marker belongs to job-1; once Cancel and Train start job-2 the
    saved-LoRA rule does not touch the member."""
    from backend.tasks.lora_trainer_tasks import LORA_SMOKE_REAP_AFTER_S
    sid, _ = saved_lora
    _die_during_smoke(app, monkeypatch, sid)
    with app.app_context():
        _cancel_and_train_again(sid, "job-2")
        assert _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S + 60)["reaped"] == 0
        s = _member(sid)
        assert s.training_status == "training"
        assert s.current_training_job_id == "job-2"
        assert s.training_error == "Cancelled by user"


@pytest.mark.parametrize("damage", ["truncated", "missing", "default_moved"])
def test_saved_lora_without_evidence_is_not_promoted(app, monkeypatch, saved_lora, tmp_path, damage):
    from backend.tasks.lora_trainer_tasks import LORA_SMOKE_REAP_AFTER_S
    sid, _ = saved_lora
    _die_during_smoke(app, monkeypatch, sid)
    lora = tmp_path / "hero.safetensors"
    with app.app_context():
        if damage == "truncated":
            lora.write_bytes(lora.read_bytes()[:-40])
        elif damage == "missing":
            lora.unlink()
        else:
            s = _member(sid)
            s.lora_path = str(tmp_path / "other.safetensors")
            db.session.commit()
        assert _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S + 60)["reaped"] == 0
        assert _member(sid).training_status == "training"
        assert _reap(monkeypatch, 0, past_cap=True)["failed"] == 1
        assert _member(sid).training_status == "failed"


def test_render_overrunning_the_window_still_completes(app, monkeypatch, saved_lora):
    """A live render that outlasts the window: the reaper promotes the member,
    and the task then completes its job instead of calling itself superseded."""
    from backend.tasks.lora_trainer_tasks import LORA_SMOKE_REAP_AFTER_S
    sid, progress = saved_lora

    def slow_smoke(**kw):
        assert _reap(monkeypatch, LORA_SMOKE_REAP_AFTER_S + 60)["trained"] == 1
        return {"ok": True, "path": "/x/smoke.png"}

    _stub_smoke(monkeypatch, slow_smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        s = _member(sid)
        assert s.training_status == "trained"
        assert s.training_error is None
    assert progress.calls[-1][0] == "complete"


def test_normal_run_clears_its_save_marker(app, monkeypatch, saved_lora):
    from backend.tasks.lora_trainer_tasks import LORA_SAVED_FOR_JOB_KEY
    sid, _ = saved_lora
    seen = {}

    def smoke(**kw):
        seen["marker"] = (_member(sid).training_settings_json or {}).get(LORA_SAVED_FOR_JOB_KEY)
        return {"ok": True}

    _stub_smoke(monkeypatch, smoke)
    with app.app_context():
        train_subject_lora_for_subject(sid, job_id="job-1")
        s = _member(sid)
        assert s.training_status == "trained"
        assert LORA_SAVED_FOR_JOB_KEY not in (s.training_settings_json or {})
    assert seen["marker"]["job_id"] == "job-1"
    assert seen["marker"]["lora_path"].endswith("hero.safetensors")
