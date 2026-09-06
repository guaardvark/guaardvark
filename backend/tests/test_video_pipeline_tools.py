"""Music-video and Film Crew NL parsers + tools. Tools must not auto-approve a render."""
import pytest

from backend.tools.video_pipeline_tools import (
    FilmCrewTool,
    MusicVideoTool,
    is_film_crew_request,
    is_music_video_request,
    parse_film_crew_nl,
    parse_music_video_nl,
    wants_film_crew,
    wants_music_video,
)


class TestParsers:
    def test_wants_music_video_not_generic_clip(self):
        assert wants_music_video("make a music video from song.mp3 neon noir") is True
        assert wants_music_video("generate a video of a fox") is False
        assert wants_music_video("/music-video song.mp3 neon") is False  # slash is separate
        assert is_music_video_request("/music-video song.mp3 neon") is True

    def test_wants_film_crew(self):
        assert wants_film_crew("start film crew") is True
        assert wants_film_crew("film this script INT. KITCHEN") is True
        assert wants_film_crew("generate a video of a fox") is False
        assert is_film_crew_request("/film-crew INT. ROOM") is True

    def test_parse_music_video_song_path_and_style(self):
        parsed = parse_music_video_nl("make a music video from song.mp3 neon noir rain")
        assert parsed["song"] == "song.mp3"
        assert "neon noir rain" in parsed["style_prompt"]

    def test_parse_music_video_document_id(self):
        parsed = parse_music_video_nl("create a music video 42 deep blue slow movement")
        assert parsed["song"] == "42"
        assert "deep blue" in parsed["style_prompt"]

    def test_parse_music_video_slash(self):
        parsed = parse_music_video_nl("/music-video /data/songs/a.wav wet asphalt night")
        assert parsed["song"] == "/data/songs/a.wav"
        assert "wet asphalt night" in parsed["style_prompt"]

    def test_parse_film_crew_strips_intent(self):
        parsed = parse_film_crew_nl("film this script INT. KITCHEN — a kettle boils.")
        assert parsed["script_text"].startswith("INT. KITCHEN")

    def test_parse_film_crew_slash(self):
        parsed = parse_film_crew_nl("/film-crew INT. ROOM. Hi.")
        assert parsed["script_text"] == "INT. ROOM. Hi."


class TestDirectIntercepts:
    def _engine(self, tool_name):
        from backend.services.unified_chat_engine import UnifiedChatEngine

        class FakeRegistry:
            def get_tool(self, name):
                return object() if name == tool_name else None

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = FakeRegistry()
        engine._save_message = lambda *a, **k: None
        engine._calls = []

        def _run(tool, params, *a, **k):
            engine._calls.append((tool, params))
            return {"success": True, "tool": tool, "params": params}

        engine._run_direct_tool_execution = _run
        return engine

    def test_music_video_nl_calls_tool_not_generate_video(self):
        engine = self._engine("generate_music_video")
        result = engine._try_music_video_direct(
            "make a music video from song.mp3 neon noir",
            "s", lambda *a: None, "r", {},
        )
        assert result is not None
        assert engine._calls[0][0] == "generate_music_video"
        assert engine._calls[0][1]["song"] == "song.mp3"

    def test_music_video_missing_song_does_not_fall_through(self):
        engine = self._engine("generate_music_video")
        events = []
        result = engine._try_music_video_direct(
            "make a music video neon noir",
            "s", lambda e, p: events.append((e, p)), "r", {},
        )
        assert result is not None
        assert engine._calls == []
        assert "song" in result["response"].lower()

    def test_film_crew_nl_calls_tool(self):
        engine = self._engine("start_film_crew")
        result = engine._try_film_crew_direct(
            "film this script INT. KITCHEN — a kettle boils.",
            "s", lambda *a: None, "r", {},
        )
        assert result is not None
        assert engine._calls[0][0] == "start_film_crew"
        assert "INT. KITCHEN" in engine._calls[0][1]["script_text"]


class TestVideoIntentExclusion:
    def test_music_video_is_not_generic_video(self):
        from backend.services.unified_chat_engine import (
            GPU_HEAVY_TOOLS,
            user_wants_image_generation,
            user_wants_video_generation,
        )
        msg = "make a music video from song.mp3 neon noir"
        assert user_wants_video_generation(msg) is False
        assert user_wants_image_generation(msg) is False
        assert user_wants_video_generation("generate a video of a fox") is True
        assert "generate_music_video" not in GPU_HEAVY_TOOLS
        assert "start_film_crew" not in GPU_HEAVY_TOOLS
        assert "generate_video" in GPU_HEAVY_TOOLS


