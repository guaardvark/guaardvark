"""get_generation_status follows a style analysis from analyze_video_style.

Job records live in a temporary outputs folder. Over MCP the tool reads
GET /api/video-style/<id>; that HTTP seam (image_tools._http_json) is served
by the real video_style_api blueprint on a bare Flask app, so the route and
the tool are exercised together. In chat the tool reads the job folder itself.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

import backend.tools.image_tools as image_tools  # noqa: E402
from backend.services.video_style import jobs  # noqa: E402
from backend.tests.services.test_video_style_recipe import _analysis  # noqa: E402
from backend.tools.image_tools import GenerationStatusTool  # noqa: E402
from backend.utils.backend_http import BackendError  # noqa: E402


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    import backend.config as config

    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(out))
    return out


@pytest.fixture
def served(outputs, monkeypatch):
    """The backend's video-style routes behind the tool's HTTP seam; returns the paths asked for."""
    from backend.api.video_style_api import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    client = app.test_client()
    calls = []

    def fake(method, path, *a, **k):
        calls.append(path)
        if not path.startswith("/api/video-style/"):
            raise RuntimeError("404 not found")
        r = client.open(path, method=method)
        if r.status_code >= 400:
            raise BackendError("http", str(r.get_json()), status=r.status_code, body=r.get_json())
        return r.get_json()

    monkeypatch.setattr(image_tools, "_http_json", fake)
    return calls


def _tool(transport):
    t = GenerationStatusTool()
    t.set_context({"transport": transport} if transport else {})
    return t


def _finish(job):
    (jobs.job_dir(job["id"]) / "analysis.json").write_text(json.dumps(_analysis()))
    jobs.update(job["id"], status="complete", stage="complete", progress=100, message="6 shots measured")


@pytest.mark.parametrize("transport", ["mcp", None])
def test_a_style_job_is_followed_from_queued_to_its_profile(served, outputs, transport):
    job = jobs.new_job(str(outputs / "clip.mp4"), name="Clip")
    jid = job["id"]
    tool = _tool(transport)

    queued = tool.execute(batch_id=jid)
    assert queued.success, queued.error
    assert queued.output.splitlines()[0] == f"Style analysis job {jid}: pending"
    assert "Still running" in queued.output

    jobs.update(jid, status="running", stage="measure", progress=40, message="measure")
    running = tool.execute(batch_id=jid)
    assert running.output.splitlines()[0] == f"Style analysis job {jid}: running"
    assert "Progress: 40% — stage: measure" in running.output

    _finish(job)
    done = tool.execute(batch_id=jid)
    assert done.success, done.error
    assert done.output.splitlines()[0] == f"Style analysis job {jid}: complete"
    assert "6 shots, average 2.9 s" in done.output
    assert "Cut briskly" in done.output
    assert f"POST /api/video-style/{jid}/recipe" in done.output
    assert "Still running" not in done.output
    assert done.metadata["profile"]["cutting"]["shot_count"] == 6
    assert done.metadata["style_clause"]
    assert served == ([f"/api/video-style/{jid}"] * 3 if transport == "mcp" else [])


@pytest.mark.parametrize("transport", ["mcp", None])
def test_a_cancelled_style_job_says_so(served, outputs, transport):
    job = jobs.new_job(str(outputs / "clip.mp4"), name="Clip")
    assert jobs.request_cancel(job["id"])
    result = _tool(transport).execute(batch_id=job["id"])
    assert result.success, result.error
    assert result.output.splitlines()[0] == f"Style analysis job {job['id']}: cancelled"
    assert "Cancelled before it finished" in result.output
    assert "Still running" not in result.output


@pytest.mark.parametrize("transport", ["mcp", None])
def test_a_failed_style_job_reports_its_error(served, outputs, transport):
    job = jobs.new_job(str(outputs / "clip.mp4"), name="Clip")
    jobs.update(job["id"], status="failed", error="ffprobe failed: no video stream", message="probing failed")
    result = _tool(transport).execute(batch_id=job["id"])
    assert result.output.splitlines()[0] == f"Style analysis job {job['id']}: failed"
    assert "Error: ffprobe failed: no video stream" in result.output


@pytest.mark.parametrize("transport", ["mcp", None])
def test_an_unknown_style_id_is_not_found(served, outputs, transport):
    result = _tool(transport).execute(batch_id="0123456789ab")
    assert not result.success
    assert result.error.startswith("No image, video, audio, bulk CSV, style analysis, music video or Film Crew job")
    assert "did not answer" not in result.error


def test_an_unreachable_backend_is_not_called_unknown(outputs, monkeypatch):
    def down(method, path, *a, **k):
        raise BackendError("unreachable", "connection refused")

    monkeypatch.setattr(image_tools, "_http_json", down)
    result = _tool("mcp").execute(batch_id="0123456789ab")
    assert not result.success
    assert "did not answer" in result.error


def test_analyze_video_style_points_at_get_generation_status(outputs):
    from backend.tools.video_style_tools import AnalyzeVideoStyleTool

    clip = outputs / "videos" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"0")
    with patch("backend.tasks.video_style_tasks.dispatch") as dispatch:
        result = AnalyzeVideoStyleTool().execute(video=str(clip), name="Clip")
    assert result.success, result.error
    jid = result.metadata["job_id"]
    dispatch.assert_called_once_with(jid)
    assert f'get_generation_status(batch_id="{jid}")' in result.output
    assert result.metadata["status_tool"] == "get_generation_status"
    assert GenerationStatusTool._STYLE_JOB_ID.match(jid)
