"""Video style job records on disk, their Jobs-page adapter, and the API routes.

The API is mounted on a bare Flask app; the analyzer stages are stubbed so
no ffmpeg runs. Media-input resolution is exercised for real on a file under
a temporary outputs folder.
"""
import os
import sys
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.video_style import jobs  # noqa: E402


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    import backend.config as config

    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(out))
    return out


def test_job_record_round_trip(outputs):
    job = jobs.new_job("/tmp/x.mp4", name="Clip")
    assert job["status"] == "pending" and job["stage"] == "probing"
    assert jobs.load_job(job["id"])["name"] == "Clip"
    jobs.update(job["id"], status="running", progress=40, message="measure")
    assert jobs.load_job(job["id"])["progress"] == 40
    jobs.update(job["id"], status="complete")
    assert jobs.load_job(job["id"])["finished_at"]
    assert [j["id"] for j in jobs.list_jobs()] == [job["id"]]
    assert jobs.load_job("../etc") is None


def test_cancel_sets_the_flag_once(outputs):
    job = jobs.new_job("/tmp/x.mp4")
    assert jobs.request_cancel(job["id"]) is True
    assert jobs.cancel_requested(job["id"]) is True
    assert jobs.request_cancel(job["id"]) is False


def test_frame_url_is_under_outputs(outputs):
    inside = outputs / "video_style" / "abc" / "frames" / "shot0000_1.jpg"
    assert jobs.public_frame_url(str(inside)) == "/api/outputs/video_style/abc/frames/shot0000_1.jpg"
    assert jobs.public_frame_url("/elsewhere/x.jpg") is None


def test_jobs_page_adapter(outputs):
    from backend.services.job_registry import adapt_video_analysis
    from backend.services.job_types import JobKind, JobStatus

    job = jobs.new_job("/tmp/x.mp4", name="Clip")
    jobs.update(job["id"], status="running", progress=55, stage="measure")
    j = adapt_video_analysis(jobs.load_job(job["id"]))
    assert j.kind is JobKind.VIDEO_ANALYSIS and j.status is JobStatus.RUNNING
    assert j.progress == 55 and j.cancellable and j.label.startswith("Style analysis: Clip")
    jobs.update(job["id"], status="failed", error="boom")
    j = adapt_video_analysis(jobs.load_job(job["id"]))
    assert j.status is JobStatus.FAILED and j.error_message == "boom" and j.finished_at


def test_stage_runner_advances_and_stops_on_cancel(outputs, tmp_path):
    from backend.tasks import video_style_tasks

    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    job = jobs.new_job(str(video), name="Clip")
    with patch.object(video_style_tasks.analyze, "new_analysis", return_value={"shots": [], "status": "pending"}), \
            patch.object(video_style_tasks.analyze, "load_analysis", return_value={"shots": [{"i": 0}], "status": "x"}), \
            patch.object(video_style_tasks.analyze, "run_stage", side_effect=lambda a, s, progress=None: a), \
            patch.object(video_style_tasks, "emit_progress_event"):
        assert video_style_tasks.run_stage(job["id"], "probing") == "shots"
        assert jobs.load_job(job["id"])["stage"] == "shots"
        assert video_style_tasks.run_stage(job["id"], "probing") is None  # wrong stage: no-op
        jobs.request_cancel(job["id"])
        assert video_style_tasks.run_stage(job["id"], "shots") is None
        assert jobs.load_job(job["id"])["status"] == "cancelled"


def test_stage_runner_records_a_failure(outputs, tmp_path):
    from backend.tasks import video_style_tasks

    job = jobs.new_job(str(tmp_path / "v.mp4"), name="Clip")
    with patch.object(video_style_tasks.analyze, "new_analysis", return_value={"shots": []}), \
            patch.object(video_style_tasks.analyze, "run_stage", side_effect=RuntimeError("ffprobe failed")), \
            patch.object(video_style_tasks, "emit_progress_event"):
        assert video_style_tasks.run_stage(job["id"], "probing") is None
    row = jobs.load_job(job["id"])
    assert row["status"] == "failed" and "ffprobe" in row["error"]


@pytest.fixture
def api(outputs):
    from backend.api.video_style_api import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def test_analyze_route_refuses_what_is_not_a_video(api, outputs):
    assert api.post("/api/video-style/analyze", json={}).status_code == 400
    note = outputs / "videos" / "notes.txt"
    note.parent.mkdir(parents=True)
    note.write_text("x")
    r = api.post("/api/video-style/analyze", json={"path": str(note)})
    assert r.status_code == 400 and "not a video" in r.get_json()["error"]
    r = api.post("/api/video-style/analyze", json={"path": "/etc/passwd"})
    assert r.status_code == 400


def test_analyze_route_queues_a_job_and_reports_when_no_worker(api, outputs):
    clip = outputs / "videos" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"0")
    with patch("backend.tasks.video_style_tasks.dispatch", side_effect=RuntimeError("no broker")):
        r = api.post("/api/video-style/analyze", json={"path": str(clip), "name": "Clip"})
    body = r.get_json()
    assert r.status_code == 202 and body["dispatched"] is False and "no broker" in body["warning"]
    assert api.get(f"/api/video-style/{body['id']}").get_json()["name"] == "Clip"
    assert api.get("/api/video-style").get_json()["jobs"][0]["id"] == body["id"]
    assert api.get("/api/video-style/nope").status_code == 404


def test_recipe_route_needs_a_finished_profile(api, outputs):
    job = jobs.new_job(str(outputs / "x.mp4"), name="Clip")
    assert api.post(f"/api/video-style/{job['id']}/recipe", json={}).status_code == 409
    jobs.update(job["id"], status="complete", stage="complete")
    assert api.post(f"/api/video-style/{job['id']}/recipe", json={}).status_code == 409  # no analysis.json


def test_compare_route_names_missing_profiles(api, outputs):
    r = api.post("/api/video-style/compare", json={"a": "nope", "b": "nada"})
    assert r.status_code == 400 and "nope" in r.get_json()["error"]
