"""ACE-Step subprocess daemon.

Runs INSIDE plugins/audio_foundry/venv-music/ — that venv has ACE-Step's
incompatible deps (transformers==4.50, accelerate==1.6, pytorch_lightning).
The main audio_foundry venv has chatterbox-tts pulling transformers==5.2,
which can't coexist. Subprocess isolation lets both live in the same plugin.

Protocol: JSON-line over stdin/stdout. One command per line in, one response
per line out. The parent process (ACEStepBackend in the chatterbox venv) owns
spawn/lifecycle/shutdown.

Commands:
    {"op": "ping"}
        -> {"ok": true, "ready": <bool>}

    {"op": "load",
     "model_id": "ACE-Step/ACE-Step-v1-3.5B"}
        -> {"ok": true} on success
        -> {"ok": false, "error": "..."} on failure

    {"op": "generate",
     "params": {
        "style_prompt": "...",
        "negative_prompt": "...",   # optional, "" or absent = no neg steering
        "lyrics": "...",
        "instrumental_only": false,
        "duration_s": 60.0,
        "steps": 60,
        "guidance_scale": 7.5,
        "sample_rate": 44100,
        "seed": null,
        "out_path": "/abs/path/to/output.wav"
     }}
        -> {"ok": true, "samples": <int>, "channels": <int>, "duration_s": <float>}
        -> {"ok": false, "error": "..."}

    {"op": "unload"}
        -> {"ok": true}

    {"op": "shutdown"}
        -> {"ok": true}, then process exits

Errors are caught and reported as {"ok": false, ...} — the daemon stays alive
unless explicitly told to shut down. stderr is reserved for human-readable
logging; the parent forwards it to logs/audio_foundry.log.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

# Heavy imports are deferred to load() — keep daemon startup snappy so the
# parent's spawn-and-ping handshake doesn't hang for 30 seconds.
_pipeline: Any = None
_torch = None


def _eprint(msg: str) -> None:
    """Human log line on stderr — parent captures these for the plugin log."""
    print(msg, file=sys.stderr, flush=True)


def _respond(payload: dict[str, Any]) -> None:
    """Single JSON line on stdout — terminator is implicit \\n from print."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _do_load(model_id: str) -> dict[str, Any]:
    global _pipeline, _torch
    if _pipeline is not None:
        return {"ok": True}

    _eprint(f"[run_acestep] loading {model_id} (first run downloads ~10 GB)...")
    import torch
    _torch = torch

    if not torch.cuda.is_available():
        return {"ok": False, "error": "CUDA not available — ACE-Step requires a GPU"}

    try:
        from acestep.pipeline_ace_step import ACEStepPipeline
    except ImportError as e:
        return {
            "ok": False,
            "error": f"acestep not installed in this venv. ImportError: {e}",
        }

    try:
        _pipeline = ACEStepPipeline(
            checkpoint_path=model_id,
            device="cuda",
            torch_dtype=torch.float16,
        )
    except TypeError:
        # Older ACE-Step releases use a different constructor — be tolerant.
        _pipeline = ACEStepPipeline.from_pretrained(
            model_id,
            torch_dtype=torch.float16,
        ).to("cuda")

    _eprint(f"[run_acestep] {model_id} loaded (fp16, cuda)")
    return {"ok": True}


