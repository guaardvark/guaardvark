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
        lora_paths=lora_paths or ["/loras/alex.safetensors"],
    )


def _make_editor(tmp_path):
    i2v = MagicMock()
    i2v.i2v_from_image.side_effect = lambda **kw: kw["output_path"]
    audio = MagicMock()
    audio.tts.side_effect = lambda **kw: kw["output_path"]
    audio.generate_music.side_effect = lambda **kw: kw["output_path"]
    ffmpeg = MagicMock()
    ffmpeg.concat_with_audio.side_effect = lambda **kw: kw["output_path"]
    ffmpeg.probe_duration.side_effect = lambda p: 3.0
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
    assert first_call["loras"] == ["/loras/alex.safetensors"]
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


def test_render_composes_timeline_when_video_editor_provided(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    video_editor = MagicMock()
    video_editor.compose_arrangement.return_value = {"mlt_path": "/tmp/proj.mlt"}

    editor = Editor(
        i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg,
        video_editor=video_editor,
    )
    result = editor.render(
        production_id=1, production_name="My Project",
        shots=[_make_shot(1), _make_shot(2)], output_dir=str(tmp_path),
    )

    video_editor.compose_arrangement.assert_called_once()
    kwargs = video_editor.compose_arrangement.call_args.kwargs
    # Two shots laid end-to-end into the arrangement
    assert len(kwargs["clips"]) == 2
    assert kwargs["clips"][0]["clip_id"] == "shot_1"
    assert kwargs["render_mp4"] is False
    assert result.mlt_path == "/tmp/proj.mlt"


def test_render_skips_timeline_when_video_editor_not_provided(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)
    result = editor.render(
        production_id=1, production_name="X",
        shots=[_make_shot(1)], output_dir=str(tmp_path),
    )
    assert result.mlt_path is None


def test_render_video_only_when_audio_foundry_none(tmp_path):
    """AudioFoundry plugin down → no VO, no music, but the render still completes."""
    i2v, _audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=None, ffmpeg=ffmpeg)
    result = editor.render(
        production_id=1, production_name="X",
        shots=[_make_shot(1, dialogue="hi")], output_dir=str(tmp_path),
    )
    assert result.music_path is None
    assert result.voiceover_paths == [None]
    ffmpeg.concat_with_audio.assert_called_once()


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


# ── scene windows (a model that renders picture and sound together) ──────────

def _scene_shot(num, scene, duration=4.0, dialogue=None):
    s = _make_shot(num, duration=duration, dialogue=dialogue)
    s.scene_number = scene
    s.scene_mood = "tense"
    s.character_name = "Mara" if dialogue else None
    return s


def test_plan_windows_groups_consecutive_shots_per_scene_under_the_cap(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg, scene_renderer=MagicMock(), max_scene_seconds=15.0)
    shots = [
        _scene_shot(1, 1, 5), _scene_shot(2, 1, 5), _scene_shot(3, 1, 6),   # 16 s: splits after two
        _scene_shot(4, 2, 4), _scene_shot(5, 2, 20),                        # scene change; 20 s alone
    ]
    windows = editor.plan_windows(shots)
    assert [[s.shot_number for s in w] for w in windows] == [[1, 2], [3], [4], [5]]


def test_scene_windows_replace_per_shot_i2v_and_voiceover(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    renderer = MagicMock()
    renderer.render_scene.side_effect = lambda **kw: kw["output_path"]
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg, scene_renderer=renderer)
    shots = [
        _scene_shot(1, 1, 5, dialogue="Watch your dog!"),
        _scene_shot(2, 1, 5, dialogue="He likes cookies."),
        _scene_shot(3, 2, 4),
    ]
    result = editor.render(production_id=7, production_name="p", shots=shots, output_dir=str(tmp_path))

    assert renderer.render_scene.call_count == 2
    first_call = renderer.render_scene.call_args_list[0].kwargs
    assert [s.shot_number for s in first_call["shots"]] == [1, 2]
    assert first_call["first_frame"] == "/tmp/storyboard/shot_1.png"
    # The next window's first still ends this window.
    assert first_call["last_frame"] == "/tmp/storyboard/shot_3.png"
    assert first_call["duration_seconds"] == 10.0
    second_call = renderer.render_scene.call_args_list[1].kwargs
    assert second_call["last_frame"] is None
    # No per-shot I2V, no TTS: the model spoke the lines.
    i2v.i2v_from_image.assert_not_called()
    audio.tts.assert_not_called()
    # The window clips carry their own audio and ride the voiceover bed.
    concat = ffmpeg.concat_with_audio.call_args.kwargs
    assert concat["video_clips"] == result.clip_paths and len(result.clip_paths) == 2
    assert concat["voiceovers"] == result.clip_paths
    assert result.clip_paths[0].endswith("scene_1_window_1.mp4")


def test_without_a_scene_renderer_the_per_shot_path_is_unchanged(tmp_path):
    i2v, audio, ffmpeg = _make_editor(tmp_path)
    editor = Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)
    shots = [_scene_shot(1, 1, 3, dialogue="Hi")]
    editor.render(production_id=7, production_name="p", shots=shots, output_dir=str(tmp_path))
    assert i2v.i2v_from_image.call_count == 1 and audio.tts.call_count == 1
