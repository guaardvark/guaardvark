"""
Import smoke tests for the media stack with CUDA forced absent.

Every media module must import cleanly on a machine with no NVIDIA GPU — a Mac,
a CPU-only Linux box, or a CI runner. Module-level code that touches an
accelerator is the risk: it runs at import, so a mistake there is not a
degraded feature, it is an ImportError for everything downstream.

Two properties of this file are load-bearing:

  * Each module is imported in its OWN subprocess. A single-process sweep would
    import module A, and any module B that A depends on comes along with it —
    so the later `import B` returns the cache and B's module-level code never
    re-runs under the no-CUDA condition. The bug would be invisible.

  * Availability is forced False at BOTH sources: `torch.cuda.is_available` and
    the GPU coordinator's `has_gpu`. Patching only torch is not enough; the
    coordinator probes the driver independently and would still report a GPU
    on a developer box.

Regression guard: `_mps_available()` was called at module scope one line group
above its own `def`. `bool(_nvidia_gpu) or _mps_available()` short-circuits away
the call whenever CUDA is present, so the NameError could only ever fire on the
machines that had no CUDA — exactly the machines the code was written for, and
never the ones it was developed on.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Media modules whose import path must survive a machine with no accelerator.
# Add to this list rather than writing a new test when a media module gains
# module-level device probing.
MEDIA_MODULES = [
    "backend.services.offline_video_generator",
    "backend.services.comfyui_video_generator",
    "backend.services.comfyui_image_generator",
    "backend.services.video_generation_router",
    "backend.services.video_text_overlay",
    "backend.services.infographic_generator",
    "backend.utils.gpu_check",
]

_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {root!r})

    # Both availability sources report "no GPU" before the module under test
    # is imported, so its module-level code runs the non-CUDA branch.
    import backend.services.gpu_resource_coordinator as grc

    class _NoGpu:
        def has_gpu(self):
            return False

    grc.get_gpu_coordinator = lambda: _NoGpu()

    import functools
    import torch

    # The stand-ins keep the real probe's metadata: torch's dynamo reads
    # ``is_available.__wrapped__`` while building its trace rules, and
    # diffusers imports dynamo, so a bare lambda here would turn every media
    # import into an AttributeError instead of exercising the no-GPU branch.
    torch.cuda.is_available = functools.wraps(torch.cuda.is_available)(lambda: False)
    torch.backends.mps.is_available = functools.wraps(torch.backends.mps.is_available)(lambda: False)

    import importlib
    importlib.import_module({module!r})
    print("IMPORT_OK")
    """
)


def _import_without_cuda(module: str) -> subprocess.CompletedProcess:
    """Import `module` in a fresh interpreter that believes it has no GPU."""
    return subprocess.run(
        [sys.executable, "-c", _PROBE.format(root=str(REPO_ROOT), module=module)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO_ROOT),
    )


@pytest.mark.parametrize("module", MEDIA_MODULES)
def test_media_module_imports_without_cuda(module):
    result = _import_without_cuda(module)
    assert "IMPORT_OK" in result.stdout, (
        f"{module} failed to import with CUDA absent.\n"
        f"exit={result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )


def test_offline_video_degrades_instead_of_raising():
    """No accelerator is an unavailable feature, not an import crash.

    Guards the specific shape of the original defect: the module must both
    import AND settle its availability flags to False, rather than raising
    NameError while computing them.
    """
    probe = _PROBE.format(
        root=str(REPO_ROOT), module="backend.services.offline_video_generator"
    ) + textwrap.dedent(
        """
        import backend.services.offline_video_generator as ovg
        assert ovg.gpu_available is False, ovg.gpu_available
        assert ovg.video_generator_available is False, ovg.video_generator_available
        print("DEGRADED_OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO_ROOT),
    )
    assert "DEGRADED_OK" in result.stdout, (
        f"offline_video_generator did not degrade cleanly with CUDA absent.\n"
        f"exit={result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
