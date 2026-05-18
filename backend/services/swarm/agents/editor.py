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

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


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


class LipsyncGenerator(Protocol):
    def lipsync(self, *, video_path: str, audio_path: str, output_path: str) -> str:
        ...


class FFmpegRunner(Protocol) :
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
    voice_id: str | None = None
    scene_number: int | None = None
    scene_mood: str | None = None


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
        lipsync: LipsyncGenerator | None = None,
    ):
        self.i2v = i2v
        self.audio_foundry = audio_foundry
        self.ffmpeg = ffmpeg
        self.video_editor = video_editor
        self.lipsync = lipsync

    def render(
        self,
        *,
        production_id: int,
        production_name: str,
        shots: list[ShotInput],
        output_dir: str,
        default_voice: str = "default",
        music_mood: str = "neutral",
    ) -> RenderResult:
        """Render the full production. Output paths land under `output_dir`.

        Per-shot work (I2V + per-line VO) is currently sequential. Parallelization
        is a v1.x optimization gated by the JobOperationGate (handled by the
        caller — production_service.gpu_stage wraps this method).
        """
        # M3: refuse to render an empty production. Fails loudly so the upstream
        # caller (production_service) can fail_stage with a clear error rather
        # than letting ffmpeg blow up on empty inputs.
        if not shots:
            raise ValueError(f"Cannot render production {production_id}: shots list is empty")

        output_path = Path(output_dir)
        clips_dir = output_path / "clips"
        audio_dir = output_path / "audio"
        clips_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

        clip_paths: list[str] = []
        voiceover_paths: list[str | None] = []

        from backend.config import FILM_CREW_PARALLEL_RENDER
        from concurrent.futures import ThreadPoolExecutor

        if FILM_CREW_PARALLEL_RENDER:
            logger.info(f"Starting parallel render for production {production_id}")
            with ThreadPoolExecutor(max_workers=3) as executor:
                def render_one_shot(shot: ShotInput) -> tuple[str, str | None]:
                    clip = self._render_clip(shot, clips_dir)
                    shot_voice = shot.voice_id or default_voice
                    vo = self._render_voiceover(shot, audio_dir, shot_voice)
                    
                    from backend.config import FILM_CREW_LIPSYNC_ENABLED
                    if FILM_CREW_LIPSYNC_ENABLED and self.lipsync and vo:
                        synced = str(clips_dir / f"shot_{shot.shot_number}_synced.mp4")
                        clip = self.lipsync.lipsync(video_path=clip, audio_path=vo, output_path=synced)
                    
                    self._emit_progress(production_id, shot.shot_number, clip)
                    return clip, vo

                results = list(executor.map(render_one_shot, shots))
                clip_paths = [r[0] for r in results]
                voiceover_paths = [r[1] for r in results]
        else:
            for shot in shots:
                clip_path = self._render_clip(shot, clips_dir)
                shot_voice = shot.voice_id or default_voice
                vo_path = self._render_voiceover(shot, audio_dir, shot_voice)

                from backend.config import FILM_CREW_LIPSYNC_ENABLED
                if FILM_CREW_LIPSYNC_ENABLED and self.lipsync and vo_path:
                    synced_path = str(clips_dir / f"shot_{shot.shot_number}_synced.mp4")
                    clip_path = self.lipsync.lipsync(video_path=clip_path, audio_path=vo_path, output_path=synced_path)

                clip_paths.append(clip_path)
                voiceover_paths.append(vo_path)
                self._emit_progress(production_id, shot.shot_number, clip_path)

        # Phase 1.3: Per-scene music
        scenes_map: dict[int, list[ShotInput]] = {}
        for s in shots:
            if s.scene_number is not None:
                scenes_map.setdefault(s.scene_number, []).append(s)
        
        music_tracks: list[str] = []
        if scenes_map:
            sorted_scene_ids = sorted(scenes_map.keys())
            for sid in sorted_scene_ids:
                scene_shots = scenes_map[sid]
                mood = scene_shots[0].scene_mood or music_mood
                duration = sum(s.duration_seconds for s in scene_shots)
                scene_music_path = self._render_music(mood, duration, audio_dir, suffix=f"_scene_{sid}")
                music_tracks.append(scene_music_path)
            
            # Use the first track or implement concat logic if needed
            music_path = music_tracks[0] if music_tracks else None
        else:
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

    def _render_music(self, mood: str, duration_seconds: float, audio_dir: Path, suffix: str = "") -> str:
        music_path = str(audio_dir / f"score{suffix}.wav")
        return self.audio_foundry.generate_music(
            mood=mood, duration_seconds=duration_seconds, output_path=music_path,
        )

    def _emit_progress(self, production_id: int, shot_number: int, clip_path: str):
        """Emit WebSocket event for shot completion."""
        try:
            from backend.socketio_instance import socketio
            # In test environments, socketio might not have a server attached
            if socketio and getattr(socketio, 'server', None):
                socketio.emit("production:shot_complete", {
                    "production_id": production_id,
                    "shot_number": shot_number,
                    "clip_path": clip_path
                }, namespace="/api/production")
        except Exception as e:
            logger.warning(f"Failed to emit progress: {e}")

