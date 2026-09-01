"""Editor — the final swarm agent. Orchestrates I2V, VO, music, ffmpeg
auto-cut, and Video Editor timeline population.

Like the Storyboard Artist, this is generation-driven (calls injected service
clients, doesn't talk to an LLM). The intent is one entry point, `render(...)`,
that the production_service hands control to once the storyboard is approved.

For v1 the cuts are intentional:
- One music track for the whole production (per-scene music in v1.2+)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


class SceneGenerator(Protocol):
    """Renders a run of consecutive shots as one clip with its own soundtrack
    (MiniMax H3): dialogue is spoken by the model, so no voiceover is laid over
    the window. Returns the clip path."""
    def render_scene(
        self, *, shots: list, first_frame: str, last_frame: str | None,
        output_path: str, duration_seconds: float, scene_mood: str | None = None,
    ) -> str: ...


class VideoEditorClient(Protocol):
    def compose_arrangement(
        self, *, clips: list[dict], audio_path: str | None,
        song_duration_seconds: float | None = None,
        render_mp4: bool = False,
    ) -> dict | None:
        """Compose the rendered shots into an editable Shotcut/MLT timeline.
        Returns the plugin response ({mlt_path, ...}) or None if unavailable."""
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
    # Who speaks and what the cast looks like, for a scene renderer that keeps
    # identity from reference images rather than a LoRA-locked still.
    character_name: str | None = None
    ref_image_paths: list[str] = field(default_factory=list)
    character_description: str | None = None


@dataclass
class RenderResult:
    final_mp4_path: str
    mlt_path: str | None  # editable Shotcut/MLT timeline; None if plugin down
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
        audio_foundry: AudioFoundry | None,
        ffmpeg: FFmpegRunner,
        video_editor: VideoEditorClient | None = None,
        lipsync: LipsyncGenerator | None = None,
        scene_renderer: SceneGenerator | None = None,
        max_scene_seconds: float = 15.0,
    ):
        self.i2v = i2v
        self.audio_foundry = audio_foundry
        self.ffmpeg = ffmpeg
        self.video_editor = video_editor
        self.lipsync = lipsync
        # When set, consecutive shots of a scene are rendered as one window of
        # at most max_scene_seconds by a model that generates picture and sound
        # together; the per-shot I2V + TTS path is not used for them.
        self.scene_renderer = scene_renderer
        self.max_scene_seconds = max_scene_seconds

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
        caller — run_editor in backend/tasks/production_swarm_tasks.py registers
        the render with the gate).
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
        # Shots rendered as scene windows, laid out per window (clip, shots).
        windows: list[tuple[str, list[ShotInput]]] = []

        from backend.config import FILM_CREW_PARALLEL_RENDER
        from concurrent.futures import ThreadPoolExecutor

        if self.scene_renderer is not None:
            plan = self.plan_windows(shots)
            for window_index, window in enumerate(plan):
                # The next window's first storyboard still ends this one, so
                # consecutive windows join on a frame the keyframe stage drew.
                next_first = plan[window_index + 1][0].storyboard_image_path if window_index + 1 < len(plan) else None
                clip = self._render_window(window, window_index, clips_dir, last_frame=next_first)
                windows.append((clip, window))
                for shot in window:
                    self._emit_progress(production_id, shot.shot_number, clip)
            clip_paths = [clip for clip, _ in windows]
            # The window's own soundtrack rides the voiceover bed: ffmpeg reads
            # the clip's audio stream at the window's offset and mixes the score
            # under it, so nothing here needs a second concat path.
            voiceover_paths = [clip for clip, _ in windows]
        elif FILM_CREW_PARALLEL_RENDER:
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

        # Phase 1.3: Per-scene music. Skipped entirely when AudioFoundry is
        # unavailable — the production still renders, just video-only.
        music_path: str | None = None
        if self.audio_foundry is not None:
            scenes_map: dict[int, list[ShotInput]] = {}
            for s in shots:
                if s.scene_number is not None:
                    scenes_map.setdefault(s.scene_number, []).append(s)

            if scenes_map:
                for sid in sorted(scenes_map.keys()):
                    scene_shots = scenes_map[sid]
                    mood = scene_shots[0].scene_mood or music_mood
                    duration = sum(s.duration_seconds for s in scene_shots)
                    track = self._render_music(mood, duration, audio_dir, suffix=f"_scene_{sid}")
                    # v1: the first scene's track scores the production; stitching
                    # per-scene tracks across the timeline is a v1.x refinement.
                    if track and music_path is None:
                        music_path = track
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

        # Compose an editable Shotcut/MLT timeline from the rendered clips so the
        # production can be reopened and tweaked in the Video Editor. Purely
        # additive: final_mp4 above is the deliverable, this is the project file.
        mlt_path: str | None = None
        if self.video_editor is not None:
            if windows:
                # One arrangement entry per window, labelled by its shots.
                arrangement_shots = [self._window_shot(window) for _, window in windows]
            else:
                arrangement_shots = shots
            arrangement_clips, total = self._build_arrangement(clip_paths, arrangement_shots)
            resp = self.video_editor.compose_arrangement(
                clips=arrangement_clips,
                audio_path=music_path,
                song_duration_seconds=total,
                render_mp4=False,
            )
            if resp:
                mlt_path = resp.get("mlt_path")

        return RenderResult(
            final_mp4_path=final_mp4,
            mlt_path=mlt_path,
            clip_paths=clip_paths,
            voiceover_paths=voiceover_paths,
            music_path=music_path,
        )

    def _build_arrangement(
        self, clip_paths: list[str], shots: list[ShotInput],
    ) -> tuple[list[dict], float]:
        """Lay the rendered clips end-to-end into ArrangedClip dicts the
        video_editor plugin's compose-arrangement endpoint understands. Uses the
        clip's actual probed duration when ffmpeg can measure it (SVD clamps
        frame counts, so the requested duration may not match), else the shot's
        intended duration."""
        probe = getattr(self.ffmpeg, "probe_duration", None)
        clips: list[dict] = []
        acc = 0.0
        for path, shot in zip(clip_paths, shots):
            dur = (probe(path) if probe else 0.0) or shot.duration_seconds
            clips.append({
                "clip_id": f"shot_{shot.shot_number}",
                "source_path": path,
                "section_label": f"shot {shot.shot_number}",
                "timeline_start": round(acc, 3),
                "timeline_end": round(acc + dur, 3),
                "source_in": 0.0,
                "source_out": round(dur, 3),
                "filter_preset": "none",
                "transition_to_next": "hard-cut",
            })
            acc += dur
        return clips, round(acc, 3)

    # --- scene windows --------------------------------------------------------

    def plan_windows(self, shots: list[ShotInput]) -> list[list[ShotInput]]:
        """Group consecutive shots of the same scene into windows no longer
        than max_scene_seconds. A shot longer than the limit gets its own
        window (the renderer clamps it). Order is preserved."""
        windows: list[list[ShotInput]] = []
        current: list[ShotInput] = []
        current_len = 0.0
        current_scene = None
        for shot in shots:
            dur = float(shot.duration_seconds or 0)
            same_scene = current and shot.scene_number == current_scene
            if current and (not same_scene or current_len + dur > self.max_scene_seconds):
                windows.append(current)
                current, current_len = [], 0.0
            current.append(shot)
            current_len += dur
            current_scene = shot.scene_number
        if current:
            windows.append(current)
        return windows

    def _render_window(self, window: list[ShotInput], index: int, clips_dir: Path,
                       last_frame: str | None = None) -> str:
        first = window[0]
        clip_path = str(clips_dir / f"scene_{first.scene_number or 1}_window_{index + 1}.mp4")
        duration = min(self.max_scene_seconds, sum(float(s.duration_seconds or 0) for s in window)) or 3.0
        return self.scene_renderer.render_scene(
            shots=window,
            first_frame=first.storyboard_image_path,
            last_frame=last_frame if last_frame and last_frame != first.storyboard_image_path else None,
            output_path=clip_path,
            duration_seconds=duration,
            scene_mood=first.scene_mood,
        )

    @staticmethod
    def _window_shot(window: list[ShotInput]) -> ShotInput:
        """A stand-in ShotInput describing a whole window for the arrangement."""
        first = window[0]
        return ShotInput(
            shot_number=first.shot_number,
            storyboard_image_path=first.storyboard_image_path,
            image_prompt=first.image_prompt,
            duration_seconds=sum(float(s.duration_seconds or 0) for s in window),
            dialogue_text=None,
            lora_paths=list(first.lora_paths),
            scene_number=first.scene_number,
            scene_mood=first.scene_mood,
        )

    # --- internals ----------------------------------------------------------

    def _render_clip(self, shot: ShotInput, clips_dir: Path) -> str:
        clip_path = str(
            clips_dir / f"shot_{shot.scene_number or 1}_{shot.shot_number}.mp4"
        )
        return self.i2v.i2v_from_image(
            image_path=shot.storyboard_image_path,
            prompt=shot.image_prompt,
            loras=shot.lora_paths,
            duration_seconds=shot.duration_seconds,
            output_path=clip_path,
        )

    def _render_voiceover(self, shot: ShotInput, audio_dir: Path, voice: str) -> str | None:
        if not shot.dialogue_text or self.audio_foundry is None:
            return None
        vo_path = str(
            audio_dir / f"shot_{shot.scene_number or 1}_{shot.shot_number}_vo.wav"
        )
        try:
            return self.audio_foundry.tts(
                text=shot.dialogue_text, voice=voice, output_path=vo_path,
            )
        except Exception as e:  # noqa: BLE001 — VO is best-effort, don't sink the render
            logger.warning("TTS failed for shot %s, continuing without VO: %s", shot.shot_number, e)
            return None

    def _render_music(self, mood: str, duration_seconds: float, audio_dir: Path, suffix: str = "") -> str | None:
        if self.audio_foundry is None:
            return None
        music_path = str(audio_dir / f"score{suffix}.wav")
        try:
            return self.audio_foundry.generate_music(
                mood=mood, duration_seconds=duration_seconds, output_path=music_path,
            )
        except Exception as e:  # noqa: BLE001 — score is best-effort
            logger.warning("Music generation failed, continuing without score: %s", e)
            return None

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

