"""What the Cast API tells the member page about the last run and the smoke score.

- ``training_note`` carries the reaper's saved-LoRA note, stored as a code
  with the LoRA it is about, for a 'trained' member while that LoRA is the
  default. An old failure text never reads as a note.
- ``smoke_identity.current`` says whether the stored smoke score was measured
  on the member's default LoRA. A killed smoke render, an import or a version
  switch leaves a score that belongs to another file.

Runs on in-memory SQLite; nothing renders, trains or reaches a worker.
"""
import io
import os

import pytest

try:
    from flask import Flask
    from backend.models import db, Subject
    import backend.api.cast_library_api as cast_library_api
    from backend.api.cast_library_api import bp as cast_library_bp, _serialize
    from backend.tasks.lora_trainer_tasks import (
        LORA_SAVED_NOTE, NOTE_LORA_SAVED, TRAINING_NOTE_KEY, _mark_saved_run_trained,
    )
    from backend.models import SubjectLora
    from backend.tests.api.test_cast_library_import_lora import _build_safetensors, _zimage_keys
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    monkeypatch.setattr(cast_library_api, "STORAGE_DIR", str(tmp_path))
    db.init_app(app)
    app.register_blueprint(cast_library_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _member(**fields) -> Subject:
    s = Subject(kind="character", name="Anna", **fields)
    db.session.add(s)
    db.session.commit()
    return s


def _smoke(still: str, lora_path: str | None = None, score: float = 0.82) -> dict:
    rec = {"ok": True, "path": still, "score": score, "method": "embed",
           "status": "measured", "threshold": 0.75}
    if lora_path is not None:
        rec["lora_path"] = lora_path
    return {"smoke_identity": rec}


def _touch(path, mtime: float) -> str:
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))
    return str(path)


# ── training_note ────────────────────────────────────────────────────────────

def _reaped(lora_path: str, **settings) -> Subject:
    """A member the reaper promoted after its worker died during the smoke render."""
    s = _member(training_status="training", lora_path=lora_path,
                training_settings_json=dict(settings) or None)
    _mark_saved_run_trained(s)
    db.session.commit()
    return s


def test_saved_lora_note_reaches_the_page_for_a_trained_member(app):
    s = _reaped("/l/anna_v2.safetensors")
    assert s.training_error == LORA_SAVED_NOTE
    assert s.training_settings_json[TRAINING_NOTE_KEY] == {
        "code": NOTE_LORA_SAVED, "lora_path": "/l/anna_v2.safetensors"}
    assert _serialize(s)["training_note"] == "lora_saved_worker_stopped"


def test_note_does_not_depend_on_the_error_wording(app):
    s = _reaped("/l/anna_v2.safetensors")
    s.training_error = "Reworded in a later release."
    db.session.commit()
    assert _serialize(s)["training_note"] == "lora_saved_worker_stopped"


def test_switching_the_default_hides_the_note(app):
    s = _reaped("/l/anna_v2.safetensors")
    s.lora_path = "/l/anna_v1.safetensors"
    db.session.commit()
    assert _serialize(s)["training_note"] is None


@pytest.mark.parametrize("status,error", [
    ("trained", None),
    ("trained", "Cancelled by user"),
    ("trained", LORA_SAVED_NOTE),  # the text alone, with no note record
    ("failed", "CUDA not available"),
])
def test_no_note_otherwise(app, status, error):
    s = _member(training_status=status, training_error=error, lora_path="/l/anna_v1.safetensors")
    assert _serialize(s)["training_note"] is None


def test_a_new_run_clears_the_note(app):
    # Dispatch clears training_error; the stale record alone shows nothing.
    s = _reaped("/l/anna_v2.safetensors")
    s.training_error = None
    db.session.commit()
    assert _serialize(s)["training_note"] is None


