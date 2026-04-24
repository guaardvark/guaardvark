"""Wire concrete backends into the dispatcher at service startup.

Lives separately from app.py so it can be skipped (set
AUDIO_FOUNDRY_DISABLE_BACKENDS=all) in tests without mocking the whole app.
Supported env var values:
    AUDIO_FOUNDRY_DISABLE_BACKENDS=all          # skip all backends
    AUDIO_FOUNDRY_DISABLE_BACKENDS=fx,music     # skip a comma-separated list
    unset / empty                               # register every backend configured in config.yaml
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from service.dispatcher import Dispatcher, Intent

logger = logging.getLogger(__name__)

# Plugin root -> project root: plugins/audio_foundry/ -> /
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def bootstrap(dispatcher: Dispatcher, config: dict[str, Any]) -> None:
    """Register each backend that (a) has config and (b) isn't disabled."""
    disabled = _parse_disabled(os.environ.get("AUDIO_FOUNDRY_DISABLE_BACKENDS", ""))
    if "all" in disabled:
        logger.info("All backends disabled via AUDIO_FOUNDRY_DISABLE_BACKENDS=all")
        return

    runtime = config.get("runtime", {})
    backends_cfg = runtime.get("backends", {})
    output_dir = _PROJECT_ROOT / runtime.get("output", {}).get("dir", "data/outputs/audio")

    if "fx" not in disabled:
        _try_register_fx(dispatcher, backends_cfg.get("audio_fx", {}), output_dir)

    # voice and music land in subsequent commits — intentionally no-op here
    # so the skeleton test suite stays green as those wire up incrementally.


def _try_register_fx(
    dispatcher: Dispatcher,
    cfg: dict[str, Any],
    output_dir: Path,
) -> None:
    try:
        from backends.audio_fx_sao import StableAudioOpenBackend
        backend = StableAudioOpenBackend(
            output_root=output_dir,
            steps=int(cfg.get("steps", 100)),
            sample_rate=int(cfg.get("sample_rate", 44100)),
            max_duration_s=float(cfg.get("max_duration_s", 47.0)),
        )
        dispatcher.register(Intent.FX, backend)
    except Exception as e:
        # Registration failure shouldn't kill the service — log and leave fx unwired.
        # /generate/fx will return 501 via NotWired, which is honest.
        logger.error("Failed to register audio_fx backend: %s", e, exc_info=True)


def _parse_disabled(val: str) -> set[str]:
    if not val:
        return set()
    return {x.strip() for x in val.split(",") if x.strip()}
