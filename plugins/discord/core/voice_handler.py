"""Voice channel audio pipeline: Discord PCM -> Whisper STT -> LLM -> Piper TTS -> Discord playback."""
import asyncio
import io
import logging
import struct
import wave
from typing import Optional

import discord

from core.api_client import GuaardvarkClient, APIError

logger = logging.getLogger(__name__)

DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1


def pcm_to_wav(pcm_data: bytes, sample_rate: int = DISCORD_SAMPLE_RATE, channels: int = DISCORD_CHANNELS) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def downsample_pcm(pcm_data: bytes, from_rate=DISCORD_SAMPLE_RATE, to_rate=TARGET_SAMPLE_RATE, from_channels=2, to_channels=1) -> bytes:
    samples = struct.unpack(f"<{len(pcm_data)//2}h", pcm_data)
    if from_channels == 2 and to_channels == 1:
        mono = [(samples[i] + samples[i+1]) // 2 for i in range(0, len(samples), 2)]
    else:
        mono = list(samples)
    ratio = from_rate // to_rate
    if ratio > 1:
        mono = mono[::ratio]
    return struct.pack(f"<{len(mono)}h", *mono)


class VoiceHandler:
    def __init__(self, api_client: GuaardvarkClient, config: dict):
        self.api = api_client
        self.config = config
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel = None
        self._processing = False
        self._listen_task = None
        self.session_id = "discord_voice"

    async def join(self, channel: discord.VoiceChannel, text_channel) -> bool:
        try:
            self.voice_client = await channel.connect()
            self.text_channel = text_channel
            self.session_id = f"discord_voice_{channel.guild.id}"
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info("Joined voice channel: %s", channel.name)
            return True
        except Exception as e:
            logger.error("Failed to join voice channel: %s", e)
            return False

    async def leave(self):
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
            self.voice_client = None
        logger.info("Left voice channel")

    async def _listen_loop(self):
        silence_ms = self.config.get("voice", {}).get("silence_threshold_ms", 1500)
        max_duration = self.config.get("voice", {}).get("max_listen_duration_s", 30)
        # SCAFFOLD: discord.py audio receive is experimental.
        # The process_audio() method below implements the complete pipeline.
        # TODO: Wire up VoiceClient.listen(sink) when discord.py voice receive is stable.
        logger.info("Voice listen loop started (silence=%dms, max=%ds)", silence_ms, max_duration)
        logger.info("NOTE: Audio capture requires discord.py experimental voice receive.")
        while self.voice_client and self.voice_client.is_connected():
            await asyncio.sleep(1.0)

    async def process_audio(self, pcm_data: bytes, user_id: int):
        """Process a completed utterance: STT -> LLM -> TTS -> playback."""
        if self._processing:
            return
        self._processing = True
        try:
            downsampled = downsample_pcm(pcm_data)
            wav_bytes = pcm_to_wav(downsampled, TARGET_SAMPLE_RATE, TARGET_CHANNELS)
            stt_result = await self.api.speech_to_text(wav_bytes)
            text = stt_result.get("text", "").strip()
            if not text:
                return
            logger.info("Voice STT: '%s'", text[:100])
            chat_result = await self.api.chat(text, self.session_id)
            response = chat_result.get("response", "")
            if not response:
                return
            logger.info("Voice LLM response: '%s'", response[:100])
            tts_result = await self.api.text_to_speech(response, voice=self.config.get("voice", {}).get("tts_voice", "ryan"))
            audio_filename = tts_result.get("filename")
            if not audio_filename:
                return
            wav_audio = await self.api.get_voice_audio(audio_filename)
            await self._play_audio(wav_audio)
        except APIError as e:
            logger.error("Voice pipeline API error: %s", e)
            if self.text_channel:
                await self.text_channel.send(f"Voice error: {e}")
        except Exception as e:
            logger.exception("Voice pipeline error")
        finally:
            self._processing = False

    async def _play_audio(self, wav_bytes: bytes):
        if not self.voice_client or not self.voice_client.is_connected():
            return
        audio_source = discord.FFmpegPCMAudio(io.BytesIO(wav_bytes), pipe=True)
        self.voice_client.play(audio_source)
        while self.voice_client.is_playing():
            await asyncio.sleep(0.1)
