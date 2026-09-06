"""Concrete service clients for the Film Crew Editor.

The Editor agent talks to three Protocols — AudioFoundry, FFmpegRunner and
VideoEditorClient — but until now the only implementations lived in the test
suite as MagicMocks. That meant the production `run_editor` Celery task (which
injects nothing) would crash the moment it touched audio or ffmpeg.

These are the real ones:
- AudioFoundryClient  → HTTP to the audio_foundry plugin (:8206) for TTS + music.
- FfmpegRunner        → subprocess ffmpeg: concat clips + mix VO/music → final mp4.
- VideoEditorComposeClient → HTTP to the video_editor plugin (via the Flask
  proxy) to compose an editable Shotcut/MLT timeline from the rendered clips.

All three degrade gracefully when their plugin is down: the audio client and the
compose client return None, and the Editor falls back to a video-only / ffmpeg
path. The pipeline never hard-fails just because an optional plugin is offline.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_AUDIO_FOUNDRY_URL = os.environ.get("AUDIO_FOUNDRY_URL", "http://127.0.0.1:8206").rstrip("/")
_GEN_TIMEOUT_S = 600  # ACE-Step songs + Chatterbox long text both run long


def _service_up(base_url: str, timeout: float = 2.0) -> bool:
    try:
        return requests.get(f"{base_url}/health", timeout=timeout).status_code == 200
    except requests.exceptions.RequestException:
        return False


class AudioFoundryClient:
    """Implements the Editor's AudioFoundry protocol against the :8206 plugin.

    Remote-capable: the plugin returns a logical ``path`` and we fetch the bytes
    over HTTP via its /view endpoint (mirrors how the ComfyUI client downloads
    outputs). Works whether the plugin is on this machine or another — just point
    AUDIO_FOUNDRY_URL at it. If the plugin is down, methods raise — the Editor
    catches and treats audio as absent (see Editor._safe_audio).
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or _AUDIO_FOUNDRY_URL).rstrip("/")

    def available(self) -> bool:
        return _service_up(self.base_url)

    def _generate(self, path: str, payload: dict, output_path: str) -> str:
        resp = requests.post(f"{self.base_url}{path}", json=payload, timeout=_GEN_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        src = data.get("path") or data.get("output_path") or data.get("file")
        if not src:
            raise RuntimeError(f"audio_foundry returned no usable path: {data!r}")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        # Fetch the file bytes over HTTP (remote-capable, mirrors ComfyUI /view).
        dl = requests.get(f"{self.base_url}/view", params={"path": src}, timeout=_GEN_TIMEOUT_S)
        dl.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(dl.content)
        return output_path

    def tts(self, *, text: str, voice: str, output_path: str) -> str:
        payload = {"text": text, "backend": "auto", "output_format": "wav"}
        # The Editor passes a voice *id*; "default" means "let the backend choose".
        if voice and voice != "default":
            payload["voice_id"] = voice
        return self._generate("/generate/voice", payload, output_path)

    def generate_music(self, *, mood: str, duration_seconds: float, output_path: str) -> str:
        payload = {
            "style_prompt": mood,
            "duration_s": max(1.0, float(duration_seconds)),
            "instrumental_only": True,
            "output_format": "wav",
        }
        return self._generate("/generate/music", payload, output_path)


class FfmpegRunner:
    """Subprocess ffmpeg: concatenate shot clips and mix VO + music into a
    finished MP4. This is also the fallback final-render when the Shotcut/MLT
    plugin is unavailable."""

    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    # -- helpers ----------------------------------------------------------
    def _run(self, args: list[str]) -> None:
        proc = subprocess.run(
            [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-500:]}")

    def probe_duration(self, path: str) -> float:
        proc = subprocess.run(
            [self.ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True,
        )
        try:
            return float(proc.stdout.strip())
        except (ValueError, AttributeError):
            return 0.0

    # -- public API (FFmpegRunner protocol) -------------------------------
    def concat_with_audio(
        self, *, video_clips: list[str], voiceovers: list[str | None],
        music_track: str | None, output_path: str,
    ) -> str:
        if not video_clips:
            raise ValueError("concat_with_audio: no video clips")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        work = out.parent

        # 1) Concat video (re-encode via concat filter — robust to clips that
        #    differ in fps/resolution/codec, which SVD vs CogVideo output can).
        silent_video = str(work / "_concat_video.mp4")
        inputs: list[str] = []
        for clip in video_clips:
            inputs += ["-i", clip]
        n = len(video_clips)
        concat_filter = "".join(f"[{i}:v:0]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        self._run([*inputs, "-filter_complex", concat_filter, "-map", "[v]",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", silent_video])

        # 2) Build the mixed audio bed: each VO delayed to its shot's start
        #    offset, plus the music track at reduced volume.
        offsets: list[float] = []
        acc = 0.0
        for clip in video_clips:
            offsets.append(acc)
            acc += self.probe_duration(clip)
        total = acc

        audio_inputs: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        idx = 0
        for vo, off in zip(voiceovers, offsets):
            if not vo:
                continue
            audio_inputs += ["-i", vo]
            delay_ms = int(off * 1000)
            filters.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1
        if music_track:
            audio_inputs += ["-i", music_track]
            filters.append(f"[{idx}:a]volume=0.35,atrim=0:{total:.3f}[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1

        if labels:
            mixed_audio = str(work / "_mixed_audio.m4a")
            amix = "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]"
            self._run([*audio_inputs, "-filter_complex", ";".join(filters) + ";" + amix,
                       "-map", "[aout]", "-c:a", "aac", mixed_audio])
            # 3) Mux concatenated video + mixed audio.
            self._run(["-i", silent_video, "-i", mixed_audio, "-map", "0:v", "-map", "1:a",
                       "-c:v", "copy", "-c:a", "copy", "-shortest", str(out)])
        else:
            # No audio at all — the silent concat IS the final render.
            shutil.move(silent_video, str(out))

        for tmp in ("_concat_video.mp4", "_mixed_audio.m4a"):
            try:
                (work / tmp).unlink(missing_ok=True)
            except OSError:
                pass
        return str(out)


class VideoEditorComposeClient:
    """Composes an editable Shotcut/MLT timeline from the rendered shot clips
    via the video_editor plugin (through the Flask proxy at /api/video-editor).

    Returns the composed .mlt path so the caller can register it as the
    production's editable timeline. None if the plugin is unavailable — the
    Editor still has its ffmpeg final.mp4, so this is purely additive.
    """

    def __init__(self, backend_url: str):
        # e.g. "http://localhost:5002/api"
        self.backend_url = backend_url.rstrip("/")

    def compose_arrangement(
        self, *, clips: list[dict], audio_path: str | None,
        song_duration_seconds: float | None = None,
        fps_num: int = 30, fps_den: int = 1,
        width: int = 1920, height: int = 1080,
        render_mp4: bool = False,
    ) -> dict | None:
        """clips: list of ArrangedClip dicts (source_path, timeline_start/end,
        source_in/out, filter_preset, transition_to_next, clip_id, section_label).
        Returns the plugin response ({mlt_path, rendered_mp4, documents, ...}) or None."""
        payload = {
            "arrangement": {"style_recipe_name": "default", "seed": 0, "clips": clips},
            "audio_path": audio_path,
            "song_duration_seconds": song_duration_seconds,
            "fps_num": fps_num, "fps_den": fps_den,
            "width": width, "height": height,
            "render_mp4": render_mp4,
            "register": False,  # the backend registers it with production folders itself
        }
        try:
            resp = requests.post(
                f"{self.backend_url}/video-editor/shotcut/compose-arrangement",
                json=payload, timeout=1200,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 — optional path, never fatal
            logger.warning("Video Editor compose unavailable: %s", e)
            return None
