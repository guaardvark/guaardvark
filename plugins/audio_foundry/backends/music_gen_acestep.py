"""ACE-Step music generation backend (StepFun, Apache 2.0).

Full-song generation with vocals + instrumental from a style prompt + lyrics.
~3.5B params, ~10 GB VRAM at fp16 — the heaviest backend in the plugin.

ACE-Step pins transformers==4.50.0 and accelerate==1.6.0, which conflicts
with chatterbox-tts (transformers==5.2.0) in the main audio_foundry venv.
The two cannot coexist in one Python environment, so ACE-Step lives in a
sibling venv (`plugins/audio_foundry/venv-music/`) and we drive it via a
long-lived subprocess.

This file IS the in-process AudioBackend implementation that the dispatcher
sees. Internally it spawns scripts/run_acestep.py with the music venv's
python and talks to it over JSON-line stdin/stdout. From the outside, the
contract (load/generate/unload/is_loaded) is identical to every other backend.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backends.base import AudioBackend, GenerationResult

logger = logging.getLogger(__name__)

# Plugin root: plugins/audio_foundry/  (this file is plugins/audio_foundry/backends/)
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_RUNNER_SCRIPT = _PLUGIN_ROOT / "scripts" / "run_acestep.py"
_MUSIC_VENV_PYTHON = _PLUGIN_ROOT / "venv-music" / "bin" / "python"

# Default load timeout — first run pulls ~10 GB of weights from HF, which
# can take several minutes on first cold start. After warm cache, ~30s.
_LOAD_TIMEOUT_S = 900
# Per-generate timeout — covers a 4-minute song at production step count.
_GENERATE_TIMEOUT_S = 600


class ACEStepBackend(AudioBackend):
    """ACE-Step v1 3.5B — full songs with vocals. Driven via subprocess."""

    name = "ace_step_v1_3.5b"
    vram_mb_estimate = 10000
    # 10 GB on a 16 GB card leaves no room for an 8 GB Ollama model to stay
    # resident — Ollama plus ACE-Step's working set OOMs even when individual
    # estimates fit. Mark exclusive so the orchestrator nukes everything else
    # (including Ollama via keep_alive=0) before we load.
    requires_exclusive_vram = True

    MODEL_ID = "ACE-Step/ACE-Step-v1-3.5B"

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 44100,
        max_duration_s: float = 240.0,
        steps: int = 60,
        # ACE-Step's model card recommends 15 for solid prompt adherence;
        # 7.5 was a diffusers-style copy-paste that let the model wander into
        # whatever its strongest training prior was (often country/folk).
        guidance_scale: float = 15.0,
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._max_duration_s = float(max_duration_s)
        self._steps = int(steps)
        self._guidance_scale = float(guidance_scale)
        self._proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def is_loaded(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def load(self) -> None:
        if self.is_loaded:
            return

        if not _MUSIC_VENV_PYTHON.exists():
            raise RuntimeError(
                f"ACE-Step venv not found at {_MUSIC_VENV_PYTHON}. "
                "Run plugins/audio_foundry/scripts/start.sh once to bootstrap it."
            )
        if not _RUNNER_SCRIPT.exists():
            raise RuntimeError(f"ACE-Step runner script missing at {_RUNNER_SCRIPT}")

        logger.info("Spawning ACE-Step daemon: %s %s", _MUSIC_VENV_PYTHON, _RUNNER_SCRIPT)
        self._proc = subprocess.Popen(
            [str(_MUSIC_VENV_PYTHON), "-u", str(_RUNNER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(_PLUGIN_ROOT),
        )

        # Forward daemon stderr to our logger on a background thread so its
        # progress output (download bars, model-load messages) shows up in
        # logs/audio_foundry.log alongside everything else.
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, daemon=True
        )
        self._stderr_thread.start()

        # Quick sanity ping before sending the heavy load command.
        pong = self._send({"op": "ping"})
        if not pong.get("ok"):
            self._kill_proc()
            raise RuntimeError(f"ACE-Step daemon ping failed: {pong}")

        # Send the load — this is the slow one (model download + GPU load).
        load_response = self._send(
            {"op": "load", "model_id": self.MODEL_ID},
            timeout_s=_LOAD_TIMEOUT_S,
        )
        if not load_response.get("ok"):
            err = load_response.get("error", "<no error>")
            self._kill_proc()
            raise RuntimeError(f"ACE-Step load failed: {err}")

        logger.info("ACE-Step daemon ready (model=%s)", self.MODEL_ID)

    def unload(self) -> None:
        if not self.is_loaded:
            self._proc = None
            return

        try:
            # Best-effort graceful shutdown — give the daemon a chance to
            # release VRAM cleanly before we pull the rug.
            self._send({"op": "shutdown"}, timeout_s=15)
        except Exception as e:
            logger.warning("ACE-Step graceful shutdown failed (%s); killing", e)

        try:
            self._proc.wait(timeout=10)  # type: ignore[union-attr]
        except (subprocess.TimeoutExpired, AttributeError):
            self._kill_proc()

        self._proc = None
        logger.info("ACE-Step daemon stopped")

    def generate(self, **params: Any) -> GenerationResult:
        if not self.is_loaded:
            raise RuntimeError("ACE-Step not loaded; dispatcher should call load() first")

        style_prompt: str = params["style_prompt"]
        negative_prompt: str | None = params.get("negative_prompt")
        lyrics: str | None = params.get("lyrics")
        instrumental_only = bool(params.get("instrumental_only", False))
        duration_s = min(float(params.get("duration_s", 60.0)), self._max_duration_s)
        seed = params.get("seed")
        requested_format = params.get("output_format", "wav")

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        wav_path = self._output_root / f"{asset_id}.wav"

        gen_payload = {
            "op": "generate",
            "params": {
                "style_prompt": style_prompt,
                "negative_prompt": negative_prompt or "",
                "lyrics": lyrics or "",
                "instrumental_only": instrumental_only,
                "duration_s": duration_s,
                "steps": self._steps,
                "guidance_scale": self._guidance_scale,
                "sample_rate": self._sample_rate,
                "seed": seed,
                "out_path": str(wav_path),
            },
        }

        t0 = time.monotonic()
        result = self._send(gen_payload, timeout_s=_GENERATE_TIMEOUT_S)
        gen_seconds = time.monotonic() - t0

        if not result.get("ok"):
            err = result.get("error", "<no error>")
            tb = result.get("traceback")
            if tb:
                # Log the daemon's full traceback at ERROR so it's visible in
                # logs/audio_foundry.log even if uvicorn's stderr capture
                # ate the daemon's own _eprint output.
                logger.error("ACE-Step daemon traceback:\n%s", tb)
            raise RuntimeError(f"ACE-Step generate failed: {err}")

        actual_duration = float(result["duration_s"])

        final_path = self.post_process(wav_path, output_format=requested_format)
        actual_format = final_path.suffix.lstrip(".").lower()

        logger.info(
            "ACE-Step wrote %s — %.2fs audio in %.1fs wall",
            final_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "model": self.MODEL_ID,
                "style_prompt": style_prompt,
                "negative_prompt": negative_prompt or "",
                "lyrics": lyrics or "",
                "instrumental_only": instrumental_only,
                "requested_duration_s": duration_s,
                "steps": self._steps,
                "guidance_scale": self._guidance_scale,
                "seed": seed,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "generation_seconds": round(gen_seconds, 2),
            },
        )

    # ----- subprocess plumbing -----

    def _send(self, command: dict[str, Any], timeout_s: float = 30) -> dict[str, Any]:
        """Send one JSON command, read one JSON response. Synchronous."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("ACE-Step daemon not running")

        line = json.dumps(command) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"ACE-Step daemon stdin closed: {e}") from e

        # Read one line back, with a timeout to avoid hangs if the daemon
        # silently dies. readline() doesn't accept a timeout on its own, so
        # we use a watchdog thread that kills the process on overrun — the
        # readline then returns "" and we surface a clean error.
        result_holder: dict[str, Any] = {}

        def _watchdog() -> None:
            time.sleep(timeout_s)
            if not result_holder:
                logger.error("ACE-Step daemon timed out after %ss; killing", timeout_s)
                self._kill_proc()

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()

        response_line = self._proc.stdout.readline()
        result_holder["done"] = True

        if not response_line:
            raise RuntimeError("ACE-Step daemon closed stdout (likely crashed or timed out)")

        try:
            return json.loads(response_line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ACE-Step daemon returned non-JSON: {response_line!r} ({e})") from e

    def _pump_stderr(self) -> None:
        """Drain the daemon's stderr to our logger on a background thread."""
        if self._proc is None or self._proc.stderr is None:
            return
        for line in self._proc.stderr:
            line = line.rstrip()
            if line:
                logger.info("acestep: %s", line)

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except Exception:
            pass
