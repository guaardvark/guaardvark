"""
Faster-Whisper transcription utility.

Uses CTranslate2-based faster-whisper for ~4x faster transcription
vs whisper.cpp subprocess calls. Models are cached globally to avoid
reload overhead on each request.

Safe import: if faster-whisper is not installed, this module loads
but functions raise ImportError on use.

Loading never downloads. Weights reach the Hugging Face cache only through
install_model(), which the voice model Install calls; a missing model raises
SpeechModelMissing, whose message is what the voice clients show.
"""

import os
import time
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None
    download_model = None

SPEECH_MODEL_MISSING_MESSAGE = "Install the speech model to use voice"

# Global cache for the loaded model
_whisper_model = None
_current_model_size = None


class SpeechModelMissing(RuntimeError):
    """The weights for a model size are not on this machine."""

    def __init__(self, model_size: str):
        super().__init__(SPEECH_MODEL_MISSING_MESSAGE)
        self.model_size = model_size


def local_model_path(model_size: str) -> Optional[str]:
    """Folder holding the complete weights for model_size, or None. No network."""
    if not FASTER_WHISPER_AVAILABLE:
        return None
    try:
        path = download_model(model_size, local_files_only=True)
    except FileNotFoundError:
        # huggingface_hub's LocalEntryNotFoundError (nothing cached) and
        # IncompleteSnapshotError (an interrupted download) are both this.
        return None
    if not os.path.isfile(os.path.join(path, "model.bin")):
        return None
    return path


def is_model_installed(model_size: str) -> bool:
    return local_model_path(model_size) is not None


def install_model(model_size: str) -> str:
    """Download the weights for model_size into the Hugging Face cache.

    The only call in this module that reaches the network. It belongs behind
    a visible Install; transcription paths use get_faster_whisper_model().
    The Whisper repos are public, so no stored Hugging Face token is sent.
    """
    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")
    return download_model(model_size, use_auth_token=False)


def weights_cache_dir(model_size: str) -> Optional[str]:
    """Hugging Face cache folder install_model() writes model_size into, or None.

    Only used to show download progress, so an unknown layout returns None
    rather than raising.
    """
    try:
        from faster_whisper.utils import _MODELS
        from huggingface_hub import constants
    except ImportError:
        return None
    repo_id = _MODELS.get(model_size)
    if not repo_id:
        return None
    return os.path.join(constants.HF_HUB_CACHE, "models--" + repo_id.replace("/", "--"))


def pick_device() -> str:
    """CUDA only when the card has room to spare; otherwise CPU.

    Speech models load in this process, outside the GPU orchestrator's
    admission, so they follow the reranker's rule (utils/reranker.py): take
    the card only above a free-VRAM floor and leave it to image and video jobs
    otherwise. Decided per load; unload() releases the choice. The
    GUAARDVARK_WHISPER_DEVICE environment variable ("cpu", "cuda") overrides.
    """
    forced = os.environ.get("GUAARDVARK_WHISPER_DEVICE", "").strip().lower()
    if forced in ("cpu", "cuda"):
        return forced
    try:
        from backend.utils.backend_http import in_mcp_process
        # Memory held by the MCP process is invisible to the backend's GPU admission.
        if in_mcp_process():
            return "cpu"
    except ImportError:
        pass
    # Chosen as a margin, not measured: the tiny.en weights are about 75 MB.
    floor_mb = int(os.environ.get("GUAARDVARK_WHISPER_MIN_VRAM_MB", "2000"))
    try:
        from backend.services.gpu_resource_coordinator import get_available_vram, has_gpu
        if not has_gpu():
            return "cpu"
        info = get_available_vram()
        if info.get("success") and info.get("available_mb", 0) >= floor_mb:
            return "cuda"
        logger.info(
            "faster-whisper: %s MB free < %s MB floor, loading on CPU",
            info.get("available_mb"), floor_mb,
        )
        return "cpu"
    except Exception as e:  # noqa: BLE001
        logger.debug("faster-whisper device probe failed (%s); using CPU", e)
        return "cpu"


