"""What this machine can do, in one place.

Guaardvark was built on one Linux workstation with an NVIDIA card, and for a
long time every assumption that machine made was a requirement nobody had
written down: a CUDA check that meant "no GPU here", a font path, an X11
display, even the card's name in an error message. Features ask here instead
of testing ``torch.cuda`` themselves, so the answer — and the sentence a user
sees when the answer is no — is the same everywhere.
"""

from __future__ import annotations

import platform as _platform
from typing import Optional


class PlatformUnsupported(RuntimeError):
    """A feature needs hardware or a subsystem this machine does not have."""


def os_name() -> str:
    """'Linux', 'Darwin' or 'Windows'."""
    return _platform.system()


def is_macos() -> bool:
    return os_name() == "Darwin"


def has_cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - no torch means no CUDA
        return False


def has_mps() -> bool:
    """Apple Silicon GPU via Metal."""
    try:
        import torch

        return bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:  # noqa: BLE001 - no torch means no MPS
        return False


def device_kind() -> str:
    """'cuda', 'mps' or 'cpu' — what torch work would run on here."""
    if has_cuda():
        return "cuda"
    if has_mps():
        return "mps"
    return "cpu"


def gpu_name() -> Optional[str]:
    """The GPU's marketing name, or None. Never assume one in a message."""
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        pass
    if has_mps():
        return "Apple Silicon (Metal)"
    return None


def describe() -> str:
    """One line for logs and messages: 'Linux, cuda (NVIDIA ...)'."""
    name = gpu_name()
    return f"{os_name()}, {device_kind()}" + (f" ({name})" if name else "")


def require_cuda(feature: str, *, metal_status: str = "not supported") -> None:
    """Raise PlatformUnsupported unless CUDA is available.

    ``metal_status`` is what to tell an Apple Silicon user: "not supported",
    "untested" or "experimental" — say which, because they will ask.
    """
    if has_cuda():
        return
    if is_macos():
        raise PlatformUnsupported(
            f"{feature} needs an NVIDIA GPU (CUDA). On Apple Silicon (Metal) it is "
            f"{metal_status}. This machine: {describe()}."
        )
    raise PlatformUnsupported(
        f"{feature} needs an NVIDIA GPU (CUDA) and none is available. This machine: {describe()}."
    )


def screen_agent_available() -> bool:
    """The screen agent is Xvfb + X11: Linux only."""
    return os_name() == "Linux"


def media_player_available() -> bool:
    """The media tools reach players over D-Bus MPRIS2 (gdbus), set volume with
    ALSA's amixer and play in a Linux VLC build: Linux only."""
    return os_name() == "Linux"


# ── LoRA trainer time budgets ─────────────────────────────────────────────────
# One platform decision, next to the probes above, so the trainer daemon caps,
# the Celery task limits, and the stuck-training reaper cutoff cannot drift
# apart.
#
# Stock (CUDA) keeps the values measured on main: a healthy Z-Image subject
# finishes well inside the 30 min train cap, so a run still going at 30 min is
# wedged and the reaper frees the Subject 15 min later. Apple Silicon (MPS)
# stages each heavy module and runs ~11s/step, so the default 640-step schedule
# is ~2h — anchored by a real 75 min / 400-step MPS run; the old 30 min CUDA-era
# cap killed a healthy run mid-training (the Elara run failed at step 151/640).
IS_APPLE_SILICON = is_macos()


def lora_train_time_budgets(
    apple_silicon: bool,
) -> tuple[int, int, int | None, int | None, int]:
    """Return (load, train, task_soft, task_hard, reap) seconds for a platform.

    Pure so the non-Darwin numbers can be asserted in tests on any host.
    ``train`` is the ceiling for one train call; on CUDA the trainer gives each
    run a tighter budget from its step count (RealLoraTrainer._train_budget_s).
    The CUDA ceiling covers 3000 steps at 1024 px at the measured 3.6 s a step
    (16 GB NVIDIA card, 2026-11-19) with a third to spare.
    """
    load = 3600 if apple_silicon else 900       # model load from disk
    train = 10800 if apple_silicon else 14400   # per train call
    if apple_silicon:
        task_soft = load + train + 300   # 245 min: leaves room for teardown
        task_hard = load + train + 900   # 255 min: > 3h train + 1h load
    else:
        # Also covers the wait for the card (CAST_TRAIN_GPU_WAIT_S, 10 min).
        task_soft = 600 + load + train + 300   # 270 min
        task_hard = 600 + load + train + 900   # 280 min
    reap = task_hard + 900               # never a false positive
    return load, train, task_soft, task_hard, reap


(
    LORA_LOAD_TIMEOUT_S,
    LORA_TRAIN_TIMEOUT_S,
    LORA_TRAIN_TASK_SOFT_TIME_LIMIT_S,
    LORA_TRAIN_TASK_TIME_LIMIT_S,
    LORA_REAP_STUCK_AFTER_S,
) = lora_train_time_budgets(IS_APPLE_SILICON)
