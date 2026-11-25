"""Delete History removes batch-image, batch-video and audio history — files
and their database mirrors — and nothing else in the same folders.

Scope is structural: a batch is a directory carrying batch_metadata.json
wherever it was moved to, a single render or infographic is a file named as
its generator names it, an audio generation is a uuid-hex-named file. Hand-named audio, editor renders,
root folders, indexing job history and running batches must all survive.

ComfyUI's output/ and input/ are emptied too (they duplicate what the batch
directories own), except input/example.png which its checkout tracks, and
never while ComfyUI has a prompt in flight.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

import requests
from flask import Flask

from backend.models import Document, EvalPair, Folder, JobHistory, RetentionAudit, db
from backend.services import generation_history_service as svc

HEX_A = "0" * 31 + "a"
HEX_B = "0" * 31 + "b"


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
def upload(tmp_path):
    """An uploads tree mixing history with things that must survive."""
    img = tmp_path / "Images" / "ImageBatch_x"
    (img / "images").mkdir(parents=True)
    (img / "batch_metadata.json").write_text(json.dumps({"batch_id": "ImageBatch_x", "status": "completed"}))
    (img / "images" / "a.png").write_bytes(b"x" * 100)

    vid = tmp_path / "Videos" / "VideoBatch_y"
    vid.mkdir(parents=True)
    (vid / "batch_metadata.json").write_text(json.dumps({"batch_id": "VideoBatch_y", "status": "completed"}))
    (vid / "clip.mp4").write_bytes(b"y" * 200)
    renders = tmp_path / "Videos" / "Editor Renders"
    renders.mkdir()
    (renders / "r.mp4").write_bytes(b"keep")

    audio = tmp_path / "Audio"
    (audio / ".jobs").mkdir(parents=True)
    (audio / f"{HEX_A}.wav").write_bytes(b"a" * 50)
    (audio / f"{HEX_A}_input_params.json").write_text("{}")
    (audio / "NARRATOR.wav").write_bytes(b"keep")
    (audio / ".jobs" / f"{HEX_A}.json").write_text(json.dumps({"id": HEX_A, "status": "done"}))
    (audio / ".jobs" / f"{HEX_B}.json").write_text(json.dumps({"id": HEX_B, "status": "running"}))
    return tmp_path


@pytest.fixture(autouse=True)
def comfy(tmp_path, monkeypatch):
    """A ComfyUI tree the service resolves by default, so no test in this
    module can reach the real plugins/comfyui/ComfyUI scratch folders."""
    root = tmp_path / "comfy"
    (root / "output").mkdir(parents=True)
    (root / "input").mkdir()
    (root / "input" / "example.png").write_bytes(b"ships-with-comfyui")
    monkeypatch.setattr(svc, "_comfyui_dir", lambda d=None: Path(d) if d is not None else root)
    return root


@pytest.fixture(autouse=True)
def outputs(tmp_path, monkeypatch):
    """Outputs and cache trees the service resolves by default, and a ComfyUI
    history call that goes nowhere, so no test can reach the real ones."""
    out = tmp_path / "outputs"
    cache = tmp_path / "cache"
    out.mkdir()
    cache.mkdir()
    cleared = []
    monkeypatch.setattr(svc, "_output_roots", lambda d=None: [Path(d) if d is not None else out])
    monkeypatch.setattr(svc, "_cache_dir", lambda d=None: Path(d) if d is not None else cache)
    monkeypatch.setattr(svc, "_comfyui_clear_history", lambda: cleared.append(True))
    monkeypatch.setattr(svc, "_upscales_running", lambda: 0)
    return {"out": out, "cache": cache, "history_cleared": cleared}


def _seed_comfy_scratch(root):
    (root / "output" / "wan22_i2v_00001.mp4").write_bytes(b"o" * 300)
    (root / "output" / "wan22_i2v_00001.png").write_bytes(b"p" * 30)
    (root / "input" / "ImageGen_ref.png").write_bytes(b"i" * 70)
    (root / "input" / "3d").mkdir()
    (root / "input" / "3d" / "model.glb").write_bytes(b"g" * 20)


@pytest.fixture
def seeded(app, upload):
    images_root = Folder(name="Images", path="Images")
    videos_root = Folder(name="Videos", path="Videos")
    audio_root = Folder(name="Audio", path="Audio")
    db.session.add_all([images_root, videos_root, audio_root])
    db.session.flush()
    img_folder = Folder(name="ImageBatch_x", path="Images/ImageBatch_x", parent_id=images_root.id)
    vid_folder = Folder(name="VideoBatch_y", path="Videos/VideoBatch_y", parent_id=videos_root.id)
    renders = Folder(name="Editor Renders", path="Videos/Editor Renders", parent_id=videos_root.id)
    db.session.add_all([img_folder, vid_folder, renders])
    db.session.flush()
    docs = [
        Document(filename="a.png", path="Images/ImageBatch_x/images/a.png", folder_id=img_folder.id),
        Document(filename="clip.mp4", path="Videos/VideoBatch_y/clip.mp4", folder_id=vid_folder.id),
        Document(filename="r.mp4", path="Videos/Editor Renders/r.mp4", folder_id=renders.id),
        Document(filename=f"{HEX_A}.wav", path=f"Audio/{HEX_A}.wav", folder_id=audio_root.id),
        Document(filename="NARRATOR.wav", path="Audio/NARRATOR.wav", folder_id=audio_root.id),
    ]
    db.session.add_all(docs)
    db.session.flush()
    db.session.add(EvalPair(question="q", expected_answer="a", source_doc_id=docs[0].id))
    now = datetime.now()
    db.session.add_all([
        JobHistory(id="unified:ImageBatch_x", kind="unified", native_id="ImageBatch_x", label="img",
                   status="completed", finished_at=now, job_metadata={"process_type": "image_generation"}),
        JobHistory(id="unified:idx1", kind="unified", native_id="idx1", label="index",
                   status="completed", finished_at=now, job_metadata={"process_type": "indexing"}),
        JobHistory(id="video_gen:VideoBatch_y", kind="video_gen", native_id="VideoBatch_y", label="vid",
                   status="completed", finished_at=now),
    ])
    db.session.commit()
    return upload


@pytest.fixture
def no_generators(monkeypatch):
    monkeypatch.setattr(svc, "_generator_instance", lambda kind: None)


@pytest.fixture
def sidecar_down(monkeypatch):
    def _down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "delete", _down)
    monkeypatch.setattr(requests, "get", _down)


def _tree(path):
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def test_counts_only_see_history(seeded, no_generators, sidecar_down):
    img_files, img_bytes = _tree(seeded / "Images" / "ImageBatch_x")
    vid_files, vid_bytes = _tree(seeded / "Videos" / "VideoBatch_y")
    counts = svc.count_generation_history(upload_dir=seeded)
    # batch_metadata.json is deleted with the batch, so it is counted with it.
    assert counts["images"] == {"batches": 1, "files": img_files, "bytes": img_bytes, "running": []}
    assert img_files == 2 and img_bytes > 100
    assert counts["videos"] == {"batches": 1, "files": vid_files, "bytes": vid_bytes, "running": []}
    assert counts["audio"] == {"jobs": 2, "files": 2, "bytes": 52, "running": []}
    # Editor render and NARRATOR docs are not counted; indexing history is not counted.
    assert counts["db"] == {"documents": 3, "folders": 2, "job_history": 2}
    assert counts["total_bytes"] == img_bytes + vid_bytes + 52


def test_delete_removes_history_and_keeps_everything_else(seeded, no_generators, sidecar_down):
    img_files, img_bytes = _tree(seeded / "Images" / "ImageBatch_x")
    _, vid_bytes = _tree(seeded / "Videos" / "VideoBatch_y")
    result = svc.delete_generation_history(upload_dir=seeded, triggered_by="test")

    assert result["success"], result["errors"]
    assert result["sidecar_available"] is False
    assert result["deleted"]["images"] == {"batches": 1, "files": img_files, "bytes": img_bytes}
    assert result["deleted"]["videos"]["batches"] == 1
    # Sidecar down: both job files are stale, both go.
    assert result["deleted"]["audio"] == {"jobs": 2, "files": 2, "bytes": 52}
    assert result["deleted"]["documents"] == 3
    assert result["deleted"]["folders"] == 2
    assert result["deleted"]["job_history"] == 2
    assert result["bytes_freed"] == img_bytes + vid_bytes + 52

    assert not (seeded / "Images" / "ImageBatch_x").exists()
    assert not (seeded / "Videos" / "VideoBatch_y").exists()
    assert (seeded / "Videos" / "Editor Renders" / "r.mp4").exists()
    assert (seeded / "Audio" / "NARRATOR.wav").exists()
    assert not (seeded / "Audio" / f"{HEX_A}.wav").exists()
    assert not list((seeded / "Audio" / ".jobs").glob("*.json"))

    remaining_folders = {f.path for f in Folder.query.all()}
    assert remaining_folders == {"Images", "Videos", "Audio", "Videos/Editor Renders"}
    remaining_docs = {d.path for d in Document.query.all()}
    assert remaining_docs == {"Videos/Editor Renders/r.mp4", "Audio/NARRATOR.wav"}
    assert [j.id for j in JobHistory.query.all()] == ["unified:idx1"]
    assert EvalPair.query.one().source_doc_id is None

    audits = {r.kind: r for r in RetentionAudit.query.all()}
    assert set(audits) == {
        "generation_history_images", "generation_history_videos",
        "generation_history_audio", "generation_history_db",
    }
    assert audits["generation_history_images"].bytes_freed == img_bytes
    assert audits["generation_history_db"].item_count == 7
    assert audits["generation_history_images"].triggered_by == "test"


def test_running_batch_is_skipped_and_reported(seeded, sidecar_down, monkeypatch):
    live = SimpleNamespace(
        active_batches={"ImageBatch_x": SimpleNamespace(status="running")},
        batch_lock=threading.Lock(),
        cancel_events={},
        queue_order=["ImageBatch_x"],
    )
    monkeypatch.setattr(svc, "_generator_instance", lambda kind: live if kind == "images" else None)

    counts = svc.count_generation_history(upload_dir=seeded)
    assert counts["images"]["running"] == ["ImageBatch_x"]

    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["skipped"]["images"] == ["ImageBatch_x"]
    assert result["deleted"]["images"]["batches"] == 0
    assert (seeded / "Images" / "ImageBatch_x" / "batch_metadata.json").exists()
    # Its DB mirror stays with it.
    assert Folder.query.filter_by(path="Images/ImageBatch_x").one()
    assert db.session.get(JobHistory, "unified:ImageBatch_x") is not None
    assert "ImageBatch_x" in live.active_batches
    # The video batch was not running and went normally.
    assert result["deleted"]["videos"]["batches"] == 1


def test_stale_running_metadata_without_a_live_generator_is_deletable(seeded, no_generators, sidecar_down):
    meta = seeded / "Images" / "ImageBatch_x" / "batch_metadata.json"
    meta.write_text(json.dumps({"batch_id": "ImageBatch_x", "status": "running"}))
    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["deleted"]["images"]["batches"] == 1
    assert not meta.exists()


def test_finished_batch_is_forgotten_by_the_live_generator(seeded, sidecar_down, monkeypatch):
    live = SimpleNamespace(
        active_batches={"VideoBatch_y": SimpleNamespace(status="completed")},
        batch_lock=threading.Lock(),
        cancel_events={"VideoBatch_y": object()},
        queue_order=["VideoBatch_y"],
    )
    monkeypatch.setattr(svc, "_generator_instance", lambda kind: live if kind == "videos" else None)
    svc.delete_generation_history(upload_dir=seeded)
    assert live.active_batches == {} and live.cancel_events == {} and live.queue_order == []


def test_sidecar_up_keeps_its_active_job_record(seeded, no_generators, monkeypatch):
    calls = []

    class _Resp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"removed": 1, "removed_ids": [HEX_A], "active_ids": [HEX_B]}

    def _delete(url, timeout):
        calls.append(url)
        # The sidecar removed its own finished record before answering.
        (seeded / "Audio" / ".jobs" / f"{HEX_A}.json").unlink()
        return _Resp()

    monkeypatch.setattr(requests, "delete", _delete)
    result = svc.delete_generation_history(upload_dir=seeded)

    assert calls == [f"{svc._sidecar_url()}/jobs"]
    assert result["sidecar_available"] is True
    assert result["skipped"]["audio"] == [HEX_B]
    assert result["deleted"]["audio"]["jobs"] == 1
    assert (seeded / "Audio" / ".jobs" / f"{HEX_B}.json").exists()
    assert not (seeded / "Audio" / f"{HEX_A}.wav").exists()


def test_sidecar_error_leaves_job_records_alone_and_is_reported(seeded, no_generators, monkeypatch):
    class _Resp:
        status_code = 500
        def raise_for_status(self):
            raise requests.HTTPError("500 boom")
        def json(self):
            return {}

    monkeypatch.setattr(requests, "delete", lambda url, timeout: _Resp())
    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["success"] is False
    assert any(e.startswith("audio sidecar") for e in result["errors"])
    assert len(list((seeded / "Audio" / ".jobs").glob("*.json"))) == 2
    # Everything else still ran.
    assert result["deleted"]["images"]["batches"] == 1
    assert result["deleted"]["documents"] == 3


def test_empty_tree_is_a_no_op(app, tmp_path, no_generators, sidecar_down):
    result = svc.delete_generation_history(upload_dir=tmp_path)
    assert result["success"] and result["bytes_freed"] == 0
    assert RetentionAudit.query.count() == 0


def test_comfyui_scratch_is_counted_and_purged_except_example(seeded, comfy, no_generators, sidecar_down):
    _seed_comfy_scratch(comfy)
    counts = svc.count_generation_history(upload_dir=seeded)
    assert counts["comfyui"] == {
        "output": {"files": 2, "bytes": 330},
        "input": {"files": 2, "bytes": 90},
        "files": 4, "bytes": 420, "running": [],
    }
    img_bytes = _tree(seeded / "Images" / "ImageBatch_x")[1]
    vid_bytes = _tree(seeded / "Videos" / "VideoBatch_y")[1]
    assert counts["total_bytes"] == img_bytes + vid_bytes + 52 + 420

    result = svc.delete_generation_history(upload_dir=seeded, triggered_by="test")
    assert result["success"], result["errors"]
    assert result["skipped"]["comfyui"] == []
    assert result["deleted"]["comfyui"] == {"output": {"files": 2, "bytes": 330}, "input": {"files": 2, "bytes": 90}}
    assert result["bytes_freed"] == img_bytes + vid_bytes + 52 + 420
    assert sorted(p.name for p in (comfy / "output").iterdir()) == []
    assert sorted(p.name for p in (comfy / "input").iterdir()) == ["example.png"]
    assert (comfy / "input" / "example.png").read_bytes() == b"ships-with-comfyui"

    audit = RetentionAudit.query.filter_by(kind="generation_history_comfyui").one()
    assert audit.item_count == 4 and audit.bytes_freed == 420
    assert audit.parameters["output"] == {"files": 2, "bytes": 330}


def test_comfyui_in_flight_defers_both_scratch_folders(seeded, comfy, no_generators, monkeypatch):
    _seed_comfy_scratch(comfy)

    class _Queue:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"queue_running": [[1, "prompt-a", {}, {}, []]], "queue_pending": [[2, "prompt-b", {}, {}, []]]}

    def _get(url, timeout):
        if url.endswith("/queue"):
            return _Queue()
        raise requests.ConnectionError("audio sidecar down")

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "delete", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")))

    assert svc.count_generation_history(upload_dir=seeded)["comfyui"]["running"] == ["prompt-a", "prompt-b"]
    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["success"], result["errors"]
    assert result["skipped"]["comfyui"] == ["prompt-a", "prompt-b"]
    assert result["deleted"]["comfyui"] == {"output": {"files": 0, "bytes": 0}, "input": {"files": 0, "bytes": 0}}
    assert (comfy / "output" / "wan22_i2v_00001.mp4").exists()
    assert (comfy / "input" / "3d" / "model.glb").exists()
    # Batch history still went.
    assert result["deleted"]["images"]["batches"] == 1
    assert RetentionAudit.query.filter_by(kind="generation_history_comfyui").count() == 0


def test_comfyui_queue_error_leaves_scratch_alone_and_is_reported(seeded, comfy, no_generators, monkeypatch):
    _seed_comfy_scratch(comfy)

    class _Boom:
        status_code = 500
        def raise_for_status(self):
            raise requests.HTTPError("500 boom")

    def _get(url, timeout):
        if url.endswith("/queue"):
            return _Boom()
        raise requests.ConnectionError("audio sidecar down")

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "delete", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")))

    result = svc.delete_generation_history(upload_dir=seeded)
    assert result["success"] is False
    assert any(e.startswith("comfyui queue") for e in result["errors"])
    assert (comfy / "output" / "wan22_i2v_00001.mp4").exists()
    assert (comfy / "input" / "ImageGen_ref.png").exists()
    assert result["deleted"]["images"]["batches"] == 1


def test_comfyui_dir_without_scratch_folders_is_a_no_op(app, tmp_path, no_generators, sidecar_down):
    result = svc.delete_generation_history(upload_dir=tmp_path, comfyui_dir=tmp_path / "nowhere")
    assert result["success"] and result["bytes_freed"] == 0
    assert result["deleted"]["comfyui"] == {"output": {"files": 0, "bytes": 0}, "input": {"files": 0, "bytes": 0}}


def test_spoken_clips_in_the_voice_subfolder_are_history(seeded, no_generators, sidecar_down):
    voice_dir = seeded / "Audio" / "Voice"
    voice_dir.mkdir()
    (voice_dir / f"{HEX_B}.wav").write_bytes(b"v" * 30)
    (voice_dir / "my intro.wav").write_bytes(b"keep")
    audio_root = Folder.query.filter_by(path="Audio").one()
    voice = Folder(name="Voice", path="Audio/Voice", parent_id=audio_root.id)
    db.session.add(voice)
    db.session.flush()
    db.session.add_all([
        Document(filename="Good morning.wav", path=f"Audio/Voice/{HEX_B}.wav", folder_id=voice.id),
        Document(filename="my intro.wav", path="Audio/Voice/my intro.wav", folder_id=voice.id),
    ])
    db.session.commit()

    counts = svc.count_generation_history(upload_dir=seeded)
    assert counts["audio"]["files"] == 3 and counts["audio"]["bytes"] == 82

    result = svc.delete_generation_history(upload_dir=seeded, triggered_by="test")
    assert result["success"], result["errors"]
    assert not (voice_dir / f"{HEX_B}.wav").exists()
    assert (voice_dir / "my intro.wav").exists()
    remaining_docs = {d.path for d in Document.query.all()}
    assert f"Audio/Voice/{HEX_B}.wav" not in remaining_docs
    assert "Audio/Voice/my intro.wav" in remaining_docs
    assert Folder.query.filter_by(path="Audio/Voice").count() == 1


def _seed_moved_and_single(upload):
    """A batch moved into a user folder, single renders, infographics, and hand-placed files beside them."""
    moved = upload / "Images" / "Keepers" / "ImageBatch_moved"
    (moved / "images").mkdir(parents=True)
    (moved / "batch_metadata.json").write_text(json.dumps({"status": "completed"}))
    (moved / "images" / "m.png").write_bytes(b"m" * 10)
    (upload / "Images" / "Keepers" / "holiday.png").write_bytes(b"h" * 5)
    images = upload / "Images"
    (images / "ImageGen_10-31-2026_045727_047.png").write_bytes(b"1" * 7)
    (images / "guaardvark-moss-image_00224_.png").write_bytes(b"2" * 8)
    (images / "guaardvark-moss-image_00229_ (2).png").write_bytes(b"3" * 9)
    (images / "yapper.jpeg").write_bytes(b"y" * 4)
    info = images / "Infographics"
    info.mkdir()
    (info / "infographic_20261113_111852_319420.png").write_bytes(b"i" * 11)
    (info / "my-chart.png").write_bytes(b"c" * 3)

    images_root = Folder.query.filter_by(path="Images").one()
    keepers = Folder(name="Keepers", path="Images/Keepers", parent_id=images_root.id)
    info_folder = Folder(name="Infographics", path="Images/Infographics", parent_id=images_root.id)
    db.session.add_all([keepers, info_folder])
    db.session.flush()
    moved_folder = Folder(name="ImageBatch_moved", path="Images/Keepers/ImageBatch_moved", parent_id=keepers.id)
    db.session.add(moved_folder)
    db.session.flush()
    db.session.add_all([
        Document(filename="m.png", path="Images/Keepers/ImageBatch_moved/images/m.png", folder_id=moved_folder.id),
        Document(filename="holiday.png", path="Images/Keepers/holiday.png", folder_id=keepers.id),
        Document(filename="ImageGen_10-31-2026_045727_047.png", path="Images/ImageGen_10-31-2026_045727_047.png", folder_id=images_root.id),
        Document(filename="guaardvark-moss-image_00224_.png", path="Images/guaardvark-moss-image_00224_.png", folder_id=images_root.id),
        Document(filename="yapper.jpeg", path="Images/yapper.jpeg", folder_id=images_root.id),
        Document(filename="infographic_20261113_111852_319420.png",
                 path="Images/Infographics/infographic_20261113_111852_319420.png", folder_id=info_folder.id),
        Document(filename="my-chart.png", path="Images/Infographics/my-chart.png", folder_id=info_folder.id),
    ])
    db.session.commit()


def test_moved_batches_single_renders_and_infographics_are_history(seeded, no_generators, sidecar_down):
    _seed_moved_and_single(seeded)
    counts = svc.count_generation_history(upload_dir=seeded)
    assert counts["images"]["batches"] == 2
    assert counts["loose"] == {"files": 3, "bytes": 24}
    assert counts["infographics"] == {"files": 1, "bytes": 11}

    result = svc.delete_generation_history(upload_dir=seeded, triggered_by="test")
    assert result["success"], result["errors"]
    assert result["deleted"]["images"]["batches"] == 2
    assert result["deleted"]["loose"] == {"files": 3, "bytes": 24}
    assert result["deleted"]["infographics"] == {"files": 1, "bytes": 11}

    images = seeded / "Images"
    assert not (images / "Keepers" / "ImageBatch_moved").exists()
    assert sorted(p.name for p in images.iterdir()) == ["Infographics", "Keepers", "yapper.jpeg"]
    assert [p.name for p in (images / "Infographics").iterdir()] == ["my-chart.png"]
    assert [p.name for p in (images / "Keepers").iterdir()] == ["holiday.png"]

    remaining_folders = {f.path for f in Folder.query.all()}
    assert remaining_folders == {
        "Images", "Videos", "Audio", "Videos/Editor Renders", "Images/Keepers", "Images/Infographics",
    }
    remaining_docs = {d.path for d in Document.query.all()}
    assert remaining_docs == {
        "Videos/Editor Renders/r.mp4", "Audio/NARRATOR.wav",
        "Images/Keepers/holiday.png", "Images/yapper.jpeg", "Images/Infographics/my-chart.png",
    }
    kinds = {r.kind for r in RetentionAudit.query.all()}
    assert {"generation_history_loose", "generation_history_infographics"} <= kinds


def _names(path):
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())


def _seed_outputs(out, cache):
    gen = out / "generated_images"
    gen.mkdir()
    (gen / "gen_20261101_120000_0a1b2c3d.png").write_bytes(b"g" * 10)
    (gen / "edit_20261101_120001_0a1b2c3d.png").write_bytes(b"e" * 20)
    (gen / "girl1_take1_lastframe.png").write_bytes(b"keep")
    src = out / "edit_inputs"
    src.mkdir()
    (src / "page_src_0123456789ab.png").write_bytes(b"s" * 5)
    (src / "page_src_0123456789ab.png.consent").write_text("{}")
    (out / "consent").mkdir()
    (out / "consent" / ("a" * 64 + ".consent")).write_text("{}")
    (out / "narrations").mkdir()
    (out / "narrations" / "narration_20261101_120000.wav").write_bytes(b"n" * 40)
    up = out / "upscaling"
    (up / "input" / "images").mkdir(parents=True)
    (up / "output").mkdir()
    (up / "input" / "images" / "a_0a1b2c3d.png").write_bytes(b"u" * 7)
    (up / "output" / "a_upscaled.mp4").write_bytes(b"U" * 70)
    (out / "productions" / "p1").mkdir(parents=True)
    (out / "productions" / "p1" / "final.mp4").write_bytes(b"keep")
    thumbs = cache / "thumbnails" / "ab"
    thumbs.mkdir(parents=True)
    (thumbs / ("ab" + "0" * 38 + ".webp")).write_bytes(b"t" * 3)
    staging = cache / "generated_videos" / "h3_refs"
    staging.mkdir(parents=True)
    (staging / (HEX_A + ".mp4")).write_bytes(b"r" * 9)
    (cache / "nltk_data").mkdir()
    (cache / "nltk_data" / "punkt.zip").write_bytes(b"keep")


def test_tool_outputs_narrations_upscales_and_previews_are_history(
    app, tmp_path, outputs, no_generators, sidecar_down
):
    out, cache = outputs["out"], outputs["cache"]
    _seed_outputs(out, cache)
    upload = tmp_path / "uploads"
    upload.mkdir()

    counts = svc.count_generation_history(upload_dir=upload)
    assert counts["tool_outputs"] == {"files": 4, "bytes": 37}
    assert counts["narrations"] == {"files": 1, "bytes": 40}
    assert counts["upscales"] == {"files": 2, "bytes": 77, "running": 0}
    assert counts["previews"] == {"files": 1, "bytes": 3}
    assert counts["comfyui"]["staging"] == {"files": 1, "bytes": 9}

    result = svc.delete_generation_history(upload_dir=upload)
    assert result["success"], result["errors"]
    assert result["deleted"]["tool_outputs"]["files"] == 4
    assert result["deleted"]["upscales"]["files"] == 2
    assert outputs["history_cleared"] == [True]
    assert _names(out) == sorted([
        "consent/" + "a" * 64 + ".consent",
        "generated_images/girl1_take1_lastframe.png",
        "productions/p1/final.mp4",
    ])
    assert (out / "upscaling" / "input").is_dir() and not (out / "upscaling" / "input" / "images").exists()
    assert _names(cache) == ["nltk_data/punkt.zip"]


def test_upscales_are_left_alone_while_one_runs(app, tmp_path, outputs, no_generators, sidecar_down, monkeypatch):
    _seed_outputs(outputs["out"], outputs["cache"])
    monkeypatch.setattr(svc, "_upscales_running", lambda: 1)
    upload = tmp_path / "uploads"
    upload.mkdir()
    result = svc.delete_generation_history(upload_dir=upload)
    assert result["skipped"]["upscales"] == 1
    assert result["deleted"]["upscales"] == {"files": 0, "bytes": 0}
    assert (outputs["out"] / "upscaling" / "output" / "a_upscaled.mp4").exists()


def test_library_rows_for_folders_already_gone_are_removed(app, tmp_path, no_generators, sidecar_down):
    upload = tmp_path / "uploads"
    (upload / "Images" / "Kept").mkdir(parents=True)
    (upload / "Images" / "Kept" / "mine.png").write_bytes(b"k")
    root = Folder(name="Images", path="Images")
    db.session.add(root)
    db.session.flush()
    gone = Folder(name="ImageBatch_gone", path="Images/ImageBatch_gone", parent_id=root.id)
    kept = Folder(name="Kept", path="Images/Kept", parent_id=root.id)
    elsewhere = Folder(name="Elsewhere", path="Images/Elsewhere", parent_id=root.id)
    db.session.add_all([gone, kept, elsewhere])
    db.session.flush()
    db.session.add_all([
        Document(filename="a.png", path="Images/ImageBatch_gone/images/a.png", folder_id=gone.id),
        Document(filename="mine.png", path="Images/Kept/mine.png", folder_id=kept.id),
        # Filed in a folder with no directory, but its file exists somewhere else.
        Document(filename="mine2.png", path=str(upload / "Images" / "Kept" / "mine.png"), folder_id=elsewhere.id),
    ])
    db.session.commit()

    result = svc.delete_generation_history(upload_dir=upload)
    assert result["success"], result["errors"]
    assert result["deleted"]["folders"] == 1 and result["deleted"]["documents"] == 1
    assert {f.path for f in Folder.query.all()} == {"Images", "Images/Kept", "Images/Elsewhere"}
    assert {d.filename for d in Document.query.all()} == {"mine.png", "mine2.png"}


def test_minimax_music_and_library_upscales_are_history(seeded, no_generators, sidecar_down):
    upload = seeded
    (upload / "Audio" / "minimax_music3_0a1b2c3d_00001.flac").write_bytes(b"m" * 11)
    (upload / "Images" / "wan22_i2v_00007__upscaled.png").write_bytes(b"u" * 13)
    result = svc.delete_generation_history(upload_dir=upload)
    assert not (upload / "Audio" / "minimax_music3_0a1b2c3d_00001.flac").exists()
    assert not (upload / "Images" / "wan22_i2v_00007__upscaled.png").exists()
    assert (upload / "Audio" / "NARRATOR.wav").exists()
    assert result["deleted"]["loose"]["files"] >= 1