def get_faster_whisper_model(
    model_size: str = "tiny.en",
    device: Optional[str] = None,
    compute_type: str = "int8"
) -> "WhisperModel":
    """Get or create a cached faster-whisper model instance.

    `device` None lets pick_device() choose against the card's free memory.
    """
    global _whisper_model, _current_model_size

    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")

    if _whisper_model is not None and _current_model_size == model_size:
        return _whisper_model

    if local_model_path(model_size) is None:
        raise SpeechModelMissing(model_size)

    if device is None:
        device = pick_device()
    logger.info(f"Loading faster-whisper model '{model_size}' (device={device}, compute_type={compute_type})")
    start = time.time()

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type,
                             local_files_only=True)
    except Exception:
        if compute_type != "int8":
            logger.info("Falling back to compute_type='int8'")
            model = WhisperModel(model_size, device=device, compute_type="int8",
                                 local_files_only=True)
        else:
            raise

    _whisper_model = model
    _current_model_size = model_size
    logger.info(f"Loaded faster-whisper '{model_size}' in {time.time() - start:.2f}s")
    return model


def is_loaded() -> bool:
    return _whisper_model is not None


def unload() -> bool:
    """Release the cached model. Returns True if something was released.

    A model loaded on CUDA keeps an encoder/decoder and a cuBLAS workspace on
    the card until this runs; the GPU orchestrator calls it when a render needs
    the memory, and the next transcription re-decides the device. Never raises.
    """
    global _whisper_model, _current_model_size
    if _whisper_model is None:
        return False
    _whisper_model = None
    _current_model_size = None
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
    logger.info("faster-whisper model unloaded")
    return True


def decode_audio(input_file, sampling_rate: int = 16000):
    """Decode audio to mono float32 at ``sampling_rate``, as
    faster_whisper.audio.decode_audio does. faster-whisper 1.2.1 opens the
    file with ``metadata_errors=``, which PyAV 19 removed from ``av.open``, so
    its decoder raises TypeError there; this one works with PyAV 18 and 19.
    """
    import gc
    import io
    import itertools

    import av
    import numpy as np

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    raw = io.BytesIO()
    dtype = None

    def frames_of(container):
        iterator = iter(container.decode(audio=0))
        while True:
            try:
                frame = next(iterator)
            except StopIteration:
                return
            except av.error.InvalidDataError:
                continue
            frame.pts = None  # Ignore timestamp checks, as faster-whisper does.
            yield frame

    def grouped(frames, num_samples=500000):
        fifo = av.audio.fifo.AudioFifo()
        for frame in frames:
            fifo.write(frame)
            if fifo.samples >= num_samples:
                yield fifo.read()
        if fifo.samples > 0:
            yield fifo.read()

    with av.open(input_file, mode="r") as container:
        # None flushes the resampler.
        for frame in itertools.chain(grouped(frames_of(container)), [None]):
            for out in resampler.resample(frame):
                array = out.to_ndarray()
                dtype = array.dtype
                raw.write(array)

    # PyAV's resampler holds memory until a collection (faster-whisper #390).
    del resampler
    gc.collect()
    if dtype is None:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw.getbuffer(), dtype=dtype).astype(np.float32) / 32768.0


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
    if isinstance(audio_input, (str, bytes)) or hasattr(audio_input, "read"):
        # A path or file: decode here, since faster-whisper's own decoder fails
        # under PyAV 19 (see decode_audio).
        import io
        audio_input = decode_audio(io.BytesIO(audio_input) if isinstance(audio_input, bytes) else audio_input)

    segments, info = model.transcribe(
        audio_input,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text = " ".join(segment.text for segment in segments)
    duration = time.time() - start

    return text.strip(), duration
