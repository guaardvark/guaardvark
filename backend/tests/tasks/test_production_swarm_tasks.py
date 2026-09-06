import pytest
import json
from unittest.mock import MagicMock, patch

from flask import Flask
from backend.models import db, Production, Subject, ProductionShot, ProductionShotSubject, SwarmMessage, Document
from backend.tasks.production_swarm_tasks import (
    run_screenwriter,
    run_casting_director,
    run_cinematographer,
    run_storyboard_artist,
    run_editor,
    regen_storyboard_shot
)


@pytest.fixture(autouse=True)
def _fresh_gpu_gate(monkeypatch):
    """Give each test a clean JobOperationGate.

    These pipeline tests share the process-wide get_gate() singleton. The GPU
    surfaces now wrap their work in gate.gpu_exclusive(), which on release sets
    an 8s cooldown — that leaked between back-to-back tests and made an editor/
    storyboard render falsely report "GPU cooling down". Resetting the singleton
    per test isolates them. (Cross-test cooldown leakage is a test artifact of
    the module singleton, not a production concern — real renders are seconds+
    apart and a single process holds the gate.)
    """
    import backend.services.job_operation_gate as jog
    fresh = jog.JobOperationGate()
    monkeypatch.setattr(jog, "_GATE_SINGLETON", fresh)
    # The render surfaces now go through gpu_session, which on a real claim runs
    # VRAM reclaim (evict Ollama / free ComfyUI). Neutralize it here so these
    # pipeline unit tests never touch the network/GPU — reclaim is exercised by
    # test_gpu_resource_policy instead.
    import backend.services.gpu_resource_policy as grp
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **kw: None)
    # With eviction neutralised, the fit check must not read the real card either:
    # it probes get_available_vram(), so whatever happens to be resident on the
    # developer's GPU (a chat model, a warm ComfyUI) decided these tests. Answer
    # with an idle 16GB card; the probe itself is covered by test_gpu_resource_policy.
    monkeypatch.setattr(
        grp, "fit_verdict",
        lambda estimate_mb, *, reserve_mb=0, margin_mb=1024: grp.Fit(
            ok=True, capacity=False, free_mb=16000, total_mb=16000,
            need_mb=int(estimate_mb) + int(margin_mb), headroom=True,
        ),
    )
    monkeypatch.setattr(grp, "_load_admit_or_busy", lambda *a, **k: None)
    monkeypatch.setattr(grp, "_acquire_cross_process_lease", lambda *a, **k: False)
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda role, explicit=None, surface=None: (explicit or "wan22-5b", None),
    )
    monkeypatch.setattr(
        "backend.services.video_model_registry.preflight_video_model",
        lambda m: (True, ""),
    )
    return fresh


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
def production(app):
    prod = Production(
        name="Test Prod",
        script_text="INT. CAFE - DAY\nA test scene.",
        current_stage="screenwriting",
        project_id=1
    )
    db.session.add(prod)
    db.session.commit()
    return prod


def test_run_screenwriter_persists_subjects_and_shots(app, production):
    def fake_llm(*args, **kwargs):
        return json.dumps({
            "subjects": [
                {"name": "Alice", "kind": "character", "description": "A test character"}
            ],
            "scenes": [
                {
                    "number": 1,
                    "location": "CAFE",
                    "shots": [
                        {"number": 1, "description": "Wide shot", "dialogue": "Hello"}
                    ]
                }
            ]
        })

    run_screenwriter(production.id, llm=fake_llm)

    # Assert Subject and ProductionShot rows created
    subject = Subject.query.filter_by(name="Alice").first()
    assert subject is not None
    assert subject.kind == "character"
    assert subject.description == "A test character"

    shot = ProductionShot.query.filter_by(production_id=production.id).first()
    assert shot is not None
    assert shot.scene_number == 1
    assert shot.shot_number == 1
    assert shot.description == "Wide shot"
    assert shot.dialogue_text == "Hello"

    # Assert stage advanced to "casting"
    db.session.refresh(production)
    assert production.current_stage == "casting"

    # Assert SwarmMessage row created with status="ok"
    msg = SwarmMessage.query.filter_by(production_id=production.id).first()
    assert msg is not None
    assert msg.status == "ok"
    assert msg.agent_name == "screenwriter"


