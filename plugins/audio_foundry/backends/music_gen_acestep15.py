"""ACE-Step 1.5 music backend: 2B turbo DiT plus the 1.7B planner LM, MIT license.

An optional model beside ACE-Step v1, which stays the default: a request picks it
with ``model: "ace-step-1.5"`` (backends/music_gen.py routes). Its code pins torch
2.10 and transformers 4.57, so it runs in its own venv
(plugins/audio_foundry/venv-music15/, built only by Install in Audio Studio →
Manage models) as a JSON-line subprocess, scripts/run_acestep15.py. The subprocess
plumbing is ACEStepBackend's; only the environment, the load check and the request
differ.

ACE-Step 1.5 has no negative conditioning for its DiT, so a negative prompt is
reported as not applied. It can continue a finished track (repaint past its end),
but that is not offered yet: the join measured on 2026-10-03 keeps the source's
fade-out and then jumps to full level.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from backends.base import GenerationResult
from backends.hub_weights import INSTALL_HINT, WeightsNotInstalled
from backends.music_gen_acestep import ACEStepBackend
from backends.music_prompt_facts import resolve_accepted_params, resolve_plan_params

logger = logging.getLogger(__name__)

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PLUGIN_ROOT.parent.parent
_RUNNER_SCRIPT = _PLUGIN_ROOT / "scripts" / "run_acestep15.py"
# Weights location, pinned revisions and the environment name; the Install flow
# (backend/services/audio_foundry_models.py) reads the same file.
CATALOG = _PLUGIN_ROOT / "backends" / "acestep15_files.json"
# setup_music15.sh writes the source commit here as its last step.
ENV_MARKER = ".acestep15-source"

_LOAD_TIMEOUT_S = 600
_GENERATE_TIMEOUT_S = 600
# A plan is one planner pass, metadata and lyrics, before any audio.
_PLAN_TIMEOUT_S = 240


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def environment_python(cat: dict[str, Any]) -> Path:
    return _PLUGIN_ROOT / cat["environment"] / "bin" / "python"


def environment_ready(cat: dict[str, Any]) -> bool:
    """The venv exists and was built from the pinned source commit."""
    marker = _PLUGIN_ROOT / cat["environment"] / ENV_MARKER
    try:
        built_from = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return environment_python(cat).exists() and built_from == cat["source"]["commit"]


def _constraints_of(arrangement: dict[str, Any]) -> dict[str, Any]:
    return {
        "bpm": arrangement.get("bpm"),
        "keyscale": arrangement.get("keyscale") or "",
        "timesignature": arrangement.get("timesignature") or "",
        "vocal_language": arrangement.get("vocal_language") or "",
        "lyrics": arrangement.get("lyrics") or "",
        "instrumental_only": bool(arrangement.get("instrumental_only")),
        "duration_s": arrangement.get("duration_s"),
    }


def _apply_locks(arrangement: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    """A stated or edited value replaces whatever the planner wrote for that field."""
    if resolved.get("bpm"):
        arrangement["bpm"] = int(resolved["bpm"])
    if resolved.get("keyscale"):
        arrangement["keyscale"] = resolved["keyscale"]
    if resolved.get("timesignature"):
        arrangement["timesignature"] = str(resolved["timesignature"])
    if resolved.get("vocal_language"):
        arrangement["vocal_language"] = resolved["vocal_language"]
    arrangement["duration_s"] = float(resolved.get("duration_s") or arrangement.get("duration_s") or 60)
    arrangement["instrumental_only"] = bool(resolved.get("instrumental_only"))
    if arrangement["instrumental_only"]:
        arrangement["lyrics"] = "[Instrumental]"
    return arrangement


def _arrangement_from_request(resolved: dict[str, Any]) -> dict[str, Any]:
    """The card that was accepted, with no second trip through the planner."""
    instrumental = bool(resolved.get("instrumental_only"))
    lyrics = "[Instrumental]" if instrumental else (resolved.get("lyrics") or "")
    return {
        "caption": (resolved.get("style_prompt") or "").strip(),
        "lyrics": lyrics,
        "bpm": resolved.get("bpm"),
        "keyscale": resolved.get("keyscale") or "",
        "timesignature": resolved.get("timesignature") or "",
        "vocal_language": resolved.get("vocal_language") or "",
        "duration_s": float(resolved.get("duration_s") or 60),
        "instrumental_only": instrumental,
        "genres": "",
        "query": (resolved.get("style_prompt") or "").strip(),
    }


def missing_weights(cat: dict[str, Any]) -> list[str]:
    checkpoints = _REPO_ROOT / cat["local_dir"] / "checkpoints"
    return [f for f in cat["files"] if not (checkpoints / f).is_file()]


class ACEStep15Backend(ACEStepBackend):
    """ACE-Step 1.5 turbo with its 1.7B planner. Driven via subprocess."""

    name = "ace_step_1.5_turbo"
    # Peak 14.4 GB (nvidia-smi, the daemon process) for 30 s and 240 s renders on a
    # 16 GB card, 2026-10-04; about 10 GB of that is bf16 weights. It takes the whole GPU.
    vram_mb_estimate = 14400
    requires_exclusive_vram = True

    MODEL_ID = "ACE-Step/Ace-Step1.5"
    # The turbo DiT is distilled for 8 steps (upstream default); every 1.5 render
    # measured on 2026-10-03 used 8.
    STEPS = 8

    def __init__(self, output_root: Path, max_duration_s: float = 240.0) -> None:
        super().__init__(output_root=output_root, max_duration_s=max_duration_s)
        self._planner: str | None = None

    def load(self) -> None:
        if self.is_loaded:
            return

        cat = load_catalog()
        if not environment_ready(cat):
            raise WeightsNotInstalled(
                f"ACE-Step 1.5's Python environment ({cat['environment']}) is not built. {INSTALL_HINT}"
            )
        missing = missing_weights(cat)
        if missing:
            raise WeightsNotInstalled(
                f"ACE-Step 1.5 weights are not on this machine (missing {missing[0]}). {INSTALL_HINT}"
            )

        # The plugin root on PYTHONPATH would put its own `backends`/`service`
        # packages ahead of ACE-Step's imports.
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        self._start_daemon(environment_python(cat), _RUNNER_SCRIPT, env=env)

        load_response = self._send({"op": "load"}, timeout_s=_LOAD_TIMEOUT_S)
        if not load_response.get("ok"):
            err = load_response.get("error", "<no error>")
            self._kill_proc()
            raise RuntimeError(f"ACE-Step 1.5 load failed: {err}")

        self._planner = cat["planner"]
        logger.info("ACE-Step 1.5 daemon ready (model=%s)", self.MODEL_ID)

    def plan(self, **params: Any) -> dict[str, Any]:
        """Expand a description with the loaded 1.7B planner. No audio."""
        if not self.is_loaded:
            raise RuntimeError("ACE-Step 1.5 not loaded; dispatcher should call load() first")
        resolved, locks = resolve_plan_params(params)
        arrangement = self._request_plan(resolved)
        arrangement = _apply_locks(arrangement, resolved)
        arrangement["locks"] = locks
        arrangement["planner"] = self._planner
        return arrangement

    def _request_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        duration_s = min(float(params.get("duration_s", 60.0)), self._max_duration_s)
        result = self._send(
            {
                "op": "plan",
                "params": {
                    "query": params.get("style_prompt") or "",
                    "lyrics": params.get("lyrics") or "",
                    "instrumental_only": bool(params.get("instrumental_only", False)),
                    "duration_s": duration_s,
                    "bpm": params.get("bpm"),
                    "keyscale": params.get("keyscale") or "",
                    "timesignature": params.get("timesignature") or "",
                    "vocal_language": params.get("vocal_language") or "",
                },
            },
            timeout_s=_PLAN_TIMEOUT_S,
        )
        if not result.get("ok"):
            raise RuntimeError(f"ACE-Step 1.5 plan failed: {result.get('error', '<no error>')}")
        arrangement = result.get("arrangement")
        if not isinstance(arrangement, dict) or not (arrangement.get("caption") or "").strip():
            raise RuntimeError("ACE-Step 1.5 plan failed: planner returned no caption")
        return arrangement

    def generate(self, **params: Any) -> GenerationResult:
        if not self.is_loaded:
            raise RuntimeError("ACE-Step 1.5 not loaded; dispatcher should call load() first")

        negative_prompt: str | None = params.get("negative_prompt")
        seed = params.get("seed")
        requested_format = params.get("output_format", "wav")
        accepted = bool(params.get("arrangement_accepted"))
        if accepted:
            resolved = resolve_accepted_params(params)
            arrangement = _arrangement_from_request(resolved)
            arrangement["accepted"] = True
            arrangement["locks"] = {}
        else:
            # A client that did not arrange still gets the planner. The page
            # arranges first so the caption can be edited; this path is the
            # one a tool call takes.
            resolved, locks = resolve_plan_params(params)
            arrangement = _apply_locks(self._request_plan(resolved), resolved)
            arrangement["locks"] = locks
            arrangement["accepted"] = False
            arrangement["planner"] = self._planner
            resolved = {**resolved, **_constraints_of(arrangement)}
            resolved["style_prompt"] = arrangement["caption"]
            resolved["lyrics"] = arrangement["lyrics"]

        style_prompt = (resolved.get("style_prompt") or "").strip()
        if not style_prompt:
            raise RuntimeError("ACE-Step 1.5 needs a caption")
        lyrics = resolved.get("lyrics") or ""
        instrumental_only = bool(resolved.get("instrumental_only", False))
        duration_s = min(float(resolved.get("duration_s", 60.0)), self._max_duration_s)
        arrangement["planner"] = arrangement.get("planner") or self._planner
        arrangement["duration_s"] = duration_s

        self._output_root.mkdir(parents=True, exist_ok=True)
        wav_path = self._output_root / f"{uuid.uuid4().hex}.wav"

        t0 = time.monotonic()
        result = self._send(
            {
                "op": "generate",
                "params": {
                    "style_prompt": style_prompt,
                    "lyrics": lyrics or "",
                    "instrumental_only": instrumental_only,
                    "duration_s": duration_s,
                    "bpm": arrangement.get("bpm"),
                    "keyscale": arrangement.get("keyscale") or "",
                    "timesignature": arrangement.get("timesignature") or "",
                    "vocal_language": arrangement.get("vocal_language") or "",
                    # The caption above is the one that was planned or edited.
                    "use_cot_caption": False,
                    "steps": self.STEPS,
                    "seed": seed,
                    "thinking": True,
                    "out_path": str(wav_path),
                },
            },
            timeout_s=_GENERATE_TIMEOUT_S,
        )
        gen_seconds = time.monotonic() - t0

        if not result.get("ok"):
            if result.get("traceback"):
                logger.error("ACE-Step 1.5 daemon traceback:\n%s", result["traceback"])
            raise RuntimeError(f"ACE-Step 1.5 generate failed: {result.get('error', '<no error>')}")

        final_path = self.post_process(wav_path, output_format=requested_format)
        logger.info(
            "ACE-Step 1.5 wrote %s — %.2fs audio in %.1fs wall",
            final_path, float(result["duration_s"]), gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=float(result["duration_s"]),
            sample_rate=int(result["sample_rate"]),
            meta={
                "backend": self.name,
                "model": self.MODEL_ID,
                "license": "MIT",
                "style_prompt": style_prompt,
                "negative_prompt": negative_prompt or "",
                "negative_prompt_applied": False,
                "lyrics": lyrics or "",
                "instrumental_only": instrumental_only,
                "requested_duration_s": duration_s,
                "arrangement": arrangement,
                "use_cot_caption": False,
                "steps": self.STEPS,
                "planner": self._planner,
                # The seed the render used, so the take can be repeated even when
                # none was asked for.
                "seed": result.get("seed", seed),
                "requested_seed": seed,
                "requested_output_format": requested_format,
                "actual_output_format": final_path.suffix.lstrip(".").lower(),
                "generation_seconds": round(gen_seconds, 2),
            },
        )
