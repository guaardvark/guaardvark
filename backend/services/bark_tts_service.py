"""
Bark TTS Service — Expressive speech synthesis using Suno's Bark model.

Supports special tokens like [laughter], [sighs], [gasps], [clears throat],
and ... for hesitation. Loads model on-demand, evicts Ollama first to free
GPU VRAM, and unloads immediately after generation.
"""

import gc
import io
import logging
import tempfile
import threading
import time
import wave

import numpy as np
import requests

logger = logging.getLogger(__name__)

# Lock to ensure only one Bark generation at a time
_bark_lock = threading.Lock()

# Bark speaker presets — v2/en_speaker_0 through _9
BARK_VOICES = {
    "bark_speaker_0": {
        "preset": "v2/en_speaker_0",
        "name": "Speaker 0 (Male, Deep)",
        "description": "Deep male voice, calm delivery",
    },
    "bark_speaker_1": {
        "preset": "v2/en_speaker_1",
        "name": "Speaker 1 (Male, Warm)",
        "description": "Warm male voice, conversational tone",
    },
    "bark_speaker_2": {
        "preset": "v2/en_speaker_2",
        "name": "Speaker 2 (Female, Clear)",
        "description": "Clear female voice, natural delivery",
    },
    "bark_speaker_3": {
        "preset": "v2/en_speaker_3",
        "name": "Speaker 3 (Male, Energetic)",
        "description": "Energetic male voice, expressive range",
    },
    "bark_speaker_4": {
        "preset": "v2/en_speaker_4",
        "name": "Speaker 4 (Female, Soft)",
        "description": "Soft female voice, gentle delivery",
    },
    "bark_speaker_5": {
        "preset": "v2/en_speaker_5",
        "name": "Speaker 5 (Male, Narrative)",
        "description": "Male narrator voice, storytelling style",
    },
    "bark_speaker_6": {
        "preset": "v2/en_speaker_6",
        "name": "Speaker 6 (Female, Bright)",
        "description": "Bright female voice, cheerful tone",
    },
    "bark_speaker_7": {
        "preset": "v2/en_speaker_7",
        "name": "Speaker 7 (Male, Authoritative)",
        "description": "Authoritative male voice, formal delivery",
    },
    "bark_speaker_8": {
        "preset": "v2/en_speaker_8",
        "name": "Speaker 8 (Female, Expressive)",
        "description": "Expressive female voice, dramatic range",
    },
    "bark_speaker_9": {
        "preset": "v2/en_speaker_9",
        "name": "Speaker 9 (Male, Casual)",
        "description": "Casual male voice, relaxed delivery",
    },
}

DEFAULT_BARK_VOICE = "bark_speaker_3"


