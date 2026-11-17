"""Kokoro TTS backend (hexgrad/Kokoro-82M, Apache 2.0).

Lightweight TTS — ~80M params, fast, and it runs on the CPU when the machine
has no CUDA device (sub-1 GB VRAM when it has one). Built-in voices in nine
languages but no reference-clip cloning. Used when a request names a built-in
voice or backend="kokoro", and when Chatterbox cannot run.

Heavy imports live inside methods.

Install (handled at first start.sh run after the requirements.txt bump):
    pip install kokoro

Everything a generation reads comes from this machine: the model files and
voice packs from the Hugging Face cache (resolved to local paths here, so
kokoro's own hf_hub_download is never reached), and the language's phonemizer
packages from the plugin venv (spaCy's English pipeline; the Japanese and
Mandarin packs). A missing piece raises WeightsNotInstalled naming Audio
Studio → Manage models, whose Install fetches all of it. The voice list, the
language of each voice and what each language needs are in
backends/kokoro_voices.json.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from backends import kokoro_voices
from backends.base import AudioBackend, GenerationResult
from backends.hub_weights import (
    INSTALL_HINT,
    WeightsNotInstalled,
    cached_hub_file,
    require_hub_files,
)

logger = logging.getLogger(__name__)

# Sentence enders, Latin, CJK and Devanagari; then the clause marks a sentence
# that is still too long is cut at.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？।…])\s*|\n+")
_CLAUSE_END = re.compile(r"(?<=[,;:、，；：])\s*")


def _split_keeping(pattern: re.Pattern, text: str) -> list[str]:
    return [part for part in pattern.split(text) if part and part.strip()]


def fit_to_phoneme_limit(text: str, phoneme_count, limit: int, pack: int) -> list[str]:
    """Cut ``text`` into pieces the model reads whole and unhurried.

    KPipeline's own chunking for the non-English languages counts characters
    and splits on ASCII sentence marks only; a piece longer than the model's
    phoneme limit is truncated, so the rest of a Japanese or Chinese paragraph
    (no ASCII full stops, several phonemes a character) would go unspoken.
    Here each piece is measured with the language's own phonemizer
    (``phoneme_count(piece)``): sentences are joined up to ``pack`` phonemes
    (the model rushes a long pass), and one sentence over ``limit`` is cut at
    clause marks, then at spaces, then in half.
    """
    def shrink(piece: str) -> list[str]:
        if phoneme_count(piece) <= limit or len(piece) < 2:
            return [piece]
        for pattern in (_CLAUSE_END, re.compile(r"\s+")):
            parts = _split_keeping(pattern, piece)
            if len(parts) > 1:
                return [small for part in parts for small in shrink(part)]
        middle = len(piece) // 2
        return shrink(piece[:middle]) + shrink(piece[middle:])

    pieces = [small for sentence in _split_keeping(_SENTENCE_END, text) for small in shrink(sentence)]
    chunks: list[str] = []
    for piece in pieces:
        # CJK text has no spaces between sentences.
        gap = "" if chunks and chunks[-1][-1] in "。！？、，；：" else " "
        joined = f"{chunks[-1]}{gap}{piece}" if chunks else piece
        if chunks and phoneme_count(joined) <= pack:
            chunks[-1] = joined
        else:
            chunks.append(piece)
    return chunks


class KokoroBackend(AudioBackend):
    """Kokoro-82M TTS — 24 kHz mono, built-in voices.

    Each voice speaks one language (its catalog group's ``lang``). The
    phonemizer, which is what the lang_code controls, has to match it or
    pronunciation gets weird — we keep one KPipeline per lang_code and route
    at generate-time.
    """

    name = "kokoro"
    vram_mb_estimate = 600  # ~500 MB observed; pad for activations

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 24000,
        default_voice: str = "af_heart",
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._default_voice = default_voice
        # One pipeline per lang_code, lazy-loaded: the default voice's at
        # load() time, another on the first voice in that language. They share
        # one copy of the model; only the phonemizer differs.
        self._pipelines: dict[str, Any] = {}
        self._model: Any = None

    @property
    def is_loaded(self) -> bool:
        return bool(self._pipelines)

    @staticmethod
    def _lang_code_for(voice_id: str) -> str:
        """Return the Kokoro lang_code of this catalog voice."""
        return kokoro_voices.voice_language(voice_id)

    def _model_paths(self) -> dict[str, str]:
        """Local paths of config.json and the weights, or WeightsNotInstalled."""
        return require_hub_files(
            kokoro_voices.hf_repo(), kokoro_voices.model_files(), "Kokoro voice",
        )

    @staticmethod
    def _require_language(lang_code: str) -> None:
        """Refuse before the phonemizer is imported without its packages.

        For English that import would pip-install spaCy's pipeline mid-request;
        for Japanese and Mandarin it fails with a bare ImportError.
        """
        missing = kokoro_voices.missing_language_packages(lang_code)
        if not missing:
            return
        language = kokoro_voices.language_label(lang_code)
        pack = kokoro_voices.language_pack(lang_code)
        if pack is None:
            raise WeightsNotInstalled(
                f"Kokoro English voices need spaCy's {missing[0]} language "
                f"model, which is not installed in the Audio Foundry environment. "
                f"{INSTALL_HINT}"
            )
        raise WeightsNotInstalled(
            f"Install {language} first: Kokoro's {language} voices need "
            f"'{pack['name']}' (missing {missing[0]}). Open Audio Studio → Manage "
            f"models and Install it. Generation never downloads on its own."
        )

    def _voice_path(self, voice_id: str) -> str:
        """Local path of a catalog voice pack.

        Passing KPipeline a path (it loads anything ending in .pt directly)
        instead of the id keeps it from calling hf_hub_download. Ids outside
        the catalog are refused here, so a caller cannot hand it an arbitrary
        file or a comma-separated blend.
        """
        voice_id = kokoro_voices.check_voice_id(voice_id)
        path = cached_hub_file(kokoro_voices.hf_repo(), kokoro_voices.voice_file(voice_id))
        if path is None:
            raise WeightsNotInstalled(
                f"Kokoro voice '{voice_id}' is not on this machine. {INSTALL_HINT}"
            )
        if "," in path:
            # KPipeline.load_voice splits on commas; a cache under such a
            # directory would be read as a blend of partial paths.
            raise WeightsNotInstalled(
                f"Kokoro voice '{voice_id}': the Hugging Face cache path contains a "
                f"comma ({path}), which Kokoro cannot load. Move HF_HOME."
            )
        return path

    def _build_model(self, device: str) -> Any:
        from kokoro import KModel

        paths = self._model_paths()
        return KModel(
            repo_id=kokoro_voices.hf_repo(),
            config=paths[kokoro_voices.config_file()],
            model=paths[kokoro_voices.weights_file()],
        ).to(device).eval()

    def _build_pipeline(self, lang_code: str, device: str) -> Any:
        from kokoro import KPipeline

        if self._model is None:
            self._model = self._build_model(device)
        return KPipeline(lang_code=lang_code, repo_id=kokoro_voices.hf_repo(), model=self._model)

    def _get_or_load_pipeline(self, lang_code: str) -> Any:
        """Return the KPipeline for this lang_code, loading it if needed."""
        if lang_code in self._pipelines:
            return self._pipelines[lang_code]

        try:
            import kokoro  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "kokoro package not installed. Run: pip install kokoro"
            ) from e
        import torch

        # Both checks raise before any model memory is allocated.
        self._model_paths()
        self._require_language(lang_code)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading Kokoro pipeline for lang_code=%r on %s", lang_code, device)
        try:
            pipeline = self._build_pipeline(lang_code, device)
        except RuntimeError as e:
            if device != "cuda" or "CUDA" not in str(e):
                raise
            # Kokoro-82M is tiny — when a render owns the card (observed:
            # 12.4GB ComfyUI job left 39MB free), narration must not die.
            # CPU synthesis is near-realtime for this model.
            logger.warning(
                "Kokoro CUDA init failed (%s) — retrying on CPU", e)
            pipeline = self._build_pipeline(lang_code, "cpu")
        self._pipelines[lang_code] = pipeline
        return pipeline

    @staticmethod
    def _text_for(pipeline: Any, lang_code: str, text: str) -> Any:
        """What to hand KPipeline: the text itself for English, whose pipeline
        chunks by phoneme count, and measured pieces for every other language."""
        if lang_code in kokoro_voices.english_g2p()["lang_codes"]:
            return text

        def phoneme_count(piece: str) -> int:
            phonemes = pipeline.g2p(piece)
            if isinstance(phonemes, tuple):
                phonemes = phonemes[0]
            return len(phonemes or "")

        return fit_to_phoneme_limit(
            text, phoneme_count, kokoro_voices.max_phonemes(), kokoro_voices.pack_phonemes())

    def load(self) -> None:
        # Pre-warm only the default voice's lang_code. Other accents lazy-load
        # on first request — keeps cold-start fast and VRAM low.
        if self._pipelines:
            return
        logger.info("Loading Kokoro-82M from local cache...")
        default = kokoro_voices.check_voice_id(self._default_voice)
        self._get_or_load_pipeline(self._lang_code_for(default))
        logger.info("Kokoro loaded")

    def unload(self) -> None:
        if not self._pipelines:
            return
        import torch

        self._pipelines.clear()
        self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Kokoro unloaded")

    def generate(self, **params: Any) -> GenerationResult:
        if not self._pipelines:
            raise RuntimeError("Kokoro not loaded; call load() first")

        text: str = params["text"]
        voice = params.get("voice_id") or self._default_voice
        requested_format = params.get("output_format", "wav")
        # Kokoro has no reference-clip cloning — silently ignore those args.

        voice = kokoro_voices.check_voice_id(voice)
        voice_path = self._voice_path(voice)

        # Route to the phonemizer of this voice's language.
        lang_code = self._lang_code_for(voice)
        pipeline = self._get_or_load_pipeline(lang_code)
        spoken = self._text_for(pipeline, lang_code, text)

        import numpy as np
        import soundfile as sf

        logger.info("Kokoro generate: chars=%d voice=%s lang=%s", len(text), voice, lang_code)
        t0 = time.monotonic()

        # KPipeline streams tuples: (graphemes, phonemes, audio_tensor).
        # We concatenate all audio chunks; each is a 1-D float tensor at 24 kHz.
        segments = []
        for _, _, audio_tensor in pipeline(spoken, voice=voice_path):
            arr = audio_tensor.cpu().numpy() if hasattr(audio_tensor, "cpu") else np.asarray(audio_tensor)
            segments.append(arr)
        gen_seconds = time.monotonic() - t0

        if not segments:
            raise RuntimeError("Kokoro produced no audio segments — input may be empty")

        audio = np.concatenate(segments)

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        out_path = self._output_root / f"{asset_id}.wav"
        sf.write(str(out_path), audio, self._sample_rate)

        # Reap the raw WAV if post_process fails or replaces it (see chatterbox).
        try:
            final_path = self.post_process(out_path, output_format=requested_format)
        except Exception:
            out_path.unlink(missing_ok=True)
            raise
        if final_path != out_path:
            out_path.unlink(missing_ok=True)
        actual_format = final_path.suffix.lstrip(".").lower()

        actual_duration = audio.shape[0] / self._sample_rate
        logger.info(
            "Kokoro wrote %s — %.2fs audio in %.1fs wall",
            final_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "text": text,
                "voice": voice,
                "language": kokoro_voices.language_label(lang_code),
                "device": str(getattr(self._model, "device", "")) or None,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "generation_seconds": round(gen_seconds, 2),
            },
        )

    def stream(self, **params: Any):
        """Yield (wav_chunk_bytes, is_first) tuples for sentence-by-sentence streaming playback.

        Per voice specialist audit: this enables first audible speech after the first Kokoro
        iteration (~hundreds of ms for short sentence) instead of waiting for the entire
        text to be synthesized and written to a single file.

        Each yielded chunk is a complete small WAV (with its own header) so it can be
        played immediately in <audio> or concatenated client-side. Cross-sentence joins
        may need crossfade for smoothness.
        """
        if not self._pipelines:
            raise RuntimeError("Kokoro not loaded; call load() first")

        text: str = params["text"]
        voice = params.get("voice_id") or self._default_voice
        voice = kokoro_voices.check_voice_id(voice)
        voice_path = self._voice_path(voice)
        lang_code = self._lang_code_for(voice)
        pipeline = self._get_or_load_pipeline(lang_code)
        spoken = self._text_for(pipeline, lang_code, text)

        import numpy as np
        import soundfile as sf
        import io

        first = True
        for _, _, audio_tensor in pipeline(spoken, voice=voice_path):
            arr = audio_tensor.cpu().numpy() if hasattr(audio_tensor, "cpu") else np.asarray(audio_tensor)
            buf = io.BytesIO()
            sf.write(buf, arr, self._sample_rate, format='WAV')
            buf.seek(0)
            chunk = buf.read()
            yield chunk, first
            first = False