def test_importing_after_a_failed_run_clears_the_old_error(app):
    s = _member(training_status="failed", training_error="Cancelled by user")
    resp = app.test_client().post(
        f"/api/cast-library/subjects/{s.id}/import-lora",
        data={
            "lora_file": (io.BytesIO(_build_safetensors(_zimage_keys())), "z.safetensors"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "anna_tok",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["subject"]
    assert body["training_status"] == "trained"
    assert body["training_error"] is None
    assert body["training_note"] is None


# ── smoke_identity.current ───────────────────────────────────────────────────

def test_score_of_the_default_lora_is_current(app, tmp_path):
    lora = _touch(tmp_path / "anna_v2.safetensors", 1000)
    still = _touch(tmp_path / "smoke.png", 2000)
    s = _member(training_status="trained", lora_path=lora,
                training_settings_json=_smoke(still, lora))
    rec = _serialize(s)["smoke_identity"]
    assert rec["current"] is True
    assert rec["score"] == 0.82


def test_score_of_an_earlier_lora_is_not_current(app, tmp_path):
    # The new LoRA saved, then its smoke render was killed: the record still
    # names v1.
    old = _touch(tmp_path / "anna_v1.safetensors", 1000)
    still = _touch(tmp_path / "smoke.png", 2000)
    new = _touch(tmp_path / "anna_v2.safetensors", 3000)
    s = _member(training_status="trained", lora_path=new,
                training_settings_json=_smoke(still, old))
    assert _serialize(s)["smoke_identity"]["current"] is False


def test_path_match_wins_over_file_times(app, tmp_path):
    # A synced box may give the LoRA a newer mtime than its smoke still.
    still = _touch(tmp_path / "smoke.png", 1000)
    lora = _touch(tmp_path / "anna_v1.safetensors", 2000)
    s = _member(training_status="trained", lora_path=lora,
                training_settings_json=_smoke(still, lora))
    assert _serialize(s)["smoke_identity"]["current"] is True


@pytest.mark.parametrize("still_mtime,current", [(2000, True), (500, False)])
def test_record_without_a_lora_path_is_judged_by_file_times(app, tmp_path, still_mtime, current):
    lora = _touch(tmp_path / "anna_v1.safetensors", 1000)
    still = _touch(tmp_path / "smoke.png", still_mtime)
    s = _member(training_status="trained", lora_path=lora,
                training_settings_json=_smoke(still))
    assert _serialize(s)["smoke_identity"]["current"] is current


def test_record_without_a_lora_path_and_no_still_is_not_current(app, tmp_path):
    lora = _touch(tmp_path / "anna_v1.safetensors", 1000)
    s = _member(training_status="trained", lora_path=lora,
                training_settings_json=_smoke(str(tmp_path / "gone.png")))
    assert _serialize(s)["smoke_identity"]["current"] is False


def test_member_without_a_default_lora_has_no_current_score(app, tmp_path):
    still = _touch(tmp_path / "smoke.png", 2000)
    s = _member(training_status="untrained",
                training_settings_json=_smoke(still, str(tmp_path / "anna_v1.safetensors")))
    assert _serialize(s)["smoke_identity"]["current"] is False


def test_no_record_stays_none(app):
    s = _member(training_status="trained", training_settings_json={"rank": 16})
    assert _serialize(s)["smoke_identity"] is None


def test_legacy_record_is_not_current_next_to_the_saved_lora_note(app, tmp_path):
    # The worker died after the shared smoke still was rewritten but before the
    # record was saved: the still is newer than the new LoRA, the record is old.
    lora = _touch(tmp_path / "anna_v2.safetensors", 1000)
    _touch(tmp_path / "smoke.png", 2000)
    s = _reaped(lora, **_smoke(str(tmp_path / "smoke.png")))
    body = _serialize(s)
    assert body["training_note"] == "lora_saved_worker_stopped"
    assert body["smoke_identity"]["current"] is False


def test_record_naming_the_default_stays_current_next_to_the_note(app, tmp_path):
    # The worker died after the record was saved: the score is the new LoRA's.
    lora = _touch(tmp_path / "anna_v2.safetensors", 1000)
    still = _touch(tmp_path / "smoke.png", 2000)
    s = _reaped(lora, **_smoke(still, lora))
    assert _serialize(s)["smoke_identity"]["current"] is True


def test_legacy_record_is_not_current_once_an_older_lora_is_the_default(app, tmp_path):
    v1 = _touch(tmp_path / "anna_v1.safetensors", 1000)
    v2 = _touch(tmp_path / "anna_v2.safetensors", 2000)
    still = _touch(tmp_path / "smoke.png", 3000)
    s = _member(training_status="trained", lora_path=v2, training_settings_json=_smoke(still))
    for version, path in ((1, v1), (2, v2)):
        db.session.add(SubjectLora(subject_id=s.id, base_model_id="zimage-turbo",
                                   lora_path=path, version=version))
    db.session.commit()
    db.session.refresh(s)
    assert _serialize(s)["smoke_identity"]["current"] is True
    s.lora_path = v1
    db.session.commit()
    assert _serialize(s)["smoke_identity"]["current"] is False
