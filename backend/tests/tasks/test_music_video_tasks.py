"""Phase 3 orchestration tests: state-machine chaining + per-clip cursor logic,
with GPU / plugin / plugin-HTTP all mocked. The real GPU generation + melt
assembly are exercised separately on a short song (Phase 4/5)."""
import contextlib
import os
from pathlib import Path

import pytest

try:
    from flask import Flask
    from backend.models import db, MusicVideo
    from backend.services.music_video_service import MusicVideoService
    import backend.tasks.music_video_tasks as mvt
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


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


class _SendRecorder:
    def __init__(self):
        self.calls = []

    def send_task(self, name, args=None, **kw):
        self.calls.append((name, tuple(args or ())))


@pytest.fixture
def sent(monkeypatch):
    """Capture celery.send_task across the dispatch sites."""
    rec = _SendRecorder()
    import backend.celery_app as ca
    monkeypatch.setattr(ca, "celery", rec, raising=False)
    return rec


def _mk(svc, **kw):
    base = dict(name="X", song_document_id=1, song_path="/tmp/song.wav",
                style_prompt="deep blue, loss", project_id=None)
    base.update(kw)
    return svc.create(**base)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


# --- analyzer ----------------------------------------------------------------

def test_analyzer_seeds_cut_plan_and_gates(app, sent, monkeypatch, tmp_path):
    svc = MusicVideoService(db.session)
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    mv = _mk(svc, song_path=str(song))
    svc.advance_if_predecessor(mv.id, expected_predecessor="draft")  # → analyzing

    monkeypatch.setattr(mvt, "ensure_plugin_running", lambda *a, **k: None)
    structure = {
        "tempo_bpm": 120.0, "duration_seconds": 10.0,
        "beat_times": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        "sections": [{"label": "a", "start": 0.0, "end": 10.0, "mean_energy": 1.0}],
    }
    monkeypatch.setattr(mvt.requests, "post", lambda *a, **k: _Resp(structure), raising=False)
    # Mock the Director so the analyzer doesn't make a real LLM call — assert it gets
    # wired and its per-cut prompts land on the clips.
    # NOTE: analyzer does a direct "from ... import _generate_storyline_and_prompts"
    # (not the public wrapper), so we must patch the internal it actually calls.
    import backend.services.music_video_director as director
    def _fake_director(style, plan, **kw):
        return {
            "prompts": [f"scene {c['index']}" for c in plan],
            "treatment": None,
            "shots": [],
        }
    monkeypatch.setattr(director, "_generate_storyline_and_prompts", _fake_director)

    mvt.run_analyzer(mv.id)
    db.session.refresh(mv)
    assert mv.cut_plan and len(mv.cut_plan) >= 1
    assert mv.clips and all(c["status"] == "pending" for c in mv.clips)
    # Director prompts are seeded per cut (distinct, not the global style).
    # The post-analyzer _ensure_distinct_and_energy_aware (always applied) guarantees the
    # global style suffix on the director-provided scenes (and would cue if they were dups).
    # Fake returns bare "scene N"; pipeline produces "scene N, {style}".
    style = mv.style_prompt or "deep blue, loss"
    assert [c["prompt"] for c in mv.clips] == [f"scene {c['index']}, {style}" for c in mv.clips]
    # advances to the USER GATE, dispatches nothing.
    assert mv.current_stage == "awaiting_approval"
    assert sent.calls == []


# --- clip generator (per-clip cursor) ----------------------------------------

def test_clip_generator_generates_one_then_tailcalls(app, sent, monkeypatch):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    mv.clips = [
        {"index": 0, "start": 0.0, "end": 2.0, "clip_path": None, "status": "pending"},
        {"index": 1, "start": 2.0, "end": 4.0, "clip_path": None, "status": "pending"},
    ]
    db.session.commit()

    monkeypatch.setattr(mvt, "ensure_plugin_running", lambda *a, **k: None)

    def fake_gen(mv_obj, clip):
        import copy
        clips = copy.deepcopy(mv_obj.clips)   # deep copy: real persistence semantics
        for c in clips:
            if c["index"] == clip["index"]:
                c["status"] = "done"
                c["clip_path"] = f"/tmp/clip_{clip['index']}.mp4"
        mv_obj.clips = clips
        db.session.commit()

    monkeypatch.setattr(mvt, "_generate_one_clip", fake_gen)

    mvt.run_clip_generator(mv.id)
    db.session.refresh(mv)
    assert mv.clips[0]["status"] == "done"
    assert mv.clips[1]["status"] == "pending"   # only ONE clip per invocation
    # tail-calls itself to continue
    assert ("music_video.run_clip_generator", (mv.id,)) in sent.calls


def test_clip_generator_all_done_advances_to_assembling(app, sent, monkeypatch, tmp_path):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    # both clips done AND files exist on disk (idempotency requires the file).
    f0 = tmp_path / "c0.mp4"; f0.write_bytes(b"x")
    f1 = tmp_path / "c1.mp4"; f1.write_bytes(b"x")
    mv.clips = [
        {"index": 0, "start": 0.0, "end": 2.0, "clip_path": str(f0), "status": "done"},
        {"index": 1, "start": 2.0, "end": 4.0, "clip_path": str(f1), "status": "done"},
    ]
    db.session.commit()

    mvt.run_clip_generator(mv.id)
    db.session.refresh(mv)
    assert mv.current_stage == "assembling"
    assert ("music_video.run_assembler", (mv.id,)) in sent.calls


