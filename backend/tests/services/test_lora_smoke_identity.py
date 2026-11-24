"""The post-train smoke still carries an identity number only from a real compare.

score_smoke_vs_refs used to divide the smoke PNG's byte count by the mean
reference byte count and the Cast page showed that as the identity score. These
pin both states from the caller's side. Without the image encoder installed,
score_smoke_vs_refs returns no score and never names the size method, and
_run_smoke_once still writes and records the smoke image while storing "not
measured". With it installed, the score is the encoder's, judged at the
measured threshold, and stored with its method and threshold for the Cast page.

The render, the GPU session, the LoRA sidecar and the encoder are stubbed
(backend/tests/fixtures/identity_encoder_stub.py); nothing here loads weights
or reaches a GPU.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from backend.models import Subject, db
from backend.services import identity_encoder
from backend.services import identity_method_data as data
from backend.services import lora_posttrain_smoke
from backend.services import video_consistency_metrics as vcm
from backend.services.stills_pipeline import StillResult
from backend.tests.fixtures import identity_encoder_stub as stub


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def refs_and_smoke(tmp_path):
    """Two references and a smoke image of about the same byte count.

    Same-sized files are the case the old score called a match, whatever face
    they held.
    """
    refs = []
    for i in range(2):
        p = tmp_path / f"ref_{i}.png"
        Image.new("RGB", (64, 64), (200, 40 * i, 10)).save(p)
        refs.append(str(p))
    smoke = tmp_path / "smoke.png"
    Image.new("RGB", (64, 64), (10, 120, 200)).save(smoke)
    return refs, str(smoke)


def test_score_smoke_vs_refs_returns_no_score(refs_and_smoke):
    refs, smoke = refs_and_smoke
    out = vcm.score_smoke_vs_refs(refs, smoke)

    identity = out["identity"]
    assert identity["score"] is None
    assert identity["status"] == "not_measured"
    assert identity["method"] != "size"
    assert identity["reason"]


def test_score_smoke_vs_refs_never_reaches_the_size_ratio(refs_and_smoke, monkeypatch):
    refs, smoke = refs_and_smoke
    seen = []
    real = vcm.score_identity_preservation

    def _spy(ref_paths, candidate_path, *, method="hist", analyzer=None):
        seen.append(method)
        return real(ref_paths, candidate_path, method=method, analyzer=analyzer)

    monkeypatch.setattr(vcm, "score_identity_preservation", _spy)
    vcm.score_smoke_vs_refs(refs, smoke)
    assert "size" not in seen


def _stub_smoke_render(monkeypatch, colour=(90, 90, 90)):
    """The render, GPU session and sidecar _run_smoke_once imports, stubbed; the
    smoke still comes out flat ``colour``."""
    @contextmanager
    def _no_gpu(*_a, **_k):
        yield None

    def _render(prompt, *, output_path, **_k):
        Image.new("RGB", (64, 64), colour).save(output_path)
        return StillResult(success=True, image_path=output_path, metadata={"family": "sdxl"})

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", _no_gpu)
    monkeypatch.setattr("backend.services.gpu_resource_policy.compositor_vram_reserve_mb", lambda: 0)
    monkeypatch.setattr("backend.services.character_still_pipeline.render_character_still", _render)
    monkeypatch.setattr("backend.services.media_model_registry.read_lora_sidecar", lambda _p: {})


def _run_smoke(tmp_path, refs):
    lora_path = tmp_path / "loras" / "anna.safetensors"
    lora_path.parent.mkdir()
    lora_path.write_bytes(b"stub")
    subject = Subject(name="Anna", kind="character", ref_image_paths=refs)
    db.session.add(subject)
    db.session.commit()
    out_path = str(lora_path.parent / "smoke" / f"smoke_{subject.id}.png")
    Path(out_path).parent.mkdir()
    result = lora_posttrain_smoke._run_smoke_once(
        subject_id=subject.id,
        lora_path=str(lora_path),
        prompt="anna_tok person, portrait",
        out_path=out_path,
        resolution=512,
        base_model_id=None,
        ref_image_paths=refs,
        subject=subject,
    )
    return subject, out_path, result


def test_smoke_run_still_writes_the_image_and_stores_not_measured(app, tmp_path, refs_and_smoke, monkeypatch):
    refs, _ = refs_and_smoke
    _stub_smoke_render(monkeypatch)

    with app.app_context():
        subject, out_path, result = _run_smoke(tmp_path, refs)

        assert result["ok"] is True
        assert result["path"] == out_path
        assert Path(out_path).is_file()
        assert result["identity"]["score"] is None

        stored = db.session.get(Subject, subject.id).training_settings_json["smoke_identity"]
        assert stored["path"] == out_path
        assert stored["lora_path"] == str(tmp_path / "loras" / "anna.safetensors")
        assert stored["score"] is None
        assert stored["status"] == "not_measured"
        assert stored["method"] != "size"


# ── With the image encoder installed ─────────────────────────────────────────

@pytest.fixture
def encoder(tmp_path, monkeypatch):
    """A verified install of the pinned encoder, with stand-in weights whose
    embedding of a picture is its mean colour."""
    root = tmp_path / "identity_encoder"
    monkeypatch.setattr(identity_encoder, "install_root", lambda: root)
    stub.fake_install(root)
    monkeypatch.setattr(vcm, "_image_encoder_classes", stub.stub_classes)
    vcm._ENCODER_CACHE.clear()
    yield
    vcm._ENCODER_CACHE.clear()


@pytest.fixture
def red_refs(tmp_path):
    refs = []
    for i, rgb in enumerate([(220, 30, 30), (210, 40, 25)]):
        p = tmp_path / f"red_ref_{i}.png"
        Image.new("RGB", (64, 64), rgb).save(p)
        refs.append(str(p))
    return refs


@pytest.mark.parametrize("colour,match", [((215, 35, 30), True), ((30, 40, 220), False)])
def test_score_smoke_vs_refs_uses_the_encoder_at_the_measured_threshold(
    encoder, red_refs, tmp_path, colour, match
):
    smoke = tmp_path / "smoke.png"
    Image.new("RGB", (64, 64), colour).save(smoke)
    identity = vcm.score_smoke_vs_refs(red_refs, str(smoke))["identity"]

    assert identity["status"] == "measured"
    assert identity["method"] == "embed"
    assert identity["model"] == "facebook/dinov2-base"
    assert identity["threshold"] == data.IDENTITY_METHODS["embed"]["threshold"]
    assert identity["match"] is match


def test_smoke_run_stores_the_encoder_score_for_the_cast_page(app, tmp_path, red_refs, encoder, monkeypatch):
    _stub_smoke_render(monkeypatch, colour=(215, 35, 30))
    with app.app_context():
        subject, _, result = _run_smoke(tmp_path, red_refs)
        stored = db.session.get(Subject, subject.id).training_settings_json["smoke_identity"]

    assert result["identity"]["status"] == "measured"
    assert stored["status"] == "measured"
    assert stored["method"] == "embed"
    assert stored["threshold"] == data.IDENTITY_METHODS["embed"]["threshold"]
    assert stored["score"] >= stored["threshold"]
