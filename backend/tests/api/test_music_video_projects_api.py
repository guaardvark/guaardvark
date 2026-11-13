"""API tests for saving / editing music videos and filing them under Projects.

Same in-memory SQLite fixture as test_music_video_api.py: never touches the live DB.
"""
import os

import pytest

try:
    from flask import Flask
    from backend.models import db, Document, MusicVideo, MusicVideoRevision, Project
    from backend.api.music_video_api import bp as music_video_bp
    from backend.services.music_video_service import MusicVideoService, clip_input_hash
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(music_video_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


DISPATCHED = []


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch, tmp_path):
    DISPATCHED.clear()
    monkeypatch.setattr(MusicVideoService, "dispatch_agent",
                        lambda self, mv_id, agent, **kw: DISPATCHED.append((mv_id, agent)))

    # A copied video's files land under the test's own folder, never data/outputs.
    def _clip_dir(mv_id):
        d = tmp_path / "outputs" / f"music_video_{mv_id}" / "clips"
        d.mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr("backend.tasks.music_video_tasks._clip_dir", _clip_dir)
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda role, explicit=None, surface=None, **k: (explicit or "wan22-5b", None),
    )
    monkeypatch.setattr(
        "backend.api.music_video_api.get_video_generator",
        lambda: type("VG", (), {"service_available": True})(),
    )
    # The box's own Plugins page settings never decide a test's outcome.
    monkeypatch.setattr("backend.services.plugin_bridge.job_plugin_blocker", lambda plugin_id: None)
    # Finished-cut history is read fresh in every test, never from a cache.
    monkeypatch.setattr("backend.api.music_video_api.CUT_HISTORY_TTL_S", 0.0)


def _video(tmp_path, *, stage="complete", rendered=True, project_id=None):
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF")
    doc = Document(filename="song.wav", path=str(song), type="wav", size=4)
    db.session.add(doc)
    db.session.commit()
    svc = MusicVideoService(db.session)
    mv = svc.create(name="Song", song_document_id=doc.id, song_path=str(song),
                    style_prompt="blue", project_id=project_id, settings={"i2v_model": "wan22-5b"})
    clips = []
    for i in range(3):
        c = {"index": i, "start": float(i), "end": float(i + 1), "prompt": f"shot {i}",
             "status": "pending", "clip_path": None}
        if rendered:
            f = tmp_path / f"clip_{i}.mp4"
            f.write_bytes(b"x")
            c.update(status="done", clip_path=str(f))
            c["input_hash"] = clip_input_hash(c, mv.settings_json)
        clips.append(c)
    mv.cut_plan = [{"index": i, "start_s": float(i), "end_s": float(i + 1), "energy": 0.5,
                    "section_label": "a"} for i in range(3)]
    mv.clips = clips
    mv.current_stage = stage
    mv.status = stage
    db.session.commit()
    return mv.id


