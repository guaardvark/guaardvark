"""scripts/video_smoke.py: what it plans from the real capability records, and
a run against a backend faked at the requests.Session seam (the four routes
it calls). The clips it downloads are real H.264 files, checked by the real
frame checker."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import video_render_limits as rl
from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, model_capabilities

ROOT = Path(__file__).resolve().parents[3]


def _smoke():
    spec = importlib.util.spec_from_file_location("video_smoke", ROOT / "scripts" / "video_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(ready=lambda mid: True):
    """/api/batch-video/models rows for every registry entry, as the route builds them."""
    return [{"id": mid, "is_ready": ready(mid), "missing_files": [] if ready(mid) else [f"{mid}.gguf"],
             "capabilities": model_capabilities(mid)} for mid in VIDEO_MODEL_REGISTRY]


# ── The plan ─────────────────────────────────────────────────────────────────

def test_every_ready_t2v_or_i2v_model_gets_one_clip_per_mode():
    runs, skipped = _smoke().plan(_rows())
    planned = {(r["model"], r["mode"]) for r in runs}
    for mid in VIDEO_MODEL_REGISTRY:
        modes = model_capabilities(mid).get("modes") or []
        for mode in ("t2v", "i2v"):
            assert ((mid, mode) in planned) == (mode in modes), (mid, mode)
    assert not skipped
    # Reference-only and audio entries have neither mode.
    assert not any(r["model"] in ("minimax-h3-ref2va-int8", "minimax-music3-int8") for r in runs)


@pytest.mark.parametrize("model,frames,size,steps", [
    ("wan22-14b", 17, (832, 480), 20),     # 1 s at 16 fps on 4n+1
    ("wan22-5b", 25, (832, 480), 20),      # 1 s at 24 fps
    ("ltx23-distilled-fp8", 17, (832, 480), 8),
    ("hunyuan-t2v", 25, (848, 480), 20),   # 16 px grid
    ("cogvideox-5b", 9, (672, 384), 50),   # its declared 16 GB canvas
    ("minimax-h3-int8", 73, (864, 480), 20),  # min_clip_s 3 s = 72, up onto 17k+5
])
def test_smallest_clip(model, frames, size, steps):
    clip = _smoke().smallest_clip(model_capabilities(model))
    assert (clip["duration_frames"], (clip["width"], clip["height"]), clip["num_inference_steps"]) == (frames, size, steps)
    assert clip["frame_rule"] == model_capabilities(model)["frame_rule"]


def test_every_planned_clip_is_one_the_render_keeps_as_asked():
    runs, _ = _smoke().plan(_rows())
    for r in runs:
        mid = r["model"]
        assert rl.resolve_frames(mid, r["duration_frames"]) == r["duration_frames"], r
        assert rl.resolve_frames(mid, r["duration_frames"], strict=True) == r["duration_frames"], r
        assert rl.resolve_canvas(mid, r["width"], r["height"], r["duration_frames"]) == (r["width"], r["height"]), r
        assert rl.resolve_steps(mid, r["num_inference_steps"], strict=True) == r["num_inference_steps"], r


def test_filters_and_skips():
    smoke = _smoke()
    runs, skipped = smoke.plan(_rows(ready=lambda mid: mid != "wan22-14b"),
                               only=["wan22-14b", "wan22-5b", "minimax-h3-ref2va-int8", "nope"], mode="i2v")
    assert [(r["model"], r["mode"]) for r in runs] == [("wan22-5b", "i2v")]
    assert {s["model"]: s["reason"] for s in skipped} == {
        "wan22-14b": "no i2v mode",
        "minimax-h3-ref2va-int8": "no i2v mode",
        "nope": "not in /api/batch-video/models",
    }
    runs, skipped = smoke.plan(_rows(ready=lambda mid: mid != "wan22-14b"), only=["wan22-14b"])
    assert not runs and skipped == [{"model": "wan22-14b", "reason": "not ready (wan22-14b.gguf)"}]


def test_requests_are_fixed_and_plain():
    smoke = _smoke()
    run = {"model": "wan22-5b", "mode": "i2v", **smoke.smallest_clip(model_capabilities("wan22-5b"))}
    route, body = smoke.request_body(run, "/box/start.png")
    assert route == "/batch-video/generate/image" and body["image_paths"] == ["/box/start.png"]
    assert (body["seed"], body["prompt"]) == (smoke.SEED, smoke.PROMPT)
    assert (body["enhance_prompt"], body["upscale"], body["interpolation_multiplier"]) == ("false", "false", 1)
    route, body = smoke.request_body({**run, "mode": "t2v"}, None)
    assert route == "/batch-video/generate/text" and body["prompts"] == [smoke.PROMPT]


# ── A run against a faked backend ────────────────────────────────────────────

def _clip(kind: str) -> bytes:
    import tempfile

    from backend.tests.fixtures import video_quality_clips as clips

    with tempfile.TemporaryDirectory() as d:
        return getattr(clips, kind)(Path(d) / "c.mp4").read_bytes()


class _Backend:
    """GET /batch-video/models, POST /generate/*, GET /status, GET /video, POST /cancel."""

    def __init__(self, models, outcomes, interrupt=False):
        self.models, self.outcomes, self.interrupt = models, dict(outcomes), interrupt
        self.posts, self.cancelled, self.queued = [], [], {}

    def __call__(self):
        return self

    @staticmethod
    def _r(payload=None, content=b"", ok=True):
        return SimpleNamespace(json=lambda: payload, content=content, ok=ok)

    def get(self, url, timeout=None):
        if url.endswith("/batch-video/models"):
            return self._r({"success": True, "data": {"models": self.models}})
        if "/status/" in url:
            if self.interrupt:
                raise KeyboardInterrupt
            model = self.queued[url.rsplit("/", 1)[1]]
            outcome = self.outcomes[model]
            if outcome.get("clip"):
                return self._r({"data": {"status": "completed", "results": [
                    {"success": True, "video_path": "item/videos/clip.mp4"}]}})
            return self._r({"data": {"status": "error", "results": [
                {"success": False, "error": outcome["error"], "error_kind": outcome.get("kind")}]}})
        if "/video/" in url:
            model = self.queued[url.split("/video/", 1)[1].split("/", 1)[0]]
            return self._r(content=self.outcomes[model]["clip"])
        raise AssertionError(url)

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        if url.endswith("/cancel"):
            self.cancelled.append(url)
            return self._r({"success": True})
        outcome = self.outcomes[json["model"]]
        if outcome.get("refuse"):
            return self._r({"success": False, "error": {"code": "BAD_REQUEST", "message": outcome["refuse"],
                                                         "details": {"failure": {"kind": "model_not_installed"}}}})
        batch_id = f"b{len(self.queued) + 1}"
        self.queued[batch_id] = json["model"]
        return self._r({"success": True, "data": {"batch_id": batch_id}})


def _models(*ids):
    return [{"id": mid, "is_ready": True, "capabilities": model_capabilities(mid)} for mid in ids]


@pytest.fixture
def fast(monkeypatch):
    smoke = _smoke()
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)
    monkeypatch.delenv("GUAARDVARK_API", raising=False)
    monkeypatch.delenv("GUAARDVARK_URL", raising=False)
    return smoke


def test_dry_run_queues_nothing(fast, monkeypatch, capsys, tmp_path):
    import requests

    backend = _Backend(_models("wan22-5b"), {})
    monkeypatch.setattr(requests, "Session", backend)
    assert fast.main(["--dry-run", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert backend.posts == [] and not list(tmp_path.iterdir())
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert [x["route"] for x in lines] == ["/batch-video/generate/text", "/batch-video/generate/image"]
    assert "2 clip(s); nothing was queued." in out


def test_a_full_run_checks_each_clip_and_writes_the_table(fast, monkeypatch, capsys, tmp_path):
    import requests

    backend = _Backend(_models("wan22-14b", "ltx23-distilled-fp8", "hunyuan-t2v"), {
        "wan22-14b": {"clip": _clip("clean")},
        "ltx23-distilled-fp8": {"clip": _clip("black_frames")},
        "hunyuan-t2v": {"error": "ComfyUI ran out of GPU memory in KSampler (node 25): Allocation", "kind": "oom"},
    })
    monkeypatch.setattr(requests, "Session", backend)
    assert fast.main(["--keep-going", "--mode", "t2v", "--out", str(tmp_path)]) == 1
    saved = json.loads((tmp_path / "results.json").read_text())
    by_model = {r["model"]: r for r in saved["runs"]}
    assert saved["passed"] == 0 and saved["total"] == 3 and saved["seed"] == fast.SEED
    # The fixture clip is 320x192 and 33 frames against the 832x480 and 17 asked
    # for: the checker compares the file with the request, not with itself.
    assert by_model["wan22-14b"]["check"]["readable"]
    assert by_model["wan22-14b"]["check"]["flags"] == ["wrong_size", "wrong_frame_count"]
    assert "black_frames" in by_model["ltx23-distilled-fp8"]["check"]["flags"]
    assert by_model["hunyuan-t2v"]["error_kind"] == "oom" and "clip" not in by_model["hunyuan-t2v"]
    # Only the render routes: nothing is installed or downloaded.
    assert all(url.endswith(("/generate/text", "/generate/image", "/cancel")) for url, _ in backend.posts)
    table = capsys.readouterr().out
    assert "hunyuan-t2v" in table and "oom" in table and "black_frames" in table


def test_the_fixture_clip_passes_when_it_is_the_size_asked(fast, monkeypatch, tmp_path):
    import requests

    from backend.tests.fixtures import video_quality_clips as clips

    monkeypatch.setattr(fast, "smallest_clip", lambda caps: {
        "duration_frames": clips.FRAMES, "fps": clips.FPS, "width": clips.WIDTH, "height": clips.HEIGHT,
        "num_inference_steps": 20, "frame_rule": "4n+1"})
    backend = _Backend(_models("wan22-14b"), {"wan22-14b": {"clip": _clip("clean")}})
    monkeypatch.setattr(requests, "Session", backend)
    assert fast.main(["--out", str(tmp_path)]) == 0
    run = json.loads((tmp_path / "results.json").read_text())["runs"][0]
    assert fast.passed(run) and run["check"]["frames"] == clips.FRAMES


def test_stops_at_the_first_failure_without_keep_going(fast, monkeypatch, tmp_path):
    import requests

    backend = _Backend(_models("wan22-14b", "wan22-5b"), {
        "wan22-14b": {"refuse": "Wan 2.2 14B is not installed."}, "wan22-5b": {"clip": _clip("clean")}})
    monkeypatch.setattr(requests, "Session", backend)
    assert fast.main(["--mode", "t2v", "--out", str(tmp_path)]) == 1
    runs = json.loads((tmp_path / "results.json").read_text())["runs"]
    assert [(r["model"], r["status"], r["error_kind"]) for r in runs] == [
        ("wan22-14b", "refused", "model_not_installed")]


def test_i2v_gets_the_fixed_start_frame(fast, monkeypatch, tmp_path):
    import requests

    from PIL import Image

    backend = _Backend(_models("wan22-5b"), {"wan22-5b": {"clip": _clip("clean")}})
    monkeypatch.setattr(requests, "Session", backend)
    fast.main(["--mode", "i2v", "--out", str(tmp_path)])
    route, body = backend.posts[0]
    assert route.endswith("/batch-video/generate/image")
    start = Path(body["image_paths"][0])
    assert start == tmp_path / "start_frame.png" and Image.open(start).size == (832, 480)


def test_ctrl_c_cancels_the_clip_in_flight(fast, monkeypatch, tmp_path):
    import requests

    backend = _Backend(_models("wan22-5b"), {"wan22-5b": {"clip": b""}}, interrupt=True)
    monkeypatch.setattr(requests, "Session", backend)
    assert fast.main(["--mode", "t2v", "--out", str(tmp_path)]) == 130
    assert backend.cancelled == ["http://127.0.0.1:5000/api/batch-video/batch/b1/cancel"]
    assert json.loads((tmp_path / "results.json").read_text())["runs"][0]["status"] == "cancelled"


def test_a_clip_past_its_timeout_is_cancelled(fast, monkeypatch, tmp_path):
    import requests

    class _Hung(_Backend):
        def get(self, url, timeout=None):
            if "/status/" in url:
                return self._r({"data": {"status": "running", "results": []}})
            return super().get(url, timeout)

    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(fast.time, "time", lambda: next(clock))
    backend = _Hung(_models("wan22-5b"), {"wan22-5b": {}})
    monkeypatch.setattr(requests, "Session", backend)
    assert fast.main(["--mode", "t2v", "--timeout", "250", "--out", str(tmp_path)]) == 1
    run = json.loads((tmp_path / "results.json").read_text())["runs"][0]
    assert run["status"] == "timeout" and backend.cancelled
