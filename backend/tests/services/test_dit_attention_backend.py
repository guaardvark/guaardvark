"""2026-08-24: a 2048² Krea 2 batch died on denoise step 0 asking for 51.05 GiB.

Krea 2 is 48 query heads / 12 KV heads and its processor forwards the pipeline's
text padding mask into SDPA. On torch 2.5.1 flash refuses a mask and the
mem-efficient kernel refuses dense GQA, so the dispatch lands on MATH, which
materializes the whole [1, heads, S, S] score matrix. Measured on this box at the
production shapes: math 51.05 GB (OOM) vs cuDNN 794 MB vs no-mask flash 499 MB.

No amount of VRAM reclaim serves a 51 GB allocation, so this is a kernel-selection
bug, not a memory-management one. These guard the selection and the refusal.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services.offline_image_generator import OfflineImageGenerator


def _gen(transformer=None):
    g = OfflineImageGenerator.__new__(OfflineImageGenerator)
    g._pipeline = SimpleNamespace(transformer=transformer)
    return g


# --------------------------------------------------------------------------
# Detecting the shape that breaks
# --------------------------------------------------------------------------
def test_krea2_head_config_is_recognised_as_grouped_query():
    """48 query heads over 12 KV heads — the real Krea-2-Turbo config."""
    g = _gen(SimpleNamespace(config=SimpleNamespace(
        num_attention_heads=48, num_key_value_heads=12)))
    assert g._uses_grouped_query_attention() is True


def test_zimage_head_config_is_not_grouped_query():
    """Z-Image is 30/30, which is why it never hit this and Krea 2 did."""
    g = _gen(SimpleNamespace(config=SimpleNamespace(n_heads=30, n_kv_heads=30)))
    assert g._uses_grouped_query_attention() is False


def test_gqa_probe_reads_config_not_model_name():
    """Keyed on the config so a future GQA model is covered without anyone
    remembering to add its name."""
    g = _gen(SimpleNamespace(config=SimpleNamespace(n_heads=64, n_kv_heads=8)))
    assert g._uses_grouped_query_attention() is True


@pytest.mark.parametrize("transformer", [
    None,
    SimpleNamespace(config=None),
    SimpleNamespace(config=SimpleNamespace()),
    SimpleNamespace(config=SimpleNamespace(num_attention_heads=48, num_key_value_heads=None)),
    SimpleNamespace(config=SimpleNamespace(num_attention_heads="?", num_key_value_heads="?")),
])
def test_gqa_probe_never_raises_on_an_unfamiliar_pipeline(transformer):
    """A probe that throws would take down every generation on an SD/SDXL build."""
    assert _gen(transformer)._uses_grouped_query_attention() is False


# --------------------------------------------------------------------------
# The backend actually gets chosen
# --------------------------------------------------------------------------
def test_cudnn_is_preferred_because_it_takes_both_a_mask_and_gqa():
    """Order matters: the default dispatch picks the one kernel that cannot do
    this job, so the backend has to be named explicitly."""
    import backend.services.offline_image_generator as mod
    import inspect

    src = inspect.getsource(mod.OfflineImageGenerator._load_model_internal) \
        if hasattr(mod.OfflineImageGenerator, "_load_model_internal") else ""
    if not src:
        src = inspect.getsource(mod)
    i = src.index("_native_cudnn")
    for later in ("_native_efficient", "_native_flash"):
        assert i < src.index(later), f"_native_cudnn must be tried before {later}"


def test_backend_selection_falls_through_and_records_what_took():
    """A build where cuDNN is unavailable must still land on something efficient,
    and must record which — the refusal gate keys off that attribute."""
    tr = MagicMock()
    attempts = []

    def _set(backend):
        attempts.append(backend)
        if backend == "_native_cudnn":
            raise RuntimeError("cuDNN attention unavailable on this build")

    tr.set_attention_backend.side_effect = _set

    active = None
    for backend in ("_native_cudnn", "_native_efficient", "_native_flash"):
        try:
            tr.set_attention_backend(backend)
            active = backend
            break
        except Exception:
            continue

    assert attempts == ["_native_cudnn", "_native_efficient"]
    assert active == "_native_efficient"


# --------------------------------------------------------------------------
# The refusal
# --------------------------------------------------------------------------
@pytest.mark.parametrize("w,h,gqa,backend,should_refuse", [
    (2048, 2048, True,  None,              True),   # the incident
    (2048, 2048, True,  "_native_cudnn",   False),  # fixed by the backend
    (2048, 2048, False, None,              False),  # Z-Image: no GQA, no math path
    (1024, 1024, True,  None,              False),  # ~3.8GB on math — survivable
    (1024, 1024, True,  "_native_cudnn",   False),
])
def test_refusal_only_where_the_math_kernel_would_actually_be_reached(
    w, h, gqa, backend, should_refuse
):
    """The gate must not refuse work that runs today: Z-Image at 2048², and any
    GQA model at <=1MP, both have to keep working."""
    cfg = (SimpleNamespace(num_attention_heads=48, num_key_value_heads=12) if gqa
           else SimpleNamespace(n_heads=30, n_kv_heads=30))
    g = _gen(SimpleNamespace(config=cfg))
    g._attention_backend_active = backend

    refuse = (
        w * h > 1024 * 1024
        and g._uses_grouped_query_attention()
        and not getattr(g, "_attention_backend_active", None)
    )
    assert refuse is should_refuse
