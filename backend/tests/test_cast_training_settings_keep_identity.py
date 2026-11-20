"""Saving a cast member's LoRA hyperparameters keeps its identity state.

Subject.training_settings_json holds the six hyperparameters and, in the same
object, the identity flags (bible_vision_grounded, bible_manual_override, the
vision tags and marks, class_token) and the post-train smoke score. A
hyperparameter write must replace only the six.

Runs on in-memory SQLite. Vision sync, captioning, the train-base gate and
the Celery dispatch are replaced, so nothing reaches Ollama, a GPU or a worker.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend.api.cast_library_api import bp as cast_library_bp
from backend.models import Subject, db
from backend.services import (
    cast_identity_manager,
    character_captioner,
    lora_train_dispatch,
    media_model_registry,
)
from backend.services.lora_training_settings import merge_training_settings

IDENTITY = {
    "bible_vision_grounded": True,
    "bible_vision_tags": ["short black hair", "green eyes"],
    "bible_identity_marks": "short black hair, green eyes",
    "class_token": "woman",
    "smoke_identity": {"ok": True, "score": 0.81, "method": "hist"},
}

SETTINGS = {
    "resolution": 1024, "rank": 32, "alpha": 32, "learning_rate": 0.0002,
    "steps": None, "base_model_id": "zimage-turbo",
}


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      STORAGE_DIR=str(tmp_path))
    db.init_app(app)
    app.register_blueprint(cast_library_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def vision_syncs(monkeypatch):
    """Record identity syncs; each one does to the row what the real sync does."""
    calls = []

    def fake_sync(subject_id, **_kwargs):
        calls.append(subject_id)
        s = db.session.get(Subject, subject_id)
        s.bible = "rewritten from photos"
        cfg = dict(s.training_settings_json or {})
        cfg.pop("bible_manual_override", None)
        cfg["bible_vision_grounded"] = True
        s.training_settings_json = cfg
        db.session.commit()
        return {"ok": True}

    monkeypatch.setattr(cast_identity_manager, "sync_identity_from_refs", fake_sync)
    monkeypatch.setattr(lora_train_dispatch, "dispatch_lora_train",
                        lambda sid: {"job_id": "test-job", "dispatched": True})
    monkeypatch.setattr(media_model_registry, "assert_train_ready", lambda base: {"id": base})
    monkeypatch.setattr(character_captioner, "ensure_subject_image_captions",
                        lambda *a, **k: {"written": 0})
    return calls


def _subject(client, **cfg):
    sid = client.post("/api/cast-library/subjects",
                      json={"kind": "character", "name": "Ada"}).get_json()["id"]
    s = db.session.get(Subject, sid)
    s.ref_image_paths = ["data/cast_refs/1/a.png"]
    s.training_status = "trained"
    s.bible = "bible from an earlier vision sync"
    s.training_settings_json = {"resolution": 768, "rank": 16, **cfg}
    db.session.commit()
    return sid


def _stored(sid):
    db.session.expire_all()
    return db.session.get(Subject, sid).training_settings_json or {}


def test_merge_replaces_the_hyperparameters_and_keeps_the_rest():
    merged = merge_training_settings({"resolution": 768, "rank": 16, **IDENTITY}, SETTINGS)
    for key, value in IDENTITY.items():
        assert merged[key] == value
    assert merged["resolution"] == 1024 and merged["rank"] == 32 and merged["steps"] is None


def test_merge_without_stored_settings_is_the_normalised_set():
    merged = merge_training_settings(None, {"resolution": 800, "rank": 99})
    assert merged["resolution"] == 768  # snapped to 64 px
    assert merged["rank"] == 64  # clamped
    assert set(merged) == {"resolution", "rank", "alpha", "learning_rate", "steps",
                           "base_model_id", "lora_targets", "base_storage"}


def test_merge_keeps_a_stored_layer_choice_the_form_does_not_send():
    merged = merge_training_settings({"lora_targets": "attention", "base_storage": "bf16"}, SETTINGS)
    assert (merged["lora_targets"], merged["base_storage"]) == ("attention", "bf16")


def test_saving_training_settings_keeps_the_identity_state(client):
    sid = _subject(client, **IDENTITY)
    resp = client.patch(f"/api/cast-library/subjects/{sid}", json={"training_settings": SETTINGS})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["bible_vision_grounded"] is True
    assert body["smoke_identity"]["score"] == 0.81
    stored = _stored(sid)
    for key, value in IDENTITY.items():
        assert stored[key] == value
    assert stored["resolution"] == 1024 and stored["rank"] == 32


def test_a_bible_edit_saved_with_settings_keeps_its_manual_override(client):
    sid = _subject(client, **IDENTITY)
    resp = client.patch(f"/api/cast-library/subjects/{sid}",
                        json={"bible": "my own words", "training_settings": SETTINGS})
    assert resp.status_code == 200
    stored = _stored(sid)
    assert stored["bible_manual_override"] is True
    assert stored["bible_vision_grounded"] is False
    assert stored["rank"] == 32


def test_training_a_grounded_member_keeps_its_identity_and_settings(client, vision_syncs):
    sid = _subject(client, **IDENTITY)
    resp = client.post(f"/api/cast-library/subjects/{sid}/train", json={"training_settings": SETTINGS})
    assert resp.status_code == 202
    assert vision_syncs == []
    stored = _stored(sid)
    for key, value in IDENTITY.items():
        assert stored[key] == value
    # The settings the run was started with are stored even though no identity
    # sync committed the row first.
    assert stored["resolution"] == 1024 and stored["rank"] == 32
    assert db.session.get(Subject, sid).bible == "bible from an earlier vision sync"


def test_training_an_ungrounded_member_still_syncs_identity_first(client, vision_syncs):
    sid = _subject(client, bible_manual_override=True, bible_vision_grounded=False)
    resp = client.post(f"/api/cast-library/subjects/{sid}/train", json={"training_settings": SETTINGS})
    assert resp.status_code == 202
    assert vision_syncs == [sid]
    stored = _stored(sid)
    assert stored["bible_vision_grounded"] is True
    assert stored["rank"] == 32