def test_detail_carries_progress_and_cuts_to_render(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval", rendered=False)
    data = client.get(f"/api/music-video/{mv_id}").get_json()
    assert data["progress"]["stages"][1]["key"] == "awaiting_approval"
    assert data["progress"]["stages"][1]["state"] == "waiting"
    assert data["cuts_to_render"] == 3
    prog = client.get(f"/api/music-video/{mv_id}/progress").get_json()
    assert prog["progress"]["overall_pct"] is not None


def test_patch_renames_files_under_a_project_and_keeps_notes(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
        out = Document(filename="final.mp4", path=str(tmp_path / "final.mp4"), type="mp4")
        db.session.add(out)
        p = Project(name="Album")
        db.session.add(p)
        db.session.commit()
        mv = db.session.get(MusicVideo, mv_id)
        mv.output_document_id = out.id
        db.session.commit()
        pid, out_id = p.id, out.id

    r = client.patch(f"/api/music-video/{mv_id}",
                     json={"name": "Renamed", "project_id": pid, "notes": "v2 cut", "tags": ["a", "a", "b"]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["name"] == "Renamed" and body["project_id"] == pid and body["project_name"] == "Album"
    assert body["notes"] == "v2 cut" and body["tags"] == ["a", "b"]
    with app.app_context():
        assert db.session.get(Document, out_id).project_id == pid
    assert [v["id"] for v in client.get(f"/api/music-video?project_id={pid}").get_json()["music_videos"]] == [mv_id]
    assert client.get("/api/music-video?project_id=none").get_json()["music_videos"] == []

    r = client.patch(f"/api/music-video/{mv_id}", json={"project_id": None})
    assert r.get_json()["project_id"] is None
    with app.app_context():
        assert db.session.get(Document, out_id).project_id is None

    assert client.patch(f"/api/music-video/{mv_id}", json={"project_id": 9999}).status_code == 400
    assert client.patch(f"/api/music-video/{mv_id}", json={"name": "  "}).status_code == 400


def test_revise_keeps_clips_and_only_edited_cuts_render(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
    r = client.post(f"/api/music-video/{mv_id}/revise")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["current_stage"] == "awaiting_approval"
    assert body["clips_done"] == 3 and body["cuts_to_render"] == 0

    r = client.post(f"/api/music-video/{mv_id}/cut/1", json={"prompt": "a new shot"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["cuts_to_render"] == 1
    assert r.get_json()["estimate"]["clips_to_generate"] == 1

    with app.app_context():
        assert MusicVideoRevision.query.filter_by(music_video_id=mv_id, reason="revise").count() == 1


def test_cut_seed_and_lock(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval", rendered=False)
    r = client.post(f"/api/music-video/{mv_id}/cut/0", json={"seed": 42, "locked": True})
    clip = next(c for c in r.get_json()["clips"] if c["index"] == 0)
    assert clip["seed"] == 42 and clip["locked"] is True
    assert client.post(f"/api/music-video/{mv_id}/cut/0", json={"seed": "x"}).status_code == 400
    assert client.post(f"/api/music-video/{mv_id}/cut/0", json={"prompt": ""}).status_code == 400
    assert client.post(f"/api/music-video/{mv_id}/cut/9", json={"locked": True}).status_code == 404


def test_cut_edit_refused_on_a_finished_video(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
    assert client.post(f"/api/music-video/{mv_id}/cut/0", json={"prompt": "x"}).status_code == 409


def test_duplicate_copies_the_plan_to_the_gate(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
    r = client.post(f"/api/music-video/{mv_id}/duplicate", json={})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["id"] != mv_id and body["name"] == "Song (copy)"
    assert body["current_stage"] == "awaiting_approval"
    assert body["cut_count"] == 3 and body["clips_done"] == 0

    kept = client.post(f"/api/music-video/{mv_id}/duplicate", json={"keep_clips": True, "name": "Alt"}).get_json()
    assert kept["name"] == "Alt" and kept["clips_done"] == 3 and kept["cuts_to_render"] == 0


def test_versions_save_list_and_restore(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval", rendered=False)
    saved = client.post(f"/api/music-video/{mv_id}/revisions", json={"label": "first"})
    assert saved.status_code == 201
    rid = saved.get_json()["id"]

    client.post(f"/api/music-video/{mv_id}/plan", json={"prompts": {"0": "changed"}})
    assert client.get(f"/api/music-video/{mv_id}").get_json()["clips"][0]["prompt"] == "changed"

    r = client.post(f"/api/music-video/{mv_id}/revisions/{rid}/restore")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["clips"][0]["prompt"] == "shot 0"
    revs = client.get(f"/api/music-video/{mv_id}/revisions").get_json()["revisions"]
    assert [x["reason"] for x in revs][:2] == ["restore", "manual"]
    assert client.get(f"/api/music-video/{mv_id}/revisions/{rid}").get_json()["snapshot"]["clips"][0]["prompt"] == "shot 0"


def test_restore_refused_while_running(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="generating", rendered=False)
    rid = client.post(f"/api/music-video/{mv_id}/revisions", json={}).get_json()["id"]
    assert client.post(f"/api/music-video/{mv_id}/revisions/{rid}/restore").status_code == 409


def test_deleting_a_video_deletes_its_versions(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
    client.post(f"/api/music-video/{mv_id}/revisions", json={})
    assert client.delete(f"/api/music-video/{mv_id}").status_code == 200
    with app.app_context():
        assert MusicVideoRevision.query.filter_by(music_video_id=mv_id).count() == 0


def test_project_delete_keeps_the_rendered_video(app, tmp_path):
    from backend.services.video_project_registry import count_by_project, detach_project_media, list_all
    with app.app_context():
        p = Project(name="Gone")
        db.session.add(p)
        db.session.commit()
        mv_id = _video(tmp_path, project_id=p.id)
        out = Document(filename="final.mp4", path=str(tmp_path / "final2.mp4"), type="mp4", project_id=p.id)
        db.session.add(out)
        db.session.commit()
        mv = db.session.get(MusicVideo, mv_id)
        mv.output_document_id = out.id
        db.session.commit()

        assert count_by_project(db.session) == {p.id: 1}
        assert [r["id"] for r in list_all(db.session, project_id=p.id)] == [mv_id]
        assert detach_project_media(db.session, p.id) == 1
        db.session.delete(p)
        db.session.commit()
        assert db.session.get(Document, out.id) is not None
        assert db.session.get(MusicVideo, mv_id).project_id is None


def test_plan_save_keeps_the_treatment_and_style(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval", rendered=False)
    r = client.post(f"/api/music-video/{mv_id}/plan",
                    json={"treatment": "A slow night drive", "style_prompt": "neon"})
    assert r.status_code == 200, r.get_json()
    body = client.get(f"/api/music-video/{mv_id}").get_json()
    assert body["user_treatment"] == "A slow night drive"
    assert body["style_prompt"] == "neon"


def test_plan_refuses_an_oversized_prompt(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval", rendered=False)
    r = client.post(f"/api/music-video/{mv_id}/plan", json={"prompts": {"0": "x" * 8001}})
    assert r.status_code == 400


def test_approve_saves_one_version_and_queues_changed_cuts(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval")
    client.post(f"/api/music-video/{mv_id}/plan", json={"prompts": {"1": "a different shot"}})
    r = client.post(f"/api/music-video/{mv_id}/approve")
    assert r.status_code == 200, r.get_json()
    statuses = [c["status"] for c in sorted(r.get_json()["clips"], key=lambda c: c["index"])]
    assert statuses == ["done", "pending", "done"]
    assert client.post(f"/api/music-video/{mv_id}/approve").status_code == 409
    with app.app_context():
        assert MusicVideoRevision.query.filter_by(music_video_id=mv_id, reason="approve").count() == 1


def test_a_cut_edit_mid_render_starts_no_second_chain(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="generating")
        mv = db.session.get(MusicVideo, mv_id)
        clips = [dict(c) for c in mv.clips]
        clips[2].update(status="pending", clip_path=None)
        mv.clips = clips
        db.session.commit()
    DISPATCHED.clear()
    r = client.post(f"/api/music-video/{mv_id}/cut/0", json={"prompt": "changed"})
    assert r.status_code == 200, r.get_json()
    assert DISPATCHED == []  # the running chain reaches cut 0 itself


def test_a_cut_edit_after_the_render_stopped_starts_it(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path, stage="generating")
    DISPATCHED.clear()
    r = client.post(f"/api/music-video/{mv_id}/cut/0", json={"prompt": "changed"})
    assert r.status_code == 200, r.get_json()
    assert DISPATCHED == [(mv_id, "clip_generator")]


def test_restore_refused_while_storyboards_render(client, app, tmp_path):
    import time
    with app.app_context():
        mv_id = _video(tmp_path, stage="awaiting_approval", rendered=False)
    rid = client.post(f"/api/music-video/{mv_id}/revisions", json={}).get_json()["id"]
    with app.app_context():
        mv = db.session.get(MusicVideo, mv_id)
        mv.settings_json = {**(mv.settings_json or {}), "storyboard_generating": True,
                            "storyboard_started_at": time.time()}
        db.session.commit()
    assert client.post(f"/api/music-video/{mv_id}/revisions/{rid}/restore").status_code == 409


def test_a_duplicate_with_clips_owns_its_files(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
    kept = client.post(f"/api/music-video/{mv_id}/duplicate", json={"keep_clips": True}).get_json()
    assert kept["clips_done"] == 3 and kept["cuts_to_render"] == 0
    for c in kept["clips"]:
        assert f"music_video_{kept['id']}" in c["clip_path"]
        assert os.path.exists(c["clip_path"])
    # The original's file changing leaves the copy as it was.
    (tmp_path / "clip_0.mp4").write_bytes(b"re-rendered")
    copied = next(c for c in kept["clips"] if c["index"] == 0)["clip_path"]
    assert open(copied, "rb").read() == b"x"


def test_patch_and_duplicate_refuse_a_name_that_is_not_text(client, app, tmp_path):
    with app.app_context():
        mv_id = _video(tmp_path)
    assert client.patch(f"/api/music-video/{mv_id}", json={"name": 5}).status_code == 400
    assert client.post(f"/api/music-video/{mv_id}/duplicate", json={"name": ["x"]}).status_code == 400
    assert client.post(f"/api/music-video/{mv_id}/revisions", json={"label": 3}).status_code == 400


def test_project_delete_keeps_outputs_of_a_video_deleted_earlier(client, app, tmp_path):
    from backend.services.music_video_service import attach_outputs_to_project
    from backend.services.video_project_registry import detach_project_media
    with app.app_context():
        p = Project(name="Album")
        db.session.add(p)
        db.session.commit()
        mv_id = _video(tmp_path, project_id=p.id)
        out = Document(filename="final.mp4", path=str(tmp_path / "final3.mp4"), type="mp4")
        db.session.add(out)
        db.session.commit()
        mv = db.session.get(MusicVideo, mv_id)
        attach_outputs_to_project(mv, [out.id])
        db.session.commit()
        pid, out_id = p.id, out.id
    assert client.delete(f"/api/music-video/{mv_id}").status_code == 200
    with app.app_context():
        assert detach_project_media(db.session, pid) == 1
        db.session.delete(db.session.get(Project, pid))
        db.session.commit()
        assert db.session.get(Document, out_id) is not None