def test_run_screenwriter_creates_production_subjects(app, production):
    def fake_llm(*args, **kwargs):
        return json.dumps({
            "subjects": [
                {"name": "Alice", "kind": "character", "description": "A test character"}
            ],
            "scenes": []
        })

    run_screenwriter(production.id, llm=fake_llm)

    from backend.models import ProductionSubject
    ps = ProductionSubject.query.filter_by(production_id=production.id).all()
    assert len(ps) == 1
    assert ps[0].subject.name == "Alice"


def test_run_screenwriter_retry_does_not_duplicate(app, production):
    def fake_llm(*args, **kwargs):
        return json.dumps({
            "subjects": [
                {"name": "Alice", "kind": "character", "description": "A test character"}
            ],
            "scenes": [
                {
                    "number": 1,
                    "location": "CAFE",
                    "shots": [
                        {"number": 1, "description": "Wide shot", "dialogue": "Hello"}
                    ]
                }
            ]
        })

    run_screenwriter(production.id, llm=fake_llm)

    from backend.models import ProductionSubject
    assert ProductionSubject.query.filter_by(production_id=production.id).count() == 1
    assert ProductionShot.query.filter_by(production_id=production.id).count() == 1

    # Reset stage and run again
    production.current_stage = "screenwriting"
    db.session.commit()

    run_screenwriter(production.id, llm=fake_llm)

    assert ProductionSubject.query.filter_by(production_id=production.id).count() == 1
    assert ProductionShot.query.filter_by(production_id=production.id).count() == 1


def test_run_screenwriter_parse_error_marks_failed_stage(app, production):
    def fake_llm(*args, **kwargs):
        return "garbage"

    run_screenwriter(production.id, llm=fake_llm)

    db.session.refresh(production)
    assert production.status == "failed_screenwriting"

    msg = SwarmMessage.query.filter_by(production_id=production.id).first()
    assert msg is not None
    assert msg.status == "parse_error"


def test_run_screenwriter_idempotent_when_stage_mismatch(app, production):
    production.current_stage = "casting"
    db.session.commit()

    def fake_llm(*args, **kwargs):
        return json.dumps({"subjects": [], "scenes": []})

    run_screenwriter(production.id, llm=fake_llm)

    # Should do nothing
    assert SwarmMessage.query.count() == 0


def _director_plans(monkeypatch, subject_ids, *, camera="wide", framing="full body",
                    duration=4.5, mood="calm", prompt="A cafe in the morning"):
    """run_cinematographer is director-first: it calls director_service.plan
    (SCRIPT_SCENES) and only falls back to the injected llm when that fails. Patch
    the seam it really calls, with the fields the mapping reads (camera, framing,
    duration, mood, prompt, subjects), so the test exercises the production path
    instead of whatever model happens to be running on the developer's box."""
    from backend.services import director_service as ds

    def fake_plan(brief):
        n = len(brief.scenes[0]["shots"]) if brief.scenes else 0
        return ds.DirectorResult(treatment=None, shots=[
            ds.ShotPrompt(prompt=prompt, index=i, camera=camera, framing=framing,
                          duration=duration, mood=mood, subjects=list(subject_ids))
            for i in range(n)
        ])

    monkeypatch.setattr(ds, "plan", fake_plan)


def test_run_cinematographer_updates_shots_with_camera_and_image_prompt(app, production, monkeypatch):
    production.current_stage = "cinematography"
    subj = Subject(name="Alice", kind="character", description="A test character")
    db.session.add(subj)
    db.session.commit()
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot")
    db.session.add(shot)
    db.session.commit()
    _director_plans(monkeypatch, [subj.id])

    def fake_llm(*args, **kwargs):
        return json.dumps({
            "plans": [
                {
                    "scene_number": 1,
                    "shot_number": 1,
                    "camera_angle": "wide",
                    "framing": "full body",
                    "duration_seconds": 4.5,
                    "mood": "calm",
                    "image_prompt": "A cafe in the morning",
                    "subjects_in_shot": [subj.id]
                }
            ]
        })

    with patch("backend.celery_app.celery.send_task") as mock_send_task:
        run_cinematographer(production.id, llm=fake_llm)

    db.session.refresh(shot)
    assert shot.camera_angle == "wide"
    assert shot.duration_seconds == 4.5
    assert "IMAGE PROMPT: A cafe in the morning" in shot.description

    pss = ProductionShotSubject.query.filter_by(shot_id=shot.id, subject_id=subj.id).first()
    assert pss is not None

    db.session.refresh(production)
    assert production.current_stage == "storyboard_gen"