def _do_generate(params: dict[str, Any]) -> dict[str, Any]:
    if _pipeline is None:
        return {"ok": False, "error": "model not loaded — send 'load' first"}

    style_prompt = params["style_prompt"]
    negative_prompt = params.get("negative_prompt") or ""
    lyrics = params.get("lyrics") or ""
    instrumental_only = bool(params.get("instrumental_only", False))
    duration_s = float(params["duration_s"])
    steps = int(params.get("steps", 60))
    guidance_scale = float(params.get("guidance_scale", 7.5))
    sample_rate = int(params.get("sample_rate", 44100))
    seed = params.get("seed")
    out_path = params["out_path"]

    effective_lyrics = "" if instrumental_only else lyrics

    # ACE-Step uses `infer_step` (not `num_inference_steps`) and `manual_seeds`
    # (a list, not a torch.Generator). The kwarg names diverge from the
    # diffusers convention chatterbox/SAO use — caught the hard way on 2026-04-28.
    manual_seeds = [int(seed)] if seed is not None else None

    _eprint(
        f"[run_acestep] generate style={style_prompt[:60]!r} duration={duration_s:.1f}s "
        f"lyrics={len(effective_lyrics)}-chars instrumental={instrumental_only} "
        f"neg={negative_prompt[:40]!r} guidance={guidance_scale} seed={seed}"
    )

    # Pass save_path/format so ACE-Step writes the WAV itself (using torchaudio
    # internally, which routes through torchcodec). This works regardless of
    # what shape the pipeline returns — empty tuple, None, or a tensor.
    #
    # NOTE: ACE-Step's __call__ does NOT accept `sample_rate` — the pipeline
    # uses its model's native rate (44.1 kHz). The caller's requested
    # `sample_rate` is honored later via sf.info() reading the actual file.
    #
    # `negative_prompt` may not be supported by every ACE-Step release — older
    # checkpoints raise TypeError if we pass an unknown kwarg. Try-with then
    # fall back keeps us forward-compatible without breaking pinned setups.
    pipeline_kwargs = dict(
        prompt=style_prompt,
        lyrics=effective_lyrics,
        audio_duration=duration_s,
        infer_step=steps,
        guidance_scale=guidance_scale,
        manual_seeds=manual_seeds,
        save_path=out_path,
        format="wav",
    )
    if negative_prompt:
        pipeline_kwargs["negative_prompt"] = negative_prompt
    try:
        result = _pipeline(**pipeline_kwargs)
    except TypeError as e:
        if "negative_prompt" in str(e) and "negative_prompt" in pipeline_kwargs:
            _eprint(
                f"[run_acestep] this ACE-Step build doesn't accept negative_prompt "
                f"({e}); retrying without it"
            )
            pipeline_kwargs.pop("negative_prompt", None)
            result = _pipeline(**pipeline_kwargs)
        else:
            raise
    _eprint(f"[run_acestep] pipeline returned: type={type(result).__name__}")

    import os
    # If ACE-Step wrote the file directly, we're done — read its shape via
    # soundfile to compute duration without re-decoding the tensor.
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        import soundfile as sf
        info = sf.info(out_path)
        samples = int(info.frames)
        channels = int(info.channels)
        actual_sr = int(info.samplerate)
        return {
            "ok": True,
            "samples": samples,
            "channels": channels,
            "duration_s": samples / actual_sr if actual_sr else 0.0,
        }

    # Fallback: pipeline didn't write a file, dig the audio out of the result.
    # Tolerate every shape we've seen — empty tuples, .audios attr, raw tensor.
    audio_tensor = None
    if hasattr(result, "audios") and len(result.audios):
        audio_tensor = result.audios[0]
    elif isinstance(result, (list, tuple)) and len(result) > 0:
        audio_tensor = result[0]
    elif result is not None:
        audio_tensor = result

    if audio_tensor is None:
        raise RuntimeError(
            f"ACE-Step pipeline returned no audio (type={type(result).__name__}, "
            f"len={len(result) if hasattr(result, '__len__') else 'n/a'}) and "
            f"didn't write {out_path}. Cannot recover output."
        )

    if hasattr(audio_tensor, "cpu"):
        audio_np = audio_tensor.float().cpu().numpy()
    else:
        import numpy as np
        audio_np = np.asarray(audio_tensor)

    # Normalize to [samples, channels] for soundfile.
    if audio_np.ndim == 3:
        audio_np = audio_np.squeeze(0)
    if audio_np.ndim == 2 and audio_np.shape[0] in (1, 2):
        audio_np = audio_np.T

    import soundfile as sf
    sf.write(out_path, audio_np, sample_rate)

    samples = int(audio_np.shape[0])
    channels = int(audio_np.shape[1]) if audio_np.ndim == 2 else 1

    return {
        "ok": True,
        "samples": samples,
        "channels": channels,
        "duration_s": samples / sample_rate,
    }


def _do_unload() -> dict[str, Any]:
    global _pipeline
    if _pipeline is None:
        return {"ok": True}
    del _pipeline
    _pipeline = None
    if _torch is not None:
        _torch.cuda.empty_cache()
    _eprint("[run_acestep] unloaded")
    return {"ok": True}


def _handle(cmd: dict[str, Any]) -> dict[str, Any]:
    op = cmd.get("op")
    if op == "ping":
        return {"ok": True, "ready": _pipeline is not None}
    if op == "load":
        return _do_load(cmd.get("model_id", "ACE-Step/ACE-Step-v1-3.5B"))
    if op == "generate":
        return _do_generate(cmd["params"])
    if op == "unload":
        return _do_unload()
    if op == "shutdown":
        return {"ok": True}
    return {"ok": False, "error": f"unknown op: {op!r}"}


def main() -> int:
    _eprint("[run_acestep] daemon ready, waiting on stdin...")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            _respond({"ok": False, "error": f"bad json: {e}"})
            continue

        try:
            response = _handle(cmd)
        except Exception as e:
            tb = traceback.format_exc()
            _eprint(f"[run_acestep] handler exception:\n{tb}")
            # Include the traceback in the response so the parent's error
            # message captures the actual failure line — uvicorn's stderr
            # capture sometimes swallows our _eprint output, leaving callers
            # with a bare exception message and no clue where it fired.
            response = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "traceback": tb,
            }

        _respond(response)

        if cmd.get("op") == "shutdown":
            _eprint("[run_acestep] shutdown requested, exiting")
            return 0

    _eprint("[run_acestep] stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