try:
    from flask import Flask
    from backend.models import db, Document
    from backend.services.music_video_service import MusicVideoService
    from backend.services.production_service import ProductionService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(flask_app)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _no_dispatch_and_no_gpu(monkeypatch):
    monkeypatch.setattr(MusicVideoService, "dispatch_agent", lambda self, mv_id, agent: None)
    monkeypatch.setattr(ProductionService, "dispatch_agent", lambda self, prod_id, agent: None)
    monkeypatch.setattr(
        "backend.services.video_model_registry.resolve_active_video_model",
        lambda role, explicit=None, surface=None: (explicit or "wan22-5b", None),
    )


@pytest.fixture
def uploads_at_tmp(tmp_path, monkeypatch):
    """Treat tmp_path as the uploads directory so temp media counts as ours."""
    import backend.services.output_registration as oreg
    monkeypatch.setattr(oreg, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def test_music_video_tool_creates_plan_and_does_not_approve(app, uploads_at_tmp):
    tmp_path = uploads_at_tmp
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    with app.app_context():
        result = MusicVideoTool().execute(
            song=str(song), style_prompt="neon noir rain", name="Night Drive",
        )
        assert result.success, result.error
        assert result.metadata["approved"] is False
        assert result.metadata["studio_url"] == "/music-video"
        assert result.metadata["stage"] in ("analyzing", "draft")
        assert "Approve" in (result.output or "")
        from backend.models import MusicVideo
        mv = db.session.get(MusicVideo, result.metadata["music_video_id"])
        assert mv is not None
        assert mv.current_stage != "generating"
        assert mv.name == "Night Drive"


def test_music_video_tool_missing_style(app):
    with app.app_context():
        result = MusicVideoTool().execute(song="1", style_prompt="  ")
        assert result.success is False
        assert "style_prompt" in (result.error or "")


def test_film_crew_tool_creates_and_does_not_render(app):
    with app.app_context():
        result = FilmCrewTool().execute(
            script_text="INT. KITCHEN.\nA kettle boils.",
            name="Kettle",
        )
        assert result.success, result.error
        assert result.metadata["rendered"] is False
        assert result.metadata["studio_url"] == "/film-crew"
        assert result.metadata["stage"] in ("screenwriting", "draft")
        from backend.models import Production
        prod = db.session.get(Production, result.metadata["production_id"])
        assert prod is not None
        assert prod.current_stage != "generating"
        assert prod.name == "Kettle"


def test_film_crew_tool_reads_script_file(app, uploads_at_tmp):
    tmp_path = uploads_at_tmp
    script = tmp_path / "scene.txt"
    script.write_text("INT. ROOM.\nHi.\n", encoding="utf-8")
    with app.app_context():
        result = FilmCrewTool().execute(script_text=str(script))
        assert result.success, result.error
        assert result.metadata["rendered"] is False
        from backend.models import Production
        prod = db.session.get(Production, result.metadata["production_id"])
        assert "INT. ROOM" in prod.script_text


def test_music_video_tool_song_document_id(app, tmp_path):
    f = tmp_path / "track.wav"
    f.write_bytes(b"RIFF")
    with app.app_context():
        doc = Document(filename="track.wav", path=str(f), type="wav", size=4, index_status="STORED")
        db.session.add(doc)
        db.session.commit()
        result = MusicVideoTool().execute(song=str(doc.id), style_prompt="deep blue")
        assert result.success, result.error
        assert result.metadata["approved"] is False


def test_music_video_tool_refuses_song_outside_data_dirs(app, tmp_path):
    """A real file that is not under uploads/outputs/install root is refused, not registered."""
    song = tmp_path / "elsewhere.mp3"
    song.write_bytes(b"ID3")
    with app.app_context():
        result = MusicVideoTool().execute(song=str(song), style_prompt="neon")
        assert result.success is False
        assert "inside the uploads" in (result.error or "")
        assert Document.query.filter_by(path=str(song)).first() is None


def test_film_crew_tool_does_not_read_script_outside_data_dirs(app, tmp_path):
    secret = tmp_path / "notes.txt"
    secret.write_text("INT. VAULT.\nThe combination is 1234.\n", encoding="utf-8")
    with app.app_context():
        result = FilmCrewTool().execute(script_text=str(secret))
        assert result.success is False
        assert "inside the uploads" in (result.error or "")