def test_run_cinematographer_falls_back_to_agent_llm_when_director_fails(app, production, monkeypatch):
    """When director_service.plan raises, the swarm Cinematographer (injected llm) plans."""
    from backend.services import director_service as ds
    production.current_stage = "cinematography"
    subj = Subject(name="Alice", kind="character", description="A test character")
    db.session.add(subj)
    db.session.commit()
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot")
    db.session.add(shot)
    db.session.commit()

    def boom(brief):
        raise RuntimeError("director down")
    monkeypatch.setattr(ds, "plan", boom)

    def fake_llm(*args, **kwargs):
        return json.dumps({"plans": [{
            "scene_number": 1, "shot_number": 1, "camera_angle": "low angle",
            "framing": "close up", "duration_seconds": 3.0, "mood": "tense",
            "image_prompt": "Rain on a window", "subjects_in_shot": [subj.id],
        }]})

    with patch("backend.celery_app.celery.send_task"):
        run_cinematographer(production.id, llm=fake_llm)

    db.session.refresh(shot)
    assert shot.camera_angle == "low angle"
    assert shot.duration_seconds == 3.0
    assert "IMAGE PROMPT: Rain on a window" in shot.description


@patch("backend.tasks.production_swarm_tasks.current_app")
def test_run_cinematographer_dispatches_storyboard_artist_next(mock_current_app, app, production, monkeypatch):
    production.current_stage = "cinematography"
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot")
    db.session.add(shot)
    db.session.commit()

    def fake_llm(*args, **kwargs):
        return json.dumps({"plans": []})

    # We need to patch celery.send_task. Since we don't have it directly, we can patch the import or current_app.extensions['celery']
    _director_plans(monkeypatch, [])
    with patch("backend.celery_app.celery.send_task") as mock_send_task:
        run_cinematographer(production.id, llm=fake_llm)
        mock_send_task.assert_called_once_with("production.run_storyboard_artist", args=[production.id])


def test_run_cinematographer_drops_hallucinated_subject_ids(app, production, monkeypatch):
    """The LLM occasionally invents subject_ids that aren't in the set we passed.
    We must filter those out before insert — otherwise the FK constraint blows
    up the whole transaction and the production gets stuck."""
    production.current_stage = "cinematography"
    real = Subject(name="Alice", kind="character", description="real subject")
    db.session.add(real); db.session.commit()
    real_id = real.id
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide")
    db.session.add(shot); db.session.commit()
    # The director (primary path) is the one that can hallucinate ids; feed them through it.
    _director_plans(monkeypatch, [real_id, 99999, -1], prompt="x", duration=3.0)

    def fake_llm(*args, **kwargs):
        return json.dumps({
            "plans": [{
                "scene_number": 1, "shot_number": 1,
                "camera_angle": "wide", "framing": "full body",
                "duration_seconds": 3.0, "mood": "calm",
                "image_prompt": "x",
                "subjects_in_shot": [real_id, 99999, -1],  # one real, two bogus
            }]
        })

    with patch("backend.celery_app.celery.send_task"):
        run_cinematographer(production.id, llm=fake_llm)

    rows = ProductionShotSubject.query.filter_by(shot_id=shot.id).all()
    assert len(rows) == 1
    assert rows[0].subject_id == real_id


def test_run_storyboard_artist_advances_to_awaiting_approval(app, production):
    production.current_stage = "storyboard_gen"
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot")
    db.session.add(shot)
    db.session.commit()

    # image_generator=None → run_storyboard_artist renders through
    # character_still_pipeline.render_character_still (the offline Z-Image path).
    # Patch THAT seam: the old ComfyUIImageGenerator patch was never reached and
    # this test loaded Z-Image on the real GPU and rendered a real still (39s).
    from types import SimpleNamespace

    def fake_still(prompt, *, output_path, **kw):
        return SimpleNamespace(success=True, image_path=output_path, error=None)

    with patch("backend.services.character_still_pipeline.render_character_still", side_effect=fake_still):
        run_storyboard_artist(production.id, image_generator=None)

    db.session.refresh(shot)
    # _storyboard_path names files shot_<scene>_<shot>.png
    assert shot.storyboard_image_path.endswith(f"/storyboards/{production.id}/shot_1_1.png")

    db.session.refresh(production)
    assert production.current_stage == "awaiting_approval"


