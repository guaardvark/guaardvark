"""MiniMaxH3SceneGenerator turns a window of Film Crew shots into one H3
request: compiled prompt, first and last frames, or references when the
reference build is installed."""
from types import SimpleNamespace

import pytest

from backend.services import comfyui_video_generator as cvg
from backend.services.comfyui_video_generator import MiniMaxH3SceneGenerator, VideoGenerationResult


class _Gen:
    def __init__(self):
        self.requests = []

    def generate_video(self, req):
        self.requests.append(req)
        out = req.output_dir / "clip.mp4"
        out.write_bytes(b"mp4")
        return VideoGenerationResult(success=True, video_path=str(out), has_audio=True)


@pytest.fixture
def gen(monkeypatch):
    fake = _Gen()
    monkeypatch.setattr(cvg, "get_video_generator", lambda: fake)
    monkeypatch.setattr("backend.services.video_model_registry.tier_defaults_for",
                        lambda model, total=None: {"width": 864, "height": 480, "speed_profile": "standard"})
    return fake


def _shots(tmp_path):
    ref = tmp_path / "mara.png"
    ref.write_bytes(b"x")
    return [
        SimpleNamespace(image_prompt="Mara sits at the window", duration_seconds=5, character_name="Mara",
                        dialogue_text="I get off at the next station.", ref_image_paths=[str(ref)],
                        character_description="a woman in a grey coat"),
        SimpleNamespace(image_prompt="a man looks up from his coffee", duration_seconds=5, character_name=None,
                        dialogue_text=None, ref_image_paths=[], character_description=None),
    ]


def test_frames_path_compiles_first_plus_last_frame_prompt(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(MiniMaxH3SceneGenerator, "_reference_model", lambda self: None)
    out = tmp_path / "out" / "w1.mp4"
    path = MiniMaxH3SceneGenerator().render_scene(
        shots=_shots(tmp_path), first_frame="/sb/1.png", last_frame="/sb/3.png",
        output_path=str(out), duration_seconds=10,
    )
    assert path == str(out) and out.exists()
    req = gen.requests[0]
    assert req.model == "minimax-h3-int8" and req.first_frame_path == "/sb/1.png" and req.last_frame_path == "/sb/3.png"
    assert req.enhance_prompt is False and req.duration_frames == 243
    assert req.prompt.startswith("How the reference pictures align with the target video")
    assert "<d>[English] I get off at the next station.</d>" in req.prompt
    assert "[Shot 2] At 00:05.060" in req.prompt
    assert req.width == 864 and req.speed_profile == "standard"
    assert req.h3_intent["mode"] == "fl2va"


def test_reference_build_takes_cast_refs_instead_of_frames(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(MiniMaxH3SceneGenerator, "_reference_model", lambda self: "minimax-h3-ref2va-int8")
    out = tmp_path / "out" / "w1.mp4"
    MiniMaxH3SceneGenerator().render_scene(
        shots=_shots(tmp_path), first_frame="/sb/1.png", last_frame=None,
        output_path=str(out), duration_seconds=10,
    )
    req = gen.requests[0]
    assert req.model == "minimax-h3-ref2va-int8"
    assert req.first_frame_path is None and req.ref_images == [str(tmp_path / "mara.png")]
    assert req.prompt.startswith("subject_definitions:\n<Subject 1> is Mara, a woman in a grey coat in <Picture 1>.")
    assert "<Subject 1> Mara (S1) says:" in req.prompt


def test_failure_raises_with_the_generators_message(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(MiniMaxH3SceneGenerator, "_reference_model", lambda self: None)
    gen.generate_video = lambda req: VideoGenerationResult(success=False, error="no room")
    with pytest.raises(RuntimeError, match="no room"):
        MiniMaxH3SceneGenerator().render_scene(
            shots=_shots(tmp_path), first_frame="/sb/1.png", last_frame=None,
            output_path=str(tmp_path / "o.mp4"), duration_seconds=5,
        )
