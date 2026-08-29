"""Voice channel audio pipeline: Discord PCM -> Whisper STT -> LLM -> TTS -> Discord playback."""
import asyncio
import io
import logging
import threading
import time
import wave
from typing import Optional

import discord

from core.api_client import GuaardvarkClient, APIError
from core.approvals import make_approval_handler
from core.chat_reply import files_from_generated, send_chunks

try:
    from discord.ext import voice_recv
    VOICE_RECV_AVAILABLE = True
except ImportError:
    voice_recv = None
    VOICE_RECV_AVAILABLE = False

logger = logging.getLogger(__name__)

DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
# 48kHz stereo s16le
BYTES_PER_SECOND = DISCORD_SAMPLE_RATE * DISCORD_CHANNELS * 2

WATCHER_INTERVAL_S = 0.2


def pcm_to_wav(pcm_data: bytes, sample_rate: int = DISCORD_SAMPLE_RATE, channels: int = DISCORD_CHANNELS) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


if VOICE_RECV_AVAILABLE:

    class UtteranceSink(voice_recv.AudioSink):
        """Accumulates per-user PCM. write() runs on the voice-recv decoder thread,
        so it only touches plain structures under a lock; the segmentation watcher
        (asyncio side) polls and pops completed utterances."""

        def __init__(self):
            super().__init__()
            self.lock = threading.Lock()
            self.buffers: dict[int, bytearray] = {}
            self.last_packet: dict[int, float] = {}
            self.names: dict[int, str] = {}

        def wants_opus(self) -> bool:
            return False

        def write(self, user, data):
            try:
                if user is None or getattr(user, "bot", False):
                    return
                if not data.pcm:
                    return
                with self.lock:
                    self.buffers.setdefault(user.id, bytearray()).extend(data.pcm)
                    self.last_packet[user.id] = time.monotonic()
                    self.names[user.id] = getattr(user, "display_name", str(user))
            except Exception:
                logger.exception("UtteranceSink.write failed")

        def cleanup(self):
            with self.lock:
                self.buffers.clear()
                self.last_packet.clear()
                self.names.clear()


