import os
import time
import logging
from typing import Optional, Tuple
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# Global cache for the loaded model to avoid reloading on every request
_whisper_model = None
_current_model_size = None

def get_faster_whisper_model(model_size: str = "tiny.en", device: str = "auto", compute_type: str = "int8") -> WhisperModel:
    global _whisper_model, _current_model_size
    
    if _whisper_model is not None and _current_model_size == model_size:
        return _whisper_model

    logger.info(f"Loading faster-whisper model '{model_size}' (device={device}, compute_type={compute_type})")
    start_time = time.time()
    
    try:
        # Load the model
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _whisper_model = model
        _current_model_size = model_size
        logger.info(f"Successfully loaded faster-whisper model '{model_size}' in {time.time() - start_time:.2f}s")
        return model
    except Exception as e:
        logger.error(f"Failed to load faster-whisper model: {e}")
        # Fallback to int8 if float16 fails, etc.
        if compute_type != "int8":
            logger.info("Falling back to compute_type='int8'")
            model = WhisperModel(model_size, device=device, compute_type="int8")
            _whisper_model = model
            _current_model_size = model_size
            return model
        raise

def transcribe_audio_faster(audio_path: str, model_size: str = "tiny.en") -> Tuple[str, float]:
    """Transcribes an audio file using faster-whisper and returns (text, duration)."""
    try:
        model = get_faster_whisper_model(model_size=model_size)
        start_time = time.time()
        
        segments, info = model.transcribe(
            audio_path,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        
        text = " ".join([segment.text for segment in segments])
        
        duration = time.time() - start_time
        return text.strip(), duration
    except Exception as e:
        logger.error(f"Error transcribing with faster-whisper: {e}")
        raise
