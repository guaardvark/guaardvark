"""Chatterbox neural TTS backend (Resemble AI, MIT).

High-quality voice synthesis with reference-clip-based voice cloning. Primary
backend for the voice_gen intent. ~500 MB weights, ~2 GB VRAM at fp16.

Heavy imports (torch, chatterbox) live inside methods so `from service.app
import app` stays cheap for tests.

Install: scripts/setup_venv.sh (start.sh does the same on first run).
chatterbox-tts goes in without its dependencies; requirements.txt says why.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backends import voice_consent
from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)

# Chatterbox has no "emotion" input. Delivery is shaped by three sampling
# controls, so the named emotions callers ask for are presets over those:
#   exaggeration — emotional intensity of the read
#   cfg_weight   — adherence to the reference clip; lower loosens pacing
#   temperature  — sampling randomness, and the main source of take-to-take
#                  drift on long passages
EMOTION_PRESETS: dict[str, dict[str, float]] = {
    "flat":      {"exaggeration": 0.25, "cfg_weight": 0.6, "temperature": 0.5},
    "narration": {"exaggeration": 0.40, "cfg_weight": 0.5, "temperature": 0.6},
    "neutral":   {"exaggeration": 0.50, "cfg_weight": 0.5, "temperature": 0.8},
    "warm":      {"exaggeration": 0.60, "cfg_weight": 0.45, "temperature": 0.8},
    "emphatic":  {"exaggeration": 0.75, "cfg_weight": 0.4, "temperature": 0.9},
    "dramatic":  {"exaggeration": 1.00, "cfg_weight": 0.3, "temperature": 1.0},
}

# Matches Chatterbox's own defaults, so callers who pass nothing are unaffected.
DEFAULT_EMOTION = "neutral"


class ChatterboxBackend(AudioBackend):
    """Chatterbox TTS — 24 kHz mono, optional reference-clip cloning."""

    name = "chatterbox"
    vram_mb_estimate = 2000  # ~1.7-2 GB observed in fp16 on consumer cards

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 24000,
        chunk_chars: int = 220,
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._chunk_chars = int(chunk_chars)
        self._model: Any = None
        # ChatterboxTTS keeps one set of voice conditionals (model.conds). The
        # stock voice from conds.pt starts there, and cloning replaces it
        # (prepare_conditionals). generate() holds this lock while it sets and
        # uses them, and puts the stock voice back when it is done, so a later
        # request without a clip never speaks in the last cloned voice.
        self._generate_lock = threading.Lock()
        self._stock_conds: Any = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return

        logger.info("Loading Chatterbox TTS from local cache...")
        import torch

        try:
            from chatterbox.tts import ChatterboxTTS
        except ImportError as e:
            raise RuntimeError(
                "chatterbox-tts package not installed. "
                "Run: plugins/audio_foundry/scripts/setup_venv.sh"
            ) from e

        from backends.hub_weights import require_hub_files

        paths = require_hub_files(
            "ResembleAI/chatterbox",
            ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
             "tokenizer.json", "conds.pt"],
            "Chatterbox voice",
        )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            logger.warning("CUDA not available — Chatterbox on CPU will be slow")

        # from_pretrained is hf_hub_download for these five files followed by
        # from_local on their folder. Calling from_local on the cached
        # snapshot keeps the load off the network. The five resolve through
        # the same refs/main, so they share one snapshot folder.
        self._model = ChatterboxTTS.from_local(Path(paths["ve.safetensors"]).parent, device)
        self._stock_conds = getattr(self._model, "conds", None)
        logger.info("Chatterbox loaded on %s", device)

    def unload(self) -> None:
        if self._model is None:
            return
        import torch

        del self._model
        self._model = None
        self._stock_conds = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Chatterbox unloaded")

    def _supported_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Keep only the keywords this build's ``generate`` actually declares."""
        import inspect

        try:
            accepted = set(
                inspect.signature(self._model.generate).parameters)
        except (TypeError, ValueError):
            return kwargs
        dropped = sorted(set(kwargs) - accepted)
        if dropped:
            logger.warning(
                "Chatterbox build does not accept %s — ignoring", ", ".join(dropped))
        return {k: v for k, v in kwargs.items() if k in accepted}

    def generate(self, **params: Any) -> GenerationResult:
        if self._model is None:
            raise RuntimeError("Chatterbox not loaded; call load() first")
        with self._generate_lock:
            return self._generate(**params)

    def _generate(self, **params: Any) -> GenerationResult:
        text: str = params["text"]
        reference_clip = params.get("reference_clip_path")
        emotion = params.get("emotion")
        seed = params.get("seed")
        requested_format = params.get("output_format", "wav")
        # Injected by the dispatcher for async jobs; absent (None) on the inline path.
        progress_cb = params.get("progress_cb")
        cancel_event = params.get("cancel_event")
        if reference_clip:
            # The clone happens here, so consent is checked here as well as in
            # the backend proxy: any process on this machine can call the plugin.
            # Checked once this request holds the model, so a request that
            # waited behind another sees consent withdrawn in the meantime.
            reference_clip = voice_consent.require_consent(reference_clip)

        import torch
        import soundfile as sf
        from backends.base import GenerationCancelled

        # Long-text chunking — single inference can OOM on very long inputs and
        # quality degrades past a few hundred characters. Naive splitter on the
        # last sentence end inside the chunk window.
        chunks = self._split_for_synthesis(text)

        gen_kwargs: dict[str, Any] = dict(EMOTION_PRESETS[DEFAULT_EMOTION])
        if emotion:
            preset = EMOTION_PRESETS.get(str(emotion).lower())
            if preset is None:
                raise ValueError(
                    f"unknown emotion '{emotion}'; choose one of "
                    f"{', '.join(sorted(EMOTION_PRESETS))}")
            gen_kwargs.update(preset)
        # Explicit values win over the preset, so a caller can nudge one knob
        # without restating the rest.
        for knob in ("exaggeration", "cfg_weight", "temperature"):
            if params.get(knob) is not None:
                gen_kwargs[knob] = float(params[knob])
        # The clip is read once, before the first chunk, so every chunk speaks
        # with the same conditionals and a clip deleted or withdrawn while this
        # runs does not stop it. A build without prepare_conditionals reads it
        # on every call instead.
        model, stock_conds = self._model, self._stock_conds
        prepare = getattr(model, "prepare_conditionals", None) if reference_clip else None
        if reference_clip and prepare is None:
            gen_kwargs["audio_prompt_path"] = str(reference_clip)
        if seed is not None:
            torch.manual_seed(int(seed))

        # Chatterbox builds differ in which sampling controls they accept, and
        # an unknown keyword is a TypeError mid-generation rather than a
        # warning. Drop anything this build does not declare.
        gen_kwargs = self._supported_kwargs(gen_kwargs)

        logger.info(
            "Chatterbox generate: chars=%d chunks=%d ref=%s emotion=%s "
            "exaggeration=%s cfg_weight=%s temperature=%s seed=%s",
            len(text), len(chunks), bool(reference_clip), emotion or DEFAULT_EMOTION,
            gen_kwargs.get("exaggeration"), gen_kwargs.get("cfg_weight"),
            gen_kwargs.get("temperature"), seed,
        )

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        out_path = self._output_root / f"{asset_id}.wav"

        # Stream each chunk straight to the WAV file. Buffering an hour of 24kHz
        # mono in a Python list then np.concatenate would cost ~345 MB+; the
        # SoundFile writer keeps memory flat regardless of total length.
        total = len(chunks)
        total_frames = 0
        t0 = time.monotonic()
        try:
            if prepare is not None:
                prepare(str(reference_clip))
            elif stock_conds is not None:
                model.conds = stock_conds
            with sf.SoundFile(
                str(out_path), mode="w", samplerate=self._sample_rate,
                channels=1, subtype="PCM_16",
            ) as writer:
                for i, chunk in enumerate(chunks):
                    if cancel_event is not None and cancel_event.is_set():
                        raise GenerationCancelled(f"cancelled after {i}/{total} chunks")
                    wav = self._model.generate(text=chunk, **gen_kwargs)
                    if hasattr(wav, "cpu"):
                        wav = wav.cpu().numpy()
                    if wav.ndim == 2:
                        wav = wav.squeeze(0)
                    writer.write(wav)
                    total_frames += int(wav.shape[0])
                    if progress_cb is not None:
                        progress_cb(i + 1, total, "synthesizing")
        except BaseException:
            # Cancel or failure: don't leak a partial WAV.
            out_path.unlink(missing_ok=True)
            raise
        finally:
            if stock_conds is not None:
                model.conds = stock_conds
        gen_seconds = time.monotonic() - t0

        # Post-process: normalization + optional MP3. NOTE: post_process uses
        # pydub, which loads the whole file into memory — see HANDOFF §7 for the
        # multi-hour caveat and the ffmpeg-streaming follow-up.
        try:
            final_path = self.post_process(out_path, output_format=requested_format)
        except Exception:
            out_path.unlink(missing_ok=True)
            raise
        if final_path != out_path:
            out_path.unlink(missing_ok=True)
        actual_format = final_path.suffix.lstrip(".").lower()

        actual_duration = total_frames / self._sample_rate
        logger.info(
            "Chatterbox wrote %s — %.2fs audio in %.1fs wall (%d chunks)",
            final_path, actual_duration, gen_seconds, total,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "text": text,
                "chunks": total,
                "reference_clip_path": str(reference_clip) if reference_clip else None,
                "emotion": emotion or DEFAULT_EMOTION,
                # Recorded so a take that reads well can be reproduced exactly.
                "exaggeration": gen_kwargs.get("exaggeration"),
                "cfg_weight": gen_kwargs.get("cfg_weight"),
                "temperature": gen_kwargs.get("temperature"),
                "seed": seed,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "generation_seconds": round(gen_seconds, 2),
            },
        )

    def _split_for_synthesis(self, text: str) -> list[str]:
        """Split text on sentence boundaries near self._chunk_chars."""
        text = text.strip()
        if len(text) <= self._chunk_chars:
            return [text]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > self._chunk_chars:
            window = remaining[: self._chunk_chars]
            # Prefer the last sentence end inside the window
            split_at = max(
                window.rfind(". "),
                window.rfind("! "),
                window.rfind("? "),
            )
            if split_at == -1:
                # No sentence end — fall back to the last space
                split_at = window.rfind(" ")
            if split_at == -1:
                # Single long token — hard cut
                split_at = self._chunk_chars
            chunks.append(remaining[: split_at + 1].strip())
            remaining = remaining[split_at + 1 :].strip()
        if remaining:
            chunks.append(remaining)
        return chunks