def test_clip_generator_regenerates_when_file_missing(app, sent, monkeypatch):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    # status=done but file is GONE (crash mid-write) → must be re-generated.
    mv.clips = [{"index": 0, "start": 0.0, "end": 2.0,
                 "clip_path": "/tmp/does_not_exist_xyz.mp4", "status": "done"}]
    db.session.commit()

    monkeypatch.setattr(mvt, "ensure_plugin_running", lambda *a, **k: None)
    called = {}
    monkeypatch.setattr(mvt, "_generate_one_clip", lambda m, c: called.setdefault("idx", c["index"]))
    mvt.run_clip_generator(mv.id)
    assert called.get("idx") == 0   # treated as not-done, regenerated


def test_clip_generator_failure_fails_stage(app, sent, monkeypatch):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    mv.clips = [{"index": 0, "start": 0.0, "end": 2.0, "clip_path": None, "status": "pending"}]
    db.session.commit()

    monkeypatch.setattr(mvt, "ensure_plugin_running", lambda *a, **k: None)

    def boom(m, c):
        raise RuntimeError("comfy exploded")
    monkeypatch.setattr(mvt, "_generate_one_clip", boom)

    mvt.run_clip_generator(mv.id)
    db.session.refresh(mv)
    assert mv.status == "failed_generating"
    # no tail-call after a failure
    assert ("music_video.run_clip_generator", (mv.id,)) not in sent.calls


# --- assembler ---------------------------------------------------------------

def test_assembler_sets_output_and_completes(app, sent, monkeypatch, tmp_path):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "assembling"
    mv.status = "assembling"
    f0 = tmp_path / "c0.mp4"; f0.write_bytes(b"x")
    mv.clips = [{"index": 0, "start": 0.0, "end": 2.0, "clip_path": str(f0), "status": "done"}]
    mv.cut_plan = [{"index": 0, "start_s": 0.0, "end_s": 2.0, "energy": 1.0, "section_label": "a"}]
    db.session.commit()

    monkeypatch.setattr(mvt, "ensure_plugin_running", lambda *a, **k: None)
    captured = {}

    def fake_post(url, json=None, timeout=None, **k):
        captured["body"] = json
        return _Resp({"rendered_mp4": "/out/final.mp4", "documents": [{"id": 77}]})
    monkeypatch.setattr(mvt.requests, "post", fake_post, raising=False)

    mvt.run_assembler(mv.id)
    db.session.refresh(mv)
    assert mv.output_document_id == 77
    assert mv.current_stage == "complete"
    # The assembly contract: each clip's timeline slot equals its source length
    # (the obs-#721 sync guarantee) and the song is the audio track.
    clip0 = captured["body"]["arrangement"]["clips"][0]
    slot = clip0["timeline_end"] - clip0["timeline_start"]
    assert slot == pytest.approx(clip0["source_out"] - clip0["source_in"])
    assert captured["body"]["audio_path"] == mv.song_path
    assert captured["body"]["render_mp4"] is True


# --- render-tuning settings (moonwalk fix + cost knobs) ----------------------

def test_settings_exposes_tuning_defaults(app):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    s = mvt._settings(mv)
    # forward = the moonwalk fix; clip-halving slowdown OFF by default (stretch 2.0).
    assert s["fill_method"] == "forward"
    assert s["max_stretch"] == 2.0
    assert s["i2v_steps"] is None
    assert s["interpolation_multiplier"] == 2          # preserves prior implicit default
    assert mvt._max_clip_s(s) == pytest.approx(49 / 16)  # WAN native forward length (16fps)


