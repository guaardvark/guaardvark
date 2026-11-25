"""Phase 3 orchestration tests: state-machine chaining + per-clip cursor logic,
with GPU / plugin / plugin-HTTP all mocked. The real GPU generation + melt
assembly are exercised separately on a short song (Phase 4/5)."""
import contextlib
import os
import traceback
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


@pytest.fixture(autouse=True)
def stage_plugins(monkeypatch):
    """Records the plugins each stage asks for and starts none.

    The stage tasks import plugin_bridge.ensure_plugins_for_stage at call time.
    The real one builds the PluginManager, whose boot restores and kills plugin
    processes on this machine, then starts the stage's plugins (Ollama, the
    video editor, ComfyUI)."""
    calls = []
    monkeypatch.setattr("backend.services.plugin_bridge.ensure_plugins_for_stage",
                        lambda context, stage, **kw: calls.append((context, stage)))
    return calls


@pytest.fixture(autouse=True)
def _keyframe_default(monkeypatch):
    """A new project's keyframe model without probing this machine.

    The real default asks the image generator which weights are installed,
    which reads ComfyUI's version through the PluginManager."""
    monkeypatch.setattr("backend.services.music_video_service.default_keyframe_model",
                        lambda: "flux-schnell")


@pytest.fixture
def no_plugin_manager(monkeypatch):
    """Records, and refuses, any reach for the PluginManager itself."""
    reached = []

    def refuse(*args, **kwargs):
        # The callers, so a failing "== []" says who reached for it.
        reached.append("".join(traceback.format_stack(limit=14)))
        raise AssertionError("the PluginManager was reached from a unit test")

    monkeypatch.setattr("backend.plugins.plugin_manager.get_plugin_manager", refuse)
    return reached


@pytest.fixture(autouse=True)
def _resolve_i2v(monkeypatch):
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda role, explicit=None, surface=None: (explicit or "wan22-5b", None),
    )


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

def test_analyzer_seeds_cut_plan_and_gates(app, sent, monkeypatch, tmp_path, stage_plugins, no_plugin_manager):
    svc = MusicVideoService(db.session)
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    mv = _mk(svc, song_path=str(song))
    svc.advance_if_predecessor(mv.id, expected_predecessor="draft")  # → analyzing

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
    # director_service binds the generator at import time, so patch its copy too or
    # the mock silently misses whenever another test imported the engine first.
    import backend.services.director_service as director_service
    monkeypatch.setattr(director_service, "_generate_storyline_and_prompts", _fake_director)
    monkeypatch.setattr(director_service, "_resolve_model", lambda m: m)  # no Ollama lookup

    mvt.run_analyzer(mv.id)
    db.session.refresh(mv)
    assert mv.status != "failed_analyzing", mv.error_blob
    assert stage_plugins == [("music-video", "analyzing")]
    assert no_plugin_manager == [], "".join(no_plugin_manager)
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


def test_analyzer_stores_the_guarded_prompts_not_the_raw_shot_text(
        app, sent, monkeypatch, tmp_path, stage_plugins, no_plugin_manager):
    svc = MusicVideoService(db.session)
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    mv = _mk(svc, song_path=str(song))
    svc.advance_if_predecessor(mv.id, expected_predecessor="draft")  # → analyzing
    style = mv.style_prompt

    structure = {
        "tempo_bpm": 120.0, "duration_seconds": 6.0,
        "beat_times": [1.0, 2.0, 3.0, 4.0, 5.0],
        "sections": [{"label": "drop", "start": 0.0, "end": 6.0, "mean_energy": 1.0}],
    }
    monkeypatch.setattr(mvt.requests, "post", lambda *a, **k: _Resp(structure), raising=False)
    cut_plan = [
        {"index": i, "start_s": 2.0 * i, "end_s": 2.0 * (i + 1), "energy": 0.9, "section_label": "drop"}
        for i in range(3)
    ]
    monkeypatch.setattr(mvt, "compute_cut_plan", lambda *a, **k: [dict(c) for c in cut_plan])

    # An LLM that answers every cut with the same shot.
    import backend.services.music_video_director as director
    def _same_shot_director(style_prompt, plan, **kw):
        return {
            "prompts": [f"a lone crow on a wire, {style_prompt}" for _ in plan],
            "treatment": None,
            "shots": [{"index": c["index"], "prompt": "a lone crow on a wire"} for c in plan],
        }
    monkeypatch.setattr(director, "_generate_storyline_and_prompts", _same_shot_director)
    import backend.services.director_service as director_service
    monkeypatch.setattr(director_service, "_generate_storyline_and_prompts", _same_shot_director)
    monkeypatch.setattr(director_service, "_resolve_model", lambda m: m)  # no Ollama lookup

    mvt.run_analyzer(mv.id)
    db.session.refresh(mv)
    assert mv.status != "failed_analyzing", mv.error_blob
    assert mv.current_stage == "awaiting_approval"
    assert stage_plugins == [("music-video", "analyzing")]
    assert no_plugin_manager == [], "".join(no_plugin_manager)

    expected = director._ensure_distinct_and_energy_aware(
        [f"a lone crow on a wire, {style}"] * 3, cut_plan, style,
    )
    stored = [c["prompt"] for c in mv.clips]
    assert stored == expected
    assert len(set(stored)) == 3
    assert all(p.endswith(style) for p in stored)


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


