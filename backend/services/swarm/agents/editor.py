"""Editor — the final swarm agent. Orchestrates I2V, VO, music, ffmpeg
auto-cut, and Video Editor timeline population.

Like the Storyboard Artist, this is generation-driven (calls injected service
clients, doesn't talk to an LLM). The intent is one entry point, `render(...)`,
that the production_service hands control to once the storyboard is approved.

For v1 the cuts are intentional:
- One char LoRA per shot (no multi-Subject stacking)
- One global TTS voice (per-Subject voices land in v1.3)
- One music track for the whole production (per-scene music in v1.2+)
- Video Editor timeline population is a stub (the editor itself isn't built yet)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class I2VGenerator(Protocol):
    def i2v_from_image(
        self, *, image_path: str, prompt: str, loras: list[str],
        duration_seconds: float, output_path: str,
    ) -> str:
        ...


class AudioFoundry(Protocol):
    def tts(self, *, text: str, voice: str, output_path: str) -> str:
        ...

    def generate_music(self, *, mood: str, duration_seconds: float, output_path: str) -> str:
        ...


class FFmpegRunner(Protocol):
    def concat_with_audio(
        self, *, video_clips: list[str], voiceovers: list[str | None],
        music_track: str | None, output_path: str,
    ) -> str:
        ...


class VideoEditorClient(Protocol):
    def create_project_with_timeline(
        self, *, name: str, video_clips: list[str],
        voiceovers: list[str | None], music_track: str | None,
    ) -> int:
        """Returns the new VideoProject id. Stub-friendly until the editor ships."""
        ...


@dataclass
class ShotInput:
    shot_number: int
    storyboard_image_path: str
    image_prompt: str
    duration_seconds: float
    dialogue_text: str | None
    lora_paths: list[str]


@dataclass
class RenderResult:
    final_mp4_path: str
    video_project_id: int | None  # None if Video Editor stubbed out
    clip_paths: list[str]
    voiceover_paths: list[str | None]
    music_path: str | None


class Editor:
    """The final stage — turns approved storyboard frames into a finished MP4."""

    name = "editor"

    def __init__(
        self,
        *,
        i2v: I2VGenerator,
        audio_foundry: AudioFoundry,
        ffmpeg: FFmpegRunner,
        video_editor: VideoEditorClient | None = None,
    ):
        self.i2v = i2v
        self.audio_foundry = audio_foundry
        self.ffmpeg = ffmpeg
        self.video_editor = video_editor

    def render(
        self,
        *,
        production_id: int,
        production_name: str,
        shots: list[ShotInput],
        output_dir: str,
        voice: str = "default",
        music_mood: str = "neutral",
    ) -> RenderResult:
        """Render the full production. Output paths land under `output_dir`.

        Per-shot work (I2V + per-line VO) is currently sequential. Parallelization
        is a v1.x optimization gated by the JobOperationGate (handled by the
        caller — production_service.gpu_stage wraps this method).
        """
        output_path = Path(output_dir)
        clips_dir = output_path / "clips"
        audio_dir = output_path / "audio"
        clips_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

        clip_paths: list[str] = []
        voiceover_paths: list[str | None] = []

        for shot in shots:
            clip_path = self._render_clip(shot, clips_dir)
            clip_paths.append(clip_path)
            vo_path = self._render_voiceover(shot, audio_dir, voice)
            voiceover_paths.append(vo_path)

        total_duration = sum(s.duration_seconds for s in shots) or 1.0
        music_path = self._render_music(music_mood, total_duration, audio_dir)

        final_mp4 = str(output_path / "final.mp4")
        self.ffmpeg.concat_with_audio(
            video_clips=clip_paths,
            voiceovers=voiceover_paths,
            music_track=music_path,
            output_path=final_mp4,
        )

        video_project_id = None
        if self.video_editor is not None:
            video_project_id = self.video_editor.create_project_with_timeline(
                name=production_name,
                video_clips=clip_paths,
                voiceovers=voiceover_paths,
                music_track=music_path,
            )

        return RenderResult(
            final_mp4_path=final_mp4,
            video_project_id=video_project_id,
            clip_paths=clip_paths,
            voiceover_paths=voiceover_paths,
            music_path=music_path,
        )

    # --- internals ----------------------------------------------------------

    def _render_clip(self, shot: ShotInput, clips_dir: Path) -> str:
        clip_path = str(clips_dir / f"shot_{shot.shot_number}.mp4")
        return self.i2v.i2v_from_image(
            image_path=shot.storyboard_image_path,
            prompt=shot.image_prompt,
            loras=shot.lora_paths,
            duration_seconds=shot.duration_seconds,
            output_path=clip_path,
        )

    def _render_voiceover(self, shot: ShotInput, audio_dir: Path, voice: str) -> str | None:
        if not shot.dialogue_text:
            return None
        vo_path = str(audio_dir / f"shot_{shot.shot_number}_vo.wav")
        return self.audio_foundry.tts(
            text=shot.dialogue_text, voice=voice, output_path=vo_path,
        )

    def _render_music(self, mood: str, duration_seconds: float, audio_dir: Path) -> str:
        music_path = str(audio_dir / "score.wav")
        return self.audio_foundry.generate_music(
            mood=mood, duration_seconds=duration_seconds, output_path=music_path,
        )