def _evict_ollama_models():
    """Evict all Ollama models from VRAM by setting keep_alive=0."""
    try:
        # Get list of loaded models
        resp = requests.get("http://localhost:11434/api/ps", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            for model_info in models:
                model_name = model_info.get("name", "")
                if model_name:
                    logger.info(f"Bark TTS: Evicting Ollama model '{model_name}' from VRAM")
                    requests.post(
                        "http://localhost:11434/api/generate",
                        json={"model": model_name, "prompt": "", "keep_alive": 0},
                        timeout=15,
                    )
            if models:
                # Give Ollama a moment to release VRAM
                time.sleep(2)
                logger.info(f"Bark TTS: Evicted {len(models)} Ollama model(s)")
            else:
                logger.info("Bark TTS: No Ollama models loaded, skipping eviction")
    except requests.ConnectionError:
        logger.info("Bark TTS: Ollama not running, no models to evict")
    except Exception as e:
        logger.warning(f"Bark TTS: Failed to evict Ollama models: {e}")


def _unload_bark():
    """Unload Bark model and free GPU memory."""
    import torch

    try:
        from bark import generation as bark_gen

        # Clear Bark's cached models
        for attr in ("_model", "_tokenizer", "_fine_model", "_codec_model"):
            if hasattr(bark_gen, attr):
                obj = getattr(bark_gen, attr)
                if obj is not None:
                    if hasattr(obj, "cpu"):
                        obj.cpu()
                    del obj
                    setattr(bark_gen, attr, None)

        # Clear any preloaded models dict
        if hasattr(bark_gen, "models"):
            bark_gen.models.clear()
        if hasattr(bark_gen, "LOADED_MODELS"):
            bark_gen.LOADED_MODELS.clear()

    except Exception as e:
        logger.warning(f"Bark TTS: Error during model attribute cleanup: {e}")

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
    logger.info("Bark TTS: Model unloaded, GPU memory freed")


def _generate_with_bark(text: str, voice_preset: str) -> np.ndarray:
    """
    Load Bark, generate audio, return numpy array at 24kHz.

    Bark generates at 24000 Hz sample rate.
    """
    import torch
    from bark import SAMPLE_RATE, generate_audio, preload_models

    # Force Bark to use GPU
    os.environ["SUNO_USE_SMALL_MODELS"] = "0"
    os.environ["SUNO_ENABLE_MPS"] = "0"

    logger.info(f"Bark TTS: Loading model (preset={voice_preset})...")
    start = time.time()
    preload_models()
    load_time = time.time() - start
    logger.info(f"Bark TTS: Model loaded in {load_time:.1f}s")

    logger.info(f"Bark TTS: Generating audio for {len(text)} chars...")
    gen_start = time.time()
    audio_array = generate_audio(text, history_prompt=voice_preset)
    gen_time = time.time() - gen_start
    logger.info(f"Bark TTS: Generated {len(audio_array)/SAMPLE_RATE:.1f}s audio in {gen_time:.1f}s")

    return audio_array


def generate_speech(text: str, voice: str = DEFAULT_BARK_VOICE) -> bytes:
    """
    Generate speech audio using Bark.

    Args:
        text: Text to speak. Supports Bark tokens: [laughter], [sighs], [gasps],
              [clears throat], [music], ... (hesitation)
        voice: Voice key from BARK_VOICES dict.

    Returns:
        WAV audio bytes.

    Raises:
        RuntimeError: If generation fails.
        ValueError: If voice is invalid.
    """
    import os
    import torch

    if voice not in BARK_VOICES:
        raise ValueError(f"Invalid Bark voice '{voice}'. Must be one of: {list(BARK_VOICES.keys())}")

    voice_preset = BARK_VOICES[voice]["preset"]

    if not _bark_lock.acquire(timeout=5):
        raise RuntimeError("Bark TTS is busy with another generation. Try again shortly.")

    try:
        # Step 1: Evict Ollama models to free GPU VRAM
        _evict_ollama_models()

        # Step 2: Check available VRAM
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            free_mb = free / (1024 * 1024)
            logger.info(f"Bark TTS: Available VRAM: {free_mb:.0f}MB")
            if free_mb < 2000:
                logger.warning(f"Bark TTS: Low VRAM ({free_mb:.0f}MB), Bark needs ~4-5GB. May fail.")

        # Step 3: Generate audio
        audio_array = _generate_with_bark(text, voice_preset)

        # Step 4: Convert numpy float32 array to WAV bytes
        from bark import SAMPLE_RATE

        # Normalize to int16
        audio_int16 = (audio_array * 32767).astype(np.int16)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_int16.tobytes())

        wav_bytes = wav_buffer.getvalue()
        logger.info(f"Bark TTS: Generated WAV: {len(wav_bytes)} bytes")
        return wav_bytes

    finally:
        # Step 5: ALWAYS unload Bark — never leave it resident
        try:
            _unload_bark()
        except Exception as e:
            logger.error(f"Bark TTS: Failed to unload: {e}")
        _bark_lock.release()


def generate_speech_to_file(text: str, output_path: str, voice: str = DEFAULT_BARK_VOICE) -> str:
    """
    Generate speech and save to a WAV file.

    Args:
        text: Text to speak.
        output_path: Path to write the WAV file.
        voice: Voice key from BARK_VOICES dict.

    Returns:
        The output_path.
    """
    wav_bytes = generate_speech(text, voice)
    with open(output_path, "wb") as f:
        f.write(wav_bytes)
    return output_path


# Needed for os.environ in _generate_with_bark
import os