def test_clip_interrupt_marks_clip_failed_and_continues(app, sent, monkeypatch):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    mv.clips = [
        {"index": 0, "start": 0.0, "end": 2.0, "clip_path": None, "status": "pending"},
        {"index": 1, "start": 2.0, "end": 4.0, "clip_path": None, "status": "pending"},
    ]
    db.session.commit()

    monkeypatch.setattr(
        "backend.services.plugin_bridge.ensure_plugins_for_stage", lambda *a, **k: None,
    )

    def boom(m, c):
        raise RuntimeError("ComfyUI execution_interrupted: prompt cancelled")
    monkeypatch.setattr(mvt, "_generate_one_clip", boom)

    mvt.run_clip_generator(mv.id)
    db.session.refresh(mv)
    assert mv.status != "failed_generating"
    assert mv.clips[0]["status"] == "failed"
    assert "execution_interrupted" in (mv.clips[0].get("error") or "")
    assert mv.clips[1]["status"] == "pending"
    assert ("music_video.run_clip_generator", (mv.id,)) in sent.calls


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
        # The editor registers the project file and the render; the render is the output.
        return _Resp({"rendered_mp4": "/out/final.mp4", "documents": [
            {"id": 76, "path": "/out/final.mlt"}, {"id": 77, "path": "/out/final.mp4"}]})
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
    assert mvt._max_clip_s(s) == pytest.approx(121 / 24)  # Wan 5B native forward length


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
    assert req.model == "wan22-5b"
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
        ("wan22-5b", 24, 5, 121, False)
    s = mvt._settings(_mk(svc, settings={"i2v_model": "minimax-h3-int8"}))
    h3 = mvt._clip_profile(s)
    assert (h3["fps"], h3["min_frames"], h3["max_frames"], h3["audio_out"]) == (24, 72, 362, True)
    assert mvt._max_clip_s(s) == pytest.approx(362 / 24)
    cog = mvt._clip_profile(mvt._settings(_mk(svc, settings={"i2v_engine": "cogvideox"})))
    assert (cog["model"], cog["fps"], cog["max_frames"]) == ("cogvideox-5b-i2v", 16, 81)


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


def test_song_lyrics_reach_the_director_as_thematic_guidance(app):
    from backend.models import Document
    svc = MusicVideoService(db.session)
    doc = Document(filename="song.wav", path="Audio/song.wav",
                   file_metadata='{"lyrics": "[Verse] we drove all night\\nunder a paper moon", "model": "MiniMax-Music3"}')
    db.session.add(doc)
    db.session.commit()
    mv = _mk(svc)
    mv.song_document_id = doc.id
    db.session.commit()
    note = mvt._song_lyrics_guidance(mv)
    assert note.startswith("LYRICS of the song, as thematic source only")
    assert "we drove all night under a paper moon" in note
    mv.song_document_id = None
    assert mvt._song_lyrics_guidance(mv) is None


# --- the clip quality check on each cut (same record as a Video Gen clip) ----

def _render_cut(app, monkeypatch, tmp_path, source_clip, settings=None):
    """Run _generate_one_clip with the model's output being ``source_clip``.

    The still, the video model, the GPU gate and the fill are stubbed where
    _generate_one_clip reads them; the quality check runs for real on the clip.
    """
    import shutil

    import backend.services.comfyui_image_generator as cig
    import backend.services.comfyui_video_generator as cvg
    import backend.services.job_operation_gate as jog
    from backend.tests.fixtures import video_quality_clips as clips

    svc = MusicVideoService(db.session)
    mv = _mk(svc, settings={"i2v_engine": "wan", **(settings or {})})
    clip = {"index": 0, "start": 0.0, "end": 2.0, "clip_path": None, "status": "pending",
            "prompt": "a fox in snow"}
    mv.clips = [clip]
    db.session.commit()

    from PIL import Image
    still = tmp_path / "still.png"
    Image.fromarray(clips._scene(0)).save(still)

    class _Img:
        def __init__(self, **kw):
            pass

        def generate_image(self, **kw):
            return str(still)
    monkeypatch.setattr(cig, "ComfyUIImageGenerator", _Img)

    out = tmp_path / "i2v.mp4"
    shutil.copyfile(source_clip, out)

    class _Result:
        success = True
        video_path = str(out)
        error = None

    class _Gen:
        def generate_video(self, req):
            # generate_video resolves the request to what it rendered.
            req.width, req.height = clips.WIDTH, clips.HEIGHT
            req.duration_frames, req.interpolation_multiplier = clips.FRAMES, 1
            return _Result()
    monkeypatch.setattr(cvg, "get_video_generator", lambda: _Gen())

    class _Gate:
        @contextlib.contextmanager
        def gpu_exclusive(self, *a, **k):
            yield
    monkeypatch.setattr(jog, "get_gate", lambda: _Gate())
    monkeypatch.setattr(mvt, "_comfyui_free_vram", lambda: None)
    monkeypatch.setattr(mvt, "fill_clip_to_duration",
                        lambda src, target_s, o, **kw: (Path(o).write_bytes(b"x"), o)[1])

    mvt._generate_one_clip(mv, clip)
    db.session.refresh(mv)
    return mv