def test_run_storyboard_artist_with_image_generator_calls_it_per_shot(app, production):
    production.current_stage = "storyboard_gen"
    shot1 = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot")
    shot2 = ProductionShot(production_id=production.id, scene_number=1, shot_number=2, description="Close up")
    db.session.add_all([shot1, shot2])
    db.session.commit()

    mock_generator = MagicMock()
    mock_generator.generate_image.side_effect = [
        "/real/path/shot_1.png",
        "/real/path/shot_2.png"
    ]

    run_storyboard_artist(production.id, image_generator=mock_generator)

    assert mock_generator.generate_image.call_count == 2
    db.session.refresh(shot1)
    assert shot1.storyboard_image_path == "/real/path/shot_1.png"
    db.session.refresh(shot2)
    assert shot2.storyboard_image_path == "/real/path/shot_2.png"


def test_run_editor_renders_and_advances_to_complete(app, production):
    production.current_stage = "rendering"
    shot = ProductionShot(
        production_id=production.id, scene_number=1, shot_number=1,
        description="Wide shot", storyboard_image_path="/tmp/img.png",
        approved=True
    )
    db.session.add(shot)
    db.session.commit()

    mock_i2v = MagicMock()
    mock_audio = MagicMock()
    mock_ffmpeg = MagicMock()

    # Need to mock Editor.render or the underlying tools.
    # The prompt says "with stubbed I2V/AudioFoundry/ffmpeg, assert RenderResult fields populated, video_clip_path updated, Document row created, stage='complete'."
    # Let's mock the editor agent.
    with patch("backend.tasks.production_swarm_tasks.Editor") as MockEditor:
        mock_editor_instance = MockEditor.return_value
        # Render returns a RenderResult
        from backend.services.swarm.agents.editor import RenderResult
        mock_editor_instance.render.return_value = RenderResult(
            final_mp4_path="/tmp/final.mp4",
            mlt_path=None,
            clip_paths=["/tmp/shot_1.mp4"],
            voiceover_paths=[],
            music_path=None
        )

        run_editor(production.id, i2v=mock_i2v, audio_foundry=mock_audio, ffmpeg=mock_ffmpeg)

    db.session.refresh(shot)
    assert shot.video_clip_path == "/tmp/shot_1.mp4"

    db.session.refresh(production)
    assert production.current_stage == "complete"

    doc = Document.query.filter_by(path="/tmp/final.mp4").first()
    assert doc is not None
    assert doc.path == "/tmp/final.mp4"


def test_run_editor_resolves_voice_and_lora_from_cast(app, production):
    """Seam C: the speaking Subject's voice_id and LoRA both come from the
    shot's cast (shot_subjects), and voice_subject_id is stamped."""
    production.current_stage = "rendering"
    subj = Subject(
        kind="character", name="Serenity", voice_id="af_bella",
        lora_path="/loras/serenity.safetensors",
    )
    db.session.add(subj)
    db.session.commit()
    shot = ProductionShot(
        production_id=production.id, scene_number=1, shot_number=1,
        description="Close up", storyboard_image_path="/tmp/i.png",
        character_name="Serenity", dialogue_text="Hello there", approved=True,
    )
    db.session.add(shot)
    db.session.commit()
    db.session.add(ProductionShotSubject(shot_id=shot.id, subject_id=subj.id))
    db.session.commit()

    mock_i2v, mock_audio, mock_ffmpeg = MagicMock(), MagicMock(), MagicMock()
    with patch("backend.tasks.production_swarm_tasks.Editor") as MockEditor:
        from backend.services.swarm.agents.editor import RenderResult
        MockEditor.return_value.render.return_value = RenderResult(
            final_mp4_path="/tmp/final.mp4", mlt_path=None,
            clip_paths=["/tmp/shot_1.mp4"], voiceover_paths=[None], music_path=None,
        )
        run_editor(production.id, i2v=mock_i2v, audio_foundry=mock_audio, ffmpeg=mock_ffmpeg)
        shots_arg = MockEditor.return_value.render.call_args.kwargs["shots"]

    assert shots_arg[0].voice_id == "af_bella"
    assert shots_arg[0].lora_paths == ["/loras/serenity.safetensors"]
    db.session.refresh(shot)
    assert shot.voice_subject_id == subj.id


def test_run_editor_refuses_empty_shots(app, production):
    production.current_stage = "rendering"
    db.session.commit()

    run_editor(production.id)

    db.session.refresh(production)
    assert production.status == "failed_rendering"


