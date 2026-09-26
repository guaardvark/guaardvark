"""Stage prep at pipeline dispatch: the right stage keys, off by default.

PipelineService looks stages up in plugin_bridge.STAGE_PLUGIN_REQUIREMENTS and
the orchestrator's STAGE_MODEL_REQUIREMENTS, which are keyed by page name
("music-video", "film-crew"), not by the celery prefix ("music_video",
"production"). The plugin start and the orchestrator are the seams patched
here; the lookup, the gating and the dispatch are the real code.
"""
import pytest

try:
    from flask import Flask
    from backend.models import db, MusicVideo, Production
    from backend.services import plugin_bridge
    from backend.services import gpu_memory_orchestrator as gmo
    from backend.services.pipeline_service import STAGE_PREP_ENV
    from backend.services.production_service import ProductionService
    from backend.services.music_video_service import MusicVideoService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


SERVICES = [MusicVideoService, ProductionService]


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
def sent(monkeypatch):
    class _Rec:
        def __init__(self):
            self.calls = []

        def send_task(self, name, args=None, **kw):
            self.calls.append((name, tuple(args or ())))
    rec = _Rec()
    import backend.celery_app as ca
    monkeypatch.setattr(ca, "celery", rec, raising=False)
    return rec


@pytest.fixture
def started(monkeypatch):
    """Record plugin starts and orchestrator stage prep instead of doing them."""
    calls = {"plugins": [], "stages": []}

    def fake_start(plugin_id, **kw):
        calls["plugins"].append(plugin_id)
        return True, "started"

    class _Orch:
        def prepare_for_stage(self, context, stage):
            calls["stages"].append((context, stage))
            return {}

    monkeypatch.setattr(plugin_bridge, "_try_start_plugin", fake_start)
    monkeypatch.setattr(gmo, "get_orchestrator", lambda: _Orch())
    return calls


def _mv(stage):
    mv = MusicVideo(name="M", song_path="/x.wav", style_prompt="x", status=stage,
                    current_stage=stage, settings_json={})
    db.session.add(mv)
    db.session.commit()
    return mv


def _prod(stage):
    p = Production(name="P", script_text="x", status=stage, current_stage=stage,
                   settings_json={})
    db.session.add(p)
    db.session.commit()
    return p


@pytest.mark.parametrize("svc", SERVICES)
def test_every_dispatched_stage_is_a_key_of_the_stage_maps(svc):
    stages = plugin_bridge.STAGE_PLUGIN_REQUIREMENTS[svc.stage_context]
    for stage, agent in svc.stage_to_agent.items():
        if agent is not None:
            assert stage in stages, f"{svc.__name__} stage {stage!r} is not in the map"


@pytest.mark.parametrize("svc", SERVICES)
def test_the_celery_prefix_is_not_a_stage_map_key(svc):
    assert svc.task_namespace not in plugin_bridge.STAGE_PLUGIN_REQUIREMENTS
    assert svc.stage_context != svc.task_namespace


def test_off_by_default_dispatch_starts_nothing(app, sent, started, monkeypatch):
    monkeypatch.delenv(STAGE_PREP_ENV, raising=False)
    mv = _mv("generating")
    MusicVideoService(db.session).dispatch_agent(mv.id, "clip_generator")
    assert started == {"plugins": [], "stages": []}
    assert sent.calls == [("music_video.run_clip_generator", (mv.id,))]


def test_on_music_video_generating_starts_comfyui_once(app, sent, started, monkeypatch):
    monkeypatch.setenv(STAGE_PREP_ENV, "1")
    mv = _mv("generating")
    MusicVideoService(db.session).dispatch_agent(mv.id, "clip_generator")
    assert started["plugins"] == ["comfyui"]
    assert started["stages"] == [("music-video", "generating")]
    assert sent.calls == [("music_video.run_clip_generator", (mv.id,))]


def test_on_film_crew_rendering_starts_its_plugins(app, sent, started, monkeypatch):
    monkeypatch.setenv(STAGE_PREP_ENV, "1")
    p = _prod("rendering")
    ProductionService(db.session).dispatch_agent(p.id, "editor")
    assert started["plugins"] == ["comfyui", "video_editor"]
    assert started["stages"] == [("film-crew", "rendering")]
    assert sent.calls == [("production.run_editor", (p.id,))]


def test_resume_preps_each_row_once(app, sent, started, monkeypatch):
    monkeypatch.setenv(STAGE_PREP_ENV, "1")
    _mv("analyzing")
    _mv("generating")
    _mv("awaiting_approval")  # user-gated: neither prepped nor dispatched
    assert MusicVideoService(db.session).resume_all() == 2
    assert sorted(started["stages"]) == [
        ("music-video", "analyzing"), ("music-video", "generating"),
    ]
    assert len(sent.calls) == 2


def test_auto_orchestrator_off_wins(app, sent, started, monkeypatch):
    monkeypatch.setenv(STAGE_PREP_ENV, "1")
    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "0")
    mv = _mv("generating")
    MusicVideoService(db.session).dispatch_agent(mv.id, "clip_generator")
    assert started == {"plugins": [], "stages": []}
    assert len(sent.calls) == 1


def test_a_plugin_that_will_not_start_still_dispatches(app, sent, monkeypatch):
    monkeypatch.setenv(STAGE_PREP_ENV, "1")
    monkeypatch.setattr(
        plugin_bridge, "_try_start_plugin",
        lambda plugin_id, **kw: (False, f"plugin '{plugin_id}' is user-disabled"),
    )
    mv = _mv("generating")
    MusicVideoService(db.session).dispatch_agent(mv.id, "clip_generator")
    assert sent.calls == [("music_video.run_clip_generator", (mv.id,))]
