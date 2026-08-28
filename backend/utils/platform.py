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