def test_run_casting_director_writes_recommendation_swarm_message(app, production):
    production.current_stage = "casting"
    db.session.commit()

    def fake_llm(*args, **kwargs):
        return json.dumps({
            "actions": [
                {"subject_name": "Alice", "action": "train_from_generated"}
            ]
        })

    run_casting_director(production.id, llm=fake_llm)

    msg = SwarmMessage.query.filter_by(production_id=production.id, agent_name="casting_director").first()
    assert msg is not None
    assert msg.status == "ok"
    assert "actions" in msg.output_json


@patch("backend.celery_app.celery.send_task")
def test_dispatch_agent_enqueues_correct_task(mock_send_task, app, production):
    from backend.services.production_service import ProductionService
    service = ProductionService(db.session)
    service.dispatch_agent(production.id, "screenwriter")
    mock_send_task.assert_called_once_with("production.run_screenwriter", args=[production.id])


def test_regen_storyboard_shot_updates_path_no_stage_change(app, production):
    production.current_stage = "awaiting_approval"
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot", storyboard_image_path="/tmp/old.png")
    db.session.add(shot)
    db.session.commit()

    mock_generator = MagicMock()
    mock_generator.generate_image.return_value = "/tmp/new.png"

    regen_storyboard_shot(shot.id, prompt_override="New prompt", image_generator=mock_generator)

    db.session.refresh(shot)
    assert shot.storyboard_image_path == "/tmp/new.png"
    
    db.session.refresh(production)
    assert production.current_stage == "awaiting_approval"


def test_regen_storyboard_shot_no_op_when_not_awaiting_approval(app, production):
    production.current_stage = "storyboard_gen"
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot", storyboard_image_path="/tmp/old.png")
    db.session.add(shot)
    db.session.commit()

    mock_generator = MagicMock()
    regen_storyboard_shot(shot.id, prompt_override="New prompt", image_generator=mock_generator)

    db.session.refresh(shot)
    assert shot.storyboard_image_path == "/tmp/old.png"
    assert mock_generator.generate_image.call_count == 0

def test_run_cinematographer_retry_does_not_duplicate_shot_subjects(app, production, monkeypatch):
    production.current_stage = "cinematography"
    subj = Subject(name="Alice", kind="character", description="A test character")
    db.session.add(subj)
    db.session.commit()
    shot = ProductionShot(production_id=production.id, scene_number=1, shot_number=1, description="Wide shot")
    db.session.add(shot)
    db.session.commit()
    _director_plans(monkeypatch, [subj.id])

    def fake_llm(*args, **kwargs):
        return json.dumps({
            "plans": [
                {
                    "scene_number": 1,
                    "shot_number": 1,
                    "camera_angle": "wide",
                    "framing": "full body",
                    "duration_seconds": 4.5,
                    "mood": "calm",
                    "image_prompt": "A cafe in the morning",
                    "subjects_in_shot": [subj.id]
                }
            ]
        })

    with patch("backend.celery_app.celery.send_task"):
        run_cinematographer(production.id, llm=fake_llm)

    assert ProductionShotSubject.query.filter_by(shot_id=shot.id).count() == 1
    db.session.refresh(shot)
    assert shot.description.count("IMAGE PROMPT:") == 1

    # Reset stage and run again
    production.current_stage = "cinematography"
    db.session.commit()

    with patch("backend.celery_app.celery.send_task"):
        run_cinematographer(production.id, llm=fake_llm)

    assert ProductionShotSubject.query.filter_by(shot_id=shot.id).count() == 1
    db.session.refresh(shot)
    assert shot.description.count("IMAGE PROMPT:") == 1


def test_run_editor_failure_calls_fail_stage(app, production):
    production.current_stage = "rendering"
    shot = ProductionShot(
        production_id=production.id, scene_number=1, shot_number=1,
        description="Wide shot", storyboard_image_path="/tmp/img.png",
        approved=True
    )
    db.session.add(shot)
    db.session.commit()

    mock_i2v = MagicMock()
    mock_i2v.generate_video.side_effect = Exception("I2V failed")

    with patch("backend.tasks.production_swarm_tasks.Editor") as MockEditor:
        mock_editor_instance = MockEditor.return_value
        mock_editor_instance.render.side_effect = Exception("I2V failed")

        run_editor(production.id, i2v=mock_i2v)

    db.session.refresh(production)
    assert production.status == "failed_rendering"
    assert "I2V failed" in str(production.error_blob)