@pytest.fixture(scope="module")
def cut_clips(tmp_path_factory):
    pytest.importorskip("av")
    from backend.tests.fixtures import video_quality_clips as clips
    root = tmp_path_factory.mktemp("cuts")
    return {name: getattr(clips, name)(root / f"{name}.mp4") for name in ("clean", "washed_out")}


def test_a_rendered_cut_gets_quality_metadata(app, monkeypatch, tmp_path, cut_clips):
    import backend.services.video_consistency_metrics as vcm
    monkeypatch.setattr(vcm, "review_video_quality", lambda *a, **k: pytest.fail("VLM ran without being asked"))

    mv = _render_cut(app, monkeypatch, tmp_path, cut_clips["clean"])

    [cut] = mv.clips
    assert cut["status"] == "done"
    quality = cut["quality"]
    assert quality["frames"]["readable"] is True
    assert quality["flags"] == [] and quality["flagged"] is False
    assert quality["colour_match"]["label"] == "colour match"
    assert "vlm_review" not in quality
    assert cut["review"] is None


def test_a_flagged_cut_is_held_for_review(app, monkeypatch, tmp_path, cut_clips):
    mv = _render_cut(app, monkeypatch, tmp_path, cut_clips["washed_out"])
    [cut] = mv.clips
    assert cut["status"] == "done"
    assert cut["review"]["state"] == "needs_review"
    # Washed out, and its palette has left the keyframe's.
    assert cut["review"]["codes"] == ["washed_out", "low_colour_match"]


def test_the_vision_review_runs_on_a_cut_only_where_enabled(app, monkeypatch, tmp_path, cut_clips):
    import backend.services.video_consistency_metrics as vcm
    monkeypatch.setattr(vcm, "review_video_quality", lambda *a, **k: {
        "status": "reviewed", "available": True, "model": "m", "reason": None, "message": None,
        "review": {"quality_score": 3},
    })
    mv = _render_cut(app, monkeypatch, tmp_path, cut_clips["clean"], settings={"high_consistency": True})
    [cut] = mv.clips
    assert cut["quality"]["vlm_review"]["review"]["quality_score"] == 3
    assert cut["review"]["codes"] == ["low_vlm_score"]


def test_a_held_cut_keeps_the_video_from_being_assembled(app, sent, tmp_path):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    f0 = tmp_path / "c0.mp4"; f0.write_bytes(b"x")
    f1 = tmp_path / "c1.mp4"; f1.write_bytes(b"x")
    mv.clips = [
        {"index": 0, "start": 0.0, "end": 2.0, "clip_path": str(f0), "status": "done", "review": None},
        {"index": 1, "start": 2.0, "end": 4.0, "clip_path": str(f1), "status": "done",
         "review": {"state": "needs_review", "codes": ["black_frames"], "reasons": ["black frames"]}},
    ]
    db.session.commit()

    mvt.run_clip_generator(mv.id)
    db.session.refresh(mv)
    assert mv.current_stage == "generating"
    assert sent.calls == []  # no assembler, and no re-render of the held cut


def test_an_approved_cut_lets_the_video_assemble(app, sent, tmp_path):
    svc = MusicVideoService(db.session)
    mv = _mk(svc)
    mv.current_stage = "generating"
    mv.status = "generating"
    f0 = tmp_path / "c0.mp4"; f0.write_bytes(b"x")
    mv.clips = [{"index": 0, "start": 0.0, "end": 2.0, "clip_path": str(f0), "status": "done",
                 "review": {"state": "approved", "codes": ["black_frames"], "reasons": ["black frames"]}}]
    db.session.commit()

    mvt.run_clip_generator(mv.id)
    db.session.refresh(mv)
    assert mv.current_stage == "assembling"
    assert ("music_video.run_assembler", (mv.id,)) in sent.calls
