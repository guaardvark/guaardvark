"""
Faster-Whisper transcription utility.

Uses CTranslate2-based faster-whisper for ~4x faster transcription
vs whisper.cpp subprocess calls. Models are cached globally to avoid
reload overhead on each request.

Safe import: if faster-whisper is not installed, this module loads
but functions raise ImportError on use.
"""

import os
import time
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None

# Global cache for the loaded model
_whisper_model = None
_current_model_size = None


def get_faster_whisper_model(
    model_size: str = "tiny.en",
    device: str = "auto",
    compute_type: str = "int8"
) -> "WhisperModel":
    """Get or create a cached faster-whisper model instance."""
    global _whisper_model, _current_model_size

    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")

    if _whisper_model is not None and _current_model_size == model_size:
        return _whisper_model

    logger.info(f"Loading faster-whisper model '{model_size}' (device={device}, compute_type={compute_type})")
    start = time.time()

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception:
        if compute_type != "int8":
            logger.info("Falling back to compute_type='int8'")
            model = WhisperModel(model_size, device=device, compute_type="int8")
        else:
            raise

    _whisper_model = model
    _current_model_size = model_size
    logger.info(f"Loaded faster-whisper '{model_size}' in {time.time() - start:.2f}s")
    return model


def transcribe_audio_faster(audio_input, model_size: str = "tiny.en") -> Tuple[str, float]:
    """Transcribe an audio file or numpy array using faster-whisper.

    Args:
        audio_input: Path to audio file (WAV, MP3, etc.) or numpy array of audio data
        model_size: Whisper model size (tiny.en, base, small, etc.)

    Returns:
        Tuple of (transcribed_text, processing_time_seconds)
    """
    model = get_faster_whisper_model(model_size=model_size)
    start = time.time()

    segments, info = model.transcribe(
        audio_input,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text = " ".join(segment.text for segment in segments)
    duration = time.time() - start

    return text.strip(), duration
