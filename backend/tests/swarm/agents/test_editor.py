from unittest.mock import MagicMock
from pathlib import Path

from backend.services.swarm.agents.editor import Editor, ShotInput, RenderResult


def _make_shot(num, duration=3.0, dialogue=None, lora_paths=None):
    return ShotInput(
        shot_number=num,
        storyboard_image_path=f"/tmp/storyboard/shot_{num}.png",
        image_prompt=f"shot {num} prompt",
        duration_seconds=duration,
        dialogue_text=dialogue,
        lora_paths=lora_paths or ["/loras/dean.safetensors"],
    )


def _make_editor(tmp_path):
    i2v = MagicMock()
    i2v.i2v_from_image.side_effect = lambda **kw: kw["output_path"]
    audio = MagicMock()
    audio.tts.side_effect = lambda **kw: kw["output_path"]
    audio.generate_music.side_effect = lambda **kw: kw["output_path"]
    ffmpeg = MagicMock()
    ffmpeg.concat_with_audio.side_effect = lambda **kw: kw["output_path"]
    return i2v, audio, ffmpeg


def test_render_calls_i2v_per_shot(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)
    shots = [_make_shot(1), _make_shot(2)]

    result = editor.render(
        production_id=42, production_name="Test",
        shots=shots, output_dir=str(tmp_path),
    )

    assert i2v.i2v_from_image.call_count == 2
    assert len(result.clip_paths) == 2
    # Verify the LoRAs were passed through
    first_call = i2v.i2v_from_image.call_args_list[0].kwargs
    assert first_call["loras"] == ["/loras/dean.safetensors"]
    assert first_call["duration_seconds"] == 3.0


def test_render_skips_vo_when_no_dialogue(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)

    shots = [_make_shot(1, dialogue=None), _make_shot(2, dialogue="Hello")]
    result = editor.render(
        production_id=1, production_name="X",
        shots=shots, output_dir=str(tmp_path),
    )

    # Only one VO call (the second shot)
    assert audio.tts.call_count == 1
    audio.tts.assert_called_with(
        text="Hello", voice="default",
        output_path=str(Path(tmp_path) / "audio" / "shot_2_vo.wav"),
    )
    assert result.voiceover_paths[0] is None
    assert result.voiceover_paths[1] is not None


def test_render_generates_one_music_track(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)

    shots = [_make_shot(1, duration=4.0), _make_shot(2, duration=3.5)]
    editor.render(
        production_id=1, production_name="X",
        shots=shots, output_dir=str(tmp_path),
        music_mood="hopeful",
    )

    assert audio.generate_music.call_count == 1
    call = audio.generate_music.call_args.kwargs
    assert call["mood"] == "hopeful"
    assert call["duration_seconds"] == 7.5  # sum of shot durations


def test_render_calls_ffmpeg_concat_at_end(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)

    result = editor.render(
        production_id=1, production_name="X",
        shots=[_make_shot(1, dialogue="hi")], output_dir=str(tmp_path),
    )

    ffmpeg.concat_with_audio.assert_called_once()
    kwargs = ffmpeg.concat_with_audio.call_args.kwargs
    assert len(kwargs["video_clips"]) == 1
    assert kwargs["voiceovers"] == [str(Path(tmp_path) / "audio" / "shot_1_vo.wav")]
    assert kwargs["music_track"] == str(Path(tmp_path) / "audio" / "score.wav")
    assert result.final_mp4_path == str(Path(tmp_path) / "final.mp4")


def test_render_populates_video_editor_when_provided(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    video_editor = MagicMock()
    video_editor.create_project_with_timeline.return_value = 99

    editor = Editor(
        i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg,
        video_editor=video_editor,
    )
    result = editor.render(
        production_id=1, production_name="My Project",
        shots=[_make_shot(1)], output_dir=str(tmp_path),
    )

    video_editor.create_project_with_timeline.assert_called_once()
    kwargs = video_editor.create_project_with_timeline.call_args.kwargs
    assert kwargs["name"] == "My Project"
    assert result.video_project_id == 99


def test_render_skips_video_editor_when_not_provided(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)
    result = editor.render(
        production_id=1, production_name="X",
        shots=[_make_shot(1)], output_dir=str(tmp_path),
    )
    assert result.video_project_id is None


def test_render_creates_output_directories(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)
    editor.render(
        production_id=1, production_name="X",
        shots=[_make_shot(1)], output_dir=str(tmp_path / "outputs" / "prod_1"),
    )
    assert (tmp_path / "outputs" / "prod_1" / "clips").exists()
    assert (tmp_path / "outputs" / "prod_1" / "audio").exists()


def test_render_raises_on_empty_shots(tmp_path):
    """M3: an empty shots list must fail loudly — no music gen, no ffmpeg crash.
    A Production with zero shots is an upstream bug (Screenwriter returned nothing);
    fail_stage upstream is the right place to handle it, not silent success."""
    import pytest as _pt
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)
    with _pt.raises(ValueError, match="empty"):
        editor.render(
            production_id=1, production_name="X",
            shots=[], output_dir=str(tmp_path),
        )
    # Must NOT have called any generators
    assert audio.generate_music.call_count == 0
    assert ffmpeg.concat_with_audio.call_count == 0
    assert i2v.i2v_from_image.call_count == 0