class VoiceHandler:
    def __init__(self, api_client: GuaardvarkClient, config: dict):
        self.api = api_client
        self.config = config
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel = None
        self._processing = False
        self._watch_task = None
        self._worker_task = None
        self.sink = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.session_id = "discord_voice"

    @property
    def listening(self) -> bool:
        return self.sink is not None and self.voice_client is not None and self.voice_client.is_connected()

    async def join(self, channel: discord.VoiceChannel, text_channel) -> bool:
        try:
            if VOICE_RECV_AVAILABLE:
                self.voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)
                self.sink = UtteranceSink()
                self.voice_client.listen(self.sink)
            else:
                logger.warning("discord-ext-voice-recv not installed; joining playback-only")
                self.voice_client = await channel.connect()
            self.text_channel = text_channel
            self.session_id = f"discord_voice_{channel.guild.id}"
            self._watch_task = asyncio.create_task(self._segmentation_watcher())
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("Joined voice channel: %s (listening=%s)", channel.name, self.sink is not None)
            return True
        except Exception as e:
            logger.error("Failed to join voice channel: %s", e)
            return False

    async def leave(self):
        for task_attr in ("_watch_task", "_worker_task"):
            task = getattr(self, task_attr)
            if task:
                task.cancel()
                setattr(self, task_attr, None)
        if self.voice_client:
            if hasattr(self.voice_client, "stop_listening"):
                try:
                    self.voice_client.stop_listening()
                except Exception:
                    logger.exception("stop_listening failed")
            if self.voice_client.is_connected():
                await self.voice_client.disconnect()
            self.voice_client = None
        self.sink = None
        logger.info("Left voice channel")

    async def _segmentation_watcher(self):
        """Poll the sink's buffers and emit completed utterances to the worker queue."""
        voice_cfg = self.config.get("voice", {})
        silence_s = voice_cfg.get("silence_threshold_ms", 1500) / 1000.0
        max_bytes = voice_cfg.get("max_listen_duration_s", 30) * BYTES_PER_SECOND
        min_bytes = int(voice_cfg.get("min_utterance_ms", 400) / 1000.0 * BYTES_PER_SECOND)
        # NOTE: interrupt_on_speech (barge-in) is deliberately not implemented yet.
        # Slot-in point: when a buffer goes empty -> non-empty while is_playing(), call voice_client.stop().
        logger.info("Voice listen loop started (silence=%.1fs, max=%ds, min=%dms)",
                    silence_s, voice_cfg.get("max_listen_duration_s", 30), voice_cfg.get("min_utterance_ms", 400))
        if not self.sink:
            logger.info("NOTE: audio capture unavailable (discord-ext-voice-recv not installed); playback-only.")
            return
        while self.voice_client and self.voice_client.is_connected():
            await asyncio.sleep(WATCHER_INTERVAL_S)
            now = time.monotonic()
            completed = []
            with self.sink.lock:
                for user_id, buf in list(self.sink.buffers.items()):
                    if not buf:
                        continue
                    ended = now - self.sink.last_packet.get(user_id, now) >= silence_s
                    overflow = len(buf) >= max_bytes
                    if ended or overflow:
                        pcm = bytes(buf)
                        del self.sink.buffers[user_id]
                        completed.append((user_id, self.sink.names.get(user_id, str(user_id)), pcm))
            for user_id, name, pcm in completed:
                if len(pcm) < min_bytes:
                    logger.debug("Discarding short utterance from %s (%d bytes)", name, len(pcm))
                    continue
                try:
                    self._queue.put_nowait((user_id, name, pcm))
                except asyncio.QueueFull:
                    logger.info("Dropped utterance from %s (busy)", name)

    async def _worker(self):
        while True:
            user_id, name, pcm = await self._queue.get()
            try:
                await self.process_audio(pcm, user_id, name)
            except Exception:
                logger.exception("Voice worker error")
            finally:
                self._queue.task_done()

    async def process_audio(self, pcm_data: bytes, user_id: int, display_name: str = ""):
        """Process a completed utterance: STT -> LLM -> TTS -> playback."""
        self._processing = True
        try:
            utt_seconds = len(pcm_data) / BYTES_PER_SECOND
            wav_bytes = pcm_to_wav(pcm_data)
            t0 = time.monotonic()
            try:
                stt_result = await self.api.speech_to_text(wav_bytes)
            except APIError as e:
                # 400 = "No speech detected" — benign (breath, cough, keyboard noise)
                if e.status_code == 400:
                    logger.debug("No speech in %.1fs utterance from %s", utt_seconds, display_name)
                    return
                raise
            text = stt_result.get("text", "").strip()
            if not text:
                return
            stt_s = time.monotonic() - t0
            logger.info("Voice STT (%s, %.1fs audio, %.1fs stt): '%s'", display_name, utt_seconds, stt_s, text[:100])
            t0 = time.monotonic()
            prompt = f"{display_name} says: {text}" if display_name else text

            async def send_fn(content, *, files=None, view=None):
                kwargs = {"content": content}
                if files:
                    kwargs["files"] = files
                if view is not None:
                    kwargs["view"] = view
                return await self.text_channel.send(**kwargs)

            approval_handler = None
            if self.text_channel is not None:
                approval_handler = make_approval_handler(
                    send_fn,
                    user_id,
                    auto_approve=bool(self.config.get("tools", {}).get("auto_approve", False)),
                )

            chat_result = await self.api.unified_chat(
                prompt,
                session_id=self.session_id,
                approval_handler=approval_handler,
                is_voice_message=True,
            )
            response = chat_result.get("response", "")
            if not response and not chat_result.get("generated_images"):
                return
            logger.info("Voice LLM response (%.1fs): '%s'", time.monotonic() - t0, (response or "")[:100])
            if response:
                await self.speak(response)
            images = chat_result.get("generated_images") or []
            if images and self.text_channel is not None:
                files = await files_from_generated(self.api, images)
                if files:
                    await send_chunks(
                        send_fn,
                        response or "Here's what Guaardvark made.",
                        files=files,
                    )
        except APIError as e:
            logger.error("Voice pipeline API error: %s", e)
            if self.text_channel:
                await self.text_channel.send(f"Voice error: {e}")
        except Exception:
            logger.exception("Voice pipeline error")
        finally:
            self._processing = False

    async def speak(self, text: str):
        """TTS the text and play it in the connected voice channel."""
        tts_result = await self.api.text_to_speech(text, voice=self.config.get("voice", {}).get("tts_voice", "ryan"))
        audio_url = tts_result.get("audio_url")
        if not audio_url and tts_result.get("filename"):
            audio_url = f"/api/voice/audio/{tts_result['filename']}"
        if not audio_url:
            logger.warning("TTS returned no audio_url/filename: %s", tts_result)
            return
        logger.info("Voice TTS engine=%s url=%s", tts_result.get("engine", "?"), audio_url)
        wav_audio = await self.api.fetch_audio_by_url(audio_url)
        await self._play_audio(wav_audio)

    async def _play_audio(self, wav_bytes: bytes):
        if not self.voice_client or not self.voice_client.is_connected():
            return
        audio_source = discord.FFmpegPCMAudio(io.BytesIO(wav_bytes), pipe=True)
        self.voice_client.play(audio_source)
        while self.voice_client.is_playing():
            await asyncio.sleep(0.1)
