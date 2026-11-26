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
import re
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


def _builtin_voice(voice: str | None) -> str | None:
    """``voice`` when it is one of Audio Foundry's built-in (Kokoro) voices, else None.

    A Cast member's voice_id is free text (typed on the Cast page, or picked by
    the Casting Director). Audio Foundry refuses a voice id it does not offer,
    and the Editor then renders the shot with no voiceover at all, so an id
    that is not in the catalog is left out and the default voice speaks the
    line; the log names the id.
    """
    voice = (voice or "").strip()
    if not voice or voice == "default":
        return None
    try:
        from backend.services.audio_foundry_models import kokoro_voice_ids
        known = kokoro_voice_ids()
    except Exception as e:  # noqa: BLE001 - the catalog ships in the checkout; never block a render on it
        logger.warning("Kokoro voice catalog unreadable (%s); sending voice %r unchecked", e, voice)
        return voice
    if voice in known:
        return voice
    logger.warning("Voice %r is not an Audio Foundry voice; the default voice speaks this line", voice)
    return None


def build_voice_record(requested: str | None, sent: str | None, meta: dict) -> dict:
    """What a Film Crew line was spoken in, from the request and Audio Foundry's meta.

    ``fallbacks`` lists every way the line ended up in a voice other than the
    one asked for, in plain words, so the production view can show it: a Cast
    voice id that is not a built-in voice (the default voice spoke instead),
    and Chatterbox failing so Kokoro spoke the line (meta["fallback"]).
    """
    requested = (requested or "").strip() or None
    if requested == "default":
        requested = None
    backend = meta.get("backend")
    spoken = meta.get("voice") or ("stock voice" if backend == "chatterbox" else None)
    fallbacks = []
    if requested and not sent:
        fallbacks.append({
            "kind": "voice_not_built_in",
            "message": f"Voice '{requested}' is not one of Audio Foundry's built-in voices; "
                       "the default voice spoke this line.",
        })
    fb = meta.get("fallback")
    if isinstance(fb, dict):
        fallbacks.append({
            "kind": "engine_fallback",
            "message": f"{str(fb.get('from') or 'Chatterbox').capitalize()} failed "
                       f"({fb.get('reason') or 'no reason given'}); "
                       f"{str(fb.get('to') or 'Kokoro').capitalize()}"
                       f"{f' ({spoken})' if spoken else ''} spoke this line.",
        })
    return {
        "requested_voice": requested,
        "voice_id_sent": sent,
        "backend": backend,
        "voice": spoken,
        "fallbacks": fallbacks,
    }