def test_run_editor_uses_a_scene_renderer_for_a_native_audio_model(app, production):
    """A production whose settings name a model that renders its own
    soundtrack gets an Editor with a scene renderer, and each shot carries the
    cast's name, references and description for it."""
    production.current_stage = "rendering"
    production.settings_json = {"video_model": "minimax-h3-int8"}
    subj = Subject(
        kind="character", name="Mara", voice_id="af_bella", description="a woman in a grey coat",
        ref_image_paths=["/refs/mara_1.png", "/refs/mara_2.png"],
    )
    db.session.add(subj)
    db.session.commit()
    shot = ProductionShot(
        production_id=production.id, scene_number=1, shot_number=1,
        description="Close up", storyboard_image_path="/tmp/i.png",
        character_name="Mara", dialogue_text="Hello there", approved=True,
    )
    db.session.add(shot)
    db.session.commit()
    db.session.add(ProductionShotSubject(shot_id=shot.id, subject_id=subj.id))
    db.session.commit()

    with patch("backend.tasks.production_swarm_tasks.Editor") as MockEditor:
        from backend.services.swarm.agents.editor import RenderResult
        MockEditor.return_value.render.return_value = RenderResult(
            final_mp4_path="/tmp/final.mp4", mlt_path=None,
            clip_paths=["/tmp/w1.mp4"], voiceover_paths=["/tmp/w1.mp4"], music_path=None,
        )
        run_editor(production.id, i2v=MagicMock(), audio_foundry=MagicMock(), ffmpeg=MagicMock())
        editor_kwargs = MockEditor.call_args.kwargs
        shots_arg = MockEditor.return_value.render.call_args.kwargs["shots"]

    from backend.services.comfyui_video_generator import MiniMaxH3SceneGenerator
    assert isinstance(editor_kwargs["scene_renderer"], MiniMaxH3SceneGenerator)
    assert editor_kwargs["scene_renderer"].model == "minimax-h3-int8"
    assert shots_arg[0].character_name == "Mara"
    assert shots_arg[0].ref_image_paths == ["/refs/mara_1.png", "/refs/mara_2.png"]
    assert shots_arg[0].character_description == "a woman in a grey coat"


def test_run_editor_honours_a_silent_i2v_model(app, production, monkeypatch):
    production.current_stage = "rendering"
    production.settings_json = {"video_model": "wan22-5b"}
    shot = ProductionShot(
        production_id=production.id, scene_number=1, shot_number=1,
        description="Wide", storyboard_image_path="/tmp/i.png", approved=True,
    )
    db.session.add(shot)
    db.session.commit()
    captured = {}

    class _I2V:
        def __init__(self, model="wan22-5b", fps=None):
            captured["model"] = model
            captured["fps"] = fps

    monkeypatch.setattr(
        "backend.services.comfyui_video_generator.Wan22I2VGenerator", _I2V,
    )
    with patch("backend.tasks.production_swarm_tasks.Editor") as MockEditor:
        from backend.services.swarm.agents.editor import RenderResult
        MockEditor.return_value.render.return_value = RenderResult(
            final_mp4_path="/tmp/final.mp4", mlt_path=None,
            clip_paths=["/tmp/shot_1.mp4"], voiceover_paths=[None], music_path=None,
        )
        run_editor(production.id, i2v=None, audio_foundry=MagicMock(), ffmpeg=MagicMock())
        assert MockEditor.call_args.kwargs["scene_renderer"] is None
    assert captured.get("model") == "wan22-5b"
    assert captured.get("fps") == 24


def test_run_editor_without_a_video_model_has_no_scene_renderer(app, production):
    production.current_stage = "rendering"
    shot = ProductionShot(
        production_id=production.id, scene_number=1, shot_number=1,
        description="Wide", storyboard_image_path="/tmp/i.png", approved=True,
    )
    db.session.add(shot)
    db.session.commit()
    with patch("backend.tasks.production_swarm_tasks.Editor") as MockEditor:
        from backend.services.swarm.agents.editor import RenderResult
        MockEditor.return_value.render.return_value = RenderResult(
            final_mp4_path="/tmp/final.mp4", mlt_path=None,
            clip_paths=["/tmp/shot_1.mp4"], voiceover_paths=[None], music_path=None,
        )
        run_editor(production.id, i2v=MagicMock(), audio_foundry=MagicMock(), ffmpeg=MagicMock())
        assert MockEditor.call_args.kwargs["scene_renderer"] is None