def test_generate_one_clip_threads_steps_interp_and_fill_method(app, monkeypatch, tmp_path):
    """Zero-placebo: the UI knobs must actually reach the generation request and
    the fill call — not be silently dropped."""
    import backend.services.comfyui_image_generator as cig
    import backend.services.comfyui_video_generator as cvg
    import backend.services.job_operation_gate as jog

    svc = MusicVideoService(db.session)
    mv = _mk(svc, settings={
        "i2v_engine": "wan", "i2v_steps": 30, "interpolation_multiplier": 4,
        "fill_method": "forward", "max_stretch": 2.5,
    })
    clip = {"index": 0, "start": 0.0, "end": 2.0, "clip_path": None, "status": "pending",
            "prompt": "a distinct shot"}
    mv.clips = [clip]
    db.session.commit()

    class _Img:
        def __init__(self, **kw):
            # Production now constructs ComfyUIImageGenerator with lora_strength/
            # flux_unet/flux_t5/flux_clip/flux_vae kwargs; tolerate any of them.
            pass
        def generate_image(self, **kw):
            captured["still_prompt"] = kw.get("prompt")
            p = tmp_path / "still.png"; p.write_bytes(b"x"); return str(p)
    monkeypatch.setattr(cig, "ComfyUIImageGenerator", _Img)

    wan_out = tmp_path / "wan.mp4"; wan_out.write_bytes(b"x")
    captured = {}

    class _Result:
        success = True
        video_path = str(wan_out)
        error = None

    class _Gen:
        def generate_video(self, req):
            captured["req"] = req
            return _Result()
    monkeypatch.setattr(cvg, "get_video_generator", lambda: _Gen())

    class _Gate:
        @contextlib.contextmanager
        def gpu_exclusive(self, *a, **k):
            yield
    monkeypatch.setattr(jog, "get_gate", lambda: _Gate())
    monkeypatch.setattr(mvt, "_comfyui_free_vram", lambda: None)

    fill_kw = {}

    def fake_fill(src, target_s, out, **kw):
        fill_kw.update(kw)
        Path(out).write_bytes(b"x")
        return out
    monkeypatch.setattr(mvt, "fill_clip_to_duration", fake_fill)

    mvt._generate_one_clip(mv, clip)

    req = captured["req"]
    assert req.model == "wan22-14b-i2v"
    assert req.num_inference_steps == 30        # steps override reached the request
    assert req.interpolation_multiplier == 4    # RIFE knob reached the request
    assert req.prompt == "a distinct shot"       # per-cut Director prompt → WAN
    assert captured["still_prompt"] == "a distinct shot"  # per-cut Director prompt → FLUX
    assert fill_kw["method"] == "forward"        # fill method reached the fill
    assert fill_kw["max_stretch"] == 2.5


def test_clip_profile_reads_the_registry_for_declared_models(app):
    svc = MusicVideoService(db.session)
    wan = mvt._clip_profile(mvt._settings(_mk(svc)))
    assert (wan["model"], wan["fps"], wan["min_frames"], wan["max_frames"], wan["audio_out"]) == \
        ("wan22-14b-i2v", 16, 17, 49, False)
    s = mvt._settings(_mk(svc, settings={"i2v_model": "minimax-h3-int8"}))
    h3 = mvt._clip_profile(s)
    assert (h3["fps"], h3["min_frames"], h3["max_frames"], h3["audio_out"]) == (24, 72, 175, True)
    assert mvt._max_clip_s(s) == pytest.approx(175 / 24)
    cog = mvt._clip_profile(mvt._settings(_mk(svc, settings={"i2v_engine": "cogvideox"})))
    assert (cog["model"], cog["fps"], cog["max_frames"]) == ("cogvideox-5b-i2v", 7, 25)


def test_generate_one_clip_on_a_native_audio_model_anchors_the_song_slice(app, monkeypatch, tmp_path):
    import backend.services.comfyui_image_generator as cig
    import backend.services.comfyui_video_generator as cvg
    import backend.services.job_operation_gate as jog

    svc = MusicVideoService(db.session)
    song = tmp_path / "song.wav"; song.write_bytes(b"RIFF")
    mv = _mk(svc, settings={"i2v_model": "minimax-h3-int8", "interpolation_multiplier": 2})
    mv.song_path = str(song)
    clip = {"index": 2, "start": 12.0, "end": 20.0, "clip_path": None, "status": "pending",
            "prompt": "the singer walks toward the camera"}
    mv.clips = [clip]
    db.session.commit()

    captured = {}

    class _Img:
        def __init__(self, **kw):
            pass
        def generate_image(self, **kw):
            p = tmp_path / "still.png"; p.write_bytes(b"x"); return str(p)
    monkeypatch.setattr(cig, "ComfyUIImageGenerator", _Img)
    out = tmp_path / "h3.mp4"; out.write_bytes(b"x")

    class _Result:
        success = True
        video_path = str(out)
        error = None

    class _Gen:
        def generate_video(self, req):
            captured["req"] = req
            return _Result()
    monkeypatch.setattr(cvg, "get_video_generator", lambda: _Gen())

    class _Gate:
        @contextlib.contextmanager
        def gpu_exclusive(self, *a, **k):
            yield
    monkeypatch.setattr(jog, "get_gate", lambda: _Gate())
    monkeypatch.setattr(mvt, "_comfyui_free_vram", lambda: None)
    monkeypatch.setattr(mvt, "fill_clip_to_duration", lambda src, target_s, o, **kw: (Path(o).write_bytes(b"x"), o)[1])

    mvt._generate_one_clip(mv, clip)

    req = captured["req"]
    assert req.model == "minimax-h3-int8" and req.fps == 24
    assert req.duration_frames == 192              # 8 s cut on the 17k+5 grid
    assert req.interpolation_multiplier == 1        # the model's own 24 fps, no RIFE
    assert req.prompt.startswith("For the target video, at 0.00 seconds")
    assert "lands on the beats of <Audio 1>" in req.prompt
    assert req.guides == [{"kind": "audio", "path": str(song), "frame_idx": 0, "seek_s": 12.0, "duration_s": 8.0}]
    assert req.h3_intent["mode"] == "i2va"
