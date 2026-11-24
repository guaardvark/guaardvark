"""get_generation_status follows a music video and a Film Crew production.

The tool runs as it does over MCP (transport "mcp"), reading the backend's own
routes; only that HTTP seam (image_tools._http_json) is replaced, with the
dicts GET /api/music-video/<id> and GET /api/production/<id> return.
"""
import pytest

import backend.tools.image_tools as image_tools
from backend.services import pipeline_status as ps
from backend.tools.image_tools import GenerationStatusTool
from backend.utils.backend_http import BackendError


def _tool():
    t = GenerationStatusTool()
    t.set_context({"transport": "mcp"})
    return t


def _serve(monkeypatch, routes):
    calls = []

    def fake(method, path, *a, **k):
        calls.append(path)
        if path in routes:
            return routes[path]
        if path.startswith(("/api/music-video/", "/api/production/")):
            # What backend_http.http_json raises for these routes' 404 {"error": "not_found"}.
            raise BackendError("http", "not_found", status=404, body={"error": "not_found"})
        raise RuntimeError("404 not found")

    monkeypatch.setattr(image_tools, "_http_json", fake)
    return calls


MV_RUNNING = {
    "id": 12, "status": "generating", "current_stage": "generating",
    "cut_count": 8, "clip_count": 8, "clips_done": 3, "clips_needing_review": 0,
    "output_document_id": None, "error_blob": None,
    "progress": {"overall_pct": 41.5, "eta_seconds": 610,
                 "stages": [{"key": "generating", "state": "active", "label": "Generating clips"}]},
}


def test_a_running_music_video_reports_stage_progress_and_clips(monkeypatch):
    calls = _serve(monkeypatch, {"/api/music-video/12": MV_RUNNING})
    result = _tool().execute(batch_id="music_video:12")
    assert result.success, result.error
    assert calls == ["/api/music-video/12"]
    out = result.output
    assert out.splitlines()[0] == "Music video music_video:12: running (3/8 finished)"
    assert "Progress: 41.5% — stage: generating" in out
    assert "About 10 min 10 s left (estimate)" in out
    assert "Still running" in out
    assert result.metadata["stage"] == "generating"


def test_a_music_video_at_the_approval_gate_says_it_waits_for_a_person(monkeypatch):
    _serve(monkeypatch, {"/api/music-video/12": {**MV_RUNNING, "status": "awaiting_approval",
                                                  "current_stage": "awaiting_approval", "clips_done": 0}})
    out = _tool().execute(batch_id="mv:12").output
    assert ": waiting" in out.splitlines()[0]
    assert "Waiting for the cut plan to be approved" in out
    assert "Still running" not in out


def test_a_failed_music_video_names_the_kind_and_next_step(monkeypatch):
    _serve(monkeypatch, {"/api/music-video/12": {
        **MV_RUNNING, "status": "failed_generating",
        "error_blob": {"stage": "generating", "error": "ComfyUI ran out of GPU memory", "error_kind": "oom"},
    }})
    result = _tool().execute(batch_id="music_video:12")
    assert result.metadata["failure"]["kind"] == "oom"
    assert "Error: Out of GPU memory: generating: ComfyUI ran out of GPU memory Next:" in result.output


def test_a_finished_music_video_links_its_file(monkeypatch):
    _serve(monkeypatch, {"/api/music-video/12": {**MV_RUNNING, "status": "complete",
                                                  "current_stage": "complete", "clips_done": 8,
                                                  "output_document_id": 77}})
    out = _tool().execute(batch_id="music_video:12").output
    assert "File: /api/files/document/77/download" in out


def test_a_production_reports_its_stage_and_clips(monkeypatch):
    shots = [{"storyboard_image_path": "a.png", "video_clip_path": "a.mp4"},
             {"storyboard_image_path": "b.png", "video_clip_path": None}]
    _serve(monkeypatch, {"/api/production/5": {"id": 5, "status": "rendering",
                                               "current_stage": "rendering", "shots": shots}})
    result = _tool().execute(batch_id="production:5")
    assert result.success, result.error
    out = result.output
    assert out.splitlines()[0] == "Film Crew production production:5: running (1/2 finished)"
    assert "Progress: 85% — stage: rendering" in out


def test_a_bare_number_asks_for_the_kind(monkeypatch):
    _serve(monkeypatch, {})
    result = _tool().execute(batch_id="12")
    assert not result.success
    assert "music_video:12 or production:12" in result.error


@pytest.mark.parametrize("batch_id", ["music_video:999", "production:999"])
def test_an_unknown_pipeline_id_is_not_found(monkeypatch, batch_id):
    _serve(monkeypatch, {})
    result = _tool().execute(batch_id=batch_id)
    assert not result.success
    assert result.error.startswith("No image, video, audio, bulk CSV, style analysis, music video or Film Crew job named")
    assert "did not answer" not in result.error


@pytest.mark.parametrize("text,parsed", [
    ("music_video:3", ("music_video", 3)), ("MV-3", ("music_video", 3)),
    ("production:4", ("production", 4)), ("film_crew:4", ("production", 4)),
    ("ImageBatch_1", None), ("4", None),
])
def test_ids(text, parsed):
    assert ps.parse_pipeline_id(text) == parsed