class AudioFoundryClient:
    """Implements the Editor's AudioFoundry protocol against the :8206 plugin.

    Same-machine, so the plugin returns an absolute path we copy to where the
    Editor wants it. If the plugin is down, methods raise — the Editor catches
    and treats audio as absent (see Editor._safe_audio).
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or _AUDIO_FOUNDRY_URL).rstrip("/")
        # Per output file: the plugin's response meta, and the voice record
        # tts builds from it (voice_record). Keyed by path, so parallel shot
        # renders do not share a slot.
        self._responses: dict[str, dict] = {}
        self._voice_records: dict[str, dict] = {}

    def available(self) -> bool:
        return _service_up(self.base_url)

    def _generate(self, path: str, payload: dict, output_path: str) -> str:
        resp = requests.post(f"{self.base_url}{path}", json=payload, timeout=_GEN_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        src = data.get("path") or data.get("output_path") or data.get("file")
        if not src or not Path(src).is_file():
            raise RuntimeError(f"audio_foundry returned no usable path: {data!r}")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, output_path)
        self._responses[output_path] = data
        return output_path

    def tts(self, *, text: str, voice: str, output_path: str) -> str:
        payload = {"text": text, "backend": "auto", "output_format": "wav"}
        # The Editor passes a voice *id*; "default" means "let the backend choose".
        # A built-in voice id makes Audio Foundry's auto mode speak it with
        # Kokoro (plugins/audio_foundry/backends/voice_gen.py).
        voice_id = _builtin_voice(voice)
        if voice_id:
            payload["voice_id"] = voice_id
        written = self._generate("/generate/voice", payload, output_path)
        meta = (self._responses.pop(output_path, None) or {}).get("meta") or {}
        self._voice_records[written] = build_voice_record(voice, voice_id, meta)
        return written

    def voice_record(self, output_path: str) -> dict | None:
        """Which voice spoke the line written to ``output_path``, and any fallback."""
        return self._voice_records.get(output_path)

    def generate_music(self, *, mood: str, duration_seconds: float, output_path: str) -> str:
        payload = {
            "style_prompt": mood,
            "duration_s": max(1.0, float(duration_seconds)),
            "instrumental_only": True,
            "output_format": "wav",
        }
        model = _sidecar_music_model()
        if model:
            payload["model"] = model
        return self._generate("/generate/music", payload, output_path)


def _sidecar_music_model() -> str | None:
    """The music model to name in a request to the sidecar, or None for its own default.

    The sidecar runs ACE-Step v1 when no model is named, whether or not v1 is
    installed. The installed default is named instead when it is another model
    the sidecar itself runs; MiniMax Music 3 renders through ComfyUI, which
    this client does not call.
    """
    try:
        from backend.services.audio_foundry_models import (
            MUSIC_CATALOG_DEFAULT, installed_default_music_model,
        )
        model = installed_default_music_model()
    except Exception as e:  # noqa: BLE001 - the score is optional; ask as before
        logger.warning("installed music model not resolved: %s", e)
        return None
    if model == MUSIC_CATALOG_DEFAULT or model.startswith("minimax-music3"):
        return None
    return model


# Loudness of a finished film, in LUFS. Each spoken line is compressed and
# levelled to the speech target, the score sits 9 dB under it, and the whole
# mix is brought to the speech target under a limiter. Measured on production 7:
# - Kokoro lines came out between -17 and -22 LUFS with peaks 18-22 dB above
#   that, and the ACE-Step score at -14; summed unlevelled (amix scaling each of
#   10 inputs by 1/10) the film played at -32.6 LUFS.
# - Levelled without compression the peaks reached +5 dBFS, and the limiter
#   took the film down to -18 LUFS. The compressor below brings a line's peaks
#   to 13-16 dB above its loudness, and the film lands at -16.6 LUFS.
# - AAC adds about 1.5 dB between samples, so a -3 dBFS sample ceiling keeps
#   the true peak under -1 dBTP.
FILM_SPEECH_LUFS = -16.0
FILM_MUSIC_LUFS = FILM_SPEECH_LUFS - 9.0
FILM_SPEECH_COMPRESSOR = "acompressor=threshold=0.0631:ratio=4:attack=3:release=80"  # 4:1 above -24 dBFS
FILM_PEAK_LIMIT = 0.71          # linear sample ceiling, -3 dBFS
_MAX_GAIN_DB = 24.0             # a near-silent stem is not lifted into noise
_SILENT_LUFS = -60.0


def _gain_db(measured_lufs: float | None, target_lufs: float) -> float:
    """Gain that brings a stem to the target, 0 when it is silent or unmeasured."""
    if measured_lufs is None or measured_lufs <= _SILENT_LUFS:
        return 0.0
    return max(-_MAX_GAIN_DB, min(_MAX_GAIN_DB, target_lufs - measured_lufs))


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

    def measure_loudness(self, path: str, pre_filter: str | None = None) -> float | None:
        """Integrated loudness of the file's first audio stream in LUFS, after
        `pre_filter` when one is given, or None when it has no audio stream or
        ffmpeg cannot read it."""
        chain = f"{pre_filter},ebur128=framelog=quiet" if pre_filter else "ebur128=framelog=quiet"
        proc = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
             "-af", chain, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return None
        found = re.findall(r"^\s+I:\s+(-?[\d.]+|-inf) LUFS", proc.stderr, re.MULTILINE)
        try:
            return float(found[-1]) if found else None
        except ValueError:
            return None

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

        # 2) Build the mixed audio bed: each VO levelled and delayed to its
        #    shot's start offset (compressed first, so its peaks leave room
        #    at speech level), plus the music track levelled under them.
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
            gain = _gain_db(self.measure_loudness(vo, FILM_SPEECH_COMPRESSOR), FILM_SPEECH_LUFS)
            filters.append(f"[{idx}:a]{FILM_SPEECH_COMPRESSOR},volume={gain:.2f}dB,"
                           f"adelay={delay_ms}|{delay_ms}[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1
        if music_track:
            audio_inputs += ["-i", music_track]
            gain = _gain_db(self.measure_loudness(music_track), FILM_MUSIC_LUFS)
            filters.append(f"[{idx}:a]volume={gain:.2f}dB,atrim=0:{total:.3f}[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1

        if labels:
            mixed_raw = str(work / "_mixed_raw.wav")
            mixed_audio = str(work / "_mixed_audio.m4a")
            # Pad the bed to the film's length: -shortest below would otherwise
            # cut the picture where the last line or the score ends. An apad
            # without a length never ends when the output is WAV. The stems are
            # already levelled, so amix sums them instead of dividing each by
            # the input count.
            amix = ("".join(labels)
                    + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,"
                    + f"apad=whole_dur={total:.3f},atrim=0:{total:.3f}[aout]")
            self._run([*audio_inputs, "-filter_complex", ";".join(filters) + ";" + amix,
                       "-map", "[aout]", "-c:a", "pcm_f32le", mixed_raw])
            # One static gain for the whole film keeps the balance set above;
            # the limiter only catches peaks the gain pushes past its ceiling.
            gain = _gain_db(self.measure_loudness(mixed_raw), FILM_SPEECH_LUFS)
            self._run(["-i", mixed_raw, "-af",
                       f"volume={gain:.2f}dB,alimiter=limit={FILM_PEAK_LIMIT}:level=0:latency=1",
                       "-c:a", "aac", "-b:a", "192k", mixed_audio])
            # 3) Mux concatenated video + mixed audio.
            self._run(["-i", silent_video, "-i", mixed_audio, "-map", "0:v", "-map", "1:a",
                       "-c:v", "copy", "-c:a", "copy", "-shortest", str(out)])
        else:
            # No audio at all — the silent concat IS the final render.
            shutil.move(silent_video, str(out))

        for tmp in ("_concat_video.mp4", "_mixed_raw.wav", "_mixed_audio.m4a"):
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
        # e.g. "http://127.0.0.1:5000/api"
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
