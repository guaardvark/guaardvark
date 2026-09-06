"""Context-killing CUDA errors are reported as GPU faults, once, and stop retries.

A launch timeout / illegal address / device-side assert leaves the process's CUDA
context dead; every later CUDA call fails with the same text until the backend
restarts. These tests pin:
- the classifier recognises the fatal family and leaves OOM to the OOM ladder,
- the first fault is recorded and the user-facing message says "restart",
- the load-failure explanation prefers the fault over the download/VRAM guess,
- the VRAM probe raises the fault instead of admitting a doomed request, while
  an ordinary probe failure still never raises,
- loader, txt2img and img2img fail fast once a fault is recorded,
- the batch runner reads the fault and fails the remaining prompts without
  trying them.
"""
import functools
from types import SimpleNamespace

import pytest

import backend.services.offline_image_generator as oig
from backend.services.batch_image_generator import BatchImageGenerator
from backend.services.offline_image_generator import (
    ImageGenerationRequest,
    is_fatal_cuda_error,
)

# Verbatim torch text from the 2026-09-03 incident (first line + the boilerplate).
LAUNCH_TIMEOUT = (
    "CUDA error: the launch timed out and was terminated\n"
    "Search for `cudaErrorLaunchTimeout' in https://docs.nvidia.com/cuda/"
    "cuda-runtime-api/group__CUDART__TYPES.html for more information.\n"
    "CUDA kernel errors might be asynchronously reported at some other API call, "
    "so the stacktrace below might be incorrect."
)


@pytest.fixture
def gen():
    g = oig.OfflineImageGenerator()
    g.service_available = True
    return g


# --- classifier -------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        LAUNCH_TIMEOUT,
        "CUDA error: an illegal memory access was encountered",
        "CUDA error: unspecified launch failure",
        "CUDA error: device-side assert triggered",
        "CUDA error: uncorrectable ECC error encountered",
    ],
)
def test_fatal_markers_are_recognised(text):
    assert is_fatal_cuda_error(RuntimeError(text))


@pytest.mark.parametrize(
    "text",
    [
        "CUDA out of memory. Tried to allocate 2.00 GiB",
        "boom",
        "",
        "CUBLAS_STATUS_ALLOC_FAILED when calling cublasCreate (out of memory)",
    ],
)
def test_non_fatal_errors_are_left_alone(text):
    assert not is_fatal_cuda_error(RuntimeError(text))


# --- recording + message ----------------------------------------------------

def test_first_fault_is_recorded_and_later_ones_ignored(gen):
    assert gen.gpu_fault_message() is None
    msg = gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    assert gen._gpu_fault["where"] == "inference"
    assert gen._gpu_fault["error"] == "CUDA error: the launch timed out and was terminated"
    assert "restart the backend" in msg.lower()
    assert "the launch timed out" in msg
    # Second fault in the same process is the same dead context; keep the first.
    gen._mark_gpu_fault(RuntimeError("CUDA error: an illegal memory access was encountered"), "load")
    assert gen._gpu_fault["where"] == "inference"


def test_load_failure_reason_prefers_the_fault(gen, monkeypatch):
    monkeypatch.setattr(gen, "_is_model_downloaded", lambda model_id: True)
    before = gen._load_failure_reason("zimage-turbo", "Tongyi-MAI/Z-Image-Turbo")
    assert "incomplete download" in before
    gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "pipeline load")
    after = gen._load_failure_reason("zimage-turbo", "Tongyi-MAI/Z-Image-Turbo")
    assert "GPU fault" in after
    assert "incomplete download" not in after


def test_status_exposes_the_fault(gen):
    gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    assert gen.get_status()["gpu_fault"]["where"] == "inference"


# --- VRAM probe -------------------------------------------------------------

def _cuda_on(monkeypatch, gen, probe_error):
    monkeypatch.setattr(gen, "_device", "cuda")
    original = oig.torch.cuda.is_available
    monkeypatch.setattr(oig.torch.cuda, "is_available", functools.wraps(original)(lambda: True))
    monkeypatch.setattr(oig.torch.cuda, "empty_cache", lambda: None)

    def _probe():
        raise probe_error

    monkeypatch.setattr(oig.torch.cuda, "mem_get_info", _probe)


def test_probe_raising_a_fatal_error_marks_and_raises(gen, monkeypatch):
    _cuda_on(monkeypatch, gen, RuntimeError(LAUNCH_TIMEOUT))
    with pytest.raises(RuntimeError) as excinfo:
        gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert "GPU fault" in str(excinfo.value)
    assert gen._gpu_fault["where"] == "VRAM probe"


def test_probe_raising_an_ordinary_error_still_admits(gen, monkeypatch):
    _cuda_on(monkeypatch, gen, RuntimeError("boom"))
    admitted = {}
    import backend.services.gpu_memory_orchestrator as gmo
    monkeypatch.setattr(
        gmo, "get_orchestrator",
        lambda: SimpleNamespace(request_model=lambda *a, **k: admitted.setdefault("ok", True)),
    )
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")  # must not raise
    assert admitted["ok"]
    assert gen._gpu_fault is None


def test_probe_refuses_immediately_once_faulted(gen, monkeypatch):
    gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    called = {}
    monkeypatch.setattr(gen, "_vram_estimate_mb", lambda *a, **k: called.setdefault("estimate", True))
    with pytest.raises(RuntimeError):
        gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert "estimate" not in called


# --- entry points fail fast --------------------------------------------------

def test_loader_refuses_once_faulted(gen, monkeypatch):
    gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    touched = {}
    monkeypatch.setattr(gen, "_is_model_downloaded", lambda m: touched.setdefault("disk", True))
    assert gen._load_pipeline("Tongyi-MAI/Z-Image-Turbo") is False
    assert "disk" not in touched


def test_generate_image_fails_fast_once_faulted(gen, monkeypatch):
    gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    monkeypatch.setattr(gen, "_load_pipeline", lambda *a, **k: pytest.fail("must not load"))
    result = gen.generate_image(ImageGenerationRequest(prompt="a cat", model="zimage-turbo"))
    assert result.success is False
    assert "GPU fault" in result.error
    assert "restart the backend" in result.error.lower()


def test_generate_image_from_image_fails_fast_once_faulted(gen, monkeypatch):
    gen._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    monkeypatch.setattr(gen, "_load_pipeline", lambda *a, **k: pytest.fail("must not load"))
    result = gen.generate_image_from_image(prompt="a cat", init_image=None, model="zimage-turbo")
    assert result.success is False
    assert "GPU fault" in result.error


# --- batch runner -------------------------------------------------------------

def test_batch_reads_the_generator_fault():
    batch = BatchImageGenerator()
    batch.image_generator = None
    assert batch._gpu_fault_message() is None

    g = oig.OfflineImageGenerator()
    batch.image_generator = g
    assert batch._gpu_fault_message() is None
    g._mark_gpu_fault(RuntimeError(LAUNCH_TIMEOUT), "inference")
    assert "GPU fault" in batch._gpu_fault_message()
